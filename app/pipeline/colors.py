"""Background colour sampling around text lines and WCAG-contrast foreground choice."""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from .types import BGR, Rect

BLACK: BGR = (0, 0, 0)
WHITE: BGR = (255, 255, 255)


def ring_pixels(image: np.ndarray, rect: Rect, gap: int = 2, width: int = 3) -> np.ndarray:
    """Pixels (N x 3 BGR) of a `width`-px ring starting `gap` px outside `rect`, clipped to the image."""
    h, w = image.shape[:2]
    x, y, rw, rh = rect
    ix0, iy0, ix1, iy1 = x - gap, y - gap, x + rw + gap, y + rh + gap  # inner edge of the ring
    ox0, oy0 = max(0, ix0 - width), max(0, iy0 - width)
    ox1, oy1 = min(w, ix1 + width), min(h, iy1 + width)
    if ox1 <= ox0 or oy1 <= oy0:
        return np.empty((0, 3), dtype=np.uint8)
    cx0, cy0, cx1, cy1 = max(ox0, ix0), max(oy0, iy0), min(ox1, ix1), min(oy1, iy1)
    parts = []
    if cy0 > oy0:
        parts.append(image[oy0:cy0, ox0:ox1])  # top band
    if oy1 > cy1:
        parts.append(image[max(cy1, oy0):oy1, ox0:ox1])  # bottom band
    if cy1 > cy0:
        if cx0 > ox0:
            parts.append(image[cy0:cy1, ox0:cx0])  # left band
        if ox1 > cx1:
            parts.append(image[cy0:cy1, max(cx1, ox0):ox1])  # right band
    parts = [p.reshape(-1, image.shape[2])[:, :3] for p in parts if p.size]
    if not parts:
        return np.empty((0, 3), dtype=np.uint8)
    return np.concatenate(parts, axis=0)


def sample_background(image: np.ndarray, rects: Iterable[Rect], gap: int = 2, width: int = 3) -> BGR:
    """Median colour of the rings just outside the given line boxes.

    Falls back to the median of the pixels inside the boxes when they cover the whole image.
    """
    rects = list(rects)
    rings = [ring_pixels(image, r, gap, width) for r in rects]
    pixels = np.concatenate(rings, axis=0) if rings else np.empty((0, 3), dtype=np.uint8)
    if not len(pixels):
        h, w = image.shape[:2]
        inside = [image[max(0, y):min(h, y + rh), max(0, x):min(w, x + rw)].reshape(-1, image.shape[2])[:, :3]
                  for x, y, rw, rh in rects]
        inside = [p for p in inside if len(p)]
        if not inside:
            return BLACK
        pixels = np.concatenate(inside, axis=0)
    med = np.median(pixels, axis=0)
    return int(med[0]), int(med[1]), int(med[2])


def _channel(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(bgr: BGR) -> float:
    """WCAG 2.x relative luminance (0 = black, 1 = white) of an sRGB colour given as BGR."""
    b, g, r = bgr
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(a: BGR, b: BGR) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def pick_foreground(bg: BGR) -> BGR:
    """Black or white, whichever contrasts more with `bg` (ties go to white)."""
    return BLACK if contrast_ratio(bg, BLACK) > contrast_ratio(bg, WHITE) else WHITE


def block_colors(image: np.ndarray, rects: Iterable[Rect]) -> tuple[BGR, BGR]:
    """(bg, fg) for a block: sampled background and a readable text colour."""
    bg = sample_background(image, rects)
    return bg, pick_foreground(bg)


def colors_close(a: BGR, b: BGR, tol: int) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))
