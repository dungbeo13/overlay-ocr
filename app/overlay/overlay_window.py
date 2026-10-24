"""Transparent, click-through, topmost, capture-excluded window that draws translations over the region.

The window covers the capture region exactly. It paints in physical pixels relative to the
region's top-left (painter scaled by 1/devicePixelRatio), so OverlayState boxes are used as is.
While a blocked game owns the foreground (`set_guarded(True)` or a state with status "guarded")
the native window is destroyed, so no layered topmost window exists over the game at all.
"""
from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QPaintEvent
from PySide6.QtWidgets import QApplication, QWidget

from app.capture.base import Region
from app.config import Config
from app.pipeline.types import OverlayState

from .affinity import WDA_EXCLUDEFROMCAPTURE, CaptureExclusion, apply_to_widget, get_display_affinity
from .geometry import logical_to_physical, physical_to_logical, screen_for_region
from .render import LayoutCache, paint_overlay

log = logging.getLogger(__name__)

OVERLAY_FLAGS = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus)
MAX_REPAINT_HZ = 30
TOPMOST_INTERVAL_MS = 1000
EXCLUSION_ERROR = "Lỗi: không ẩn được overlay khỏi capture"

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, wintypes.UINT]
_user32.SetWindowPos.restype = wintypes.BOOL
_HWND_TOPMOST = wintypes.HWND(-1)
_SWP_NOSIZE, _SWP_NOMOVE, _SWP_NOACTIVATE, _SWP_NOOWNERZORDER = 0x0001, 0x0002, 0x0010, 0x0200


def assert_topmost(hwnd: int) -> bool:
    """Put `hwnd` back at the top of the topmost band without activating it."""
    return bool(_user32.SetWindowPos(hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
                                     _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_NOOWNERZORDER))


class OverlayWindow(QWidget):
    """Draws the latest OverlayState over its region. GUI thread only.

    Signals:
        exclusion_changed(bool): capture exclusion was lost (False) or restored (True). While it is
            False the app must keep the pipeline paused (never OCR our own translations).
    """

    exclusion_changed = Signal(bool)

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent, OVERLAY_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowTitle("Screen Translator overlay")
        self._config = config
        self._region: Region | None = None
        self._phys: Region | None = None  # physical rect the window actually got
        self._state: OverlayState | None = None
        self._text_visible = True
        self._guarded = False  # set_guarded()
        self._state_guarded = False  # latest state has status "guarded"
        self._suspended = False  # set_suspended(), e.g. while the region selector is open
        self._cache = LayoutCache()
        self._native = QGuiApplication.platformName() == "windows"
        self._exclusion: CaptureExclusion | None = None
        self._exclusion_ok = False
        self.paint_count = 0  # diagnostics / tests
        self._last_update = 0.0

        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.timeout.connect(self._flush_repaint)
        self._maintain_timer = QTimer(self)
        self._maintain_timer.setInterval(TOPMOST_INTERVAL_MS)
        self._maintain_timer.timeout.connect(self._maintain)

        if self._native:
            self._exclusion = apply_to_widget(self)  # creates the (hidden) HWND
            res = self._exclusion.last_result
            self._exclusion_ok = bool(res and res.excluded)
            if not self._exclusion_ok:
                log.warning("overlay capture exclusion failed: %s", res)

    # --- public API ------------------------------------------------------------------------

    def set_region(self, region: Region | None) -> None:
        """Cover `region` (physical px) or hide when None."""
        if region is not None and (region.width <= 0 or region.height <= 0):
            region = None
        if region != self._region:
            self._region = region
            self._cache.clear()
            if region is not None:
                self._place()
        self._sync_visibility()
        self._schedule_repaint()

    def region(self) -> Region | None:
        return self._region

    def set_state(self, state: OverlayState) -> bool:
        """Show `state` unless it is older than (or as old as) the current one. Returns True if taken.

        The window follows state.region (None hides it) and hides while status is "guarded".
        """
        if self._state is not None and state.seq <= self._state.seq:
            return False
        self._state = state
        self._state_guarded = state.status == "guarded"
        self.set_region(state.region)  # also syncs visibility and schedules a repaint
        return True

    def state(self) -> OverlayState | None:
        return self._state

    def set_text_visible(self, visible: bool) -> None:
        if visible != self._text_visible:
            self._text_visible = visible
            self._schedule_repaint()

    def text_visible(self) -> bool:
        return self._text_visible

    def set_guarded(self, guarded: bool) -> None:
        """Hide and destroy the native window while a blocked game is in the foreground."""
        if guarded != self._guarded:
            self._guarded = guarded
            self._sync_visibility()

    def set_suspended(self, suspended: bool) -> None:
        """Hide (keeping the HWND), e.g. while the region selector is open."""
        if suspended != self._suspended:
            self._suspended = suspended
            self._sync_visibility()

    def exclusion_ok(self) -> bool:
        """True if the window is excluded from screen capture (live GetWindowDisplayAffinity readback)."""
        if not self._native or self._exclusion is None:
            return False
        hwnd = int(self.internalWinId())
        if hwnd:
            return get_display_affinity(hwnd) == WDA_EXCLUDEFROMCAPTURE
        res = self._exclusion.last_result  # no native window right now (guarded): nothing to capture
        return bool(res and res.excluded)

    def snapshot(self) -> QImage:
        """Render the overlay as it would be drawn now (QWidget.grab; works while hidden)."""
        return self.grab().toImage()

    def shutdown(self) -> None:
        self._repaint_timer.stop()
        self._maintain_timer.stop()
        self.hide()
        self.close()

    # --- internals -------------------------------------------------------------------------

    def _should_show(self) -> bool:
        return self._region is not None and not (self._guarded or self._state_guarded or self._suspended)

    def _place(self) -> None:
        region = self._region
        screen = screen_for_region(region)
        if screen is not None and self.screen() is not screen:
            self.setScreen(screen)
        self.setGeometry(physical_to_logical(region))
        self._phys = logical_to_physical(self.geometry())

    def _sync_visibility(self) -> None:
        if self._should_show():
            if not self.isVisible():
                self.show()  # WA_ShowWithoutActivating: SW_SHOWNOACTIVATE
                self._after_show()
            return
        if self.isVisible():
            self.hide()
        self._maintain_timer.stop()
        if (self._guarded or self._state_guarded) and self._native and self.internalWinId():
            self.destroy()  # no layered topmost HWND at all while a protected game is in front
            log.info("overlay native window destroyed while guarded")

    def _after_show(self) -> None:
        if not self._native:
            return
        hwnd = int(self.internalWinId())
        if hwnd:
            assert_topmost(hwnd)
        self._set_exclusion_ok(self.exclusion_ok())
        self._maintain_timer.start()

    def _maintain(self) -> None:
        """Every ~1 s while visible: stay on top and make sure capture exclusion still holds."""
        hwnd = int(self.internalWinId())
        if not hwnd or not self.isVisible():
            return
        if QApplication.activePopupWidget() is None:  # never cover our own open tray menu
            assert_topmost(hwnd)
        ok = get_display_affinity(hwnd) == WDA_EXCLUDEFROMCAPTURE
        if not ok and self._exclusion is not None:
            res = self._exclusion.apply("maintain")
            ok = bool(res and res.excluded)
        self._set_exclusion_ok(ok)

    def _set_exclusion_ok(self, ok: bool) -> None:
        if ok != self._exclusion_ok:
            self._exclusion_ok = ok
            (log.info if ok else log.warning)("overlay capture exclusion %s", "restored" if ok else "LOST")
            self.exclusion_changed.emit(ok)
            self._schedule_repaint()

    def _schedule_repaint(self) -> None:
        if self._repaint_timer.isActive():
            return
        wait_ms = 1000 / MAX_REPAINT_HZ - (time.monotonic() - self._last_update) * 1000
        self._repaint_timer.start(max(0, int(wait_ms)))

    def _flush_repaint(self) -> None:
        self._last_update = time.monotonic()
        self.update()

    def event(self, e: QEvent) -> bool:
        if e.type() in (QEvent.Type.DevicePixelRatioChange, QEvent.Type.ScreenChangeInternal) and self._region:
            QTimer.singleShot(0, self._reposition)
        return super().event(e)

    def _reposition(self) -> None:
        if self._region is not None:
            self._place()
            self._cache.clear()
            self._schedule_repaint()

    def moveEvent(self, e) -> None:
        self._phys = logical_to_physical(self.geometry())
        super().moveEvent(e)

    def resizeEvent(self, e) -> None:
        self._phys = logical_to_physical(self.geometry())
        super().resizeEvent(e)

    def paintEvent(self, _e: QPaintEvent) -> None:
        self.paint_count += 1
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        region = self._region
        if region is not None:
            dpr = self.devicePixelRatioF()
            phys = self._phys or logical_to_physical(self.geometry())
            p.scale(1 / dpr, 1 / dpr)
            p.translate(region.left - phys.left, region.top - phys.top)
            state = self._state or OverlayState(seq=-1, region=region, src_lang=self._config.src_lang,
                                                tgt_lang=self._config.tgt_lang)
            error = EXCLUSION_ERROR if self._native and not self._exclusion_ok else None
            paint_overlay(p, state, self._config, (region.width, region.height), text_visible=self._text_visible,
                          scale=dpr, cache=self._cache, error=error)
        p.end()
