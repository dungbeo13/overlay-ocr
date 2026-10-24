"""Desktop Duplication (dxcam) first, GDI BitBlt (mss) when it delivers nothing.

DXGI Desktop Duplication hands out no frames at all while the monitor sleeps (measured: DWM down
to 1 refresh/s, dxcam returned None for a whole minute while mss still read the desktop) and can
fail outright (e.g. an output in exclusive use). dxcam also returns None on a static screen until
its first frame arrives, so the fallback only kicks in after `after_s` without any frame. dxcam is
tried again after `retry_primary_s`, then at doubling intervals up to `retry_primary_max_s`: each
retry recreates the duplication and blocks the grab for ~0.3 s. Both backends honour
WDA_EXCLUDEFROMCAPTURE, so the overlay stays invisible to either.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

from .base import CaptureBackend, Frame, Region

log = logging.getLogger(__name__)


class FallbackCapture(CaptureBackend):
    """`primary` until it stops delivering frames, then `fallback` (both created lazily, on the grab thread)."""

    name = "auto"

    def __init__(self, primary_factory: Callable[[], CaptureBackend], fallback_factory: Callable[[], CaptureBackend],
                 *, after_s: float = 1.0, retry_primary_s: float = 5.0, retry_primary_max_s: float = 60.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._primary_factory = primary_factory
        self._fallback_factory = fallback_factory
        self._after_s = after_s
        self._retry_first_s = retry_primary_s
        self._retry_max_s = max(retry_primary_s, retry_primary_max_s)
        self._retry_s = retry_primary_s  # current interval
        self._clock = clock
        self._primary: CaptureBackend | None = None
        self._fallback: CaptureBackend | None = None
        self._using_fallback = False
        self._none_since: float | None = None
        self._retry_at = 0.0
        self._closed = False
        self.fallback_grabs = 0  # diagnostics

    @property
    def using_fallback(self) -> bool:
        return self._using_fallback

    def grab(self, region: Region) -> Frame | None:
        if self._closed:
            raise RuntimeError("FallbackCapture is closed")
        now = self._clock()
        if not self._using_fallback or now >= self._retry_at:
            frame, failed = self._grab_primary(region)
            if frame is not None:
                self._none_since = None
                self._retry_s = self._retry_first_s
                if self._using_fallback:
                    log.info("%s delivers frames again; GDI fallback off", self._primary_name())
                    self._using_fallback = False
                return frame
            if self._using_fallback:
                self._retry_s = min(self._retry_max_s, self._retry_s * 2)
                self._retry_at = now + self._retry_s
            else:
                self._none_since = self._none_since if self._none_since is not None else now
                if not failed and now - self._none_since < self._after_s:
                    return None  # static screen: dxcam's first frame may still come
                if not failed:  # a failure was logged by _grab_primary already
                    log.warning("%s delivered no frame for %.1f s (display asleep, or duplication unavailable); "
                                "capturing with GDI (mss) instead", self._primary_name(), now - self._none_since)
                self._using_fallback = True
                self._retry_at = now + self._retry_s
        if self._fallback is None:
            self._fallback = self._fallback_factory()
        self.fallback_grabs += 1
        return self._fallback.grab(region)

    def _grab_primary(self, region: Region) -> tuple[Frame | None, bool]:
        """(frame, failed): failed means the primary raised or could not be created."""
        try:
            if self._primary is None:
                self._primary = self._primary_factory()
            return self._primary.grab(region), False
        except Exception:
            log.warning("%s failed; capturing with GDI (mss) for now", self._primary_name(), exc_info=True)
            self._close(self._primary)
            self._primary = None
            return None, True

    def _primary_name(self) -> str:
        return getattr(self._primary, "name", "primary capture")

    @staticmethod
    def _close(backend: CaptureBackend | None) -> None:
        if backend is not None:
            try:
                backend.close()
            except Exception:
                log.debug("capture close failed", exc_info=True)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._close(self._primary)
        self._close(self._fallback)
        self._primary = self._fallback = None
