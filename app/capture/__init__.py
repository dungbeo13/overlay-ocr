"""Screen capture backends (dxcam / Windows Graphics Capture / mss) plus monitor helpers.

Backends are imported lazily by `create_backend` because dxcam and mss set the process DPI
awareness on import; every backend calls `enable_per_monitor_dpi_awareness()` first.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from app.winutil import enable_per_monitor_dpi_awareness

from .base import CaptureBackend, Frame, Region

__all__ = [
    "BACKEND_NAMES",
    "CaptureBackend",
    "Frame",
    "MonitorInfo",
    "Region",
    "create_backend",
    "enum_monitors",
    "intersect",
    "primary_monitor",
]

# Names accepted by create_backend for whole-desktop capture ("wgc_window" also needs hwnd=).
BACKEND_NAMES = ("auto", "dxcam", "wgc", "mss")


def create_backend(name: str, **kwargs) -> CaptureBackend:
    """Create a backend by name: auto (dxcam, mss when dxcam delivers nothing) | dxcam |
    wgc (alias wgc_monitor) | wgc_window (hwnd=...) | mss."""
    key = name.lower()
    if key == "auto":
        from .dxcam_backend import DxcamCapture
        from .fallback import FallbackCapture
        from .mss_backend import MssCapture
        return FallbackCapture(DxcamCapture, MssCapture, **kwargs)
    if key == "dxcam":
        from .dxcam_backend import DxcamCapture
        return DxcamCapture(**kwargs)
    if key in ("wgc", "wgc_monitor"):
        from .wgc_backend import WgcMonitorCapture
        return WgcMonitorCapture(**kwargs)
    if key == "wgc_window":
        from .wgc_backend import WgcWindowCapture
        return WgcWindowCapture(**kwargs)
    if key == "mss":
        from .mss_backend import MssCapture
        return MssCapture(**kwargs)
    raise ValueError(f"unknown capture backend {name!r}")


def intersect(a: Region, b: Region) -> Region | None:
    """Overlap of two regions, or None if they do not overlap."""
    left, top = max(a.left, b.left), max(a.top, b.top)
    right, bottom = min(a.right, b.right), min(a.bottom, b.bottom)
    if right <= left or bottom <= top:
        return None
    return Region(left, top, right - left, bottom - top)


@dataclass(frozen=True)
class MonitorInfo:
    index: int  # 0-based EnumDisplayMonitors order (windows-capture uses index + 1)
    handle: int  # HMONITOR
    device: str  # GDI name, e.g. \\.\DISPLAY1
    bounds: Region  # physical px, virtual-desktop coordinates
    work: Region  # bounds minus taskbar
    primary: bool
    dpi: int  # effective DPI (96 = 100 %)

    @property
    def scale(self) -> float:
        return self.dpi / 96


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
)
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), _MONITORENUMPROC, wintypes.LPARAM]
_user32.EnumDisplayMonitors.restype = wintypes.BOOL
_user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFOEXW)]
_user32.GetMonitorInfoW.restype = wintypes.BOOL
_shcore = ctypes.WinDLL("shcore")
_shcore.GetDpiForMonitor.argtypes = [wintypes.HMONITOR, ctypes.c_int, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT)]
_shcore.GetDpiForMonitor.restype = ctypes.c_long
_MONITORINFOF_PRIMARY = 1
_MDT_EFFECTIVE_DPI = 0


def _rect_region(rc: wintypes.RECT) -> Region:
    return Region(rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)


def enum_monitors() -> list[MonitorInfo]:
    """All active monitors in EnumDisplayMonitors order, in physical pixels."""
    enable_per_monitor_dpi_awareness()
    handles: list[int] = []

    @_MONITORENUMPROC
    def _cb(hmon, _hdc, _rect, _lparam):
        handles.append(hmon)
        return True

    _user32.EnumDisplayMonitors(None, None, _cb, 0)
    monitors = []
    for i, hmon in enumerate(handles):
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        if not _user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            continue
        dx, dy = wintypes.UINT(96), wintypes.UINT(96)
        _shcore.GetDpiForMonitor(hmon, _MDT_EFFECTIVE_DPI, ctypes.byref(dx), ctypes.byref(dy))
        monitors.append(MonitorInfo(
            index=i,
            handle=int(hmon),
            device=info.szDevice,
            bounds=_rect_region(info.rcMonitor),
            work=_rect_region(info.rcWork),
            primary=bool(info.dwFlags & _MONITORINFOF_PRIMARY),
            dpi=int(dx.value),
        ))
    return monitors


def primary_monitor() -> MonitorInfo:
    monitors = enum_monitors()
    return next((m for m in monitors if m.primary), monitors[0])
