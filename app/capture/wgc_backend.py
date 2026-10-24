"""Windows Graphics Capture through windows-capture, for whole monitors or a single window.

Sessions run free-threaded: the capture thread copies every delivered BGRA frame into a
double buffer, grab() crops + converts the latest one under a lock. On Windows 11 24H2+ WGC only
delivers a frame when content changes, so the latest frame is kept and served until replaced
(Frame.timestamp tells callers when those pixels were captured).
"""
from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes

import cv2
import numpy as np

from app.winutil import enable_per_monitor_dpi_awareness

from . import MonitorInfo, enum_monitors, intersect
from .base import CaptureBackend, Frame, Region

log = logging.getLogger(__name__)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL
_user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetClientRect.restype = wintypes.BOOL
_user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
_user32.ClientToScreen.restype = wintypes.BOOL
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_dwmapi = ctypes.WinDLL("dwmapi")
_dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
_dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
_DWMWA_EXTENDED_FRAME_BOUNDS = 9


class _WgcSession:
    """One free-threaded windows-capture session holding its latest frame (BGRA)."""

    def __init__(self, *, monitor_index: int | None = None, hwnd: int | None = None,
                 min_update_interval_ms: int | None = None, first_frame_timeout_s: float = 1.0) -> None:
        from windows_capture import WindowsCapture

        self._lock = threading.Lock()
        self._front: np.ndarray | None = None  # latest frame, only touched under the lock
        self._spare: np.ndarray | None = None  # buffer the next frame is copied into
        self.timestamp = 0.0
        self.frame_count = 0
        self.closed = threading.Event()  # set when the capture item goes away (window closed)
        self._first = threading.Event()
        self._stopping = False
        self._control = None
        self.border_setting_accepted: bool | None = None
        self.border_error: str | None = None

        errors = []
        for draw_border in (False, None):  # None = API default (yellow border may be drawn)
            try:
                capture = WindowsCapture(
                    cursor_capture=False, draw_border=draw_border,
                    minimum_update_interval=min_update_interval_ms,
                    monitor_index=monitor_index, window_hwnd=hwnd,
                )
                capture.frame_handler = self._on_frame
                capture.closed_handler = self._on_closed
                control = capture.start_free_threaded()
            except Exception as e:
                errors.append(f"draw_border={draw_border}: {e!r}")
                continue
            got_frame = self._first.wait(first_frame_timeout_s)
            if got_frame or not control.is_finished():
                self._capture, self._control = capture, control
                self.border_setting_accepted = draw_border is False
                break
            # The thread died before the first frame (e.g. border toggle unsupported): collect why.
            try:
                control.stop()
            except Exception as e:
                errors.append(f"draw_border={draw_border}: {e!r}")
        if self._control is None:
            raise RuntimeError("windows-capture session failed: " + "; ".join(errors))
        if errors:
            self.border_error = "; ".join(errors)

    def _on_frame(self, frame, capture_control) -> None:
        if self._stopping:
            capture_control.stop()
            return
        try:
            src = frame.frame_buffer  # (h, w, 4) view of the mapped texture, valid only here
            dst = self._spare
            if dst is None or dst.shape != src.shape:
                dst = np.empty(src.shape, np.uint8)
            np.copyto(dst, src)
            with self._lock:
                self._spare, self._front = self._front, dst
                self.timestamp = time.perf_counter()
                self.frame_count += 1
            self._first.set()
        except Exception:
            log.exception("WGC frame handler failed")

    def _on_closed(self) -> None:
        self.closed.set()

    @property
    def frame_size(self) -> tuple[int, int] | None:
        with self._lock:
            return None if self._front is None else (self._front.shape[1], self._front.shape[0])

    def crop(self, x: int, y: int, w: int, h: int) -> tuple[np.ndarray | None, float]:
        """BGR copy of frame pixels [y:y+h, x:x+w] (clamped) and the frame's timestamp."""
        with self._lock:
            img = self._front
            if img is None:
                return None, 0.0
            fh, fw = img.shape[:2]
            x0, y0 = max(0, x), max(0, y)
            x1, y1 = min(fw, x + w), min(fh, y + h)
            if x1 <= x0 or y1 <= y0:
                return None, 0.0
            return cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGRA2BGR), self.timestamp

    def stop(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        if self._control is not None:
            try:
                self._control.stop()
            except Exception:
                log.debug("windows-capture stop failed", exc_info=True)
        with self._lock:
            self._front = self._spare = None


class WgcMonitorCapture(CaptureBackend):
    """WGC capture of whole monitors; one session per monitor, started on first use.

    A region spanning several monitors is clamped to the monitor it overlaps most.
    """

    name = "wgc"
    background_capture = True  # the session copies every frame on its own thread

    def __init__(self, min_update_interval_ms: int | None = None, first_frame_timeout_s: float = 1.0) -> None:
        enable_per_monitor_dpi_awareness()
        self._monitors = enum_monitors()
        self._opts = dict(min_update_interval_ms=min_update_interval_ms, first_frame_timeout_s=first_frame_timeout_s)
        self._sessions: dict[int, _WgcSession] = {}
        self._start_lock = threading.Lock()
        self._closed = False

    @property
    def border_setting_accepted(self) -> bool | None:
        """True if draw_border=False was accepted by every started session (None: none started)."""
        if not self._sessions:
            return None
        return all(s.border_setting_accepted for s in self._sessions.values())

    @property
    def frame_count(self) -> int:
        return sum(s.frame_count for s in self._sessions.values())

    def _pick(self, region: Region) -> tuple[MonitorInfo, Region] | tuple[None, None]:
        best, best_rect, best_area = None, None, 0
        for mon in self._monitors:
            rect = intersect(region, mon.bounds)
            if rect is not None and rect.width * rect.height > best_area:
                best, best_rect, best_area = mon, rect, rect.width * rect.height
        return best, best_rect

    def start(self, monitor: MonitorInfo) -> _WgcSession:
        """Session for `monitor`, started on first use (waits up to first_frame_timeout_s)."""
        with self._start_lock:
            if self._closed:  # checked under the lock so a grab racing close() cannot leak a session
                raise RuntimeError("WgcMonitorCapture is closed")
            sess = self._sessions.get(monitor.index)
            if sess is None:
                # windows-capture numbers monitors from 1 in EnumDisplayMonitors order.
                sess = _WgcSession(monitor_index=monitor.index + 1, **self._opts)
                size = sess.frame_size
                if size is not None and size != (monitor.bounds.width, monitor.bounds.height):
                    log.warning("WGC monitor %d frame %s != bounds %s", monitor.index, size, monitor.bounds)
                self._sessions[monitor.index] = sess
            return sess

    def grab(self, region: Region) -> Frame | None:
        if self._closed:
            raise RuntimeError("WgcMonitorCapture is closed")
        mon, rect = self._pick(region)
        if mon is None:
            return None
        sess = self.start(mon)
        size = sess.frame_size
        if size is None:
            return None
        rect = intersect(rect, Region(mon.bounds.left, mon.bounds.top, *size))
        if rect is None:
            return None
        img, ts = sess.crop(rect.left - mon.bounds.left, rect.top - mon.bounds.top, rect.width, rect.height)
        return None if img is None else Frame(img, ts, rect)

    def close(self) -> None:
        with self._start_lock:
            if self._closed:
                return
            self._closed = True
            for sess in self._sessions.values():
                sess.stop()
            self._sessions.clear()


class WgcWindowCapture(CaptureBackend):
    """WGC capture of one window (works while it is covered by other windows, not when minimized).

    WGC frames cover the window's DWM extended frame bounds; grab_client() crops the client area.
    All rectangles are physical virtual-desktop pixels (the process is PMv2).
    """

    name = "wgc_window"
    background_capture = True

    def __init__(self, hwnd: int, min_update_interval_ms: int | None = None,
                 first_frame_timeout_s: float = 1.0) -> None:
        enable_per_monitor_dpi_awareness()
        if not _user32.IsWindow(hwnd):
            raise ValueError(f"not a window: {hwnd:#x}")
        self.hwnd = int(hwnd)
        self._session = _WgcSession(hwnd=self.hwnd, min_update_interval_ms=min_update_interval_ms,
                                    first_frame_timeout_s=first_frame_timeout_s)
        self._closed = False

    @property
    def border_setting_accepted(self) -> bool | None:
        return self._session.border_setting_accepted

    @property
    def frame_count(self) -> int:
        return self._session.frame_count

    @property
    def is_alive(self) -> bool:
        return not self._closed and not self._session.closed.is_set() and bool(_user32.IsWindow(self.hwnd))

    def window_bounds(self) -> Region:
        """Visible window rectangle (DWMWA_EXTENDED_FRAME_BOUNDS, falls back to GetWindowRect)."""
        rc = wintypes.RECT()
        hr = _dwmapi.DwmGetWindowAttribute(self.hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rc), ctypes.sizeof(rc))
        if hr != 0:
            _user32.GetWindowRect(self.hwnd, ctypes.byref(rc))
        return Region(rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)

    def client_region(self) -> Region:
        """Client area on screen: both corners via ClientToScreen so DPI virtualization cannot skew it."""
        rc = wintypes.RECT()
        _user32.GetClientRect(self.hwnd, ctypes.byref(rc))
        tl, br = wintypes.POINT(0, 0), wintypes.POINT(rc.right, rc.bottom)
        _user32.ClientToScreen(self.hwnd, ctypes.byref(tl))
        _user32.ClientToScreen(self.hwnd, ctypes.byref(br))
        return Region(tl.x, tl.y, br.x - tl.x, br.y - tl.y)

    def grab(self, region: Region) -> Frame | None:
        if self._closed:
            raise RuntimeError("WgcWindowCapture is closed")
        size = self._session.frame_size
        if size is None or not _user32.IsWindow(self.hwnd):
            return None  # nothing captured yet, or the window is gone (its last frame has no screen position)
        bounds = self.window_bounds()
        if bounds.width <= 0 or bounds.height <= 0:
            return None
        # Use the frame's own size: during a resize the last frame may lag behind the bounds.
        rect = intersect(region, Region(bounds.left, bounds.top, *size))
        if rect is None:
            return None
        img, ts = self._session.crop(rect.left - bounds.left, rect.top - bounds.top, rect.width, rect.height)
        return None if img is None else Frame(img, ts, rect)

    def grab_client(self) -> Frame | None:
        """Current client-area pixels; Frame.region is the client rect on screen (clamped)."""
        return self.grab(self.client_region())

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._session.stop()
