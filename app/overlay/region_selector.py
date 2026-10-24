"""Region picker (Ctrl+Alt+R): one dim, capture-excluded window per screen; drag a rectangle.

Points are read with Win32 GetCursorPos at press / move / release, so the result is in physical
pixels whatever the screens' scale factors (the process is Per-Monitor-DPI-aware v2). Esc or a
right click cancels; a selection smaller than 16x16 is rejected and the user can drag again.
Tests drive it without real input through simulate_press / simulate_move / simulate_release.
"""
from __future__ import annotations

import ctypes
import logging

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QKeyEvent, QMouseEvent, QPainter, QPen, QScreen
from PySide6.QtWidgets import QWidget

from app.capture.base import Region

from .affinity import apply_to_widget
from .geometry import ScreenMap, cursor_pos, screen_maps

log = logging.getLogger(__name__)

MIN_SIZE = 16
DIM_ALPHA = 110
HINT = "Kéo chuột để chọn vùng dịch  ·  Esc để hủy"
TOO_SMALL = f"Vùng quá nhỏ (tối thiểu {MIN_SIZE}×{MIN_SIZE}), hãy kéo lại  ·  Esc để hủy"
ACCENT = QColor(0, 170, 255)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short
_VK_ESCAPE = 0x1B

Point = tuple[int, int]


def _esc_down() -> bool:
    return bool(_user32.GetAsyncKeyState(_VK_ESCAPE) & 0x8000)


def _map_for_screen(screen: QScreen, maps: list[ScreenMap]) -> ScreenMap:
    g = screen.geometry()
    for sm in maps:
        if sm.logical == g and sm.name == screen.name():
            return sm
    for sm in maps:
        if sm.logical == g:
            return sm
    dpr = screen.devicePixelRatio()
    return ScreenMap(screen.name(), Region(g.x(), g.y(), round(g.width() * dpr), round(g.height() * dpr)), g, dpr)


class _ShadeWindow(QWidget):
    """Dims one screen and draws the current selection; forwards mouse/keys to the selector."""

    def __init__(self, selector: "RegionSelector", screen: QScreen, smap: ScreenMap, activate: bool = True) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, not activate)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setWindowTitle("Chọn vùng dịch")
        self._selector = selector
        self.smap = smap
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self.exclusion = apply_to_widget(self) if selector.native else None

    def _local(self, region: Region) -> QRectF:
        x0, y0 = self.smap.to_logical(region.left, region.top)
        x1, y1 = self.smap.to_logical(region.right, region.bottom)
        g = self.geometry()
        return QRectF(x0 - g.x(), y0 - g.y(), x1 - x0, y1 - y0)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), QColor(0, 0, 0, DIM_ALPHA))
        sel = self._selector.selection()
        font = QFont("Segoe UI")
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.DemiBold)
        fm = QFontMetricsF(font)
        p.setFont(font)
        if sel is not None and sel.width > 0 and sel.height > 0:
            r = self._local(sel)
            p.fillRect(r, QColor(0, 0, 0, 1))  # alpha 1, not 0: the hole stays hit-testable
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            pen = QPen(ACCENT)
            pen.setCosmetic(True)
            pen.setWidth(1)
            p.setPen(pen)
            p.drawRect(r.adjusted(-0.5, -0.5, 0.5, 0.5))
            label = f"{sel.width} × {sel.height}"
            lw, lh = fm.horizontalAdvance(label) + 12, fm.height() + 6
            lx = min(max(0.0, r.right() - lw), self.width() - lw)
            ly = r.bottom() + 4 if r.bottom() + 4 + lh <= self.height() else max(0.0, r.top() - lh - 4)
            self._pill(p, QRectF(lx, ly, lw, lh), label, fm)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        msg = self._selector.message()
        if msg:
            mw, mh = fm.horizontalAdvance(msg) + 24, fm.height() + 12
            self._pill(p, QRectF((self.width() - mw) / 2, 24, mw, mh), msg, fm)
        p.end()

    @staticmethod
    def _pill(p: QPainter, r: QRectF, text: str, fm: QFontMetricsF) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 22, 28, 230))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QPointF(r.left() + (r.width() - fm.horizontalAdvance(text)) / 2,
                           r.top() + (r.height() - fm.height()) / 2 + fm.ascent()), text)

    def _point(self, e: QMouseEvent) -> Point:
        if self._selector.native:
            pos = cursor_pos()
            if pos is not None:
                return pos
        gp = e.globalPosition()
        x, y = self.smap.to_physical(gp.x(), gp.y())
        return round(x), round(y)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._selector._on_press(self._point(e))
        elif e.button() == Qt.MouseButton.RightButton:
            self._selector.cancel()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        self._selector._on_move(self._point(e))

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._selector._on_release(self._point(e))

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self._selector.cancel()
        else:
            super().keyPressEvent(e)


class RegionSelector(QObject):
    """Interactive region picker. `selected` carries an app.capture.Region (physical px)."""

    selected = Signal(object)  # Region
    cancelled = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.native = QGuiApplication.platformName() == "windows"
        self._windows: list[_ShadeWindow] = []
        self._press: Point | None = None
        self._current: Point | None = None
        self._message = HINT
        self._esc_armed = False
        self._esc_timer = QTimer(self)
        self._esc_timer.setInterval(40)
        self._esc_timer.timeout.connect(self._poll_escape)

    # --- public API ------------------------------------------------------------------------

    def start(self, activate: bool = True) -> None:
        """Show the dim windows (one per screen) and wait for a drag. No-op while active.

        activate=False shows them without taking focus (Esc then only works through the
        GetAsyncKeyState fallback); used by tests on the real desktop.
        """
        if self.is_active():
            if activate:
                self._activate()
            return
        self._press = self._current = None
        self._message = HINT
        maps = screen_maps()
        for screen in QGuiApplication.screens():
            self._windows.append(_ShadeWindow(self, screen, _map_for_screen(screen, maps), activate))
        for w in self._windows:
            w.show()
        if activate:
            self._activate()
        if self.native:
            self._esc_armed = not _esc_down()  # ignore an Esc that is still held from before
            self._esc_timer.start()

    def is_active(self) -> bool:
        return bool(self._windows)

    def cancel(self) -> None:
        if self.is_active():
            self._close()
            self.cancelled.emit()

    def selection(self) -> Region | None:
        """Current rubber-band rectangle (physical px), None before the first press."""
        if self._press is None or self._current is None:
            return None
        (x0, y0), (x1, y1) = self._press, self._current
        return Region(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))

    def message(self) -> str:
        return self._message

    def windows(self) -> list[QWidget]:
        return list(self._windows)

    # Test hooks: drive the selector with explicit physical points instead of real input.
    def simulate_press(self, x: int, y: int) -> None:
        self._on_press((int(x), int(y)))

    def simulate_move(self, x: int, y: int) -> None:
        self._on_move((int(x), int(y)))

    def simulate_release(self, x: int, y: int) -> None:
        self._on_release((int(x), int(y)))

    # --- internals -------------------------------------------------------------------------

    def _activate(self) -> None:
        if not self._windows:
            return
        target = self._windows[0]
        pos = cursor_pos() if self.native else None
        if pos is not None:
            for w in self._windows:
                p = w.smap.physical
                if p.left <= pos[0] < p.right and p.top <= pos[1] < p.bottom:
                    target = w
        target.raise_()
        target.activateWindow()
        target.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _update(self) -> None:
        for w in self._windows:
            w.update()

    def _on_press(self, pt: Point) -> None:
        if not self.is_active():
            return
        self._press = self._current = pt
        self._message = ""
        self._update()

    def _on_move(self, pt: Point) -> None:
        if self._press is not None:
            self._current = pt
            self._update()

    def _on_release(self, pt: Point) -> None:
        if self._press is None or not self.is_active():
            return
        self._current = pt
        region = self.selection()
        self._press = self._current = None
        if region is None or region.width < MIN_SIZE or region.height < MIN_SIZE:
            self._message = TOO_SMALL
            self._update()
            return
        log.info("region selected: %s", region)
        self._close()
        self.selected.emit(region)

    def _poll_escape(self) -> None:
        down = _esc_down()
        if not down:
            self._esc_armed = True
        elif self._esc_armed:
            self.cancel()

    def _close(self) -> None:
        self._esc_timer.stop()
        windows, self._windows = self._windows, []
        for w in windows:
            w.hide()
            w.deleteLater()
        self._press = self._current = None
