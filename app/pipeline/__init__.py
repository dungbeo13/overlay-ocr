"""Translation pipeline (capture -> OCR -> layout -> tracking -> translation) and its data types."""
from .colors import block_colors, contrast_ratio, pick_foreground, relative_luminance, sample_background
from .fingerprint import ChangeDetector, DetectorParams, FrameDiff, compare, fingerprint
from .layout import LayoutParams, TextGroup, layout_blocks
from .matching import BlockTracker, TrackedBlock, TrackerParams, TrackResult
from .pipeline import PAUSE_REASONS, PipelineTuning, TranslationPipeline, region_from_config, state_to_dict
from .types import BGR, Block, OverlayState, Rect

__all__ = [
    "BGR",
    "Block",
    "OverlayState",
    "Rect",
    "BlockTracker",
    "ChangeDetector",
    "DetectorParams",
    "FrameDiff",
    "LayoutParams",
    "PAUSE_REASONS",
    "PipelineTuning",
    "TextGroup",
    "TrackResult",
    "TrackedBlock",
    "TrackerParams",
    "TranslationPipeline",
    "block_colors",
    "compare",
    "contrast_ratio",
    "fingerprint",
    "layout_blocks",
    "pick_foreground",
    "region_from_config",
    "relative_luminance",
    "sample_background",
    "state_to_dict",
]
