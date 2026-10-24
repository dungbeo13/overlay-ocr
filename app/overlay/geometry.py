"""Physical-pixel <-> Qt logical coordinate mapping for overlay windows.

Qt 6 keeps every screen's top-left corner at its native (physical) position and divides sizes by
the screen's devicePixelRatio, so inside one screen

    logical  = origin + (physical - origin) / dpr
    physical = origin + (logical - origin) * dpr

where `origin` is the screen's physical top-left (== QScreen.geometry().topLeft()). Qt rounds a
window's native position and size separately (qRound), which `logical_to_physical` reproduces.
Verified against GetWindowRect with QT_SCALE_FACTOR=1.5 (see tests/test_overlay.py).

QScreen.name() is the monitor's friendly name in Qt 6.12 (its EDID model name, e.g. "DELL U2723QE"), not
the GDI device name, so screens are matched to `enum_monitors()` by HMONITOR (QNativeInterface.QWindowsScreen),
then by device name, then by geometry.
"""
from __future__ import annotations

import ctypes
import math
from ctypes import wintypes
from dataclasses import dataclass

from PySide6.QtCore import QRect, QRectF
from PySide6.QtGui import QGuiApplication, QScreen

from app.capture import MonitorInfo, Region, enum_monitors, intersect


@dataclass(frozen=True)
class ScreenMap:
    """One QScreen with its physical (Win32) and logical (Qt) geometry."""

    name: str
    physical: Region  # monitor bounds, physical px, virtual-desktop coordinates
    logical: QRect  # QScreen.geometry()
    dpr: float  # QScreen.devicePixelRatio()
    hmonitor: int = 0
    device: str = ""  # GDI device name, e.g. \\.\DISPLAY6 ("" if unmatched)

    def to_logical(self, x: float, y: float) -> tuple[float, float]:
        return (self.logical.x() + (x - self.physical.left) / self.dpr,
                self.logical.y() + (y - self.physical.top) / self.dpr)

    def to_physical(self, x: float, y: float) -> tuple[float, float]:
        return (self.physical.left + (x - self.logical.x()) * self.dpr,
                self.physical.top + (y - self.logical.y()) * self.dpr)


def _screen_hmonitor(screen: QScreen) -> int:
    try:
        native = screen.nativeInterface()  # QNativeInterface.QWindowsScreen on the windows platform
        return int(native.handle()) if native is not None else 0
    except Exception:
        return 0


def _match_monitor(screen: QScreen, monitors: list[MonitorInfo]) -> MonitorInfo | None:
    hmon = _screen_hmonitor(screen)
    for m in monitors:
        if hmon and m.handle == hmon:
            return m
    for m in monitors:
        if m.device and m.device == screen.name():
            return m
    g, dpr = screen.geometry(), screen.devicePixelRatio()
    for m in monitors:  # Qt keeps the native origin and scales the size
        if (m.bounds.left, m.bounds.top) == (g.x(), g.y()) \
                and abs(m.bounds.width - g.width() * dpr) <= 2 and abs(m.bounds.height - g.height() * dpr) <= 2:
            return m
    return None


def screen_maps() -> list[ScreenMap]:
    """Current screens. Without a QGuiApplication, monitors map 1:1 (dpr 1)."""
    monitors = enum_monitors()
    app = QGuiApplication.instance()
    if app is None:
        return [ScreenMap(m.device, m.bounds, QRect(m.bounds.left, m.bounds.top, m.bounds.width, m.bounds.height),
                          1.0, m.handle, m.device) for m in monitors]
    maps = []
    for screen in QGuiApplication.screens():
        g, dpr = screen.geometry(), float(screen.devicePixelRatio())
        mon = _match_monitor(screen, monitors)
        if mon is not None:
            maps.append(ScreenMap(screen.name(), mon.bounds, QRect(g), dpr, mon.handle, mon.device))
        else:  # e.g. the offscreen platform: derive the physical rect the way Qt does
            phys = Region(g.x(), g.y(), round(g.width() * dpr), round(g.height() * dpr))
            maps.append(ScreenMap(screen.name(), phys, QRect(g), dpr))
    return maps


def map_for_region(region: Region, maps: list[ScreenMap] | None = None) -> ScreenMap | None:
    """The screen the region overlaps most (dxcam clamps to the same one); nearest if none."""
    maps = screen_maps() if maps is None else maps
    if not maps:
        return None
    best, best_area = None, 0
    for sm in maps:
        r = intersect(region, sm.physical)
        if r is not None and r.width * r.height > best_area:
            best, best_area = sm, r.width * r.height
    if best is not None:
        return best
    cx, cy = region.left + region.width / 2, region.top + region.height / 2

    def dist(sm: ScreenMap) -> float:
        p = sm.physical
        dx = max(p.left - cx, 0, cx - p.right)
        dy = max(p.top - cy, 0, cy - p.bottom)
        return math.hypot(dx, dy)

    return min(maps, key=dist)


def map_for_logical(rect: QRect | QRectF, maps: list[ScreenMap] | None = None) -> ScreenMap | None:
    """The screen whose logical geometry contains the rect's centre (else overlaps it most)."""
    maps = screen_maps() if maps is None else maps
    if not maps:
        return None
    r = QRectF(rect)
    c = r.center()
    for sm in maps:
        if QRectF(sm.logical).contains(c):
            return sm
    best = max(maps, key=lambda sm: _area(QRectF(sm.logical).intersected(r)))
    return best


def _area(r: QRectF) -> float:
    return max(0.0, r.width()) * max(0.0, r.height())


def physical_to_logical_f(region: Region, maps: list[ScreenMap] | None = None) -> QRectF:
    """Exact (fractional) logical rect of a physical region."""
    sm = map_for_region(region, maps)
    if sm is None:
        return QRectF(region.left, region.top, region.width, region.height)
    x0, y0 = sm.to_logical(region.left, region.top)
    x1, y1 = sm.to_logical(region.right, region.bottom)
    return QRectF(x0, y0, x1 - x0, y1 - y0)


def physical_to_logical(region: Region, maps: list[ScreenMap] | None = None) -> QRect:
    """Integer logical rect for a window that covers `region` (left/top floored, right/bottom ceiled)."""
    r = physical_to_logical_f(region, maps)
    eps = 1e-6
    left, top = math.floor(r.left() + eps), math.floor(r.top() + eps)
    right, bottom = math.ceil(r.right() - eps), math.ceil(r.bottom() - eps)
    return QRect(left, top, max(1, right - left), max(1, bottom - top))


def _qround(v: float) -> int:
    """qRound: round half away from zero."""
    return int(math.floor(v + 0.5)) if v >= 0 else -int(math.floor(-v + 0.5))


def logical_to_physical(rect: QRect | QRectF, maps: list[ScreenMap] | None = None) -> Region:
    """Native rect Qt gives a window with logical geometry `rect` (same rounding as Qt)."""
    sm = map_for_logical(rect, maps)
    r = QRectF(rect)
    if sm is None:
        return Region(_qround(r.x()), _qround(r.y()), _qround(r.width()), _qround(r.height()))
    left = sm.physical.left + _qround((r.x() - sm.logical.x()) * sm.dpr)
    top = sm.physical.top + _qround((r.y() - sm.logical.y()) * sm.dpr)
    return Region(left, top, _qround(r.width() * sm.dpr), _qround(r.height() * sm.dpr))


def screen_for_region(region: Region) -> QScreen | None:
    """The QScreen that `map_for_region` picks, or None without a QGuiApplication."""
    if QGuiApplication.instance() is None:
        return None
    sm = map_for_region(region)
    if sm is None:
        return QGuiApplication.primaryScreen()
    for screen in QGuiApplication.screens():
        if screen.geometry() == sm.logical and screen.name() == sm.name:
            return screen
    return QGuiApplication.primaryScreen()


def device_pixel_ratio_for_region(region: Region) -> float:
    sm = map_for_region(region)
    return sm.dpr if sm is not None else 1.0


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_user32.GetCursorPos.restype = wintypes.BOOL


def cursor_pos() -> tuple[int, int] | None:
    """Cursor position in physical px (the process is Per-Monitor-DPI-aware v2), None on failure."""
    pt = wintypes.POINT()
    if not _user32.GetCursorPos(ctypes.byref(pt)):
        return None
    return int(pt.x), int(pt.y)
