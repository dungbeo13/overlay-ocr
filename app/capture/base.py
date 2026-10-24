"""Capture backend interface shared by dxcam, Windows Graphics Capture and mss."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Region:
    """Rectangle in physical pixels, virtual-desktop coordinates (process must be PMv2)."""

    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


@dataclass
class Frame:
    image: np.ndarray  # H x W x 3, BGR, uint8, C-contiguous; may be read-only (dxcam cache), copy before drawing
    timestamp: float  # time.perf_counter() when the pixels were obtained
    region: Region


class CaptureBackend(ABC):
    """Grabs pixels of a screen region. Implementations convert to BGR uint8."""

    name: str = "base"
    background_capture: bool = False  # True: frames are copied in the background, not only inside grab()

    @abstractmethod
    def grab(self, region: Region) -> Frame | None:
        """Return the current pixels of `region`, or None if no frame is available yet."""

    def close(self) -> None:
        """Release native resources. Safe to call more than once."""

    def __enter__(self) -> "CaptureBackend":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
