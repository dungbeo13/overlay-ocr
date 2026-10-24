"""GDI BitBlt capture through mss: slowest, but works everywhere (fallback backend).

Each grab reads the current pixels (no frame caching needed). Regions may span monitors;
they are clamped to the virtual desktop. mss keeps GDI device contexts, so one MSS instance
is created per calling thread.
"""
from __future__ import annotations

import ctypes
import threading
import time

import cv2
import numpy as np

from app.winutil import enable_per_monitor_dpi_awareness

from . import intersect
from .base import CaptureBackend, Frame, Region

_user32 = ctypes.WinDLL("user32")
_user32.GetSystemMetrics.argtypes = [ctypes.c_int]
_user32.GetSystemMetrics.restype = ctypes.c_int
_SM_XVIRTUALSCREEN, _SM_YVIRTUALSCREEN, _SM_CXVIRTUALSCREEN, _SM_CYVIRTUALSCREEN = 76, 77, 78, 79


def _virtual_screen() -> Region:
    gsm = _user32.GetSystemMetrics
    return Region(gsm(_SM_XVIRTUALSCREEN), gsm(_SM_YVIRTUALSCREEN), gsm(_SM_CXVIRTUALSCREEN), gsm(_SM_CYVIRTUALSCREEN))


class MssCapture(CaptureBackend):
    name = "mss"

    def __init__(self) -> None:
        enable_per_monitor_dpi_awareness()
        import mss  # sets PMv1 when constructed unless PMv2 is already active

        self._mss = mss
        self._local = threading.local()
        self._instances: list = []
        self._lock = threading.Lock()
        self._closed = False

    def _sct(self):
        sct = getattr(self._local, "sct", None)
        if sct is None:
            sct = self._mss.MSS()
            self._local.sct = sct
            with self._lock:
                self._instances.append(sct)
        return sct

    def grab(self, region: Region) -> Frame | None:
        if self._closed:
            raise RuntimeError("MssCapture is closed")
        rect = intersect(region, _virtual_screen())
        if rect is None:
            return None
        shot = self._sct().grab({"left": rect.left, "top": rect.top, "width": rect.width, "height": rect.height})
        ts = time.perf_counter()
        w, h = shot.size
        bgra = np.frombuffer(shot.raw, dtype=np.uint8).reshape(h, w, 4)
        return Frame(cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR), ts, Region(rect.left, rect.top, w, h))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with self._lock:
            for sct in self._instances:
                try:
                    sct.close()
                except Exception:
                    pass
            self._instances.clear()
