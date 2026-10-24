"""Cheap frame-change detection: a coarse luminance grid plus OCR scheduling decisions.

A fingerprint is a 32x16 grid of mean luminance plus the grey frame scaled down to <= 640 px
wide. Against the frame last OCR'd, a cell counts as changed when its mean moved by more than
16 grey levels, or when its pixels moved by more than 16 levels on average (mean absolute
difference). The second test is an addition to the plain mean-luminance rule: swapping one
line of text for another of similar ink density (the next subtitle) barely moves the cell
means once cells are ~40x45 px or larger, but moves the pixels a lot.

A frame counts as changed when more than 5% of the cells changed, or when a band of 2 adjacent
rows (or columns) holds at least 4 changed cells: a subtitle swap on a tall region touches far
fewer than 5% of the cells. A blink (cursor, "next" arrow) or a global brightness drift stays
below both rules.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np

GRID_COLS = 32
GRID_ROWS = 16
DETAIL_WIDTH = 640


@dataclass(frozen=True)
class Fingerprint:
    grid: np.ndarray  # mean luminance (0-255) per cell, float32 [rows, cols]
    detail: np.ndarray  # grey frame scaled down to <= DETAIL_WIDTH px wide, uint8

    @property
    def shape(self) -> tuple[int, int]:
        return self.grid.shape  # type: ignore[return-value]


def fingerprint(image: np.ndarray, cols: int = GRID_COLS, rows: int = GRID_ROWS,
                detail_width: int = DETAIL_WIDTH) -> Fingerprint:
    """Fingerprint of a BGR, BGRA or grey frame. The grid shrinks for regions smaller than it."""
    if image.ndim == 3:
        code = cv2.COLOR_BGRA2GRAY if image.shape[2] == 4 else cv2.COLOR_BGR2GRAY
        grey = cv2.cvtColor(image, code)
    else:
        grey = image
    h, w = grey.shape[:2]
    scale = min(1.0, detail_width / w) if w else 1.0
    if scale < 1.0:
        grey = cv2.resize(grey, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    gc, gr = max(1, min(cols, w)), max(1, min(rows, h))
    grid = cv2.resize(grey, (gc, gr), interpolation=cv2.INTER_AREA).astype(np.float32)
    return Fingerprint(grid, np.ascontiguousarray(grey))


@dataclass(frozen=True)
class FrameDiff:
    changed: bool
    changed_cells: int
    total_cells: int
    band_cells: int  # most changed cells inside any band of `band` adjacent rows or columns
    max_delta: float


def compare(
    a: Fingerprint | None,
    b: Fingerprint | None,
    *,
    cell_threshold: float = 16.0,
    detail_threshold: float | None = 16.0,
    frame_fraction: float = 0.05,
    band: int = 2,
    band_min_cells: int = 4,
) -> FrameDiff:
    """Compare two fingerprints. Different shapes (region resized) or a missing one = changed.

    detail_threshold=None disables the per-cell mean-absolute-difference test.
    """
    if a is None or b is None or a.grid.shape != b.grid.shape:
        n = 0 if b is None else int(b.grid.size)
        return FrameDiff(True, n, n, n, 255.0)
    delta = np.abs(a.grid - b.grid)
    cells = delta > cell_threshold
    max_delta = float(delta.max()) if delta.size else 0.0
    if detail_threshold is not None and a.detail.shape == b.detail.shape:
        rows, cols = a.grid.shape
        mad = cv2.resize(cv2.absdiff(a.detail, b.detail), (cols, rows), interpolation=cv2.INTER_AREA)
        cells |= mad > detail_threshold
        max_delta = max(max_delta, float(mad.max()))
    n_changed = int(cells.sum())
    total = int(cells.size)
    band_cells = 0
    if n_changed:
        band_cells = max(_max_window_sum(cells.sum(axis=1), band), _max_window_sum(cells.sum(axis=0), band))
    changed = n_changed > frame_fraction * total or band_cells >= band_min_cells
    return FrameDiff(changed, n_changed, total, band_cells, max_delta)


def _max_window_sum(counts: np.ndarray, window: int) -> int:
    if counts.size <= window:
        return int(counts.sum())
    return int(np.convolve(counts, np.ones(window, dtype=counts.dtype), mode="valid").max())


@dataclass
class DetectorParams:
    cols: int = GRID_COLS
    rows: int = GRID_ROWS
    detail_width: int = DETAIL_WIDTH
    cell_threshold: float = 16.0
    detail_threshold: float | None = 16.0
    frame_fraction: float = 0.05
    band: int = 2
    band_min_cells: int = 4
    settle_polls: int = 1  # polls the frame must stay unchanged before OCR
    force_after_s: float = 0.6  # OCR anyway after this much continuous change
    idle_rescan_s: float = 2.0  # OCR an unchanged region this often


class ChangeDetector:
    """Decides, frame by frame, when an OCR pass is worth running.

    update() returns None (skip) or the reason for running OCR on this frame:
    "initial" (first frame after reset), "settled" (changed, then stable for `settle_polls`),
    "forced" (changing continuously for `force_after_s`), "idle" (periodic rescan) or "confirm"
    (a pass the caller asked for with request_pass(), e.g. to see whether typewriter text is
    still growing: its last glyphs change too few cells to count as a change).
    A non-None result means the caller runs OCR on this frame: it becomes the new reference.
    """

    def __init__(self, params: DetectorParams | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.p = params or DetectorParams()
        self.clock = clock
        self.last_diff: FrameDiff | None = None
        self.reset()

    def reset(self) -> None:
        """Forget everything: the next frame triggers an "initial" OCR pass."""
        self._ref: Fingerprint | None = None  # frame last OCR'd
        self._prev: Fingerprint | None = None  # previous poll
        self._dirty_since: float | None = None
        self._stable_polls = 0
        self._last_ocr = -1e18
        self._requested: float | None = None

    def request_pass(self, at: float) -> None:
        """Run an OCR pass on the first frame at or after `at`, changed or not. Requests merge
        (the earliest wins) and are cleared by any pass."""
        self._requested = at if self._requested is None else min(self._requested, at)

    @property
    def requested(self) -> float | None:
        return self._requested

    @property
    def dirty(self) -> bool:
        """True while the screen differs from the frame last OCR'd (an OCR pass is coming)."""
        return self._dirty_since is not None

    def _compare(self, a: Fingerprint | None, b: Fingerprint | None) -> FrameDiff:
        p = self.p
        return compare(a, b, cell_threshold=p.cell_threshold, detail_threshold=p.detail_threshold,
                       frame_fraction=p.frame_fraction, band=p.band, band_min_cells=p.band_min_cells)

    def update(self, image: np.ndarray, now: float | None = None) -> str | None:
        return self.update_fingerprint(fingerprint(image, self.p.cols, self.p.rows, self.p.detail_width), now)

    def update_fingerprint(self, fp: Fingerprint, now: float | None = None) -> str | None:
        now = self.clock() if now is None else now
        prev, self._prev = self._prev, fp
        if self._ref is None:
            return self._fire(fp, now, "initial")

        diff = self._compare(self._ref, fp)
        self.last_diff = diff
        if not diff.changed:
            self._dirty_since = None
            self._stable_polls = 0
            if now - self._last_ocr >= self.p.idle_rescan_s:
                return self._fire(fp, now, "idle")
            if self._requested is not None and now >= self._requested:
                return self._fire(fp, now, "confirm")
            return None

        if self._dirty_since is None:
            self._dirty_since = now
        moving = self._compare(prev, fp).changed
        self._stable_polls = 0 if moving else self._stable_polls + 1
        if self._stable_polls >= self.p.settle_polls:
            return self._fire(fp, now, "settled")
        if now - self._dirty_since >= self.p.force_after_s:
            return self._fire(fp, now, "forced")
        return None  # a "settled" or "forced" pass follows within force_after_s and serves any request

    def _fire(self, fp: Fingerprint, now: float, reason: str) -> str:
        self._ref = fp
        self._last_ocr = now
        self._dirty_since = None
        self._stable_polls = 0
        self._requested = None
        return reason
