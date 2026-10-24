"""LookupReport -> a draft glossary Profile shaped like profiles/templates/blue_archive.json, and merging a
draft into an existing profile without touching what the user wrote.

Draft rules (the project's conventions, reports/ and research_notes/):
- a character's `name` is the name as the game's text writes it (牧瀬紅莉栖: the space VNDB puts between
  family and given name is dropped, a ・ is kept); `name_vi` keeps the romanization (Hepburn, long vowels
  as "ou"/"uu" like "Yuuka"), family name first as in the source text, no honorific; gender from the
  sources; self_pronoun stays empty (the user picks it with the pronoun picker); notes only say the role;
- the parts of a full name (牧瀬, 紅莉栖) become "person" terms so a partial name maps too (one-character
  parts and common words are left out: 至 alone would rewrite every 至; a two-kana part such as るか, which
  is also inside するか, is only a suggestion, an auto term); profile.lookup["aliases"] links each part one
  character owns to that character, so a name box showing 紅莉栖 gets her gender and pronoun rules;
- VNDB aliases (nicknames) are left out unless asked for (`include_aliases`): VNDB has no spoiler flag on
  them (鷹原羽未, Servants' true names). When asked for, only nickname-shaped ones (short kana, ～ちゃん…) that
  belong to one character and name nobody else are kept, as suggestions (auto terms: yellow in the editor,
  a hint for the LLM until the user confirms them);
- a common word used as a name (少年, 謎の男) is translated, not romanized; with an official Vietnamese
  version, Wikidata's Vietnamese label wins (旅人 -> Nhà Lữ Hành);
- places / organizations / the game title only when a source names them.
Every imported entry is listed in profile.lookup["review"] with its sources (a record of where it came
from; the user reviews the draft before it is saved, and auto terms in the editor).
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.lookup.names import (all_kanji, compact, fix_long_vowels, is_cjk, latin_key, name_key, nfc, norm_title,
                              split_parts)
from app.lookup.types import CharacterCandidate, LookupReport
from app.profiles import _HONORIFICS, DEFAULT_PROFILE, Profile, junk_reason, normalize_source, profile_from_dict
from app.translate.contextual import Character, Term

log = logging.getLogger(__name__)

ROLE_NOTES = {"main": "Nhân vật chính (người kể chuyện / người chơi).", "primary": "Nhân vật chính.",
              "side": "Nhân vật phụ.", "appears": "Nhân vật xuất hiện ít."}
_LATIN_HONORIFIC = re.compile(r"(?i)[-\s](san|sama|kun|chan|senpai|sensei|dono|tan)$")
NAME_ORDERS = ("source", "given_first")
# Common words some games use as a character's name: translated, not romanized ("Shounen").
DESCRIPTOR_NAMES = {"少年": "Cậu bé", "少女": "Cô bé", "青年": "Chàng trai", "老人": "Ông lão", "老婆": "Bà lão",
                    "謎の男": "Người đàn ông bí ẩn", "謎の女": "Người phụ nữ bí ẩn", "謎の少女": "Cô gái bí ẩn",
                    "謎の少年": "Cậu bé bí ẩn", "謎の人物": "Nhân vật bí ẩn", "男": "Người đàn ông", "女": "Người phụ nữ",
                    "男の子": "Cậu bé", "女の子": "Cô bé", "母": "Mẹ", "父": "Bố", "母親": "Mẹ", "父親": "Bố",
                    "店員": "Nhân viên", "店長": "Chủ tiệm", "兵士": "Người lính", "医者": "Bác sĩ"}
_LATIN_TITLES = ((re.compile(r"^(Doctor|Dr\.?)\s+"), "Tiến sĩ "), (re.compile(r"^Professor\s+"), "Giáo sư "))
# Name parts that are particles of a foreign name (イリヤスフィール・フォン・アインツベルン), not a name.
_NAME_PARTICLES = frozenset({"フォン", "ヴォン", "ヴァン", "ファン", "ディ", "デル", "デラ", "オブ", "ドゥ", "エル", "ジュニア",
                             "セント", "ド", "デ", "ダ", "ラ", "ル", "ザ", "ヴィ", "フォー"})
_KANA_RE = re.compile(r"^[぀-ゟ゠-ヿｦ-ﾟー・･]+$")
_NICKNAME_ENDINGS = ("ちゃん", "くん", "っち", "にゃん", "たん", "ちゃま", "先輩", "せんぱい", "さん")
MAX_NICKNAME = 5  # kana characters


@dataclass
class MergeStats:
    added_characters: list[str] = field(default_factory=list)
    added_terms: list[str] = field(default_factory=list)
    kept_characters: list[str] = field(default_factory=list)  # already in the profile: left as the user has them
    kept_terms: list[str] = field(default_factory=list)

    @property
    def added(self) -> int:
        return len(self.added_characters) + len(self.added_terms)


def _now() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def text_name(original: str) -> str:
    """The name as game text writes it: 牧瀬 紅莉栖 -> 牧瀬紅莉栖 (spaces only exist in databases)."""
    t = nfc(original)
    return re.sub(r"[ 　]+", "", t) if is_cjk(t) else t


def display_name(c: CharacterCandidate, name_order: str = "source", *, prefer_vi: bool = False) -> str:
    """The romanized display name: Hepburn long vowels doubled, no honorific, family name first unless
    `name_order` is "given_first" (the order of a two-part name is then swapped). A common word used as a name is
    translated (少年 -> Cậu bé); `prefer_vi` (the game has an official Vietnamese version): Wikidata's Vietnamese
    label first."""
    original = text_name(c.original)
    if original in DESCRIPTOR_NAMES:
        return DESCRIPTOR_NAMES[original]
    label_vi = re.sub(r"\s*\([^)]*\)$", "", nfc(c.label_vi)) if c.label_vi and not is_cjk(c.label_vi) else ""
    if prefer_vi and label_vi:
        return label_vi
    rom = fix_long_vowels(nfc(c.romanized)) if c.romanized and not is_cjk(c.romanized) else ""
    rom = rom or label_vi
    rom = _LATIN_HONORIFIC.sub("", re.sub(r"\s*\([^)]*\)$", "", rom)).strip()  # "Venti (Genshin Impact)" -> "Venti"
    for rx, vi in _LATIN_TITLES:  # "Doctor Nakabachi" -> "Tiến sĩ Nakabachi" (no English word in Vietnamese text)
        rom = rx.sub(vi, rom)
    words = rom.split()
    raw = split_parts(nfc(c.original))
    if "wikidata" in c.sources and "vndb" not in c.sources and len(raw) == 2 and len(words) == 2 \
            and all(not _KANA_RE.match(p) for p in raw):
        rom = f"{words[1]} {words[0]}"  # Wikidata's English label is given name first; the text's order is family first
        words = rom.split()
    if name_order == "given_first" and len(c.parts) == 2 and len(words) == 2 and is_cjk(c.original):
        rom = f"{words[1]} {words[0]}"
    return rom or original


def _strip_honorific(name: str) -> str:
    for h in _HONORIFICS:
        if not h.startswith("-") and name.endswith(h) and len(name) > len(h) + 1:
            return name[: -len(h)]
    return name


def _usable_part(part: str, target: str) -> bool:
    p = compact(part)
    if len(p) < 2 or not target or is_cjk(target) or p in _NAME_PARTICLES:
        return False  # 至 (Itaru) alone is also the verb "reach": never a fixed rendering
    return junk_reason(Term(p, target, "person")) is None


def _short_kana(part: str) -> bool:
    """A kana part of two letters: also inside ordinary words (るか in するか, りえ in ありえない, うみ "sea").
    Three or more (まゆり, the name box Mayuri's lines show) stay fixed renderings."""
    p = compact(part)
    return bool(_KANA_RE.match(p)) and len(p) <= 2


def _usable_alias(alias: str, target: str) -> bool:
    a = compact(alias)
    if not a or not target or is_cjk(target) or not is_cjk(a):
        return False
    if all_kanji(a) and len(a) <= 2:
        return False  # 助手 "Joshu": a common word more often than a name
    return junk_reason(Term(a, target, "person")) is None


def _nickname(alias: str) -> bool:
    """Shaped like a nickname (オカリン, まゆしぃ, ダル, ～ちゃん), not like a full or real name (鷹原羽未, ジョン・タイター)."""
    raw = nfc(alias)
    if " " in raw or "・" in raw or "･" in raw:
        return False  # two names: a full name (often the spoiler: a real name, a true identity)
    a = compact(raw)
    if a.endswith(_NICKNAME_ENDINGS) and len(a) <= MAX_NICKNAME + 3:
        return True
    return bool(_KANA_RE.match(a)) and len(a) <= MAX_NICKNAME


def nickname_aliases(characters: list[CharacterCandidate]) -> dict[int, list[tuple[str, str]]]:
    """{index in `characters`: its aliases safe to suggest}. VNDB's aliases have no spoiler flag, so only nicknames
    are kept, and of those none that two characters share, that starts with another character's family name, that
    romanizes to another character's name (エミヤ: Archer is Emiya), or that shares a word with another
    character's alias (the "Titor" identities)."""
    def pieces(text: str) -> set[str]:
        return {name_key(p) for p in split_parts(nfc(text)) if len(compact(p)) >= 2}

    owners: dict[str, set[int]] = {}
    alias_pieces: dict[int, set[str]] = {}
    for i, c in enumerate(characters):
        for o, _ro in c.aliases:
            if o:
                owners.setdefault(name_key(o), set()).add(i)
                alias_pieces.setdefault(i, set()).update(pieces(o))
    out: dict[int, list[tuple[str, str]]] = {}
    for i, c in enumerate(characters):
        others = [x for j, x in enumerate(characters) if j != i]
        families = {name_key(x.parts[0][0]) for x in others if x.parts} - {""}
        other_latin = {latin_key(r) for x in others for r in [x.romanized] + [p[1] for p in x.parts] if r} - {""}
        other_names = {name_key(x.original) for x in others} | {name_key(p[0]) for x in others for p in x.parts}
        other_pieces = set().union(*(alias_pieces.get(j, set()) for j in range(len(characters)) if j != i))
        keep = []
        for o, ro in c.aliases:
            key = name_key(o)
            if not o or not _nickname(o) or len(owners.get(key, ())) > 1 or key in other_names:
                continue
            if any(key.startswith(f) for f in families) or (ro and latin_key(ro) in other_latin):
                continue
            if pieces(o) & other_pieces:
                continue
            keep.append((o, ro))
        if keep:
            out[i] = keep
    return out


def profile_title(report: LookupReport) -> str:
    """The profile's name: the title the user (or the game's files) gave when it is this game's ("Ever17", not
    Wikidata's "Ever 17: The Out of Infinity"), else Steam's, else the chosen title."""
    query = " ".join((report.query or "").split())
    titles = [c.title for c in report.candidates if c.title] + ([report.title] if report.title else [])
    if query and any(norm_title(query) == norm_title(t) for t in titles):
        return query
    if query and len(norm_title(query)) >= 4 and norm_title(report.title).startswith(norm_title(query)):
        return query
    steam = next((c.title for c in report.candidates if c.source == "steam" and c.title), "")
    return steam or report.title or query


def has_aliases(report: LookupReport) -> bool:
    """The report has nicknames that could be added on request (DraftPanel's "Thêm biệt danh" option)."""
    return report.src_lang == "ja" and bool(nickname_aliases(report.characters))


def build_profile(report: LookupReport, *, name: str = "", exe: str = "", name_order: str = "source",
                  include_aliases: bool = False, max_characters: int = 60) -> Profile:
    """The draft profile for `report` (the template itself when one matched)."""
    ident = report.identity
    exe = exe or (ident.exe_name if ident is not None and not ident.generic_exe else "")
    if report.template:
        try:
            prof = profile_from_dict(json.loads(Path(report.template).read_text(encoding="utf-8-sig")))
        except (OSError, ValueError):
            log.warning("template %s unreadable", report.template, exc_info=True)
        else:
            if exe and not any(p.casefold() == exe.casefold() for p in prof.processes):
                prof.processes.append(exe)
            if name:
                prof.name = name
            prof.lookup = {**(prof.lookup or {}), "template": Path(report.template).name, "imported": _now()}
            return prof

    lang = report.src_lang if report.src_lang in ("ja", "en") else None
    prof = Profile(name=nfc(name or profile_title(report)) or "Game", processes=[exe] if exe else [],
                   src_lang=lang)
    review_chars: dict[str, list[str]] = {}
    review_terms: dict[str, list[str]] = {}
    aliases: dict[str, str] = {}
    seen_chars: set[str] = set()
    prefer_vi = bool(report.vi_official)
    for c in report.characters[:max_characters]:
        cname = _strip_honorific(text_name(c.original))
        key = normalize_source(cname)
        if not key or key in seen_chars or (lang == "ja" and not is_cjk(cname) and not c.native):
            continue  # a romanized name only appears in Japanese text when a source says so (2B, 9S)
        seen_chars.add(key)
        vi = display_name(c, name_order, prefer_vi=prefer_vi)
        notes = ROLE_NOTES.get(c.role, "")
        prof.characters.append(Character(name=cname, name_vi=vi, gender=c.gender if c.gender in ("nam", "nữ") else "",
                                         self_pronoun="", notes=notes))
        review_chars[cname] = list(c.sources)

    # Name parts: 牧瀬 -> Makise (one rendering per part; a part two characters spell differently is dropped).
    part_targets: dict[str, set[str]] = {}
    part_owner: dict[str, list[str]] = {}
    part_sources: dict[str, list[str]] = {}
    if lang == "ja":
        by_name = {normalize_source(ch.name): ch for ch in prof.characters}
        for c in report.characters[:max_characters]:
            owner = by_name.get(normalize_source(_strip_honorific(text_name(c.original))))
            if owner is None:
                continue
            for part, rom in c.parts:
                part, rom = text_name(part), fix_long_vowels(rom)
                if not _usable_part(part, rom) or normalize_source(part) in seen_chars:
                    continue
                part_targets.setdefault(part, set()).add(rom)
                part_owner.setdefault(part, []).append(owner.name_vi)
                part_sources.setdefault(part, list(c.sources))
                aliases.setdefault(part, owner.name)
    taken = set(seen_chars)
    for part, targets in part_targets.items():
        owners = list(dict.fromkeys(part_owner[part]))
        if len(owners) > 1:
            aliases.pop(part, None)  # a family name two characters share names neither of them alone
        if len(targets) != 1:
            aliases.pop(part, None)
            continue
        note = "một phần tên của " + ", ".join(owners[:3]) + (" …" if len(owners) > 3 else "")
        # A short kana part (るか, りえ, うみ) is also part of ordinary words: a suggestion, sent only when it is on screen.
        prof.terms.append(Term(part, next(iter(targets)), "person", note=note, auto=_short_kana(part)))
        taken.add(normalize_source(part))
        review_terms[part] = part_sources[part]

    if include_aliases and lang == "ja":
        chars = report.characters[:max_characters]
        by_name = {normalize_source(ch.name): ch for ch in prof.characters}
        for i, safe in nickname_aliases(chars).items():
            owner = by_name.get(normalize_source(_strip_honorific(text_name(chars[i].original))))
            for alias, rom in safe:
                a, target = text_name(alias), fix_long_vowels(rom)
                if not _usable_alias(a, target) or normalize_source(a) in taken:
                    continue
                prof.terms.append(Term(a, target, "person", note="biệt danh", auto=True))
                taken.add(normalize_source(a))
                review_terms[a] = list(chars[i].sources)
                if owner is not None:
                    aliases.setdefault(a, owner.name)  # ダル in the name box is 橋田至

    for t in report.terms:
        key = normalize_source(t.source)
        if not key or key in taken or not t.target:
            continue
        if lang == "ja" and not is_cjk(t.source):
            continue
        prof.terms.append(Term(nfc(t.source), nfc(t.target), t.kind or "term", note=t.note, auto=t.auto))
        taken.add(key)
        review_terms[nfc(t.source)] = list(t.sources)

    prof.lookup = lookup_metadata(report, review_chars, review_terms, aliases)
    return prof


def lookup_metadata(report: LookupReport, review_chars: dict[str, list[str]] | None = None,
                    review_terms: dict[str, list[str]] | None = None, aliases: dict[str, str] | None = None) -> dict:
    """The profile.lookup block for `report` (JSON-safe)."""
    meta: dict = {"title": report.title or report.query, "imported": _now()}
    if report.title_ja:
        meta["title_ja"] = report.title_ja
    if report.title_vi:
        meta["title_vi"] = report.title_vi
    if report.ids:
        meta["ids"] = dict(report.ids)
    if report.vi_official is not None:
        meta["vi_official"] = report.vi_official
    if report.vi_note:
        meta["vi_note"] = report.vi_note
    if report.patches:
        meta["patches"] = [{"title": p.title, "url": p.url, "source": p.source, "note": p.note, "official": p.official,
                            "verified": p.verified, **({"info_url": p.info_url} if p.info_url else {})}
                           for p in report.patches]
    sources = [s.name for s in report.statuses if s.state == "ok" and s.name not in ("template",)]
    if sources:
        meta["sources"] = sources
    if report.attribution:
        meta["attribution"] = [dict(a) for a in report.attribution]
    if review_chars or review_terms:
        meta["review"] = {"characters": review_chars or {}, "terms": review_terms or {}}
    if aliases:
        meta["aliases"] = aliases
    return meta


# --- review marks -------------------------------------------------------------------------------

def pending_review(profile: Profile) -> tuple[set[str], set[str]]:
    """(character keys, term keys) still marked as imported (normalize_source keys)."""
    review = _dict(_dict(profile.lookup).get("review"))
    chars = {normalize_source(str(k)) for k in _dict(review.get("characters"))}
    terms = {normalize_source(str(k)) for k in _dict(review.get("terms"))}
    return chars - {""}, terms - {""}


def mark_reviewed(profile: Profile, *, characters: list[str] = (), terms: list[str] = (),
                  everything: bool = False) -> Profile:
    """A copy with the given entries (or all of them) no longer marked as imported."""
    out = profile.copy()
    meta = _dict(out.lookup)
    review = _dict(meta.get("review"))
    if everything:
        review = {}
    else:
        drop_c = {normalize_source(x) for x in characters}
        drop_t = {normalize_source(x) for x in terms}
        review = {"characters": {k: v for k, v in _dict(review.get("characters")).items()
                                 if normalize_source(str(k)) not in drop_c},
                  "terms": {k: v for k, v in _dict(review.get("terms")).items()
                            if normalize_source(str(k)) not in drop_t}}
    if review.get("characters") or review.get("terms"):
        meta["review"] = review
    else:
        meta.pop("review", None)
    out.lookup = meta
    return out


# --- merge into an existing profile -----------------------------------------------------------

def _merge_list(old: list, new: list, key) -> list:
    seen = {key(x) for x in old if isinstance(x, dict)}
    out = [x for x in old if isinstance(x, dict)]
    for x in new:
        if isinstance(x, dict) and key(x) not in seen:
            seen.add(key(x))
            out.append(x)
    return out


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


def derive_aliases(profile: Profile) -> dict[str, str]:
    """{name part: character} from the profile's own "person" terms: a term that begins or ends exactly one
    character's name (紅莉栖 in 牧瀬紅莉栖), so a name box showing only that part is linked to the character. For a
    profile an assistant wrote (claude_request.parse_import); the lookup builds this map itself."""
    names = [(compact(c.name), c.name) for c in profile.characters if c.name and is_cjk(c.name)]
    out: dict[str, str] = {}
    for t in profile.terms:
        part = compact(t.source)
        if t.kind != "person" or t.auto or len(part) < 2 or not is_cjk(part):
            continue
        owners = [full for key, full in names if key != part and (key.startswith(part) or key.endswith(part))]
        if len(owners) == 1:
            out[t.source] = owners[0]
    return out


def merge_into(existing: Profile, incoming: Profile, *, new: bool = False) -> tuple[Profile, MergeStats]:
    """`existing` plus what `incoming` has and it does not. Nothing the user has is changed: an entry with
    the same source text (normalize_source) stays as it is, terms the user deleted (rejected) are not
    added back, the style is only filled when empty, processes are added. src_lang only on a `new` profile:
    None on an existing one means "keep the app's language setting". The default profile (every game without
    one uses it) never gets a game's processes or language."""
    out = existing.copy()
    stats = MergeStats()
    default = normalize_source(out.name) == normalize_source(DEFAULT_PROFILE)
    char_keys = {normalize_source(c.name) for c in out.characters}
    term_keys = {normalize_source(t.source) for t in out.terms}
    rejected = set(out.rejected)
    added_c: dict[str, list[str]] = {}
    added_t: dict[str, list[str]] = {}
    in_review = _dict(_dict(incoming.lookup).get("review"))
    for c in incoming.characters:
        k = normalize_source(c.name)
        if not k:
            continue
        if k in char_keys or k in term_keys:
            stats.kept_characters.append(c.name)
            continue
        out.characters.append(c)
        char_keys.add(k)
        stats.added_characters.append(c.name)
        added_c[c.name] = [str(x) for x in _list(_dict(in_review.get("characters")).get(c.name))]
    for t in incoming.terms:
        k = normalize_source(t.source)
        if not k:
            continue
        if k in term_keys or k in char_keys or k in rejected:
            stats.kept_terms.append(t.source)
            continue
        out.terms.append(t)
        term_keys.add(k)
        stats.added_terms.append(t.source)
        added_t[t.source] = [str(x) for x in _list(_dict(in_review.get("terms")).get(t.source))]
    if not default:
        for p in incoming.processes:
            if not any(p.casefold() == q.casefold() for q in out.processes):
                out.processes.append(p)
        if new and out.src_lang is None:
            out.src_lang = incoming.src_lang
    if not out.style.strip():
        out.style = incoming.style
    meta = dict(_dict(out.lookup))
    inc = _dict(incoming.lookup)  # an assistant's answer may hold anything here: only the expected shapes count
    for k, v in inc.items():
        if k == "imported":
            meta[k] = v
        elif k not in ("review", "aliases", "ids", "patches", "attribution", "sources"):
            meta.setdefault(k, v)
    meta["ids"] = {**{str(k): str(v) for k, v in _dict(inc.get("ids")).items()}, **_dict(meta.get("ids"))}
    if not meta["ids"]:
        meta.pop("ids")
    for k, key in (("patches", lambda x: str(x.get("url", "")).casefold()),
                   ("attribution", lambda x: str(x.get("url", "")).casefold())):
        merged = _merge_list(_list(meta.get(k)), _list(inc.get(k)), key)
        if merged:
            meta[k] = merged
        else:
            meta.pop(k, None)
    sources = list(dict.fromkeys(str(s) for s in [*_list(meta.get("sources")), *_list(inc.get("sources"))]
                                 if isinstance(s, str)))
    if sources:
        meta["sources"] = sources
    aliases = {**{str(k): str(v) for k, v in _dict(inc.get("aliases")).items()
                  if isinstance(v, str) and normalize_source(v) in char_keys},
               **_dict(meta.get("aliases"))}
    if aliases:
        meta["aliases"] = aliases
    review = _dict(meta.get("review"))
    review = {"characters": {**_dict(review.get("characters")), **added_c},
              "terms": {**_dict(review.get("terms")), **added_t}}
    if review["characters"] or review["terms"]:
        meta["review"] = review
    else:
        meta.pop("review", None)
    out.lookup = meta or None
    return out, stats


# --- text for the UI / CLI ------------------------------------------------------------------------

def describe(report: LookupReport) -> list[str]:
    """Short Vietnamese lines about what was found (for the confirmation dialog and the CLI)."""
    if report.template:
        return [f"Dùng hồ sơ mẫu có sẵn: {report.title}."]
    if not report.title:
        return ["Không tìm thấy dữ liệu nào về game này."] + report.notes
    head = report.title + (f" ({report.title_ja})" if report.title_ja and report.title_ja != report.title else "")
    lines = [f"{'Game' if report.confidence == 'high' else 'Gần đúng nhất'}: {head}"]
    if report.vi_official and report.vi_note:
        lines.append(report.vi_note)  # first: the user may not need a screen translation at all
    if report.confidence != "high":
        others = [c.title for c in report.candidates if c.title and c.title != report.title] + \
            [a.title for a in report.alternatives]
        if others:
            lines.append("Có phải bạn tìm: " + "; ".join(list(dict.fromkeys(others))[:3]) + "?")
        return lines + report.notes
    counts = []
    if report.characters:
        counts.append(f"{len(report.characters)} nhân vật")
    if report.terms:
        counts.append(f"{len(report.terms)} thuật ngữ")
    if report.patches:
        counts.append(f"{len(report.patches)} trang Việt hóa")
    lines.append("Tìm thấy: " + (", ".join(counts) if counts else "chỉ có tên game"))
    if report.src_lang:
        lines.append("Ngôn ngữ gốc: " + {"ja": "tiếng Nhật", "en": "tiếng Anh"}.get(report.src_lang, report.src_lang))
    if report.vi_note and not report.vi_official:
        lines.append(report.vi_note)
    return lines + report.notes
