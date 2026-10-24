"""Pure painting helpers for the translation overlay, shared by OverlayWindow and the preview tool.

All block coordinates are physical pixels relative to the capture region's top-left corner; the
caller sets up the painter transform (OverlayWindow scales by 1/devicePixelRatio). A
QGuiApplication must exist (fonts).
"""
from __future__ import annotations

import functools
import math
import statistics
import unicodedata
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QTextLayout, QTextOption

from app.config import Config
from app.pipeline.types import BGR, Block, OverlayState, Rect

ELLIPSIS = "…"
STALE_TEXT_OPACITY = 0.5  # stale_translation is drawn dimmed while the new one is pending
REFINING_PREFIX = "Đang tinh chỉnh"  # app.pipeline.pipeline.MSG_REFINING: the pill stays dimmed
FILL_PAD_PX = 2  # line boxes are expanded by this much before filling them with the bg colour
MIN_CONTRAST = 3.0  # below this the block's fg is replaced by black/white
STATUS_FONT_PX = 12  # status pill font size in device-independent px (times `scale`)

_VI_TALL = "ẤẦẨẪẬẮẰẲẴẶẾỀỂỄỆỐỒỔỖỘỚỜỞỠỢỨỪỬỮỰÁÀẢÃẠỈỊ"  # capitals with stacked diacritics
_VI_LOW = "gjpqyỵỴỤỌẠỊ"  # descenders and dot-below
_LINE_SEP = " "  # QTextLayout's forced line break

Align = str  # "left" | "center"


# --- colours -----------------------------------------------------------------------------------

def _channel(c: int) -> float:
    s = c / 255
    return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4


def relative_luminance(bgr: BGR) -> float:
    """WCAG 2 relative luminance of a BGR colour (0 = black, 1 = white)."""
    b, g, r = bgr
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(a: BGR, b: BGR) -> float:
    """WCAG 2 contrast ratio, 1..21."""
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def pick_fg(bg: BGR) -> BGR:
    """Black or white, whichever contrasts more with `bg` (always >= 4.58:1)."""
    white, black = (255, 255, 255), (0, 0, 0)
    return white if contrast_ratio(white, bg) >= contrast_ratio(black, bg) else black


def readable_fg(fg: BGR, bg: BGR, minimum: float = MIN_CONTRAST) -> BGR:
    """`fg` if it is readable on `bg`, else black/white."""
    return fg if contrast_ratio(fg, bg) >= minimum else pick_fg(bg)


def bgr_to_qcolor(bgr: BGR, alpha: int = 255) -> QColor:
    b, g, r = bgr
    return QColor(int(r), int(g), int(b), max(0, min(255, int(alpha))))


# --- text fitting ------------------------------------------------------------------------------

@dataclass(frozen=True)
class FontMetrics:
    px: int
    ascent: float  # baseline to the top of the tallest Vietnamese capital (stacked diacritics)
    descent: float  # baseline to the bottom of descenders / dot-below
    line_height: float  # ascent + descent: lines are stacked at this pitch
    hpad: float  # horizontal inset that absorbs negative side bearings (j, ư horn, ...)


def make_font(family: str, px: int, weight: int = 400) -> QFont:
    font = QFont(family)
    font.setPixelSize(max(1, int(px)))
    font.setWeight(QFont.Weight(weight))
    # On the translucent overlay surface Qt renders text with grayscale AA (no ClearType fringes).
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    font.setKerning(True)
    return font


@functools.lru_cache(maxsize=1024)
def font_metrics(family: str, px: int, weight: int = 400) -> FontMetrics:
    fm = QFontMetricsF(make_font(family, px, weight))
    tall = fm.tightBoundingRect(_VI_TALL)
    low = fm.tightBoundingRect(_VI_LOW)
    ascent = math.ceil(max(fm.ascent(), -tall.top()) + 1)
    descent = math.ceil(max(fm.descent(), low.bottom()) + 1)
    return FontMetrics(px, ascent, descent, ascent + descent, max(1.0, round(px * 0.06)))


@dataclass(frozen=True)
class TextLine:
    text: str
    x: float  # left edge of the line's advance box
    baseline: float
    width: float  # natural advance width


@dataclass(frozen=True)
class FitResult:
    font_px: int
    lines: tuple[TextLine, ...]
    rect: QRectF  # box the text was fitted into (the input rect, possibly grown down/right)
    text_rect: QRectF  # union of the line boxes (ascent..descent incl. diacritics)
    grown: bool = False
    elided: bool = False
    family: str = "Segoe UI"
    weight: int = 400

    @property
    def font(self) -> QFont:
        return make_font(self.family, self.font_px, self.weight)

    @property
    def text(self) -> str:
        return " ".join(ln.text for ln in self.lines)


def prepare_text(text: str) -> str:
    """NFC (precomposed Vietnamese), trimmed, newlines as QTextLayout line separators."""
    t = unicodedata.normalize("NFC", text or "").strip()
    return t.replace("\r\n", "\n").replace("\n", _LINE_SEP)


def _wrap(text: str, font: QFont, width: float, anywhere: bool) -> list[tuple[int, int, float]]:
    """Line-break `text` at `width`: [(start, length, natural width)]."""
    layout = QTextLayout(text, font)
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere if anywhere else QTextOption.WrapMode.WordWrap)
    layout.setTextOption(opt)
    layout.beginLayout()
    lines = []
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(max(1.0, width))
        lines.append((line.textStart(), line.textLength(), line.naturalTextWidth()))
    layout.endLayout()
    return lines


@dataclass
class _Attempt:
    px: int
    metrics: FontMetrics
    lines: list[tuple[int, int, float]]
    height: float
    overflow: bool  # a word is wider than the line

    def fits(self, height: float) -> bool:
        return not self.overflow and self.height <= height + 0.01


def _attempt(text: str, family: str, px: int, weight: int, box_width: float, anywhere: bool) -> _Attempt:
    m = font_metrics(family, px, weight)
    avail = box_width - 2 * m.hpad
    lines = _wrap(text, make_font(family, px, weight), avail, anywhere)
    overflow = any(w > avail + 0.01 for _, _, w in lines)
    return _Attempt(px, m, lines, len(lines) * m.line_height, overflow)


def _to_rectf(rect: QRectF | Rect | tuple) -> QRectF:
    if isinstance(rect, QRectF):
        return QRectF(rect)
    if hasattr(rect, "x") and callable(rect.x):  # QRect
        return QRectF(rect)
    x, y, w, h = rect
    return QRectF(float(x), float(y), float(w), float(h))


def _elide(fm: QFontMetricsF, text: str, width: float) -> str:
    """`text` cut to `width` with a trailing ellipsis (always present: the text continues)."""
    s = fm.elidedText(text.strip(), Qt.TextElideMode.ElideRight, width)
    if s.endswith(ELLIPSIS):
        return s
    s = s.rstrip()
    while s and fm.horizontalAdvance(s + ELLIPSIS) > width:
        s = s[:-1].rstrip()
    return s + ELLIPSIS


def _place(text: str, a: _Attempt, rect: QRectF, align: Align, valign: str,
           elide_from: int | None = None, family: str = "", weight: int = 400) -> tuple[tuple[TextLine, ...], QRectF]:
    m = a.metrics
    lines = a.lines if elide_from is None else a.lines[:elide_from + 1]
    total_h = len(lines) * m.line_height
    top = rect.top()
    if valign == "center" and total_h < rect.height():
        top += (rect.height() - total_h) / 2
    avail = rect.width() - 2 * m.hpad
    fm = QFontMetricsF(make_font(family, a.px, weight)) if elide_from is not None else None
    out: list[TextLine] = []
    for i, (start, length, width) in enumerate(lines):
        if elide_from is not None and i == elide_from:
            s = _elide(fm, text[start:].replace(_LINE_SEP, " "), avail)
            width = fm.horizontalAdvance(s)
        else:
            s = text[start:start + length]
        s = s.replace(_LINE_SEP, "").rstrip()
        x = rect.left() + m.hpad
        if align == "center":
            x += max(0.0, (avail - width) / 2)
        out.append(TextLine(s, x, top + i * m.line_height + m.ascent, width))
    if out:
        x0 = min(ln.x for ln in out) - m.hpad
        x1 = max(ln.x + ln.width for ln in out) + m.hpad
        text_rect = QRectF(x0, top, x1 - x0, total_h)
    else:
        text_rect = QRectF(rect.topLeft(), QSizeF(0, 0))
    return tuple(out), text_rect


def fit_text(text: str, rect: QRectF | Rect, family: str, min_px: int, max_px: int, *,
             bounds: QRectF | Rect | None = None, align: Align = "left", valign: str = "top",
             weight: int = 400, prefer_wide: bool = False) -> FitResult:
    """Wrap `text` inside `rect` at the largest pixel size in [min_px, max_px] that fits.

    Binary search over integer sizes (word wrap; a word wider than the box does not fit). If even
    `min_px` does not fit, the box grows down, then right (centred text: to both sides) only as
    much as needed within `bounds`, breaking long words anywhere; whatever still does not fit is
    elided with "…". With `prefer_wide` (a one-row source such as a menu item) the box may also
    widen within `bounds` at its own height, whole words only; that layout wins whenever it gives
    a larger font than wrapping inside the box. Every rendered glyph, Vietnamese stacked
    diacritics included, stays inside `result.rect`.
    """
    r = _to_rectf(rect)
    min_px = max(1, int(min_px))
    max_px = max(min_px, int(max_px))
    t = prepare_text(text)
    if not t:
        return FitResult(min_px, (), r, QRectF(r.topLeft(), QSizeF(0, 0)), family=family, weight=weight)

    def done(a: _Attempt, box: QRectF, grown: bool = False, elide_from: int | None = None) -> FitResult:
        lines, text_rect = _place(t, a, box, align, valign, elide_from, family, weight)
        return FitResult(a.px, lines, box, text_rect, grown, elide_from is not None, family, weight)

    b = _to_rectf(bounds) if bounds is not None else r
    centred = align == "center"
    max_h = max(r.height(), b.bottom() - r.top())
    max_w = max(r.width(), b.width() if centred else b.right() - r.left())

    def widened(width: float, height: float) -> QRectF:
        left = r.left()
        if centred:
            left = min(max(b.left(), r.center().x() - width / 2), b.right() - width)
        return QRectF(left, r.top(), width, max(r.height(), height))

    def wide_fit() -> tuple[_Attempt, float] | None:
        """Largest size on the box's own rows when the box may widen within bounds (whole words),
        at the narrowest width that keeps that size."""
        px, lo, hi = None, min_px, max_px
        while lo <= hi:
            mid = (lo + hi) // 2
            if _attempt(t, family, mid, weight, max_w, anywhere=False).fits(r.height()):
                px, lo = mid, mid + 1
            else:
                hi = mid - 1
        if px is None:
            return None
        lo_w, hi_w = int(math.ceil(r.width())), int(math.floor(max_w))
        best_w, best_a = max_w, _attempt(t, family, px, weight, max_w, anywhere=False)
        while lo_w <= hi_w:
            mid = (lo_w + hi_w) // 2
            cand = _attempt(t, family, px, weight, mid, anywhere=False)
            if cand.fits(r.height()):
                best_w, best_a, hi_w = mid, cand, mid - 1
            else:
                lo_w = mid + 1
        return best_a, best_w

    # 1) largest size that fits the box as is
    best: _Attempt | None = None
    lo, hi = min_px, max_px
    while lo <= hi:
        mid = (lo + hi) // 2
        a = _attempt(t, family, mid, weight, r.width(), anywhere=False)
        if a.fits(r.height()):
            best, lo = a, mid + 1
        else:
            hi = mid - 1
    # A one-row source (menu item, label, one-line subtitle) widens rather than wrapping into
    # smaller lines inside its box, and rather than growing down into the next row of a list.
    wide = wide_fit() if prefer_wide and max_w > r.width() and (best is None or best.px < max_px) else None
    if best is not None and (wide is None or wide[0].px <= best.px):
        return done(best, r)
    if wide is not None:
        return done(wide[0], widened(wide[1], r.height()), grown=True)
    a = _attempt(t, family, min_px, weight, r.width(), anywhere=True)
    if a.fits(r.height()):
        return done(a, r)

    # 2) grow down, then right (centred text: both sides), within bounds; never shrink the box
    if a.height <= max_h:
        return done(a, widened(r.width(), a.height), grown=True)
    wide = _attempt(t, family, min_px, weight, max_w, anywhere=True)
    if wide.height <= max_h:
        lo_w, hi_w = int(math.ceil(r.width())), int(math.floor(max_w))
        best_w, best_a = max_w, wide
        while lo_w <= hi_w:  # narrowest width that fits in the available height
            mid = (lo_w + hi_w) // 2
            cand = _attempt(t, family, min_px, weight, mid, anywhere=True)
            if cand.height <= max_h:
                best_w, best_a, hi_w = mid, cand, mid - 1
            else:
                lo_w = mid + 1
        return done(best_a, widened(best_w, best_a.height), grown=True)

    # 3) elide in the largest box
    box = widened(max_w, max_h)
    keep = max(1, int((max_h + 0.01) // wide.metrics.line_height))
    return done(wide, box, grown=(box != r), elide_from=min(keep, len(wide.lines)) - 1)


# --- blocks ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class BlockLayout:
    """Everything paint_block needs, cached per block by LayoutCache."""

    text: str  # NFC text that is drawn
    dimmed: bool  # drawing stale_translation
    fit: FitResult
    fill_rects: tuple[QRectF, ...]  # painted with the bg colour (original lines + translated lines)
    cover_rects: tuple[QRectF, ...]  # original lines only: painted a second time (see paint_block)
    corner_radius: float

    @property
    def bounds(self) -> QRectF:
        r = QRectF()
        for f in self.fill_rects:
            r = r.united(f)
        return r


def _area(r: QRectF) -> float:
    return max(0.0, r.width()) * max(0.0, r.height()) if not r.isEmpty() else 0.0


def _median(values: list[float], default: float) -> float:
    return float(statistics.median(values)) if values else default


def _char_size(block: Block) -> float:
    """Source glyph size: column width for vertical text, line height otherwise."""
    lines = block.lines or (block.box,)
    if block.vertical:
        return _median([w for _, _, w, _ in lines if w > 0], block.box[2])
    if block.line_height > 0:
        return float(block.line_height)
    return _median([h for _, _, _, h in lines if h > 0], block.box[3])


def _is_centered(block: Block, region_w: int) -> bool:
    """Centred text (subtitles, captions): line centres line up but their left edges do not."""
    lines = block.lines or (block.box,)
    if len(lines) >= 2:
        centres = [x + w / 2 for x, _, w, _ in lines]
        lefts = [x for x, _, _, _ in lines]
        mean = sum(centres) / len(centres)
        return max(abs(c - mean) for c in centres) <= 0.02 * region_w + 3 and max(lefts) - min(lefts) > 6
    x, _, w, _ = block.box
    return region_w > 0 and abs(x + w / 2 - region_w / 2) <= 0.05 * region_w and w < 0.85 * region_w


def _single_row(block: Block) -> bool:
    """All OCR boxes of the block sit on one row (a label, a menu item, a one-line subtitle)."""
    lines = block.lines or (block.box,)
    centres = [y + h / 2 for _, y, _, h in lines]
    return max(centres) - min(centres) <= 0.5 * min(max(1, h) for *_, h in lines)


def block_text(block: Block) -> tuple[str | None, bool]:
    """(text to draw, dimmed): the translation, else the stale translation (dimmed), else None."""
    if block.translation:
        return block.translation, False
    if block.stale_translation:
        return block.stale_translation, True
    return None, False


def growth_bounds(blocks, region_size: tuple[int, int]) -> dict[int, QRectF]:
    """Per block id, the area its translation may grow into: the region, minus everything from the
    next block below it (overlapping it horizontally) down and the nearest blocks left and right of
    it on the same rows. A translation that does not fit therefore never covers another block's
    text; it widens, or is elided."""
    rw, rh = region_size
    pad = 2 * FILL_PAD_PX
    out: dict[int, QRectF] = {}
    for b in blocks:
        x, y, w, h = b.box
        left, right, bottom = 0.0, float(rw), float(rh)
        for o in blocks:
            if o.id == b.id:
                continue
            ox, oy, ow, oh = o.box
            rows_overlap = min(y + h, oy + oh) - max(y, oy) > 0
            cols_overlap = min(x + w, ox + ow) - max(x, ox) > 0
            if cols_overlap and oy >= y + h - pad:
                bottom = min(bottom, oy - FILL_PAD_PX)
            elif rows_overlap and ox >= x + w - pad:
                right = min(right, ox - FILL_PAD_PX)
            elif rows_overlap and ox + ow <= x + pad:
                left = max(left, ox + ow + FILL_PAD_PX)
        out[b.id] = QRectF(left, 0.0, max(0.0, right - left), max(0.0, bottom))
    return out


def layout_block(block: Block, config: Config, region_size: tuple[int, int],
                 bounds: QRectF | None = None) -> BlockLayout | None:
    """Fit the block's translation into its box; None if there is nothing to draw yet.

    `bounds` limits where a translation that does not fit may grow (see growth_bounds); default:
    the whole region."""
    raw, dimmed = block_text(block)
    if raw is None:
        return None
    text = unicodedata.normalize("NFC", raw).strip()
    if not text:
        return None
    rw, rh = region_size
    region = QRectF(0, 0, rw, rh)
    grow = region if bounds is None else QRectF(bounds).intersected(region)
    x, y, w, h = block.box
    char = max(1.0, _char_size(block))
    min_px = max(1, int(config.min_font_px))
    vpad = max(FILL_PAD_PX, round(0.12 * char))
    if block.vertical:
        # Horizontal Vietnamese needs a wider box: widen around the original centre (about one column
        # each side, which keeps it inside a typical speech bubble), keep the height. Latin text at
        # the CJK em size looks oversized, hence the lower size cap.
        max_px = max(min_px, int(0.8 * char))
        target_w = min(float(rw), max(float(w), min(0.55 * h, w + 1.5 * char), 3.0 * char))
        left = min(max(0.0, x + w / 2 - target_w / 2), max(0.0, rw - target_w))
        rect = QRectF(left, y - vpad, target_w, h + 2 * vpad)
        align, valign = "center", "center"
    else:
        max_px = max(min_px, int(0.9 * char))
        rect = QRectF(x - FILL_PAD_PX, y - vpad, w + 2 * FILL_PAD_PX, h + 2 * vpad)
        align = "center" if _is_centered(block, rw) else "left"
        valign = "top"
        if align == "left" and x < rw / 2:
            # Grow right at most to the mirror of the left margin: a game's dialogue box is usually
            # centred, so a longer translation of a line that starts 100 px in must not run past the
            # box's right border (seen with the LLM's fuller sentences) when it can shrink or wrap.
            right = min(grow.right(), max(float(x + w + FILL_PAD_PX), float(rw - x)))
            grow = QRectF(grow.left(), grow.top(), max(0.0, right - grow.left()), grow.height())
        if len(block.lines) <= 1:
            # An OCR box hugs the glyphs; a line of text needs its full ascent + descent (stacked
            # diacritics included). Give a single line that height, centred on the source line.
            valign = "center"
            need = font_metrics(config.font_family, max_px).line_height
            if need > rect.height():
                top = min(max(0.0, y + h / 2 - need / 2), max(0.0, rh - need))
                rect = QRectF(rect.left(), top, rect.width(), need)
    rect = rect.intersected(region) if region.contains(rect.center()) else rect
    fit = fit_text(text, rect, config.font_family, min_px, max_px, bounds=grow, align=align, valign=valign,
                   prefer_wide=not block.vertical and _single_row(block))

    covers = [QRectF(lx - FILL_PAD_PX, ly - FILL_PAD_PX, lw + 2 * FILL_PAD_PX, lh + 2 * FILL_PAD_PX)
              for lx, ly, lw, lh in (block.lines or (block.box,))]
    fills = list(covers)
    m = font_metrics(config.font_family, fit.font_px)
    for ln in fit.lines:
        fills.append(QRectF(ln.x - m.hpad - 1, ln.baseline - m.ascent, ln.width + 2 * m.hpad + 2, m.line_height))
    fills = [f.intersected(region) for f in fills]
    covers = [f.intersected(region) for f in covers]
    radius = max(1.0, min(4.0, char * 0.18))
    return BlockLayout(text, dimmed, fit, tuple(f for f in fills if not f.isEmpty()),
                       tuple(f for f in covers if not f.isEmpty()), radius)


def _layout_key(block: Block, config: Config, region_size: tuple[int, int], bounds: QRectF | None = None) -> tuple:
    text, dimmed = block_text(block)
    b = None if bounds is None else (bounds.x(), bounds.y(), bounds.width(), bounds.height())
    return (block.box, block.lines, block.vertical, block.line_height, text, dimmed, tuple(region_size),
            config.font_family, config.min_font_px, b)


class LayoutCache:
    """Per-block layout cache: fit_text runs once per (block, text) change, not on every paint."""

    def __init__(self) -> None:
        self._entries: dict[int, tuple[tuple, BlockLayout | None]] = {}

    def get(self, block: Block, config: Config, region_size: tuple[int, int],
            bounds: QRectF | None = None) -> BlockLayout | None:
        key = _layout_key(block, config, region_size, bounds)
        hit = self._entries.get(block.id)
        if hit is not None and hit[0] == key:
            return hit[1]
        layout = layout_block(block, config, region_size, bounds)
        self._entries[block.id] = (key, layout)
        return layout

    def prune(self, keep_ids) -> None:
        keep = set(keep_ids)
        for bid in [b for b in self._entries if b not in keep]:
            del self._entries[bid]

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


def paint_block(painter: QPainter, block: Block, config: Config, region_size: tuple[int, int],
                opacity: float = 1.0, layout: BlockLayout | None = None) -> BlockLayout | None:
    """Cover the source lines with the block's bg colour and draw its translation on top.

    The box is filled at config.box_alpha; the source lines get a second pass at the same alpha,
    because at 235/255 high-contrast source text still ghosts through visibly (8 %). `opacity`
    scales everything; a stale translation's text is additionally drawn at STALE_TEXT_OPACITY.
    Blocks without any translation draw nothing (the original stays readable). Returns the layout.
    """
    if layout is None:
        layout = layout_block(block, config, region_size)
    if layout is None or opacity <= 0:
        return layout
    _paint_fill(painter, block, config, layout, opacity)
    _paint_text(painter, block, layout, opacity)
    return layout


def _paint_fill(painter: QPainter, block: Block, config: Config, layout: BlockLayout, opacity: float = 1.0) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    bg = bgr_to_qcolor(block.bg, round(config.box_alpha * opacity))
    for rects in (layout.fill_rects, layout.cover_rects):
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.WindingFill)  # overlapping boxes are filled once (no darker seams)
        for r in rects:
            path.addRoundedRect(r, layout.corner_radius, layout.corner_radius)
        painter.fillPath(path, bg)
    painter.restore()


def _paint_text(painter: QPainter, block: Block, layout: BlockLayout, opacity: float = 1.0) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    fg = readable_fg(block.fg, block.bg)
    text_alpha = 255 * opacity * (STALE_TEXT_OPACITY if layout.dimmed else 1.0)
    painter.setPen(QPen(bgr_to_qcolor(fg, round(text_alpha))))
    painter.setFont(layout.fit.font)
    for ln in layout.fit.lines:
        painter.drawText(QPointF(ln.x, ln.baseline), ln.text)
    painter.restore()


# --- status pill and whole overlay ------------------------------------------------------------

_STATUS_BG = {
    "running": QColor(24, 26, 32),
    "idle": QColor(24, 26, 32),
    "paused": QColor(110, 80, 10),
    "guarded": QColor(110, 80, 10),
    "error": QColor(170, 30, 30),
}


def lang_pair(state: OverlayState) -> str:
    return f"{state.src_lang.upper()}→{state.tgt_lang.upper()}"


def status_text(state: OverlayState, *, text_visible: bool = True, error: str | None = None) -> str:
    """Short Vietnamese status for the pill / tray tooltip."""
    if error:
        return error
    if state.status == "error":
        return state.message or "Lỗi"
    if state.status == "guarded":
        return state.message or "Đã tạm dừng: game có anti-cheat"
    if state.status == "paused":
        return state.message or "Tạm dừng"
    if state.pending > 0:
        text = f"Đang dịch {state.pending}…"
    elif state.message:
        text = state.message
    else:
        text = lang_pair(state)
    if not text_visible:
        text = f"{text} · đã ẩn bản dịch"
    return text


def paint_status(painter: QPainter, state: OverlayState, rect: QRectF | Rect, *, scale: float = 1.0,
                 text_visible: bool = True, error: str | None = None, family: str = "Segoe UI",
                 avoid=()) -> QRectF:
    """Small rounded pill in the top-right corner of `rect`; returns the pill's rect.

    If the pill would cover one of the `avoid` rects (drawn translations) it moves to the first
    free corner: bottom-right, top-left, bottom-left (else the corner with the least overlap).
    """
    area = _to_rectf(rect)
    text = status_text(state, text_visible=text_visible, error=error)
    font = make_font(family, round(STATUS_FONT_PX * scale), 600)
    fm = QFontMetricsF(font)
    pad_x, pad_y, inset = 8 * scale, 3 * scale, 6 * scale
    max_w = max(0.0, area.width() - 2 * inset)
    text = fm.elidedText(text, Qt.TextElideMode.ElideRight, max(0.0, max_w - 2 * pad_x))
    w = min(max_w, fm.horizontalAdvance(text) + 2 * pad_x)
    h = fm.height() + 2 * pad_y
    if w <= 2 * pad_x or h + 2 * inset > area.height():
        return QRectF()
    left, right = area.left() + inset, area.right() - inset - w
    top, bottom = area.top() + inset, area.bottom() - inset - h
    corners = [QRectF(right, top, w, h), QRectF(right, bottom, w, h), QRectF(left, top, w, h), QRectF(left, bottom, w, h)]
    avoid = [QRectF(a) for a in avoid if not QRectF(a).isEmpty()]

    def overlap(c: QRectF) -> float:
        return sum(_area(c.intersected(a)) for a in avoid)

    pill = min(corners, key=overlap)  # min() keeps the first of equal ones: top-right preferred
    # Nothing to report (the pipeline's running message is just the language pair, or the LLM is
    # refining text that is already readable on screen): dimmed.
    quiet = error is None and state.status in ("running", "idle") and state.pending == 0 \
        and (state.message in ("", lang_pair(state)) or state.message.startswith(REFINING_PREFIX)) and text_visible
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setOpacity(painter.opacity() * (0.6 if quiet else 0.92))
    bg = _STATUS_BG.get("error" if error else state.status, _STATUS_BG["idle"])
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(bg)
    painter.drawRoundedRect(pill, h / 2, h / 2)
    painter.setPen(QColor(255, 255, 255))
    painter.setFont(font)
    painter.drawText(QPointF(pill.left() + pad_x, pill.top() + pad_y + fm.ascent()), text)
    painter.restore()
    return pill


BORDER_COLOR = QColor(0, 170, 255, 200)


def paint_border(painter: QPainter, region_size: tuple[int, int]) -> None:
    """1 device-pixel dashed accent line just inside the region."""
    w, h = region_size
    painter.save()
    pen = QPen(BORDER_COLOR)
    pen.setCosmetic(True)
    pen.setWidth(1)
    pen.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.drawRect(QRectF(0.5, 0.5, max(0, w - 1), max(0, h - 1)))
    painter.restore()


def paint_overlay(painter: QPainter, state: OverlayState, config: Config, region_size: tuple[int, int], *,
                  text_visible: bool = True, scale: float = 1.0, cache: LayoutCache | None = None,
                  error: str | None = None, show_status: bool = True) -> None:
    """Blocks (unless hidden), the optional border and the status pill, in region coordinates.

    Every block's background goes first, then every block's text, so a block's fill can never
    paint over the text of a block drawn before it."""
    drawn: list[QRectF] = []
    if text_visible:
        limits = growth_bounds(state.blocks, region_size)
        layouts: list[tuple[Block, BlockLayout]] = []
        for block in state.blocks:
            bounds = limits.get(block.id)
            layout = cache.get(block, config, region_size, bounds) if cache is not None \
                else layout_block(block, config, region_size, bounds)
            if layout is not None:
                layouts.append((block, layout))
        for block, layout in layouts:
            _paint_fill(painter, block, config, layout)
        for block, layout in layouts:
            _paint_text(painter, block, layout)
        drawn = [layout.bounds for _, layout in layouts]
        if cache is not None:
            cache.prune(b.id for b in state.blocks)
    if config.show_border:
        paint_border(painter, region_size)
    if show_status:
        paint_status(painter, state, QRectF(0, 0, *region_size), scale=scale, text_visible=text_visible,
                     error=error, family=config.font_family, avoid=drawn)
