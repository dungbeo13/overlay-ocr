"""Data passed from the translation pipeline (worker threads) to the overlay (GUI thread).

The pipeline publishes immutable OverlayState snapshots; the overlay only ever draws the latest
one. All boxes are in physical pixels relative to the capture region's top-left corner.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.capture.base import Region

Rect = tuple[int, int, int, int]  # x, y, w, h
BGR = tuple[int, int, int]


@dataclass(frozen=True)
class Block:
    """One paragraph / dialog line / speech bubble of source text and its translation."""

    id: int  # stable while the same text stays in roughly the same place
    box: Rect  # union of `lines`
    lines: tuple[Rect, ...]  # per-line OCR boxes, used to paint over the original text
    source: str  # recognised text, lines joined for translation
    vertical: bool = False  # vertical Japanese column(s)
    bg: BGR = (0, 0, 0)  # background colour sampled around the text
    fg: BGR = (255, 255, 255)  # text colour with enough contrast against `bg`
    line_height: int = 0  # median source line height (px), a hint for the font size
    translation: str | None = None  # translation of `source`; None while pending or failed
    stale_translation: str | None = None  # previous translation, drawn dimmed while pending
    error: str | None = None  # last translation error for this block, if any
    quality: int = 0  # tier of `translation`: 0 none, 1 machine translation, 2 contextual LLM, 3 human
    speaker: str | None = None  # speaker name the LLM attributed this line to (source text), if any


@dataclass(frozen=True)
class OverlayState:
    seq: int  # increases with every snapshot; the overlay ignores older ones
    region: Region | None  # None = no region selected (overlay hidden)
    blocks: tuple[Block, ...] = ()
    status: str = "idle"  # "idle" | "running" | "paused" | "guarded" | "error"
    message: str = ""  # short human-readable status (Vietnamese), e.g. "Đang dịch 2 đoạn…"
    src_lang: str = "ja"
    tgt_lang: str = "vi"
    pending: int = 0  # translations in flight
    timestamp: float = 0.0  # time.time() when the snapshot was made
    metrics: dict = field(default_factory=dict)  # last pass timings (ms), counters; for logs/debug
