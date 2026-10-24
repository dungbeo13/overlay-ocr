"""Turn OCR text lines into translation blocks (paragraphs, dialog lines, speech bubbles).

Pure functions, no state. Thresholds are relative to the OCR box size. RapidOCR boxes are
padded (a 23 px tall glyph line gets a ~34 px box), so a vertical gap of 0.5 box heights is
roughly 0.8 glyph heights: lines of one paragraph sit ~0.1 box heights apart, paragraphs
~0.7 apart on the synthetic samples. role_hints() guesses what each block is (speaker name,
dialogue, subtitle, UI label) for the contextual LLM tier.
"""
from __future__ import annotations

import statistics
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.ocr.base import TextBox
from app.ocr.textnorm import is_cjk, is_vertical_layout, join_lines, union_rects

from .types import Rect


@dataclass(frozen=True)
class TextGroup:
    """One block of source text as found in a single OCR pass (boxes relative to the region)."""

    box: Rect  # union of `lines`
    lines: tuple[Rect, ...]  # every OCR box of the block, in reading order
    source: str
    vertical: bool = False
    line_height: int = 0  # median line box height (vertical: median column width)


@dataclass
class LayoutParams:
    min_score: float = 0.5
    height_ratio: float = 0.65  # lines of one block: min/max size >= this (within ~35%)
    max_gap: float = 0.5  # vertical gap between lines <= max_gap * box height
    max_gap_vertical: float = 0.6  # horizontal gap between JA columns <= this * column width
    min_gap: float = -0.5  # small overlaps of padded boxes are fine
    align_tol: float = 0.6  # left edges (columns: top edges) "aligned" within this * size
    row_gap: float = 0.6  # fragments of one line: gap <= this * height (buttons are ~1.1 apart)
    label_ratio: float = 0.5  # a lone line this much shorter than the next, left-aligned: a label
    short_line: float = 6.0  # lines narrower than this many heights, stacked left-aligned: list items
    vertical_ratio: float = 1.5  # JA box with h >= ratio * w (and >= 2 chars) is a vertical column


@dataclass
class _Line:
    """A row of horizontal fragments, or a column of vertical ones."""

    boxes: list[TextBox] = field(default_factory=list)
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0

    @classmethod
    def of(cls, b: TextBox) -> "_Line":
        x, y, w, h = b.box
        return cls([b], x, y, w, h)

    def add(self, b: TextBox) -> None:
        self.boxes.append(b)
        self.x, self.y, self.w, self.h = union_rects([(self.x, self.y, self.w, self.h), b.box])

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    def text(self, lang: str, vertical: bool) -> str:
        key = (lambda b: b.box[1]) if vertical else (lambda b: b.box[0])
        return join_lines((b.text for b in sorted(self.boxes, key=key)), lang)

    def rects(self, vertical: bool) -> list[Rect]:
        key = (lambda b: b.box[1]) if vertical else (lambda b: b.box[0])
        return [tuple(int(v) for v in b.box) for b in sorted(self.boxes, key=key)]  # type: ignore[misc]


def is_junk(box: TextBox, min_score: float = 0.5) -> bool:
    """Empty text, low confidence, degenerate box, or punctuation/symbols only ("…", "▼", "。")."""
    text = box.text.strip()
    if not text or box.box[2] <= 0 or box.box[3] <= 0:
        return True
    if box.score is not None and box.score < min_score:
        return True
    return all(unicodedata.category(ch)[0] in "PSZ" for ch in text)


def has_letters(text: str) -> bool:
    return any(ch.isalpha() for ch in text)


def is_lone_latin_letter(text: str) -> bool:
    """At most one non-CJK letter: circles, icons and gauges read as "O", "C", "A" (measured in
    the end-to-end demo: a sun and UI bubbles became "O" -> "ồ"); nothing worth translating."""
    letters = [ch for ch in text if ch.isalpha()]
    return len(letters) < 2 and not any(is_cjk(ch) for ch in letters)


def is_stray_letter(text: str) -> bool:
    """One OCR box that is a single Latin letter and nothing else (a circle read as "O"). Dropped
    before same-row boxes are joined, so two circles side by side cannot pass as "O O"."""
    letters = [ch for ch in text if ch.isalpha()]
    return len(letters) == 1 and not is_cjk(letters[0]) and not any(ch.isdigit() for ch in text)


def _similar(a: float, b: float, ratio: float) -> bool:
    lo, hi = min(a, b), max(a, b)
    return hi <= 0 or lo / hi >= ratio


def _is_vertical_box(b: TextBox, lang: str, page_vertical: bool, p: LayoutParams) -> bool:
    if not lang.startswith("ja"):
        return False
    _, _, w, h = b.box
    n = len(b.text.strip())
    if n >= 2 and h >= p.vertical_ratio * w:
        return True
    return page_vertical and n == 1 and h >= 0.8 * w  # lone kana on a vertical page


# -- horizontal text --------------------------------------------------------------------------
def _rows(boxes: list[TextBox], p: LayoutParams) -> list[_Line]:
    """Join fragments that sit on one row with a small horizontal gap."""
    lines: list[_Line] = []
    for b in sorted(boxes, key=lambda b: (b.box[0], b.box[1])):
        x, y, w, h = b.box
        cy = y + h / 2
        best, best_gap = None, None
        for ln in lines:
            if not _similar(ln.h, h, p.height_ratio):
                continue
            if abs((ln.y + ln.h / 2) - cy) > 0.5 * min(ln.h, h):
                continue
            gap = x - ln.right
            size = max(ln.h, h)
            if -0.5 * size <= gap <= p.row_gap * size and (best_gap is None or gap < best_gap):
                best, best_gap = ln, gap
        if best is None:
            lines.append(_Line.of(b))
        else:
            best.add(b)
    return lines


def _continues_paragraph(prev: _Line, line: _Line, block_len: int, p: LayoutParams) -> float | None:
    """Gap (px) if `line` can follow `prev` in the same block, else None."""
    if not _similar(prev.h, line.h, p.height_ratio):
        return None
    size = (prev.h + line.h) / 2
    if line.y + line.h / 2 <= prev.y + prev.h / 2 + 0.5 * min(prev.h, line.h):
        return None  # same row or above
    gap = line.y - prev.bottom
    if not p.min_gap * size <= gap <= p.max_gap * size:
        return None
    overlap = min(prev.right, line.right) - max(prev.x, line.x)
    left_aligned = abs(prev.x - line.x) <= p.align_tol * size
    if overlap <= 0 and not left_aligned:
        return None
    if left_aligned:
        if block_len == 1 and prev.w < p.label_ratio * line.w:
            return None  # speaker name / heading / label above a longer line
        if prev.w < p.short_line * size and line.w < p.short_line * size:
            return None  # menu / list items (wrapped paragraph lines are long; centred bubbles are not left-aligned)
    return gap


def _group_horizontal(boxes: list[TextBox], lang: str, p: LayoutParams) -> list[TextGroup]:
    blocks: list[list[_Line]] = []
    for line in sorted(_rows(boxes, p), key=lambda ln: (ln.y, ln.x)):
        best, best_gap = None, None
        for blk in blocks:
            gap = _continues_paragraph(blk[-1], line, len(blk), p)
            if gap is not None and (best_gap is None or gap < best_gap):
                best, best_gap = blk, gap
        if best is None:
            blocks.append([line])
        else:
            best.append(line)
    out = []
    for blk in blocks:
        rects = [r for ln in blk for r in ln.rects(False)]
        out.append(TextGroup(
            box=union_rects(rects),
            lines=tuple(rects),
            source=join_lines((ln.text(lang, False) for ln in blk), lang),
            vertical=False,
            line_height=int(round(statistics.median(ln.h for ln in blk))),
        ))
    return out


# -- vertical Japanese ------------------------------------------------------------------------
def _columns(boxes: list[TextBox], p: LayoutParams) -> list[_Line]:
    """Join fragments stacked in one column with a small vertical gap."""
    cols: list[_Line] = []
    for b in sorted(boxes, key=lambda b: (b.box[1], -b.box[0])):
        x, y, w, h = b.box
        cx = x + w / 2
        best, best_gap = None, None
        for col in cols:
            if not _similar(col.w, w, p.height_ratio):
                continue
            if abs((col.x + col.w / 2) - cx) > 0.5 * min(col.w, w):
                continue
            gap = y - col.bottom
            size = max(col.w, w)
            if -0.5 * size <= gap <= p.row_gap * size and (best_gap is None or gap < best_gap):
                best, best_gap = col, gap
        if best is None:
            cols.append(_Line.of(b))
        else:
            best.add(b)
    return cols


def _continues_columns(prev: _Line, col: _Line, p: LayoutParams) -> float | None:
    """Gap (px) if column `col` (left of `prev`) belongs to the same bubble, else None."""
    if not _similar(prev.w, col.w, p.height_ratio):
        return None
    size = (prev.w + col.w) / 2
    if col.x + col.w / 2 >= prev.x + prev.w / 2 - 0.5 * min(prev.w, col.w):
        return None  # same column or to the right
    gap = prev.x - col.right
    if not p.min_gap * size <= gap <= p.max_gap_vertical * size:
        return None
    overlap = min(prev.bottom, col.bottom) - max(prev.y, col.y)
    if overlap <= 0 and abs(prev.y - col.y) > p.align_tol * size:
        return None
    return gap


def _group_vertical(boxes: list[TextBox], lang: str, p: LayoutParams) -> list[TextGroup]:
    blocks: list[list[_Line]] = []
    for col in sorted(_columns(boxes, p), key=lambda c: (-c.right, c.y)):
        best, best_gap = None, None
        for blk in blocks:
            gap = _continues_columns(blk[-1], col, p)
            if gap is not None and (best_gap is None or gap < best_gap):
                best, best_gap = blk, gap
        if best is None:
            blocks.append([col])
        else:
            best.append(col)
    out = []
    for blk in blocks:
        rects = [r for col in blk for r in col.rects(True)]
        out.append(TextGroup(
            box=union_rects(rects),
            lines=tuple(rects),
            source=join_lines((col.text(lang, True) for col in blk), lang),
            vertical=True,
            line_height=int(round(statistics.median(col.w for col in blk))),
        ))
    return out


# -- role hints (for the contextual LLM tier) ---------------------------------------------------
ROLE_NAME, ROLE_DIALOG, ROLE_SUBTITLE, ROLE_UI = "name", "dialog", "subtitle", "ui"
_SENTENCE_END = (".", "!", "?", "。", "！", "？", "」", "』", "…", "♪")


@dataclass
class RoleParams:
    name_max_chars: int = 10  # CJK characters (Latin text: name_max_words words, 2x+4 chars)
    name_max_words: int = 3
    label_max_chars: int = 12  # short UI label
    label_max_words: int = 4
    name_gap: float = 1.6  # name above a dialog block: vertical gap <= this * line height
    name_side_gap: float = 3.0  # name left of a dialog block: horizontal gap <= this * line height
    subtitle_low: float = 0.6  # a subtitle's centre lies below this share of the region height
    subtitle_centre_tol: float = 0.06  # single line: centre within this share of the width of the middle


def _is_short(text: str, max_chars: int, max_words: int) -> bool:
    t = text.strip()
    if any(is_cjk(ch) for ch in t):
        return len(t.replace(" ", "")) <= max_chars
    return len(t.split()) <= max_words and len(t) <= 2 * max_chars + 4


def _one_row(g: TextGroup) -> bool:
    centres = [y + h / 2 for _, y, _, h in g.lines] or [g.box[1] + g.box[3] / 2]
    heights = [max(1, h) for *_, h in g.lines] or [max(1, g.box[3])]
    return max(centres) - min(centres) <= 0.5 * min(heights)


def _ends_sentence(text: str) -> bool:
    return text.rstrip().rstrip("\"'”’)）]】»").endswith(_SENTENCE_END)


def _centred(g: TextGroup, region_w: int, tol: float) -> bool:
    """Lines centred on each other (multi-line) or on the region (one line); left edges not aligned."""
    if len(g.lines) >= 2 and not _one_row(g):
        centres = [x + w / 2 for x, _, w, _ in g.lines]
        lefts = [x for x, _, _, _ in g.lines]
        mean = sum(centres) / len(centres)
        return max(abs(c - mean) for c in centres) <= 0.02 * region_w + 3 and max(lefts) - min(lefts) > 6
    x, _, w, _ = g.box
    return region_w > 0 and abs(x + w / 2 - region_w / 2) <= tol * region_w and w < 0.85 * region_w


def _labels(name: TextGroup, dialog: TextGroup, p: RoleParams) -> bool:
    """`name` sits just above or just left of `dialog` the way a speaker label does."""
    nx, ny, nw, nh = name.box
    dx, dy, dw, _ = dialog.box
    h = max(name.line_height or nh, dialog.line_height or 1, 1)
    above = (ny + nh <= dy + 0.25 * h and dy - (ny + nh) <= p.name_gap * h
             and dx - 2 * h <= nx + nw / 2 <= dx + dw)
    left = (nx + nw <= dx + 0.25 * h and dx - (nx + nw) <= p.name_side_gap * h
            and dy - 0.5 * h <= ny + nh / 2 <= dy + 1.5 * h)
    return above or left


def role_hints(groups: Sequence[TextGroup], region_size: tuple[int, int] = (0, 0),
               params: RoleParams | None = None) -> list[str]:
    """Guess each block's role for the LLM: "name" (a short one-line label just above / left of a
    dialogue block), "subtitle" (centred text in the lower part of the region), "ui" (a short
    isolated label: menu item, button, heading) or "dialog" (everything else)."""
    p = params or RoleParams()
    rw, rh = region_size
    long_blocks = [g for g in groups if not g.vertical
                   and not (_one_row(g) and _is_short(g.source, p.label_max_chars, p.label_max_words))]
    out: list[str] = []
    for g in groups:
        one_row, end = _one_row(g), _ends_sentence(g.source)
        if (not g.vertical and one_row and not end and _is_short(g.source, p.name_max_chars, p.name_max_words)
                and any(d is not g and _labels(g, d, p) for d in long_blocks)):
            out.append(ROLE_NAME)
        elif not g.vertical and rh > 0 and g.box[1] + g.box[3] / 2 >= p.subtitle_low * rh \
                and _centred(g, rw, p.subtitle_centre_tol):
            out.append(ROLE_SUBTITLE)
        elif one_row and not end and _is_short(g.source, p.label_max_chars, p.label_max_words):
            out.append(ROLE_UI)
        else:
            out.append(ROLE_DIALOG)
    return out


def layout_blocks(boxes: Sequence[TextBox], lang: str, params: LayoutParams | None = None) -> list[TextGroup]:
    """Drop junk, group lines into blocks and order the blocks top-to-bottom, left-to-right.

    Blocks whose text has no letters at all (clocks, scores, page numbers) are dropped too:
    there is nothing to translate and a painted copy would go stale as the number changes. So are
    blocks that are a single Latin letter (shapes misread as "O", "C"), and such single-letter
    boxes are dropped before grouping.
    """
    p = params or LayoutParams()
    kept = [b for b in boxes if not is_junk(b, p.min_score) and not is_stray_letter(b.text)]
    page_vertical = lang.startswith("ja") and is_vertical_layout(kept)
    vert = [b for b in kept if _is_vertical_box(b, lang, page_vertical, p)]
    vert_ids = {id(b) for b in vert}
    horiz = [b for b in kept if id(b) not in vert_ids]
    groups = _group_horizontal(horiz, lang, p) + _group_vertical(vert, lang, p)
    groups = [g for g in groups if has_letters(g.source) and not is_lone_latin_letter(g.source)]
    groups.sort(key=lambda g: (g.box[1], g.box[0]))
    return groups
