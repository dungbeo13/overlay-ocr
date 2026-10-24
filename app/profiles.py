"""Glossary profiles: per game/app terms, characters and style notes, one JSON file each.

A profile is picked from the tray or automatically when one of its `processes` owns the foreground
window. Files live in PROFILES_DIR as <slug>.json (UTF-8, one term / character per line so they stay
easy to edit by hand); the display name stored inside the file is what the UI shows, so names can be
Vietnamese or Japanese while file names stay ASCII.

The store is shared by the GUI, the pipeline worker and the asyncio translate thread: every method
takes one lock and returns copies. Files are re-scanned at most once per `rescan_s` (and always
before a write), so hand edits made while the app runs are picked up and never overwritten. A file
that cannot be parsed is ignored (logged) and moved aside to <file>.bad before anything is written in
its place. A file that was readable and becomes unreadable (a typo in a hand edit, an editor caught
mid-save) keeps serving its last good version, so the app does not fall back to "default"; automatic
writes (auto terms) leave such a file alone until it is readable again.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path

from app.config import PROFILES_DIR
from app.translate.contextual import Character, HistoryLine, Term, TranslationContext
from app.translate.textnorm import normalize_text

log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_PROFILE", "AUTO_TERM_CAP", "Profile", "ProfileStore", "normalize_source", "junk_reason", "slugify",
    "profile_from_dict", "profile_to_json", "exe_matches",
]

DEFAULT_PROFILE = "default"
AUTO_TERM_CAP = 200  # LLM-proposed terms kept per profile (least recently proposed dropped first)
REJECTED_CAP = 500
MAX_TERM_SOURCE = 40
MAX_TERM_TARGET = 60
MAX_NAME = 80
FILE_FORMAT = 1

_CJK_RE = re.compile(r"[぀-ヿㇰ-ㇿ㐀-䶿一-鿿豈-﫿ｦ-ﾟ]")
_EDGE_PUNCT = " \t「」『』【】〔〕［］[]()（）<>《》〈〉\"'“”‘’・･.,、。!?！？:：;；…~〜-"
_SENTENCE_END = (".", "!", "?", "。", "！", "？")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}

# Honorific suffixes: "セラさん" proposed while the user has "セラ" is the same name.
_HONORIFICS = ("さん", "さま", "様", "くん", "君", "ちゃん", "殿", "どの", "先輩", "先生", "氏", "たん", "-san", "-sama",
               "-kun", "-chan", "-senpai", "-sensei")
# Words that must never be fixed automatically: pronouns, kinship / address words (the very thing the
# LLM has to choose per speaker), greetings and other common words.
_COMMON_JA = frozenset("""
私 わたし あたし わたくし 僕 ぼく 俺 おれ オレ 儂 わし 我 われ 我々 我ら 自分 うち あなた あんた 君 きみ お前 おまえ
てめえ 貴様 きさま 貴方 貴女 彼 彼女 彼ら あいつ こいつ そいつ 皆 みんな 皆さん みなさん 誰 だれ
さん 様 さま くん ちゃん 殿 先輩 先生 お兄ちゃん お兄さん 兄さん 兄ちゃん 兄貴 お姉ちゃん お姉さん 姉さん 姉ちゃん 姉貴
お父さん 父さん お母さん 母さん 父上 母上 父 母 兄 姉 弟 妹 おじさん おばさん おじいちゃん おばあちゃん おじいさん
おばあさん 爺さん 婆さん 坊ちゃん お嬢様 お嬢さん 旦那 旦那様 ご主人様 主人 王様 姫様 殿下 陛下
はい いいえ うん ええ いや ありがとう すみません ごめん ごめんなさい おはよう こんにちは こんばんは さようなら
何 なに これ それ あれ どれ ここ そこ あそこ 今日 明日 昨日 今 人 男 女 子供 大丈夫 本当 本当に
""".split())
_COMMON_EN = frozenset("""
i me my mine myself you your yours yourself he him his she her hers it its we us our ours they them their
this that these those the a an and or but yes no ok okay hey hi hello bye oh ah um uh sir madam ma'am miss mister
mr mrs ms lady lord master mom mommy mother dad daddy father brother sister bro sis son daughter uncle aunt grandpa
grandma grandfather grandmother boss man woman boy girl kid child friend everyone someone nobody people
what who why how where when thanks please sorry
""".split())
_PRONOUNS_VI = frozenset({
    "tôi", "tao", "tớ", "mình", "ta", "ông", "bà", "anh", "em", "chị", "cậu", "mày", "bạn", "cô", "chú", "bác", "dì",
    "cháu", "con", "nó", "hắn", "họ", "ngài", "ngươi", "người", "anh ấy", "chị ấy", "cô ấy", "em ấy", "ông ấy",
    "bà ấy", "cậu ấy", "bọn họ", "chúng ta", "chúng tôi", "chúng mình", "các bạn", "mọi người", "bọn mình",
})


# --- helpers ------------------------------------------------------------------------------------

def normalize_source(text: str) -> str:
    """Comparison key for term sources: NFKC (full/half-width unified), spacing collapsed, edge
    quotes/brackets/punctuation stripped, casefolded."""
    t = normalize_text(text or "", "ja")
    return t.strip(_EDGE_PUNCT).casefold()


def _strip_honorific(norm: str) -> str:
    for h in _HONORIFICS:
        if norm.endswith(h) and len(norm) > len(h):
            return norm[: -len(h)].rstrip(" ・")
    return norm


def _is_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def junk_reason(term: Term) -> str | None:
    """Why an LLM-proposed term should not be stored (None = keep it)."""
    src = normalize_text(term.source or "", "ja")
    tgt = normalize_text(term.target or "")
    if not src or not tgt:
        return "empty"
    if "\n" in src or "\n" in tgt:
        return "multi-line"
    if len(src) > MAX_TERM_SOURCE or len(tgt) > MAX_TERM_TARGET:
        return "too long"
    letters = sum(ch.isalpha() for ch in src)
    if letters == 0:
        return "no letters"
    if letters < 2:
        return "too short"
    norm = normalize_source(src)
    if _is_cjk(src):
        if norm == normalize_source(tgt) or _is_cjk(tgt):
            return "untranslated"
    else:
        if not any(ch.isupper() for ch in src):
            return "common word"  # Latin names and terms are capitalised; lowercase is ordinary text
        if len(src.split()) > 4:
            return "phrase"
    if src.endswith(_SENTENCE_END):
        return "sentence"
    if norm in _COMMON_JA or norm in _COMMON_EN or _strip_honorific(norm) in _COMMON_JA:
        return "common word"
    if tgt.strip(_EDGE_PUNCT).casefold() in _PRONOUNS_VI:
        return "pronoun"
    return None


def slugify(name: str) -> str:
    """ASCII file stem for a display name ("Hồ sơ Đặc biệt" -> "ho-so-dac-biet")."""
    t = unicodedata.normalize("NFKD", name.replace("đ", "d").replace("Đ", "D"))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^A-Za-z0-9]+", "-", t).strip("-").lower()[:40].strip("-")
    if not t:
        t = "profile-" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    if t in _RESERVED:
        t += "-1"
    return t


def _clean_name(name: str) -> str:
    n = " ".join(unicodedata.normalize("NFC", str(name or "")).split())[:MAX_NAME].strip()
    if not n:
        raise ValueError("profile name is empty")
    return n


def _key(name: str) -> str:
    return " ".join(unicodedata.normalize("NFC", name or "").split()).casefold()


_FOLDER_ENTRY = re.compile(r"^[^\\:]+\\[^\\:]+$")  # exactly one folder, no drive: "Summer Pockets\SiglusEngine.exe"


def _exe_name(exe: str) -> str:
    return os.path.basename(str(exe).strip().strip('"').replace("/", "\\")).lower()


def _exe_matches(exe: str, entry: str) -> bool:
    """Same rule as the anti-cheat guard: case-insensitive basename; an entry without ".exe" also
    matches the stem. An entry with a folder ("Summer Pockets/SiglusEngine.exe", for an exe every game of an
    engine shares) matches only that exe inside a folder of that name (`exe` must then be a full path)."""
    raw = str(entry or "").strip().strip('"').replace("/", "\\")
    if _FOLDER_ENTRY.match(raw):  # "<folder>\<exe>"; a full path ("C:/Games/x.exe") still matches by its name
        folder, _, base = raw.partition("\\")
        path = str(exe or "").strip().strip('"').replace("/", "\\")
        if "\\" not in path or not _exe_matches(path, base):
            return False
        return folder.casefold() in [p.casefold() for p in path.split("\\")[:-1]][-5:]
    name, e = _exe_name(exe), _exe_name(entry)
    if not name or not e:
        return False
    stem = name[:-4] if name.endswith(".exe") else name
    return e == name or (not e.endswith(".exe") and e == stem)


exe_matches = _exe_matches  # public name (app.lookup.templates)


def _str(value, limit: int | None = None) -> str:
    s = value.strip() if isinstance(value, str) else ""
    return s[:limit] if limit else s


# --- profile ------------------------------------------------------------------------------------

@dataclass
class Profile:
    name: str
    processes: list[str] = field(default_factory=list)  # exe names that auto-select this profile
    src_lang: str | None = None  # None = keep the app's language setting
    style: str = ""
    terms: list[Term] = field(default_factory=list)
    characters: list[Character] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)  # normalised sources of auto terms the user deleted
    # Game-lookup metadata (app.lookup): where imported entries came from, ids, Vietnamese patch links,
    # entries still to review. A JSON object kept as is; None = none, and ProfileStore.save keeps the
    # stored one (an editor that does not know it does not drop it). Not part of version().
    lookup: dict | None = None

    def version(self) -> str:
        """Stable short hash of style + terms + characters (+ the name-part links, when there are any; row order
        does not matter)."""
        payload = {
            "style": self.style.strip(),
            "terms": sorted([t.source, t.target, t.kind, t.note, t.auto] for t in self.terms),
            "characters": sorted([c.name, c.name_vi, c.gender, c.self_pronoun, c.notes] for c in self.characters),
        }
        aliases = self.name_aliases()
        if aliases:  # absent otherwise: the versions of profiles without links stay as they were
            payload["aliases"] = sorted(list(a) for a in aliases)
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]

    def name_aliases(self) -> tuple[tuple[str, str], ...]:
        """lookup["aliases"] ({name part: character name}) for the characters the profile has now."""
        raw = (self.lookup or {}).get("aliases") if isinstance(self.lookup, dict) else None
        if not isinstance(raw, dict):
            return ()
        names = {normalize_source(c.name) for c in self.characters}
        return tuple((str(k), str(v)) for k, v in raw.items()
                     if isinstance(k, str) and isinstance(v, str) and k.strip() and normalize_source(v) in names)

    def to_context(self, history: tuple[HistoryLine, ...] = ()) -> TranslationContext:
        """Context for the contextual translator: user terms first, then auto terms (auto=True)."""
        user = [t for t in self.terms if not t.auto]
        auto = [t for t in self.terms if t.auto]
        return TranslationContext(profile=self.name, style=self.style.strip(), terms=tuple(user + auto),
                                  characters=tuple(self.characters), history=tuple(history), version=self.version(),
                                  aliases=self.name_aliases())

    def copy(self) -> "Profile":
        return replace(self, processes=list(self.processes), terms=list(self.terms),
                       characters=list(self.characters), rejected=list(self.rejected),
                       lookup=_parse_lookup(self.lookup))

    def auto_terms(self) -> list[Term]:
        return [t for t in self.terms if t.auto]


def _parse_terms(raw) -> list[Term]:
    items: list = []
    if isinstance(raw, dict):  # hand-written shorthand: {"セラ": "Sera"}
        items = [{"source": k, "target": v} for k, v in raw.items()]
    elif isinstance(raw, list):
        items = raw
    out: list[Term] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            item = {"source": item[0], "target": item[1], "kind": item[2] if len(item) > 2 else "term"}
        if not isinstance(item, dict):
            log.warning("profile: skipping term %r (not an object)", item)
            continue
        source = _str(item.get("source"))
        norm = normalize_source(source)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(Term(source=source, target=_str(item.get("target")), kind=_str(item.get("kind"), 24) or "term",
                        note=_str(item.get("note")), auto=item.get("auto") is True))
    return out


def _parse_characters(raw) -> list[Character]:
    out: list[Character] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            log.warning("profile: skipping character %r (not an object)", item)
            continue
        name = _str(item.get("name"))
        norm = normalize_source(name)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(Character(name=name, name_vi=_str(item.get("name_vi")), gender=_str(item.get("gender"), 16),
                             self_pronoun=_str(item.get("self_pronoun"), 32), notes=_str(item.get("notes"))))
    return out


def _parse_lookup(raw) -> dict | None:
    """A deep copy of the lookup metadata when it is a JSON object, else None."""
    if not isinstance(raw, dict):
        return None
    try:
        return json.loads(json.dumps(raw, ensure_ascii=False, default=str))
    except (TypeError, ValueError, RecursionError):
        log.warning("profile: lookup metadata is not plain JSON; dropped")
        return None


def profile_from_dict(raw, fallback_name: str = DEFAULT_PROFILE) -> Profile:
    """Tolerant parser: unknown keys are ignored, malformed entries skipped. Raises ValueError only
    when the root is not a JSON object."""
    if not isinstance(raw, dict):
        raise ValueError("profile root is not a JSON object")
    try:
        name = _clean_name(raw.get("name") if isinstance(raw.get("name"), str) else "")
    except ValueError:
        name = fallback_name
    procs = raw.get("processes")
    src = raw.get("src_lang")
    rejected = raw.get("rejected")
    return Profile(
        name=name,
        processes=[p.strip() for p in procs if isinstance(p, str) and p.strip()] if isinstance(procs, list) else [],
        src_lang=src.strip().lower() if isinstance(src, str) and src.strip() else None,
        style=raw.get("style") if isinstance(raw.get("style"), str) else "",
        terms=_parse_terms(raw.get("terms")),
        characters=_parse_characters(raw.get("characters")),
        rejected=[r for r in rejected if isinstance(r, str) and r][:REJECTED_CAP] if isinstance(rejected, list) else [],
        lookup=_parse_lookup(raw.get("lookup")),
    )


def profile_to_json(profile: Profile) -> str:
    """Pretty JSON with one term / character per line."""
    def dump(v) -> str:
        return json.dumps(v, ensure_ascii=False)

    def term(t: Term) -> dict:
        d = {"source": t.source, "target": t.target, "kind": t.kind}
        if t.note:
            d["note"] = t.note
        if t.auto:
            d["auto"] = True
        return d

    def character(c: Character) -> dict:
        return {"name": c.name, "name_vi": c.name_vi, "gender": c.gender, "self_pronoun": c.self_pronoun,
                "notes": c.notes}

    head = {"format": FILE_FORMAT, "name": profile.name, "processes": profile.processes,
            "src_lang": profile.src_lang, "style": profile.style}
    parts = [f"  {dump(k)}: {dump(v)}" for k, v in head.items()]
    for key, rows in (("terms", [term(t) for t in profile.terms]),
                      ("characters", [character(c) for c in profile.characters])):
        body = ",\n".join("    " + dump(r) for r in rows)
        parts.append(f'  "{key}": [\n{body}\n  ]' if rows else f'  "{key}": []')
    parts.append(f'  "rejected": {dump(profile.rejected)}')
    if profile.lookup:  # one key per line, list items one per line (absent when there is none)
        rows = []
        for k, v in profile.lookup.items():
            if isinstance(v, list) and v:
                body = ",\n".join("      " + dump(x) for x in v)
                rows.append(f"    {dump(k)}: [\n{body}\n    ]")
            else:
                rows.append(f"    {dump(k)}: {dump(v)}")
        parts.append('  "lookup": {\n' + ",\n".join(rows) + "\n  }")
    return "{\n" + ",\n".join(parts) + "\n}\n"


# --- store --------------------------------------------------------------------------------------

@dataclass
class _File:
    stamp: tuple[int, int]  # (mtime_ns, size)
    profile: Profile | None  # None: the file could not be parsed
    broken: bool = False  # unreadable now; `profile` is its last good version


class ProfileStore:
    """Thread-safe access to the profile files in `directory`."""

    def __init__(self, directory: Path = PROFILES_DIR, *, auto_cap: int = AUTO_TERM_CAP,
                 rescan_s: float = 1.0) -> None:
        self.directory = Path(directory)
        self.auto_cap = auto_cap
        self.rescan_s = rescan_s
        self._lock = threading.RLock()
        self._files: dict[Path, _File] = {}
        self._index: dict[str, Path] = {}  # name key -> file
        self._scanned: float | None = None
        self._listeners: list[Callable[[str], None]] = []
        self._seen: dict[tuple[str, str], int] = {}  # (profile key, term key) -> last proposal tick
        self._tick = 0
        self._logged_bad: set[tuple[Path, tuple[int, int]]] = set()

    # --- public API ------------------------------------------------------------------------

    def names(self) -> list[str]:
        """Display names, "default" first (always present, even without a file)."""
        with self._lock:
            changed = self._scan()
            names = [f.profile.name for p, f in self._files.items()
                     if f.profile is not None and self._index.get(_key(f.profile.name)) == p]
        self._notify(changed)
        others = sorted((n for n in names if _key(n) != DEFAULT_PROFILE), key=_key)
        return [DEFAULT_PROFILE, *others]

    def get(self, name: str) -> Profile:
        """A copy of profile `name`; the "default" profile (fresh if it has no file) when `name` is
        unknown or its file is unreadable. Never raises."""
        with self._lock:
            changed = self._scan()
            profile = self._lookup(name) or self._lookup(DEFAULT_PROFILE) or Profile(DEFAULT_PROFILE)
            out = profile.copy()
        self._notify(changed)
        return out

    def exists(self, name: str) -> bool:
        with self._lock:
            changed = self._scan()
            found = self._lookup(name) is not None
        self._notify(changed)
        return found

    def save(self, profile: Profile) -> None:
        """Write `profile` (atomically) to its file, creating it if needed. A profile without lookup
        metadata (None) keeps the stored file's."""
        name = _clean_name(profile.name)
        stored = profile.copy()
        stored.name = name
        with self._lock:
            changed = self._scan(force=True)
            path = self._index.get(_key(name))
            if stored.lookup is None and path is not None and self._files[path].profile is not None:
                stored.lookup = _parse_lookup(self._files[path].profile.lookup)
            if path is not None and self._files[path].broken:
                self._quarantine(path)  # the unreadable text the user was editing stays as <file>.bad
            self._write(path or self._new_path(name), stored)
        self._notify([*changed, name])

    def create(self, name: str) -> Profile:
        """Create an empty profile (returns the existing one if the name is taken). Raises ValueError
        for an empty name."""
        name = _clean_name(name)
        with self._lock:
            changed = self._scan(force=True)
            existing = self._lookup(name)
            if existing is not None:
                out = existing.copy()
            else:
                out = Profile(name)
                self._write(self._new_path(name), out.copy())
                changed.append(name)
        self._notify(changed)
        return out

    def delete(self, name: str) -> None:
        """Remove a profile (its file is kept as <file>.deleted). "default" cannot be deleted."""
        if _key(name) == DEFAULT_PROFILE:
            raise ValueError("the default profile cannot be deleted")
        with self._lock:
            changed = self._scan(force=True)
            path = self._index.get(_key(name))
            if path is not None:
                removed = self._files[path].profile
                try:
                    os.replace(path, path.with_name(path.name + ".deleted"))
                except OSError:
                    path.unlink(missing_ok=True)
                self._files.pop(path, None)
                self._reindex()
                changed.append(removed.name if removed else name)
        self._notify(changed)

    def match_process(self, exe: str) -> str | None:
        """Name of the profile whose `processes` lists `exe` (case-insensitive basename)."""
        if not exe or not _exe_name(exe):
            return None
        with self._lock:
            changed = self._scan()
            profiles = [self._files[p].profile for p in self._index.values()]
        self._notify(changed)
        profiles.sort(key=lambda p: (_key(p.name) == DEFAULT_PROFILE, _key(p.name)))
        for profile in profiles:
            if any(_exe_matches(exe, entry) for entry in profile.processes):
                return profile.name
        return None

    def add_terms(self, name: str, terms: Iterable[Term]) -> int:
        """Merge LLM-proposed terms into profile `name` as auto terms; returns how many were added.

        User terms and characters always win (compared by normalize_source, also ignoring an
        honorific suffix), junk is dropped (see junk_reason), terms the user deleted are not proposed
        again, an already stored auto term keeps its first rendering, and at most `auto_cap` auto
        terms are kept (the least recently proposed are dropped first). Best effort: a file that
        cannot be written is logged and 0 returned (it runs on the pipeline threads).
        """
        proposals = [t for t in terms if isinstance(t, Term)]
        if not proposals:
            return 0
        try:
            return self._add_terms(name, proposals)
        except OSError:
            log.warning("profile %s: auto terms not saved", name, exc_info=True)
            return 0

    def _add_terms(self, name: str, proposals: list[Term]) -> int:
        with self._lock:
            changed = self._scan(force=True)
            current = self._lookup(name) or self._lookup(DEFAULT_PROFILE) or Profile(DEFAULT_PROFILE)
            pkey = _key(current.name)
            path = self._index.get(pkey)
            if path is not None and self._files[path].broken:
                log.info("profile %s: file is being edited (not valid JSON now); auto terms not added", current.name)
                proposals = []
            taken = {normalize_source(t.source) for t in current.terms if not t.auto}
            taken |= {normalize_source(c.name) for c in current.characters}
            taken |= set(current.rejected)
            taken.discard("")
            # Honorific variants are one name: 東雲くん after 東雲 (or the other way round) adds nothing.
            stored_auto = {k for t in current.terms if t.auto
                           for k in (normalize_source(t.source), _strip_honorific(normalize_source(t.source)))}
            fresh: list[Term] = []
            fresh_keys: set[str] = set()
            for t in proposals:
                reason = junk_reason(t)
                if reason is not None:
                    log.debug("auto term %r -> %r ignored: %s", t.source, t.target, reason)
                    continue
                norm = normalize_source(t.source)
                base = _strip_honorific(norm)
                if norm in taken or base in taken or norm in fresh_keys or base in fresh_keys:
                    continue
                self._tick += 1
                self._seen[(pkey, norm)] = self._tick
                if norm in stored_auto or base in stored_auto:
                    continue
                fresh_keys.update((norm, base))
                fresh.append(Term(source=normalize_text(t.source, "ja"), target=normalize_text(t.target),
                                  kind=_str(t.kind, 24) or "term", note=_str(t.note, 200), auto=True))
            added = 0
            if fresh:
                merged = self._cap(pkey, [*current.terms, *fresh])
                added = sum(1 for t in merged if t.auto and normalize_source(t.source) in fresh_keys)
                if added:
                    updated = current.copy()
                    updated.terms = merged
                    self._write(self._index.get(pkey) or self._new_path(current.name), updated)
                    changed.append(current.name)
                    log.info("profile %s: %d auto term(s) added", current.name, added)
        self._notify(changed)
        return added

    def add_listener(self, callback: Callable[[str], None]) -> None:
        """`callback(profile_name)` after any change (from the thread that made it)."""
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[str], None]) -> None:
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def path_of(self, name: str) -> Path | None:
        """File of profile `name`, None if it has none yet (diagnostics, "open folder")."""
        with self._lock:
            self._scan()
            return self._index.get(_key(name))

    # --- internals -------------------------------------------------------------------------

    def _lookup(self, name: str) -> Profile | None:
        path = self._index.get(_key(name))
        return self._files[path].profile if path is not None else None

    def _notify(self, names: list[str]) -> None:
        if not names:
            return
        with self._lock:
            listeners = list(self._listeners)
        for name in dict.fromkeys(names):
            for cb in listeners:
                try:
                    cb(name)
                except Exception:
                    log.exception("profile listener failed")

    def _scan(self, force: bool = False) -> list[str]:
        """Reload changed files; returns the names of profiles changed by someone else."""
        now = time.monotonic()
        if not force and self._scanned is not None and now - self._scanned < self.rescan_s:
            return []
        first = self._scanned is None
        self._scanned = now
        try:
            paths = sorted(p for p in self.directory.glob("*.json") if p.is_file())
        except OSError:
            paths = []
        files: dict[Path, _File] = {}
        changed: list[str] = []
        for path in paths:
            old = self._files.get(path)
            try:
                st = path.stat()
                stamp = (st.st_mtime_ns, st.st_size)
                if old is not None and old.stamp == stamp:
                    files[path] = old
                    continue
                data = path.read_bytes()
            except OSError as e:  # e.g. being replaced right now: keep what we had, retry next scan
                log.debug("profile file %s not readable now: %s", path.name, e)
                if old is not None:
                    files[path] = old
                continue
            profile = None
            try:  # UnicodeDecodeError and JSONDecodeError are ValueErrors
                profile = profile_from_dict(json.loads(data.decode("utf-8-sig")), path.stem)
            except (ValueError, RecursionError) as e:
                last_good = old.profile if old is not None else None
                if (path, stamp) not in self._logged_bad:
                    self._logged_bad.add((path, stamp))
                    log.warning("profile file %s is not valid (%s); %s", path, e,
                                "keeping its last good version until it is fixed" if last_good else "ignoring it")
                if last_good is not None:
                    files[path] = _File(stamp, last_good, broken=True)
                    continue
            files[path] = _File(stamp, profile)
            if not first:
                changed.append(profile.name if profile else path.stem)
        if not first:
            changed += [f.profile.name for p, f in self._files.items() if p not in files and f.profile is not None]
        self._files = files
        self._reindex()
        return changed

    def _reindex(self) -> None:
        index: dict[str, Path] = {}
        for path, f in self._files.items():  # sorted by path: the first file wins a name clash
            if f.profile is None:
                continue
            k = _key(f.profile.name)
            if k in index:
                log.warning("profile name %r used by both %s and %s; using the first", f.profile.name,
                            index[k].name, path.name)
                continue
            index[k] = path
        self._index = index

    def _new_path(self, name: str) -> Path:
        base = slugify(name)
        for i in range(1, 1000):
            cand = self.directory / (f"{base}.json" if i == 1 else f"{base}-{i}.json")
            entry = self._files.get(cand)
            if not cand.exists():
                return cand
            if entry is not None and entry.profile is None:
                self._quarantine(cand)  # an unreadable file: keep it aside, do not overwrite it
                return cand
        raise OSError(f"no free file name for profile {name!r}")

    def _quarantine(self, path: Path) -> None:
        for i in range(1, 100):
            target = path.with_name(path.name + (".bad" if i == 1 else f".bad{i}"))
            if not target.exists():
                os.replace(path, target)
                log.warning("unreadable profile file kept as %s", target.name)
                self._files.pop(path, None)
                return
        raise OSError(f"cannot move {path} aside")

    def _write(self, path: Path, profile: Profile) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        data = profile_to_json(profile).encode("utf-8")
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with open(tmp, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            for attempt in range(20):
                try:
                    os.replace(tmp, path)
                    break
                except PermissionError:  # another program (editor, antivirus) has the file open
                    if attempt == 19:
                        raise
                    time.sleep(0.05)
        except BaseException:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        st = path.stat()
        self._files[path] = _File((st.st_mtime_ns, st.st_size), profile.copy())
        self._files = dict(sorted(self._files.items()))
        self._reindex()

    def _cap(self, pkey: str, terms: list[Term]) -> list[Term]:
        autos = [(i, t) for i, t in enumerate(terms) if t.auto]
        excess = len(autos) - max(0, self.auto_cap)
        if excess <= 0:
            return terms
        # least recently proposed first; never-proposed-this-session ones in file order (oldest first)
        order = sorted(autos, key=lambda it: (self._seen.get((pkey, normalize_source(it[1].source)), -1), it[0]))
        drop = {i for i, _ in order[:excess]}
        for i in drop:
            self._seen.pop((pkey, normalize_source(terms[i].source)), None)
        return [t for i, t in enumerate(terms) if i not in drop]
