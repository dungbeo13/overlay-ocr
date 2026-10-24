"""Text and box helpers shared by OCR engines and the OCR benchmark.

- CJK-aware space handling (Windows OCR puts a space between every Japanese character).
- Quad -> axis-aligned box conversion and reading-order sorting of TextBoxes.
- Normalisation used for CER scoring.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence

import numpy as np

from .base import TextBox

# Hiragana, Katakana (+ phonetic ext, halfwidth), CJK ideographs (+ ext A, compat),
# CJK symbols/punctuation, fullwidth forms. Fullwidth ASCII (FF01-FF5E) is included
# because Japanese text uses it without surrounding spaces.
_CJK_RANGES = (
    (0x3000, 0x303F),  # CJK symbols and punctuation
    (0x3040, 0x309F),  # Hiragana
    (0x30A0, 0x30FF),  # Katakana
    (0x31F0, 0x31FF),  # Katakana phonetic extensions
    (0x3400, 0x4DBF),  # CJK ext A
    (0x4E00, 0x9FFF),  # CJK unified ideographs
    (0xF900, 0xFAFF),  # CJK compatibility ideographs
    (0xFF00, 0xFFEF),  # half/fullwidth forms
    (0x20000, 0x2FA1F),  # CJK ext B.. + compat supplement
)


def is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


_SPACE_RUN = re.compile(r"\s+")


def strip_cjk_spaces(text: str) -> str:
    """Remove whitespace runs that sit between two CJK characters ("日 本 語" -> "日本語")."""
    if not text:
        return text
    parts: list[str] = []
    last = 0
    for m in _SPACE_RUN.finditer(text):
        s, e = m.span()
        if 0 < s and e < len(text) and is_cjk(text[s - 1]) and is_cjk(text[e]):
            parts.append(text[last:s])
            last = e
    parts.append(text[last:])
    return "".join(parts)


def normalize_for_cer(text: str, lang: str) -> str:
    """NFKC, collapse whitespace; Japanese drops all whitespace (spacing is not meaningful)."""
    t = unicodedata.normalize("NFKC", text)
    if lang.startswith("ja"):
        return _SPACE_RUN.sub("", t)
    return _SPACE_RUN.sub(" ", t).strip()


def join_lines(lines: Iterable[str], lang: str) -> str:
    """Join OCR lines with spaces; Japanese without, except between two non-CJK ends, so English
    words read in Japanese mode (UI labels, a subtitle) keep their word boundary at a line break."""
    parts = [s.strip() for s in lines if s.strip()]
    if not lang.startswith("ja"):
        return " ".join(parts)
    out = ""
    for s in parts:
        if out and not is_cjk(out[-1]) and not is_cjk(s[0]) and not out[-1].isspace():
            out += " "
        out += s
    return out


def quad_to_xywh(quad: np.ndarray | Sequence[Sequence[float]], width: int, height: int) -> tuple[int, int, int, int]:
    """Axis-aligned bounding box of a 4-point polygon, clipped to the image."""
    pts = np.asarray(quad, dtype=np.float64).reshape(-1, 2)
    x0 = int(max(0, np.floor(pts[:, 0].min())))
    y0 = int(max(0, np.floor(pts[:, 1].min())))
    x1 = int(min(width, np.ceil(pts[:, 0].max())))
    y1 = int(min(height, np.ceil(pts[:, 1].max())))
    return x0, y0, max(0, x1 - x0), max(0, y1 - y0)


def union_rects(rects: Iterable[tuple[float, float, float, float]]) -> tuple[int, int, int, int]:
    """Union of (x, y, w, h) rectangles as an integer (x, y, w, h)."""
    rs = list(rects)
    if not rs:
        return 0, 0, 0, 0
    x0 = min(r[0] for r in rs)
    y0 = min(r[1] for r in rs)
    x1 = max(r[0] + r[2] for r in rs)
    y1 = max(r[1] + r[3] for r in rs)
    ix0, iy0 = int(np.floor(x0)), int(np.floor(y0))
    return ix0, iy0, int(np.ceil(x1)) - ix0, int(np.ceil(y1)) - iy0


def sort_top_to_bottom(boxes: list[TextBox]) -> list[TextBox]:
    """Group boxes into rows (vertical-centre overlap), rows top-to-bottom, each row left-to-right."""
    if len(boxes) < 2:
        return list(boxes)
    by_y = sorted(boxes, key=lambda b: (b.box[1], b.box[0]))
    rows: list[list[TextBox]] = []
    row_cy = row_h = 0.0
    for b in by_y:
        cy = b.box[1] + b.box[3] / 2
        if rows and abs(cy - row_cy) <= max(4.0, 0.5 * min(row_h, b.box[3])):
            rows[-1].append(b)
            n = len(rows[-1])
            row_cy += (cy - row_cy) / n
            row_h = max(row_h, b.box[3])
        else:
            rows.append([b])
            row_cy, row_h = cy, b.box[3]
    return [b for row in rows for b in sorted(row, key=lambda b: b.box[0])]


def is_vertical_layout(boxes: Sequence[TextBox]) -> bool:
    """True when most multi-character boxes are tall columns (vertical CJK, e.g. manga)."""
    multi = [b for b in boxes if len(b.text.strip()) >= 2 and b.box[2] > 0]
    if not multi:
        return False
    tall = sum(1 for b in multi if b.box[3] >= 2.0 * b.box[2])
    return tall >= 0.6 * len(multi)


def reading_order(boxes: list[TextBox], vertical: bool | None = None) -> list[TextBox]:
    """Order boxes for reading: rows top-to-bottom, or columns right-to-left for vertical text."""
    if vertical is None:
        vertical = is_vertical_layout(boxes)
    if not vertical:
        return sort_top_to_bottom(boxes)
    # Group into columns by horizontal-centre overlap, then right-to-left, top-to-bottom.
    by_x = sorted(boxes, key=lambda b: -(b.box[0] + b.box[2] / 2))
    cols: list[list[TextBox]] = []
    col_cx = col_w = 0.0
    for b in by_x:
        cx = b.box[0] + b.box[2] / 2
        if cols and abs(cx - col_cx) <= max(4.0, 0.5 * min(col_w, b.box[2])):
            cols[-1].append(b)
            col_w = max(col_w, b.box[2])
        else:
            cols.append([b])
            col_cx, col_w = cx, b.box[2]
    return [b for col in cols for b in sorted(col, key=lambda b: b.box[1])]
