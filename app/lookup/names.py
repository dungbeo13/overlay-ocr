"""Text helpers of the game lookup: title matching, name keys, romanization clean-up."""
from __future__ import annotations

import html
import re
import unicodedata
from difflib import SequenceMatcher

_KANA_RE = re.compile(r"[぀-ゟ゠-ヿㇰ-ㇿｦ-ﾟ]")
_KANJI_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_CJK_RE = re.compile(r"[぀-ヿㇰ-ㇿ㐀-䶿一-鿿豈-﫿ｦ-ﾟ가-힯]")
_NAME_SEPARATORS = " 　・･=＝·"
_TAG_RE = re.compile(r"<[^>]+>")
_MACRONS = str.maketrans({"ā": "aa", "ī": "ii", "ū": "uu", "ē": "ee", "ō": "ou", "â": "aa", "î": "ii", "û": "uu",
                          "ê": "ee", "ô": "ou", "Ā": "Aa", "Ī": "Ii", "Ū": "Uu", "Ē": "Ee", "Ō": "Ou", "Â": "Aa",
                          "Î": "Ii", "Û": "Uu", "Ê": "Ee", "Ô": "Ou"})
# Words that make a store / database entry something other than the game itself.
_EXTRA_WORDS = ("soundtrack", "ost", "dlc", "pack", "demo", "artbook", "art book", "bundle", "season pass", "wallpaper",
                "costume", "voice", "trial", "playtest", "anime", "manga", "film", "movie", "television", "tv series",
                "novel series", "light novel", "character", "album", "single", "song", "franchise", "wiki")


def is_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def has_kana(text: str) -> bool:
    return bool(_KANA_RE.search(text or ""))


def all_kanji(text: str) -> bool:
    t = compact(text)
    return bool(t) and all(_KANJI_RE.match(ch) for ch in t)


def nfc(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text or "").split())


def norm_title(text: str) -> str:
    """Comparison key for titles: NFKC, casefolded, "&" -> "and", only letters and digits
    ("STEINS;GATE" == "Steins;Gate" == "steins gate")."""
    t = unicodedata.normalize("NFKC", text or "").casefold().replace("&", " and ")
    return "".join(ch for ch in t if ch.isalnum())


def title_similarity(query: str, title: str) -> float:
    """0..1: 1 for the same normalized title; a candidate with extra words such as "Soundtrack" or
    "DLC" scores lower than the game itself."""
    q, t = norm_title(query), norm_title(title)
    if not q or not t:
        return 0.0
    if q == t:
        return 1.0
    score = SequenceMatcher(None, q, t).ratio()
    low_q, low_t = (query or "").casefold(), (title or "").casefold()
    if any(w in low_t and w not in low_q for w in _EXTRA_WORDS):
        score -= 0.25
    if t.startswith(q) or q.startswith(t):
        score = max(score, 0.8 - 0.02 * abs(len(t) - len(q)))
    return max(0.0, min(1.0, score))


def compact(text: str) -> str:
    """A name without spaces and name separators, NFKC: the key that matches 牧瀬 紅莉栖 with 牧瀬紅莉栖."""
    t = unicodedata.normalize("NFKC", text or "")
    return "".join(ch for ch in t if ch not in _NAME_SEPARATORS and not ch.isspace())


def name_key(text: str) -> str:
    return compact(text).casefold()


def latin_key(text: str) -> str:
    """Loose key for romanized names: order-free, long vowels collapsed ("Rintaro Okabe" == "Okabe Rintarou")."""
    t = unicodedata.normalize("NFKD", fix_long_vowels(text or "")).casefold()
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    for a, b in (("ou", "o"), ("oo", "o"), ("uu", "u"), ("aa", "a"), ("ii", "i"), ("ee", "e")):
        t = t.replace(a, b)
    words = sorted(w for w in re.split(r"[^a-z0-9]+", t) if w)
    return " ".join(words)


def fix_long_vowels(text: str) -> str:
    """Hepburn macrons as doubled vowels / "ou", like the Blue Archive profile ("Yuuka", "Rintarou")."""
    return (text or "").translate(_MACRONS)


def strip_html(text: str) -> str:
    return " ".join(html.unescape(_TAG_RE.sub(" ", text or "")).split())


def split_parts(text: str) -> list[str]:
    parts = re.split(r"[ 　・･=＝·]+", unicodedata.normalize("NFKC", text or "").strip())
    return [p for p in parts if p]


def pair_parts(original: str, romanized: str) -> list[tuple[str, str]]:
    """(original part, romanized part) pairs when both names split into the same number of parts,
    in source order (VNDB writes Japanese names family name first in both scripts)."""
    o, r = split_parts(original), split_parts(romanized)
    if len(o) < 2 or len(o) != len(r) or not is_cjk(original) or is_cjk(romanized):
        return []
    return list(zip(o, r))


def clean_local_title(raw: str) -> str:
    """A title guessed from a file / folder name: "BlueArchive" -> "Blue Archive",
    "My_Game-Win64-Shipping" -> "My Game"; "" for names that say nothing."""
    t = unicodedata.normalize("NFC", raw or "").strip().strip('"')
    if t.lower().endswith(".exe"):
        t = t[:-4]
    t = re.sub(r"(?i)[-_ ]?(win64|win32|x64|x86)?[-_ ]?shipping$", "", t)
    t = re.sub(r"(?i)[-_ ](win64|win32|x64|x86|dx11|dx12|vulkan|steam|launcher|release)$", "", t)
    t = re.sub(r"(?i)\s*[(\[][^)\]]*[)\]]\s*$", "", t)  # "Game (x64)", "Game [v1.2]"
    t = re.sub(r"(?i)[\s_-]+v?\d+(\.\d+)+[a-z]?$", "", t)  # trailing version number
    t = t.replace("_", " ")
    if " " not in t and re.search(r"[a-z][A-Z]", t):
        t = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", t)  # BlueArchive -> Blue Archive
    t = " ".join(t.split()).strip(" -.")
    return t if sum(ch.isalnum() for ch in t) >= 2 else ""
