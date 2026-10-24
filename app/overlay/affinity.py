"""Hide overlay windows from screen capture with SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE).

WDA_EXCLUDEFROMCAPTURE (Windows 10 2004+) keeps the window visible on the monitor but removes it
from Desktop Duplication, Windows Graphics Capture and GDI BitBlt results, so our own capture
pipeline never OCRs the translations we draw. Affinity is per HWND: if Qt recreates the native
window the setting is lost, so `apply_to_widget` re-applies it automatically.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QPlatformSurfaceEvent
from PySide6.QtWidgets import QWidget

WDA_NONE = 0x00
WDA_MONITOR = 0x01
WDA_EXCLUDEFROMCAPTURE = 0x11

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
_user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
_user32.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowDisplayAffinity.restype = wintypes.BOOL


@dataclass(frozen=True)
class AffinityResult:
    ok: bool  # SetWindowDisplayAffinity succeeded
    last_error: int  # GetLastError() after the call (0 on success)
    readback: int  # GetWindowDisplayAffinity value, -1 if that call failed

    @property
    def excluded(self) -> bool:
        return self.readback == WDA_EXCLUDEFROMCAPTURE


def get_display_affinity(hwnd: int) -> int:
    """Current affinity of `hwnd` (WDA_*), or -1 if it cannot be read."""
    value = wintypes.DWORD(0)
    if not _user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(value)):
        return -1
    return int(value.value)


def set_exclude_from_capture(hwnd: int, enabled: bool = True) -> AffinityResult:
    """Set WDA_EXCLUDEFROMCAPTURE (or WDA_NONE) on a top-level window owned by this process."""
    ctypes.set_last_error(0)
    ok = bool(_user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE))
    err = ctypes.get_last_error() if not ok else 0
    return AffinityResult(ok=ok, last_error=err, readback=get_display_affinity(hwnd))


class CaptureExclusion(QObject):
    """Event filter that keeps a widget's capture affinity applied across native-window changes.

    Re-applies on the widget's Show (sent after the HWND exists but before it becomes visible)
    and WinIdChange events, and on the QWindow's PlatformSurface(SurfaceCreated) event.
    """

    def __init__(self, widget: QWidget, exclude: bool = True) -> None:
        super().__init__(widget)
        self._widget = widget
        self._window = None
        self.exclude = exclude
        self.auto_reapply = True  # set False to suspend automatic re-application (tests)
        self.last_result: AffinityResult | None = None
        self.last_hwnd = 0
        self.history: list[tuple[str, int, AffinityResult]] = []  # (trigger, hwnd, result)
        widget.installEventFilter(self)

    def apply(self, trigger: str = "manual") -> AffinityResult | None:
        """Apply the current setting to the widget's HWND now (creates the HWND if needed)."""
        return self._apply(int(self._widget.winId()), trigger)

    def set_exclude(self, exclude: bool) -> AffinityResult | None:
        self.exclude = exclude
        return self.apply("set_exclude")

    def _apply(self, hwnd: int, trigger: str) -> AffinityResult | None:
        if not hwnd:
            return None
        self._watch_window()
        result = set_exclude_from_capture(hwnd, self.exclude)
        self.last_result, self.last_hwnd = result, hwnd
        self.history.append((trigger, hwnd, result))
        del self.history[:-50]
        return result

    def _watch_window(self) -> None:
        handle = self._widget.windowHandle()
        if handle is not None and handle is not self._window:
            handle.installEventFilter(self)
            self._window = handle

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if self.auto_reapply:
            etype = event.type()
            if obj is self._widget and etype in (QEvent.Type.Show, QEvent.Type.WinIdChange):
                # internalWinId() does not force creation; it is 0 while the HWND is being destroyed.
                self._apply(int(self._widget.internalWinId()), etype.name)
            elif obj is self._window and etype == QEvent.Type.PlatformSurface \
                    and isinstance(event, QPlatformSurfaceEvent) \
                    and event.surfaceEventType() == QPlatformSurfaceEvent.SurfaceEventType.SurfaceCreated:
                self._apply(int(self._widget.internalWinId()), "SurfaceCreated")
        return False


def apply_to_widget(widget: QWidget, exclude: bool = True) -> CaptureExclusion:
    """Exclude `widget` (a top-level window) from capture now and whenever its HWND changes.

    Calls int(widget.winId()) immediately, which creates the native window if needed. The
    returned filter is parented to the widget; read `.last_result` for the AffinityResult and
    use `.set_exclude(False)` to make the window capturable again.
    """
    existing = getattr(widget, "_capture_exclusion", None)
    filt = existing if isinstance(existing, CaptureExclusion) else CaptureExclusion(widget, exclude)
    widget._capture_exclusion = filt  # keep the Python wrapper alive with the widget
    filt.exclude = exclude
    filt.apply("apply_to_widget")
    return filt
