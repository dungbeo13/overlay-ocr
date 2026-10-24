""""Tạo yêu cầu tra cứu": a ready-to-paste prompt asking Claude (or another assistant) for a glossary profile
in this app's exact JSON format, and parse_import() for the answer ("Nhập hồ sơ từ file" / from the
clipboard): raw JSON, a ```json block pasted from a chat with text around it, or a profile file.

Nothing is sent anywhere by this module: the user copies the prompt and pastes the answer back."""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.lookup.names import is_cjk
from app.lookup.profile_builder import _now, derive_aliases
from app.lookup.types import GameIdentity, LookupReport
from app.profiles import Profile, profile_from_dict

MAX_LISTED = 40  # entries of an existing draft listed in the prompt

_EXAMPLE = """{
  "format": 1,
  "name": "Steins;Gate",
  "processes": ["STEINS;GATE.exe"],
  "src_lang": "ja",
  "style": "",
  "terms": [
    {"source": "未来ガジェット研究所", "target": "Phòng thí nghiệm Tiện ích Tương lai", "kind": "term", "note": "đề xuất"},
    {"source": "秋葉原", "target": "Akihabara", "kind": "place"},
    {"source": "牧瀬", "target": "Makise", "kind": "person", "note": "một phần tên của Makise Kurisu"},
    {"source": "紅莉栖", "target": "Kurisu", "kind": "person", "note": "một phần tên của Makise Kurisu"}
  ],
  "characters": [
    {"name": "牧瀬紅莉栖", "name_vi": "Makise Kurisu", "gender": "nữ", "self_pronoun": "", "notes": "Nhân vật chính. Nhà khoa học thần kinh 18 tuổi; tự xưng 私."}
  ],
  "lookup": {
    "sources": ["https://vndb.org/v2002", "https://steins-gate.fandom.com/wiki/Kurisu_Makise"],
    "patches": [{"title": "Steins;Gate tiếng Việt", "url": "https://...", "note": "bản vá không chính thức"}]
  }
}"""


def _lang_name(code: str | None) -> str:
    return {"ja": "tiếng Nhật", "en": "tiếng Anh"}.get(code or "", code or "chưa rõ")


def build_request(game: str | GameIdentity | LookupReport, *, profile: Profile | None = None,
                  exe_name: str = "", src_lang: str | None = None) -> str:
    """The Markdown prompt for `game` (a title, the identity from the game's files, or a lookup report:
    what it found is listed so the assistant completes and corrects it instead of starting over)."""
    report = game if isinstance(game, LookupReport) else None
    ident = report.identity if report else game if isinstance(game, GameIdentity) else None
    title = (report.title or report.query) if report else ident.title if ident else str(game)
    exe = exe_name or (ident.exe_name if ident and not ident.generic_exe else "")
    lang = src_lang or (report.src_lang if report else None) or (profile.src_lang if profile else None)
    lines = [f"# Yêu cầu: hồ sơ thuật ngữ cho game “{title}”", "",
             "Mình dùng một app dịch màn hình game sang tiếng Việt (OCR + dịch máy + LLM chạy trên máy). App cần "
             "một **hồ sơ** cho game này: tên nhân vật, giới tính, thuật ngữ cố định. Hãy tra cứu (wiki của game, "
             "VNDB, Wikidata, trang chính thức…) và trả lời bằng **đúng một khối ```json** theo định dạng bên dưới, "
             "không thêm chữ nào khác trong khối đó.", "", "## Game", f"- Tên: {title}"]
    if report and report.title_ja and report.title_ja != title:
        lines.append(f"- Tên tiếng Nhật: {report.title_ja}")
    if exe:
        lines.append(f"- Tệp chạy: `{exe}` (ghi vào `processes`)")
    lines.append(f"- Ngôn ngữ chữ trong game: {_lang_name(lang)} (`src_lang`: \"ja\" hoặc \"en\")")
    if report and report.ids:
        ids = ", ".join(f"{k}: {v}" for k, v in report.ids.items() if k in ("steam", "vndb", "wikidata", "fandom"))
        if ids:
            lines.append(f"- Mã trên các trang dữ liệu: {ids}")
    known_chars = list(profile.characters) if profile else []
    # Suggestions (auto terms: nicknames, the LLM's proposals) are not listed: unconfirmed, and a nickname
    # may give the story away.
    known_terms = [t for t in profile.terms if not t.auto] if profile else []
    if known_chars or known_terms:
        lines += ["", "## App đã có sẵn (giữ nguyên nếu đúng, sửa nếu sai, bổ sung phần còn thiếu)"]
        for c in known_chars[:MAX_LISTED]:
            bits = [x for x in (c.name_vi, c.gender) if x]
            lines.append(f"- Nhân vật {c.name}" + (f" ({', '.join(bits)})" if bits else ""))
        for t in known_terms[:MAX_LISTED]:
            lines.append(f"- Thuật ngữ {t.source} → {t.target}")
        more = max(0, len(known_chars) - MAX_LISTED) + max(0, len(known_terms) - MAX_LISTED)
        if more:
            lines.append(f"- … và {more} mục khác")
    ja = (lang or "ja") == "ja"
    lines += ["", "## Quy tắc",
              ("1. `characters[].name`: tên **đúng như chữ hiện trong game** (kanji/kana, viết liền, không dấu cách "
               "giữa họ và tên, không kèm さん/くん/様…). Nếu ô tên trong game chỉ ghi tên gọi (ví dụ まゆり), "
               "dùng đúng dạng đó." if ja else
               "1. `characters[].name`: tên đúng như chữ tiếng Anh trong game, không kèm Mr./Ms."),
              "2. `name_vi`: tên La-tinh như bản chính thức / cộng đồng hay dùng (romaji Hepburn, nguyên âm dài viết "
              "\"ou\", \"uu\" như Yuuka, họ trước tên như thứ tự gốc). Không dịch Hán-Việt, không kèm -san/-chan.",
              "3. `gender`: \"nam\", \"nữ\" hoặc \"\" khi nguồn không nói rõ.",
              "4. `self_pronoun`: để \"\" (người dùng tự chọn cách xưng hô trong app). Trong `notes` có thể ghi "
              "ngắn gọn bằng tiếng Việt: vai trò, nhân vật gọi mình bằng gì trong tiếng gốc (私, 僕, 俺, あたし…), "
              "quan hệ chính. Không quá 2 câu mỗi nhân vật.",
              ("5. `terms`: chỉ địa danh, tổ chức, vật phẩm, kỹ năng có tên riêng mà nguồn có ghi. `kind` là "
               "\"place\", \"term\", \"item\", \"skill\" hoặc \"person\". Họ, tên gọi tắt, biệt danh của nhân vật "
               "(牧瀬, 紅莉栖, オカリン) đưa vào `terms` với kind \"person\" để chữ trong game chỉ ghi một phần tên "
               "vẫn dịch đúng." if ja else
               "5. `terms`: chỉ địa danh, tổ chức, vật phẩm, kỹ năng có tên riêng mà nguồn có ghi; `kind` là "
               "\"place\", \"term\", \"item\", \"skill\" hoặc \"person\"."),
              "6. `target`: giữ tên riêng ở dạng La-tinh / tên tiếng Anh chính thức (Kivotos, Schale). Chỉ dịch phần "
              "chung chung sang tiếng Việt (学園 → Học viện, 部 → CLB, 委員会 → Ủy ban) và ghi `\"note\": \"đề xuất\"`.",
              "7. **Không tiết lộ nội dung (spoiler)**: bỏ nhân vật / tên thật / thân phận chỉ lộ ra về sau.",
              "8. `lookup.sources`: các trang bạn đã dùng (URL). Nếu biết bản Việt hóa (chính thức hoặc của cộng "
              "đồng) thì ghi vào `lookup.patches` (title, url, note): app chỉ hiện đường link.",
              "9. Không bịa: thiếu nguồn thì bỏ trống hoặc bỏ mục đó. Tối đa khoảng 60 nhân vật, ưu tiên nhân vật chính.",
              "", "## Định dạng (ví dụ)", "", "```json", _EXAMPLE, "```", "",
              "Trả lời: chỉ một khối ```json chứa đúng một object như trên."]
    return "\n".join(lines) + "\n"


# --- import --------------------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```[ \t]*(?:json|JSON|jsonc)?[ \t]*\n(.*?)```", re.DOTALL)
_SMART = str.maketrans({"“": '"', "”": '"', "„": '"', "‟": '"'})


def _objects(text: str):
    """Candidate JSON texts: the whole text, fenced blocks, then each balanced {...} span. Quotes count only
    inside braces: a lone " in the prose around the JSON (Hồ sơ cho "Steins;Gate: {...}) hides nothing."""
    yield text
    for m in _FENCE_RE.finditer(text):
        yield m.group(1)
    depth, start, in_str, esc = 0, -1, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"' and depth > 0:
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                yield text[start:i + 1]


def _loads(candidate: str):
    s = candidate.strip().lstrip("﻿")
    for attempt in (s, re.sub(r",\s*([}\]])", r"\1", s)):  # trailing commas, as chats sometimes write
        try:
            return json.loads(attempt)
        except ValueError:
            continue
    if "“" in s or "”" in s:  # quotes "smartened" by a chat app or a word processor
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", s.translate(_SMART)))
        except ValueError:
            pass
    return None


def parse_import(text: str, *, fallback_name: str = "Game", source: str = "assistant") -> Profile:
    """A Profile from pasted text or a file's content. Raises ValueError (Vietnamese message) when no
    profile is found. Every entry is marked as imported (profile.lookup["review"])."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Không có nội dung để nhập.")
    raw = None
    for cand in _objects(text):
        data = _loads(cand)
        if isinstance(data, dict) and isinstance(data.get("profile"), dict):
            data = data["profile"]
        if isinstance(data, dict) and (isinstance(data.get("characters"), list) or isinstance(data.get("terms"), (list, dict))):
            raw = data
            break
    if raw is None:
        raise ValueError("Không tìm thấy hồ sơ JSON (cần một object có \"terms\" hoặc \"characters\").")
    prof = profile_from_dict(raw, fallback_name)
    if not prof.characters and not prof.terms:
        raise ValueError("Hồ sơ không có thuật ngữ hay nhân vật nào.")
    prof.characters = [c for c in prof.characters if c.name]
    meta = _clean_lookup(prof.lookup)
    patches = meta.get("patches") or []
    for p in patches:
        p.setdefault("source", source)
        p["verified"] = False
    meta["sources"] = list(meta.get("sources") or [])[:30] + [source]
    meta["imported"] = _now()
    meta["review"] = {"characters": {c.name: [source] for c in prof.characters},
                      "terms": {t.source: [source] for t in prof.terms}}
    names = {c.name for c in prof.characters}
    aliases = {k: v for k, v in (meta.get("aliases") or {}).items() if v in names}
    for part, full in derive_aliases(prof).items():  # 紅莉栖 (a "person" term) in the name box is 牧瀬紅莉栖
        aliases.setdefault(part, full)
    if aliases:
        meta["aliases"] = aliases
    else:
        meta.pop("aliases", None)
    prof.lookup = meta
    if prof.src_lang not in ("ja", "en", None):
        prof.src_lang = None
    return prof


_LOOKUP_STR = ("title", "title_ja", "title_vi", "vi_note", "imported", "template")
_PATCH_KEYS = ("title", "url", "note", "source", "info_url")


def _clean_lookup(raw) -> dict:
    """An assistant's "lookup" block, keeping only the keys and shapes the app uses (anything else is dropped):
    strings, vi_official bool, ids / aliases {str: str}, sources [str], patches / attribution [{str: str}] with
    http(s) URLs."""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for k in _LOOKUP_STR:
        if isinstance(raw.get(k), str) and raw[k].strip():
            out[k] = raw[k].strip()[:300]
    if isinstance(raw.get("vi_official"), bool):
        out["vi_official"] = raw["vi_official"]
    for k in ("ids", "aliases"):
        if isinstance(raw.get(k), dict):
            d = {str(a).strip(): str(b).strip() for a, b in raw[k].items()
                 if isinstance(a, str) and isinstance(b, (str, int)) and str(a).strip() and str(b).strip()}
            if d:
                out[k] = d
    if isinstance(raw.get("sources"), list):
        out["sources"] = [s.strip() for s in raw["sources"] if isinstance(s, str) and s.strip()]
    patches = []
    for p in raw.get("patches") if isinstance(raw.get("patches"), list) else []:
        if isinstance(p, dict) and isinstance(p.get("url"), str) and p["url"].startswith(("http://", "https://")):
            patches.append({k: str(p[k])[:300] for k in _PATCH_KEYS if isinstance(p.get(k), str)}
                           | ({"official": p["official"]} if isinstance(p.get("official"), bool) else {}))
    if patches:
        out["patches"] = patches[:30]
    attribution = [{k: str(a[k])[:300] for k in ("source", "url", "license") if isinstance(a.get(k), str)}
                   for a in (raw.get("attribution") if isinstance(raw.get("attribution"), list) else [])
                   if isinstance(a, dict)]
    attribution = [a for a in attribution if a]
    if attribution:
        out["attribution"] = attribution[:30]
    return out


def romanized_only(profile: Profile) -> list[str]:
    """Characters of a Japanese profile whose name has no Japanese script (the app cannot spot them in
    Japanese text): for a warning after an import."""
    if profile.src_lang != "ja":
        return []
    return [c.name for c in profile.characters if not is_cjk(c.name)]


def import_file(path: str | Path, **kw) -> Profile:
    p = Path(path)
    data = p.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else data.decode("cp1258", "replace")
    kw.setdefault("fallback_name", p.stem)
    return parse_import(text, **kw)


__all__ = ["build_request", "parse_import", "import_file", "romanized_only"]
