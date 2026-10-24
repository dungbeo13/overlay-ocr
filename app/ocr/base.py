"""OCR engine interface shared by RapidOCR and Windows.Media.Ocr."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass
class TextBox:
    text: str
    box: tuple[int, int, int, int]  # x, y, w, h in input-image pixels (axis-aligned)
    score: float | None = None  # None when the engine reports no confidence


class OcrEngine(ABC):
    name: str = "base"

    @abstractmethod
    def recognize(self, image_bgr: np.ndarray) -> list[TextBox]:
        """Return text lines found in a BGR uint8 image, top-to-bottom."""

    def warmup(self, runs: int = 3) -> None:
        """Run a few dummy passes so first-call overhead does not skew timings."""
        blank = np.full((64, 256, 3), 255, dtype=np.uint8)
        for _ in range(runs):
            self.recognize(blank)
