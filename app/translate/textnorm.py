"""Text normalization helpers shared by the translation cache, engines and benches."""
from __future__ import annotations

import re
import unicodedata
from typing import Callable

# Kana, CJK ideographs (incl. ext. A, compatibility), CJK punctuation and the full-width block.
_CJK = r"　-〿぀-ゟ゠-ヿㇰ-ㇿ㐀-䶿一-鿿豈-﫿＀-￯"
# NFKC turns full-width punctuation into ASCII, so "すごい ！" becomes "すごい !": treat that
# punctuation like CJK on one side of the space.
_PUNCT = r"!?.,:;~()\[\]…"
_CJK_SPACE = re.compile(rf"(?<=[{_CJK}]) +(?=[{_CJK}{_PUNCT}])|(?<=[{_CJK}{_PUNCT}]) +(?=[{_CJK}])")
_HSPACE = re.compile(r"[^\S\n]+")  # whitespace except newline
_NEWLINES = re.compile(r" *\n\s*")  # a line break plus any surrounding blank space/lines
_DIGITS = re.compile(r"\d+")
_KANA = re.compile(r"[぀-ヿ]")

_STRIP_CJK_SPACES = frozenset({"ja", "zh"})  # Korean keeps word spaces


def normalize_text(text: str, lang: str | None = None) -> str:
    """Canonical form used for cache keys and engine input.

    NFKC (full-width ASCII -> ASCII, half-width kana -> full-width), runs of spaces/tabs
    collapse to one space, line breaks are kept but blank lines collapse, and for ja/zh
    spaces between CJK characters (a common OCR artefact) are removed. Idempotent.
    """
    t = unicodedata.normalize("NFKC", text)
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = _HSPACE.sub(" ", t)
    t = _NEWLINES.sub("\n", t).strip()
    if lang in _STRIP_CJK_SPACES:
        t = _CJK_SPACE.sub("", t)
    return t


def needs_translation(text: str) -> bool:
    """False for text with no letters at all (numbers, punctuation, symbols)."""
    return any(ch.isalpha() for ch in text)


def letter_count(text: str) -> int:
    return sum(ch.isalpha() for ch in text)


def digit_signature(text: str) -> tuple[str, ...]:
    """Digit runs in order; fuzzy cache reuse requires these to match exactly ("12 HP" != "13 HP")."""
    return tuple(_DIGITS.findall(text))


def is_passthrough(source: str, output: str, *, min_letters: int = 4) -> bool:
    """True when the engine echoed a non-trivial source back untranslated."""
    if letter_count(source) < min_letters:
        return False
    return normalize_text(source).casefold() == normalize_text(output).casefold()


_JA_SCRIPT = re.compile(r"[぀-ヿㇰ-ㇿ㐀-䶿一-鿿豈-﫿ｦ-ﾟ]")  # kana, kanji, half-width kana
_LATIN = re.compile(r"[A-Za-zÀ-ɏ]")


def source_lang_for(text: str, default: str) -> str:
    """Language to translate `text` from. `default` unless the script says otherwise: in Japanese
    mode a text with Latin letters but no kana/kanji is English (menus, names, subtitles in a JA
    game) and in English mode a text with kana/kanji is Japanese. Sending English with sl=ja makes
    Google return it untranslated."""
    has_ja = bool(_JA_SCRIPT.search(text))
    if default == "ja" and not has_ja and _LATIN.search(text):
        return "en"
    if default == "en" and has_ja:
        return "ja"
    return default


def has_kana(text: str) -> bool:
    """True if Japanese kana remain (a JA->VI result that still has kana is likely untranslated)."""
    return bool(_KANA.search(text))


def apply_glossary(
    text: str,
    glossary: dict[str, str] | None,
    lang: str | None,
    render: Callable[[str, str], str] | None = None,
) -> str:
    """Replace glossary terms in one pass, longest first (so terms never nest).

    `render(term, translation)` builds the replacement (default: the translation itself).
    Word boundaries are enforced except for ja/zh/ko, where words are not space-delimited.
    """
    if not glossary:
        return text
    terms = sorted((t for t in glossary if t), key=len, reverse=True)
    if not terms:
        return text
    alt = "|".join(re.escape(t) for t in terms)
    pattern = alt if lang in ("ja", "zh", "ko") else rf"(?<!\w)(?:{alt})(?!\w)"
    fmt = render or (lambda term, tr: tr)
    return re.sub(pattern, lambda m: fmt(m.group(0), glossary[m.group(0)]), text)
