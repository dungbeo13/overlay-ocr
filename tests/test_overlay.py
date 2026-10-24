"""Tests for app.overlay (geometry, rendering, overlay window, region selector) and app.ui.

Run: python -m unittest tests.test_overlay -v

Qt runs on the offscreen platform in this process when no QApplication exists yet. Qt's offscreen
platform on Windows has no font database (every family resolves to a dummy font), so the
font/render tests and the tests that need a real HWND run in a child process with the windows
platform (WindowsPlatformSubprocessTests); rendering into QImages shows nothing on screen, the
native-window tests show a transparent, capture-excluded, non-activating window for a moment.
Set SCREEN_TRANSLATOR_SKIP_GUI_TESTS=1 to skip those child processes.
"""
from __future__ import annotations

import ctypes
import dataclasses
import os
import subprocess
import sys
import time
import unicodedata
import unittest
from ctypes import wintypes
from pathlib import Path

import numpy as np
from PySide6.QtCore import QByteArray, QCoreApplication, QEventLoop, QPointF, QRect, QRectF
from PySide6.QtGui import QColor, QFont, QFontInfo, QGuiApplication, QImage, QPainter
from PySide6.QtWidgets import QApplication

from app.capture.base import Region
from app.config import Config, Hotkeys
from app.overlay import geometry
from app.overlay.geometry import ScreenMap, logical_to_physical, map_for_region, physical_to_logical
from app.overlay.render import (
    ELLIPSIS,
    LayoutCache,
    _attempt,
    contrast_ratio,
    fit_text,
    font_metrics,
    growth_bounds,
    layout_block,
    paint_block,
    paint_status,
    pick_fg,
    prepare_text,
    readable_fg,
    status_text,
)
from app.pipeline.types import Block, OverlayState
from app.ui.game_guard import NAME_CACHE_SIZE, GameGuard, matches, process_image_name
from app.ui.hotkeys import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    WM_HOTKEY,
    HotkeyManager,
    format_combo,
    parse_combo,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FAMILY = "Segoe UI"
LONG_VI = ("Người gác ngọn hải đăng ở mũi đá phía bắc đã không còn được ai nhìn thấy kể từ trận bão khủng "
           "khiếp ba ngày trước; dân làng đồn rằng ông đã ra khơi tìm con thuyền mất tích, nhưng không ai "
           "dám chắc điều gì, và đêm nay ngọn đèn vẫn tắt ngấm như mọi đêm trước đó.")
DIACRITICS_VI = "ỄỂỀẾỆ Ữ Ỡ Ặ Ẵ Ẫ Ỗ Ộ ỵ Ỵ ự Ự gặp quỹ Đặng Ấn Ỷ"

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostThreadMessageW.restype = wintypes.BOOL
_kernel32 = ctypes.WinDLL("kernel32")
_GWL_EXSTYLE = -20
EX_FLAGS = {"TOPMOST": 0x8, "TRANSPARENT": 0x20, "TOOLWINDOW": 0x80, "LAYERED": 0x80000, "NOACTIVATE": 0x8000000}


# --- helpers ---------------------------------------------------------------------------------

def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication([sys.argv[0]])
    return app


def platform() -> str:
    qapp()
    return QGuiApplication.platformName()


def fonts_available() -> bool:
    qapp()
    return QFontInfo(QFont(FAMILY)).family() == FAMILY


def pump(ms: int) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
        time.sleep(0.003)


def rgba(img: QImage) -> np.ndarray:
    """H x W x 4 uint8 RGBA (straight alpha) copy of a QImage."""
    conv = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h, bpl = conv.width(), conv.height(), conv.bytesPerLine()
    arr = np.frombuffer(conv.constBits(), np.uint8, count=bpl * h).reshape(h, bpl)[:, :w * 4]
    return arr.reshape(h, w, 4).copy()


def transparent_image(w: int, h: int) -> QImage:
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    return img


def ink_bbox(arr: np.ndarray) -> tuple[int, int, int, int] | None:
    """(x0, y0, x1, y1) exclusive bounds of pixels with alpha > 0."""
    ys, xs = np.nonzero(arr[..., 3] > 0)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def make_block(**kw) -> Block:
    base = dict(id=1, box=(20, 20, 260, 30), lines=((20, 20, 260, 30),), source="Hello", bg=(0, 0, 0),
                fg=(255, 255, 255), line_height=30, translation="Xin chào")
    base.update(kw)
    return Block(**base)


def window_rect(hwnd: int) -> Region:
    rc = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(rc))
    return Region(rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)


def exstyle_flags(hwnd: int) -> set[str]:
    ex = _user32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE) & 0xFFFFFFFF
    return {k for k, v in EX_FLAGS.items() if ex & v}


def magenta_px(img_bgr: np.ndarray, tol: int = 40) -> int:
    b, g, r = img_bgr[..., 0].astype(int), img_bgr[..., 1].astype(int), img_bgr[..., 2].astype(int)
    return int(np.count_nonzero((b >= 255 - tol) & (g <= tol) & (r >= 255 - tol)))


# --- geometry --------------------------------------------------------------------------------

class GeometryTests(unittest.TestCase):
    ONE = [ScreenMap("A", Region(0, 0, 2560, 1440), QRect(0, 0, 2560, 1440), 1.0)]
    FAKE_15 = [ScreenMap("B", Region(0, 0, 2560, 1440), QRect(0, 0, 1707, 960), 1.5)]
    MIXED = [  # primary 100 % at the origin, secondary 150 % to its right (Qt keeps native origins)
        ScreenMap("P", Region(0, 0, 1920, 1080), QRect(0, 0, 1920, 1080), 1.0),
        ScreenMap("S", Region(1920, 0, 2880, 1620), QRect(1920, 0, 1920, 1080), 1.5),
    ]

    def test_scale_1_is_identity(self):
        r = Region(100, 200, 300, 400)
        self.assertEqual(physical_to_logical(r, self.ONE), QRect(100, 200, 300, 400))
        self.assertEqual(logical_to_physical(QRect(100, 200, 300, 400), self.ONE), r)

    def test_fake_scale_15_exact(self):
        r = Region(300, 150, 600, 300)
        q = physical_to_logical(r, self.FAKE_15)
        self.assertEqual(q, QRect(200, 100, 400, 200))
        self.assertEqual(logical_to_physical(q, self.FAKE_15), r)

    def test_fake_scale_15_rounding_covers_region(self):
        r = Region(301, 151, 401, 201)
        q = physical_to_logical(r, self.FAKE_15)
        self.assertEqual(q, QRect(200, 100, 268, 135))  # left/top floored, right/bottom ceiled
        back = logical_to_physical(q, self.FAKE_15)
        self.assertEqual(back, Region(300, 150, 402, 203))  # Qt rounds position and size separately
        self.assertLessEqual(back.left, r.left)
        self.assertLessEqual(back.top, r.top)
        self.assertGreaterEqual(back.right, r.right)
        self.assertGreaterEqual(back.bottom, r.bottom)

    def test_mixed_dpi_secondary_screen(self):
        r = Region(2220, 150, 600, 300)
        self.assertEqual(map_for_region(r, self.MIXED).name, "S")
        q = physical_to_logical(r, self.MIXED)
        self.assertEqual(q, QRect(2120, 100, 400, 200))  # origin + (physical - origin) / dpr
        self.assertEqual(logical_to_physical(q, self.MIXED), r)

    def test_region_spanning_screens_uses_largest_overlap(self):
        r = Region(1800, 100, 400, 100)  # 120 px on P, 280 px on S
        self.assertEqual(map_for_region(r, self.MIXED).name, "S")
        r = Region(1500, 100, 500, 100)  # 420 px on P
        self.assertEqual(map_for_region(r, self.MIXED).name, "P")

    def test_offscreen_region_uses_nearest_screen(self):
        self.assertEqual(map_for_region(Region(-500, 100, 50, 50), self.MIXED).name, "P")
        self.assertEqual(map_for_region(Region(9000, 100, 50, 50), self.MIXED).name, "S")

    def test_qround_half_away_from_zero(self):
        self.assertEqual([geometry._qround(v) for v in (0.5, 1.5, 2.4999, -0.5, -1.5)], [1, 2, 2, -1, -2])

    def test_screen_maps_with_app(self):
        qapp()
        maps = geometry.screen_maps()
        self.assertGreaterEqual(len(maps), 1)
        for sm in maps:
            self.assertGreater(sm.dpr, 0)
            self.assertGreater(sm.physical.width, 0)

    def test_cursor_pos(self):
        pos = geometry.cursor_pos()
        self.assertTrue(pos is None or (isinstance(pos[0], int) and isinstance(pos[1], int)))


# --- hotkeys ---------------------------------------------------------------------------------

class HotkeyParseTests(unittest.TestCase):
    def test_valid(self):
        cases = {
            "ctrl+alt+r": (MOD_CONTROL | MOD_ALT, 0x52),
            "Ctrl + Shift + F12": (MOD_CONTROL | MOD_SHIFT, 0x7B),
            "win+space": (MOD_WIN, 0x20),
            "alt+1": (MOD_ALT, 0x31),
            "ctrl++": (MOD_CONTROL, 0xBB),
            "ctrl+numpad5": (MOD_CONTROL, 0x65),
            "f24": (0, 0x87),
            "control+alt+pageup": (MOD_CONTROL | MOD_ALT, 0x21),
            "shift+alt+ctrl+q": (MOD_CONTROL | MOD_ALT | MOD_SHIFT, 0x51),
            "ctrl+-": (MOD_CONTROL, 0xBD),
        }
        for combo, expected in cases.items():
            with self.subTest(combo=combo):
                self.assertEqual(parse_combo(combo), expected)
        self.assertEqual(HotkeyManager.parse_combo("ctrl+alt+t"), (MOD_CONTROL | MOD_ALT, 0x54))

    def test_invalid(self):
        for combo in ("", "   ", "ctrl+alt", "ctrl+r+t", "ctrl+ctrl+r", "ctrl+foo", "ctrl+f25", "ctrl+", "+r",
                      "ctrl++alt+r", None, 5):
            with self.subTest(combo=combo):
                with self.assertRaises(ValueError):
                    parse_combo(combo)

    def test_format(self):
        self.assertEqual(format_combo("ctrl+alt+r"), "Ctrl+Alt+R")
        self.assertEqual(format_combo("shift+win+f5"), "Shift+Win+F5")
        self.assertEqual(format_combo("ctrl+numpad0"), "Ctrl+Num0")
        self.assertEqual(format_combo("bogus+x"), "bogus+x")


class HotkeyManagerTests(unittest.TestCase):
    COMBO = "ctrl+alt+shift+f23"  # nothing uses this; registration is per-thread and brief

    def setUp(self):
        qapp()
        self.mgr = HotkeyManager()
        self.got: list[str] = []
        self.mgr.triggered.connect(self.got.append)

    def tearDown(self):
        self.mgr.close()

    def _id(self, name: str) -> int:
        return self.mgr._names[name][0]

    def test_register_and_wm_hotkey_posted_to_thread(self):
        self.assertTrue(self.mgr.register("select_region", self.COMBO), self.mgr.last_errors())
        self.assertEqual(self.mgr.registered(), {"select_region": self.COMBO})
        tid = _kernel32.GetCurrentThreadId()
        self.assertTrue(_user32.PostThreadMessageW(tid, WM_HOTKEY, self._id("select_region"), 0))
        pump(150)
        self.assertEqual(self.got, ["select_region"])

    def test_filter_called_directly(self):
        self.assertTrue(self.mgr.register("quit", self.COMBO))
        msg = wintypes.MSG()
        msg.message, msg.wParam, msg.time = WM_HOTKEY, self._id("quit"), 12345
        handled = self.mgr.handle_native_message(QByteArray(b"windows_generic_MSG"), ctypes.addressof(msg))
        self.assertEqual(handled, (True, 0))
        # the same MSG arriving through the other dispatch path is not acted on twice
        again = self.mgr.handle_native_message(QByteArray(b"windows_dispatcher_MSG"), ctypes.addressof(msg))
        self.assertEqual(again, (True, 0))
        other = wintypes.MSG()
        other.message = 0x0200  # WM_MOUSEMOVE
        self.assertEqual(self.mgr.handle_native_message(QByteArray(b"windows_generic_MSG"), ctypes.addressof(other)),
                         (False, 0))
        unknown = wintypes.MSG()
        unknown.message, unknown.wParam = WM_HOTKEY, 9999
        self.assertEqual(self.mgr.handle_native_message(QByteArray(b"windows_generic_MSG"), ctypes.addressof(unknown)),
                         (False, 0))
        self.assertEqual(self.mgr.handle_native_message(QByteArray(b"x"), 0), (False, 0))
        pump(100)
        self.assertEqual(self.got, ["quit"])

    def test_conflict_and_bad_combo_are_reported_not_raised(self):
        self.assertTrue(self.mgr.register("a", self.COMBO))
        self.assertFalse(self.mgr.register("b", self.COMBO))  # already registered (by us)
        self.assertIn("b", self.mgr.last_errors())
        self.assertIn("Ctrl+Alt+Shift+F23", self.mgr.last_errors()["b"])
        self.assertFalse(self.mgr.register("c", "ctrl+nonsense"))
        self.assertIn("không hợp lệ", self.mgr.last_errors()["c"])
        self.assertEqual(set(self.mgr.registered()), {"a"})

    def test_unregister_all_and_reregister(self):
        self.assertTrue(self.mgr.register("a", self.COMBO))
        self.mgr.unregister_all()
        self.assertEqual(self.mgr.registered(), {})
        self.assertTrue(self.mgr.register("a2", self.COMBO))  # free again
        self.assertTrue(self.mgr.register("a2", self.COMBO))  # re-register under the same name
        self.assertEqual(self.mgr.registered(), {"a2": self.COMBO})


# --- game guard ------------------------------------------------------------------------------

class GameGuardTests(unittest.TestCase):
    def setUp(self):
        qapp()
        self.cfg = Config(guard_mode="pause", blocked_processes=["Game.exe", "other"])
        self.fg: tuple[int, str | None] | None = None
        self.guard = GameGuard(self.cfg, reader=lambda: self.fg, use_event_hook=False)
        self.changes: list[tuple[bool, str]] = []
        self.warns: list[str] = []
        self.guard.changed.connect(lambda g, e: self.changes.append((g, e)))
        self.guard.warn.connect(self.warns.append)

    def test_matches(self):
        self.assertTrue(matches("C:\\Games\\GAME.EXE", ["game.exe"]))
        self.assertTrue(matches("other.exe", ["other"]))  # entry without .exe matches the stem
        self.assertTrue(matches("cs2.exe", ["C:/Steam/cs2.exe"]))
        self.assertFalse(matches("game.exe.bak", ["game.exe"]))
        self.assertFalse(matches("mygame.exe", ["game.exe"]))
        self.assertFalse(matches(None, ["game.exe"]))
        self.assertFalse(matches("game.exe", []))
        self.assertTrue(GameGuard.matches("VALORANT-Win64-Shipping.exe", Config().blocked_processes))

    def test_pause_mode_transitions(self):
        g = self.guard
        self.assertEqual(g.evaluate("notepad.exe", 100), (False, ""))
        self.assertEqual(g.evaluate("GAME.exe", 200), (True, "GAME.exe"))
        self.assertEqual(g.evaluate("GAME.exe", 200), (True, "GAME.exe"))
        self.assertEqual(g.evaluate(None, 300), (True, "GAME.exe"))  # unknown name keeps the state
        self.assertEqual(g.evaluate("python.exe", os.getpid()), (True, "GAME.exe"))  # our own process
        self.assertEqual(g.evaluate("explorer.exe", 400), (False, ""))
        self.assertEqual(self.changes, [(True, "GAME.exe"), (False, "")])
        self.assertEqual(g.current(), (False, ""))

    def test_warn_mode_once_per_exe(self):
        self.cfg.guard_mode = "warn"
        for _ in range(3):
            self.assertEqual(self.guard.evaluate("game.exe", 200), (False, ""))
        self.guard.evaluate("other.exe", 201)
        self.assertEqual(self.warns, ["game.exe", "other.exe"])
        self.assertEqual(self.changes, [])

    def test_off_mode_and_switching_modes(self):
        self.cfg.guard_mode = "off"
        self.assertEqual(self.guard.evaluate("Game.exe", 200), (False, ""))
        self.cfg.guard_mode = "pause"
        self.assertEqual(self.guard.evaluate("Game.exe", 200), (True, "Game.exe"))
        # the user opens our tray menu (our process is in front) and turns the guard off
        self.cfg.guard_mode = "off"
        self.assertEqual(self.guard.evaluate(None, os.getpid()), (False, ""))
        # ... back to pause: the game is still the last foreign foreground window
        self.cfg.guard_mode = "pause"
        self.assertEqual(self.guard.evaluate(None, os.getpid()), (True, "Game.exe"))
        self.cfg.guard_mode = "warn"
        self.assertEqual(self.guard.evaluate(None, os.getpid()), (False, ""))
        self.assertEqual(self.warns, ["Game.exe"])
        self.assertEqual(self.changes, [(True, "Game.exe"), (False, ""), (True, "Game.exe"), (False, "")])

    def test_unknown_mode_behaves_like_pause(self):
        self.cfg.guard_mode = "bogus"
        self.assertEqual(self.guard.evaluate("game.exe", 5), (True, "game.exe"))

    def test_check_uses_reader_and_survives_errors(self):
        self.fg = (321, "Game.exe")
        self.assertEqual(self.guard.check(), (True, "Game.exe"))
        self.fg = None  # no foreground window: keep the state
        self.assertEqual(self.guard.check(), (True, "Game.exe"))

        def boom():
            raise OSError("access denied")

        self.guard._reader = boom
        self.assertEqual(self.guard.check(), (True, "Game.exe"))

    def test_process_names_are_cached_across_alt_tabs(self):
        opened: list[int] = []

        def lookup(pid: int) -> str:
            opened.append(pid)  # stands for OpenProcess on the game
            return {1: "cs2.exe", 2: "chrome.exe"}[pid]

        for _ in range(10):  # alt-tab between the game and a browser
            self.assertEqual(self.guard._cached_name(100, 1, lookup), "cs2.exe")
            self.assertEqual(self.guard._cached_name(200, 2, lookup), "chrome.exe")
        self.assertEqual(opened, [1, 2])  # each process opened once, not on every return to it
        for i in range(2 * NAME_CACHE_SIZE):
            self.guard._cached_name(1000 + i, 1000 + i, lambda pid: "x.exe")
        self.assertLessEqual(len(self.guard._name_cache), NAME_CACHE_SIZE)  # bounded
        failed: list[int] = []
        for _ in range(2):  # an unknown name is not cached: asked again next time
            self.assertIsNone(self.guard._cached_name(5, 5, lambda pid: failed.append(pid)))
        self.assertEqual(failed, [5, 5])

    def test_real_foreground_lookup_and_timer(self):
        name = GameGuard.foreground_process_name()
        self.assertTrue(name is None or name.lower().endswith(".exe") or "." not in name, name)
        self.assertEqual(process_image_name(os.getpid()).lower(), Path(sys.executable).name.lower())
        self.assertIsNone(process_image_name(0))
        self.assertIsInstance(process_image_name(4) or "", str)  # System: OpenProcess refused -> snapshot
        guard = GameGuard(Config(guard_mode="pause", blocked_processes=[]), interval_ms=50)
        guard.start()
        self.assertTrue(guard.is_running())
        pump(120)
        self.assertEqual(guard.current(), (False, ""))
        guard.stop()
        self.assertFalse(guard.is_running())


# --- overlay window logic (offscreen OK) -----------------------------------------------------

class OverlayWindowLogicTests(unittest.TestCase):
    def setUp(self):
        qapp()
        from app.overlay.overlay_window import OverlayWindow
        self.region = Region(40, 40, 320, 200)
        self.ov = OverlayWindow(Config(show_border=False))

    def tearDown(self):
        self.ov.shutdown()
        self.ov.deleteLater()
        pump(20)

    def state(self, seq: int, **kw) -> OverlayState:
        kw.setdefault("region", self.region)
        return OverlayState(seq=seq, **kw)

    def test_ignores_older_and_equal_seq(self):
        self.assertTrue(self.ov.set_state(self.state(5, message="five")))
        self.assertFalse(self.ov.set_state(self.state(4, message="four")))
        self.assertFalse(self.ov.set_state(self.state(5, message="five again")))
        self.assertEqual(self.ov.state().message, "five")
        self.assertTrue(self.ov.set_state(self.state(6, message="six")))
        self.assertEqual(self.ov.state().seq, 6)

    def test_follows_state_region_and_guard(self):
        self.ov.set_state(self.state(1))
        self.assertTrue(self.ov.isVisible())
        self.assertEqual(self.ov.region(), self.region)
        self.ov.set_state(self.state(2, status="guarded"))
        self.assertFalse(self.ov.isVisible())
        self.ov.set_state(self.state(3, status="running"))
        self.assertTrue(self.ov.isVisible())
        self.ov.set_guarded(True)
        self.assertFalse(self.ov.isVisible())
        self.ov.set_guarded(False)
        self.ov.set_suspended(True)
        self.assertFalse(self.ov.isVisible())
        self.ov.set_suspended(False)
        self.assertTrue(self.ov.isVisible())
        self.ov.set_state(self.state(4, region=None))
        self.assertFalse(self.ov.isVisible())
        self.assertIsNone(self.ov.region())

    def test_repaints_are_coalesced(self):
        self.ov.set_state(self.state(1))
        pump(100)
        before = self.ov.paint_count
        t0 = time.monotonic()
        for seq in range(2, 202):  # 200 states as fast as possible
            self.ov.set_state(self.state(seq, pending=seq % 3))
        pump(300)
        elapsed = time.monotonic() - t0
        paints = self.ov.paint_count - before
        self.assertGreaterEqual(paints, 1)
        self.assertLessEqual(paints, int(elapsed * 30) + 3, f"{paints} paints in {elapsed:.2f}s")

    def test_text_visible_and_snapshot(self):
        self.assertTrue(self.ov.text_visible())
        self.ov.set_text_visible(False)
        self.assertFalse(self.ov.text_visible())
        self.ov.set_state(self.state(1))
        img = self.ov.snapshot()
        self.assertFalse(img.isNull())
        dpr = self.ov.devicePixelRatioF()
        self.assertAlmostEqual(img.width(), self.ov.width() * dpr, delta=1)
        if QGuiApplication.platformName() != "windows":
            self.assertFalse(self.ov.exclusion_ok())  # not the windows platform: nothing applied


# --- region selector logic (offscreen OK) ----------------------------------------------------

class RegionSelectorLogicTests(unittest.TestCase):
    def setUp(self):
        if platform() == "windows":
            self.skipTest("start() would show and activate full-screen windows; covered offscreen")
        from app.overlay.region_selector import MIN_SIZE, TOO_SMALL, RegionSelector
        self.MIN_SIZE, self.TOO_SMALL = MIN_SIZE, TOO_SMALL
        self.sel = RegionSelector()
        self.picked: list[Region] = []
        self.cancels: list[bool] = []
        self.sel.selected.connect(self.picked.append)
        self.sel.cancelled.connect(lambda: self.cancels.append(True))

    def tearDown(self):
        self.sel.cancel()
        pump(20)

    def test_drag_selects_physical_region(self):
        self.sel.start()
        self.assertTrue(self.sel.is_active())
        self.assertEqual(len(self.sel.windows()), len(QGuiApplication.screens()))
        self.sel.simulate_press(500, 400)
        self.sel.simulate_move(300, 350)
        self.assertEqual(self.sel.selection(), Region(300, 350, 200, 50))
        self.sel.simulate_release(100, 300)  # dragged up-left: normalised
        self.assertEqual(self.picked, [Region(100, 300, 400, 100)])
        self.assertFalse(self.sel.is_active())
        self.assertEqual(self.cancels, [])

    def test_too_small_keeps_selecting(self):
        self.sel.start()
        self.sel.simulate_press(10, 10)
        self.sel.simulate_release(10 + self.MIN_SIZE - 1, 200)
        self.assertEqual(self.picked, [])
        self.assertTrue(self.sel.is_active())
        self.assertEqual(self.sel.message(), self.TOO_SMALL)
        self.sel.simulate_press(10, 10)
        self.sel.simulate_release(10 + self.MIN_SIZE, 10 + self.MIN_SIZE)
        self.assertEqual(self.picked, [Region(10, 10, self.MIN_SIZE, self.MIN_SIZE)])

    def test_cancel_and_events_when_inactive(self):
        self.sel.simulate_press(1, 1)  # not active: ignored
        self.sel.simulate_release(100, 100)
        self.assertEqual(self.picked, [])
        self.sel.start()
        self.sel.start()  # second start while active is a no-op
        self.assertEqual(len(self.sel.windows()), len(QGuiApplication.screens()))
        self.sel.cancel()
        self.assertEqual(self.cancels, [True])
        self.assertFalse(self.sel.is_active())
        pump(20)


# --- tray ------------------------------------------------------------------------------------

class TrayTests(unittest.TestCase):
    def setUp(self):
        qapp()
        from app.ui.tray import TrayController, make_icon
        self.make_icon = make_icon
        self.tray = TrayController(Config())

    def test_labels_and_hotkeys(self):
        texts = "\n".join(self.tray.action_texts())
        for label in ("Chọn vùng dịch\tCtrl+Alt+R", "Tạm dừng\tCtrl+Alt+T", "Ẩn bản dịch\tCtrl+Alt+H",
                      "Ngôn ngữ nguồn\tCtrl+Alt+L", "Tiếng Nhật", "Tiếng Anh", "Chế độ anti-cheat", "Chỉ cảnh báo",
                      "Tắt", "Mở thư mục dữ liệu", "Thoát\tCtrl+Alt+Q"):
            self.assertIn(label, texts)

    def test_update_status(self):
        st = OverlayState(seq=1, region=Region(0, 0, 10, 10), status="running", pending=2, src_lang="en")
        self.tray.update_status(st, paused_by_user=False, text_visible=True)
        self.assertIn("EN→VI", self.tray.tooltip())
        self.assertIn("Đang dịch 2…", self.tray.tooltip())
        self.assertTrue(self.tray._lang_actions["en"].isChecked())
        self.tray.update_status(st, paused_by_user=True, text_visible=False)
        texts = self.tray.action_texts()
        self.assertTrue(any(t.startswith("Tiếp tục") for t in texts))
        self.assertTrue(any(t.startswith("Hiện bản dịch") for t in texts))
        self.assertIn("Tạm dừng", self.tray.tooltip())
        guarded = OverlayState(seq=2, region=Region(0, 0, 10, 10), status="guarded")
        self.tray.update_status(guarded, paused_by_user=False, text_visible=True)
        self.assertIn("anti-cheat", self.tray.tooltip())
        self.tray.update_status(OverlayState(seq=3, region=None), paused_by_user=False, text_visible=True)
        self.assertIn("Chưa chọn vùng", self.tray.tooltip())

    def test_no_region_hint_names_only_a_working_hotkey(self):
        idle = OverlayState(seq=1, region=None, status="idle", message="Chưa chọn vùng — nhấn Ctrl+Alt+R")
        self.tray.update_status(idle, paused_by_user=False, text_visible=True)
        self.assertIn("Ctrl+Alt+R", self.tray.tooltip())
        self.tray.set_hotkeys(Hotkeys("", "", "", "", ""))  # --no-hotkeys, or the registration failed
        self.tray.update_status(idle, paused_by_user=False, text_visible=True)
        self.assertNotIn("Ctrl+Alt+R", self.tray.tooltip())
        self.assertIn("menu Chọn vùng", self.tray.tooltip())
        self.assertNotIn("Ctrl+Alt+R", self.tray.action_texts()[0])

    def test_menus_are_capture_excluded(self):
        menus = [self.tray.menu(), self.tray._lang_menu, self.tray._guard_menu]
        self.assertEqual([e._widget for e in self.tray._exclusions], menus)
        if QGuiApplication.platformName() == "windows":
            from app.overlay.affinity import get_display_affinity
            for m in menus:
                self.assertEqual(get_display_affinity(int(m.winId())), 0x11)

    def test_signals(self):
        got = []
        self.tray.set_lang.connect(lambda c: got.append(("lang", c)))
        self.tray.set_guard_mode.connect(lambda m: got.append(("guard", m)))
        self.tray.quit.connect(lambda: got.append(("quit",)))
        self.tray.select_region.connect(lambda: got.append(("select",)))
        self.tray._lang_actions["en"].trigger()
        self.tray._guard_actions["warn"].trigger()
        self.tray._quit_action.trigger()
        self.tray._select_action.trigger()
        self.tray.set_lang_checked("ja")  # programmatic: no signal
        self.tray.set_guard_checked("off")
        self.assertEqual(got, [("lang", "en"), ("guard", "warn"), ("quit",), ("select",)])
        self.assertTrue(self.tray._guard_actions["off"].isChecked())

    def test_icons(self):
        for status in ("running", "paused", "guarded", "error", "idle"):
            icon = self.make_icon(status)
            self.assertFalse(icon.isNull())
            self.assertFalse(icon.pixmap(32, 32).isNull())


# --- rendering (needs real fonts: windows platform) -------------------------------------------

class ContrastTests(unittest.TestCase):
    def test_contrast_helpers(self):
        self.assertAlmostEqual(contrast_ratio((255, 255, 255), (0, 0, 0)), 21.0, places=3)
        self.assertAlmostEqual(contrast_ratio((10, 20, 30), (10, 20, 30)), 1.0)
        self.assertEqual(pick_fg((255, 255, 255)), (0, 0, 0))
        self.assertEqual(pick_fg((0, 0, 0)), (255, 255, 255))
        for bg in [(0, 0, 255), (0, 255, 0), (255, 0, 0), (128, 128, 128), (60, 200, 250), (245, 63, 169)]:
            self.assertGreaterEqual(contrast_ratio(pick_fg(bg), bg), 4.5, bg)
        self.assertEqual(readable_fg((20, 20, 20), (0, 0, 0)), (255, 255, 255))
        self.assertEqual(readable_fg((0, 215, 255), (40, 30, 30)), (0, 215, 255))  # yellow on dark kept

    def test_status_text(self):
        def st(**kw):
            return OverlayState(seq=1, region=None, **kw)
        self.assertEqual(status_text(st(status="running")), "JA→VI")
        self.assertEqual(status_text(st(status="running", src_lang="en")), "EN→VI")
        self.assertEqual(status_text(st(status="running", pending=2)), "Đang dịch 2…")
        self.assertEqual(status_text(st(status="paused")), "Tạm dừng")
        self.assertEqual(status_text(st(status="guarded")), "Đã tạm dừng: game có anti-cheat")
        self.assertEqual(status_text(st(status="error", message="Lỗi dịch: 429")), "Lỗi dịch: 429")
        self.assertEqual(status_text(st(status="running"), error="Lỗi X"), "Lỗi X")
        self.assertIn("đã ẩn bản dịch", status_text(st(status="running"), text_visible=False))

    def test_prepare_text_nfc(self):
        decomposed = unicodedata.normalize("NFD", "Tiếng Việt")
        self.assertNotEqual(decomposed, "Tiếng Việt")
        self.assertEqual(prepare_text("  " + decomposed + "\n"), "Tiếng Việt")


class RenderTests(unittest.TestCase):
    def setUp(self):
        if not fonts_available():
            self.skipTest("no real fonts on this Qt platform (offscreen): run via WindowsPlatformSubprocessTests")

    def assertLinesInside(self, fit, rect: QRectF):
        m = font_metrics(FAMILY, fit.font_px)
        for ln in fit.lines:
            self.assertGreaterEqual(ln.x - m.hpad, rect.left() - 0.01, ln)
            self.assertLessEqual(ln.x + ln.width, rect.right() + 0.01, ln)
            self.assertGreaterEqual(ln.baseline - m.ascent, rect.top() - 0.01, ln)
            self.assertLessEqual(ln.baseline + m.descent, rect.bottom() + 0.01, ln)

    def test_fit_picks_largest_size(self):
        for text, (w, h) in [("Xin chào thế giới", (400, 40)), ("Nhiệm vụ: Tìm chìa khóa", (150, 60)),
                             (LONG_VI, (600, 200)), ("Đ", (30, 30))]:
            with self.subTest(text=text[:20], box=(w, h)):
                fit = fit_text(text, (10, 10, w, h), FAMILY, 8, 48)
                self.assertFalse(fit.grown or fit.elided)
                self.assertTrue(_attempt(prepare_text(text), FAMILY, fit.font_px, 400, w, False).fits(h))
                if fit.font_px < 48:
                    self.assertFalse(_attempt(prepare_text(text), FAMILY, fit.font_px + 1, 400, w, False).fits(h))
                self.assertLinesInside(fit, fit.rect)
                self.assertEqual(" ".join(fit.text.split()), " ".join(prepare_text(text).split()))

    def test_max_size_cap_and_alignment(self):
        fit = fit_text("Ngắn", (0, 0, 500, 100), FAMILY, 10, 20, align="center", valign="center")
        self.assertEqual(fit.font_px, 20)
        ln = fit.lines[0]
        self.assertAlmostEqual(ln.x + ln.width / 2, 250, delta=1.5)
        m = font_metrics(FAMILY, 20)
        self.assertAlmostEqual(ln.baseline - m.ascent + m.line_height / 2, 50, delta=1.0)

    def test_grows_down_within_bounds(self):
        fit = fit_text(LONG_VI, (10, 10, 200, 30), FAMILY, 12, 20, bounds=(0, 0, 400, 400))
        self.assertTrue(fit.grown)
        self.assertFalse(fit.elided)
        self.assertEqual(fit.font_px, 12)
        self.assertEqual((fit.rect.left(), fit.rect.top(), fit.rect.width()), (10, 10, 200))
        self.assertLessEqual(fit.rect.bottom(), 400)
        self.assertGreater(fit.rect.height(), 30)
        self.assertLinesInside(fit, fit.rect)
        self.assertEqual(" ".join(fit.text.split()), LONG_VI)

    def test_grows_right_when_down_is_not_enough(self):
        fit = fit_text(LONG_VI, (10, 250, 100, 30), FAMILY, 12, 20, bounds=(0, 0, 900, 300))
        self.assertTrue(fit.grown)
        self.assertFalse(fit.elided)
        self.assertGreater(fit.rect.width(), 100)
        self.assertEqual(fit.rect.left(), 10)
        self.assertLessEqual(fit.rect.right(), 900)
        self.assertLessEqual(fit.rect.bottom(), 300)
        self.assertLinesInside(fit, fit.rect)
        narrower = _attempt(prepare_text(LONG_VI), FAMILY, 12, 400, fit.rect.width() - 2, True)
        self.assertGreater(narrower.height, 300 - 250)  # it grew only as much as needed

    def test_centred_text_grows_to_both_sides(self):
        fit = fit_text(LONG_VI, (400, 50, 200, 30), FAMILY, 12, 20, bounds=(0, 0, 1000, 100), align="center")
        self.assertTrue(fit.grown)
        self.assertAlmostEqual(fit.rect.center().x(), 500, delta=1.0)

    def test_elides_when_nothing_fits(self):
        fit = fit_text(LONG_VI, (10, 10, 100, 20), FAMILY, 12, 20, bounds=(0, 0, 160, 60))
        self.assertTrue(fit.elided)
        self.assertTrue(fit.lines[-1].text.endswith(ELLIPSIS))
        self.assertLessEqual(fit.rect.right(), 160)
        self.assertLessEqual(fit.rect.bottom(), 60)
        self.assertLinesInside(fit, fit.rect)
        # without bounds the box cannot grow: elided inside the original rect
        fit2 = fit_text(LONG_VI, (10, 10, 300, 40), FAMILY, 12, 20)
        self.assertTrue(fit2.elided)
        self.assertEqual(fit2.rect, QRectF(10, 10, 300, 40))
        self.assertLinesInside(fit2, fit2.rect)

    def test_long_word_breaks_only_at_min_size(self):
        word = "Siêuđặcbiệtdàikhôngcókhoảngtrắngnàocả"
        fit = fit_text(word, (0, 0, 120, 200), FAMILY, 12, 30)
        self.assertEqual(fit.font_px, 12)
        self.assertGreater(len(fit.lines), 1)
        self.assertLinesInside(fit, fit.rect)

    def test_vietnamese_diacritics_not_clipped(self):
        for px, (w, h), valign in [(12, (600, 18), "top"), (20, (300, 120), "top"), (33, (700, 46), "center"),
                                   (16, (150, 300), "center")]:
            with self.subTest(px=px):
                fit = fit_text(DIACRITICS_VI, (6, 6, w, h), FAMILY, 6, px, valign=valign)
                img = transparent_image(w + 12, h + 12)
                p = QPainter(img)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                p.setFont(fit.font)
                p.setPen(QColor(0, 0, 0))
                for ln in fit.lines:
                    p.drawText(QPointF(ln.x, ln.baseline), ln.text)
                p.end()
                box = ink_bbox(rgba(img))
                self.assertIsNotNone(box)
                r = fit.rect
                self.assertGreaterEqual(box[0], r.left() - 0.5, (box, r))
                self.assertGreaterEqual(box[1], r.top() - 0.5, (box, r))
                self.assertLessEqual(box[2], r.right() + 0.5, (box, r))
                self.assertLessEqual(box[3], r.bottom() + 0.5, (box, r))

    def _render(self, block: Block, config: Config, size=(300, 80)) -> np.ndarray:
        img = transparent_image(*size)
        p = QPainter(img)
        paint_block(p, block, config, size)
        p.end()
        return rgba(img)

    def test_paint_block_uses_bg_and_fg(self):
        cfg = Config(box_alpha=255)
        block = make_block(bg=(200, 0, 0), fg=(255, 255, 255), translation="Xin chào")  # BGR blue bg
        arr = self._render(block, cfg)
        self.assertEqual(tuple(arr[35, 270]), (0, 0, 200, 255))  # inside the line box, right of the text
        self.assertEqual(tuple(arr[5, 5]), (0, 0, 0, 0))  # outside every box: untouched
        text = arr[18:52, 20:120]
        self.assertGreater(int(((text[..., 0] > 230) & (text[..., 1] > 230) & (text[..., 2] > 230)).sum()), 20)

    def test_source_lines_get_a_second_pass(self):
        arr = self._render(make_block(translation="X"), Config(box_alpha=235))
        expected = round(255 * (1 - (1 - 235 / 255) ** 2))
        self.assertGreaterEqual(int(arr[35, 270, 3]), expected - 1)  # over the source line
        self.assertLessEqual(int(arr[35, 270, 3]), 255)

    def test_unreadable_fg_is_replaced(self):
        arr = self._render(make_block(bg=(0, 0, 0), fg=(15, 15, 15), translation="Xin chào"), Config(box_alpha=255))
        text = arr[18:52, 20:120]
        self.assertGreater(int((text[..., :3].min(axis=2) > 230).sum()), 20)  # drawn white instead

    def test_stale_translation_is_dimmed(self):
        cfg = Config(box_alpha=255)
        fresh = self._render(make_block(bg=(0, 0, 0), translation="Xin chào"), cfg)
        stale = self._render(make_block(bg=(0, 0, 0), translation=None, stale_translation="Xin chào"), cfg)
        f_max = int(fresh[18:52, 20:120, :3].max())
        s_max = int(stale[18:52, 20:120, :3].max())
        self.assertGreater(f_max, 240)
        self.assertGreater(s_max, 0.35 * f_max)
        self.assertLess(s_max, 0.65 * f_max)
        self.assertEqual(tuple(stale[35, 270]), (0, 0, 0, 255))  # bg not dimmed: source stays hidden

    def test_nothing_drawn_without_translation(self):
        arr = self._render(make_block(translation=None, stale_translation=None, error="timeout"), Config())
        self.assertEqual(int(arr[..., 3].max()), 0)

    def test_vertical_block_is_widened_and_centred(self):
        cfg = Config()
        block = make_block(box=(400, 50, 90, 300), lines=((454, 50, 36, 300), (400, 50, 36, 280)), vertical=True,
                           line_height=0, translation="Vẫn chưa kết thúc đâu. Tin được không?")
        lay = layout_block(block, cfg, (960, 720))
        self.assertGreater(lay.fit.rect.width(), 90)
        self.assertAlmostEqual(lay.fit.rect.center().x(), 445, delta=1.0)
        self.assertLessEqual(lay.fit.font_px, int(0.8 * 36))
        edge = layout_block(dataclasses.replace(block, box=(0, 50, 90, 300),
                                                lines=((54, 50, 36, 300), (0, 50, 36, 280))), cfg, (960, 720))
        self.assertGreaterEqual(edge.fit.rect.left(), 0)  # clamped to the region

    def test_single_line_gets_full_line_height(self):
        block = make_block(box=(20, 40, 400, 19), lines=((20, 40, 400, 19),), line_height=19, translation="Elara")
        lay = layout_block(block, Config(), (600, 200))
        self.assertEqual(lay.fit.font_px, int(0.9 * 19))
        self.assertAlmostEqual(lay.fit.rect.center().y(), 49.5, delta=1.0)

    def test_layout_cache(self):
        cache, cfg = LayoutCache(), Config()
        b = make_block()
        first = cache.get(b, cfg, (300, 80))
        self.assertIs(cache.get(b, cfg, (300, 80)), first)
        changed = cache.get(make_block(translation="Khác"), cfg, (300, 80))
        self.assertIsNot(changed, first)
        cache.prune([])
        self.assertEqual(len(cache), 0)

    def test_status_pill_avoids_translations(self):
        st = OverlayState(seq=1, region=None, status="running", pending=1)
        img = transparent_image(400, 200)
        p = QPainter(img)
        free = paint_status(p, st, (0, 0, 400, 200))
        self.assertGreater(free.left(), 200)
        self.assertLess(free.top(), 50)  # top-right by default
        moved = paint_status(p, st, (0, 0, 400, 200), avoid=[QRectF(250, 0, 150, 60)])
        p.end()
        self.assertGreater(moved.top(), 100)  # bottom-right
        self.assertFalse(moved.intersects(QRectF(250, 0, 150, 60)))

    def test_status_pill_is_dimmed_when_there_is_nothing_to_report(self):
        def alpha(state: OverlayState) -> int:
            img = transparent_image(300, 100)
            p = QPainter(img)
            pill = paint_status(p, state, (0, 0, 300, 100))
            p.end()
            return int(rgba(img)[int(pill.center().y()), int(pill.left() + 4), 3])

        running = OverlayState(seq=1, region=None, status="running", message="JA→VI")  # what the pipeline sends
        self.assertEqual(alpha(running), alpha(dataclasses.replace(running, message="")))
        self.assertLess(alpha(running), alpha(dataclasses.replace(running, pending=1, message="Đang dịch 1…")))

    def test_narrow_list_items_grow_sideways_not_into_the_next_item(self):
        # A vertical menu of one-kanji items (34 px boxes, 44 px pitch) with long translations.
        cfg = Config(min_font_px=12)
        texts = ["Thuốc hồi phục máu cỡ lớn", "Khiên", "Chìa khóa cửa hầm ngục phía bắc", "Vật phẩm"]
        blocks = [make_block(id=i + 1, box=(38, 26 + 44 * i, 34, 34), lines=((38, 26 + 44 * i, 34, 34),),
                             line_height=34, translation=t) for i, t in enumerate(texts)]
        size = (480, 300)
        limits = growth_bounds(blocks, size)
        lays = [layout_block(b, cfg, size, limits[b.id]) for b in blocks]
        for i in (0, 2):
            self.assertEqual(len(lays[i].fit.lines), 1, lays[i].fit.text)  # widened, whole words
            self.assertGreater(lays[i].fit.rect.width(), 100)
        for i, lay in enumerate(lays):
            self.assertFalse(lay.fit.elided)
            for j, other in enumerate(lays):
                if j != i:
                    for cover in other.cover_rects:
                        self.assertFalse(lay.fit.text_rect.intersects(cover), (texts[i], texts[j]))
        # Painted all together, every item's text survives (no later fill erases an earlier text).
        img = transparent_image(*size)
        p = QPainter(img)
        from app.overlay.render import paint_overlay
        paint_overlay(p, OverlayState(seq=1, region=None, blocks=tuple(blocks), status="running"), cfg, size,
                      show_status=False)
        p.end()
        arr = rgba(img)
        for lay in lays:
            r = lay.fit.text_rect
            crop = arr[int(r.top()):int(r.bottom()), int(r.left()):int(r.right())]
            self.assertGreater(int((crop[..., :3].min(axis=2) > 200).sum()), 20)  # white text pixels

    def test_one_row_label_widens_instead_of_wrapping_small(self):
        # The e2e menu: 冒険を続ける (a 172x32 box) -> a longer Vietnamese label with free space to the right.
        cfg = Config(min_font_px=12)
        block = make_block(box=(120, 172, 172, 32), lines=((120, 172, 172, 32),), line_height=32,
                           translation="tiếp tục cuộc phiêu lưu")
        size = (1280, 720)
        lay = layout_block(block, cfg, size, growth_bounds([block], size)[block.id])
        self.assertEqual(len(lay.fit.lines), 1, [ln.text for ln in lay.fit.lines])
        self.assertGreaterEqual(lay.fit.font_px, int(0.9 * 32) - 1)  # full size, not two small lines
        self.assertTrue(lay.fit.grown)
        # A label that already fits keeps its box.
        short = dataclasses.replace(block, translation="cài đặt")
        lay2 = layout_block(short, cfg, size, growth_bounds([short], size)[short.id])
        self.assertFalse(lay2.fit.grown)

    def test_long_dialogue_line_stays_inside_a_centred_box(self):
        # The LLM e2e: a 537 px line starting 97 px into the region (a dialogue box from 60 to 1220 px)
        # with a longer Vietnamese sentence. It may widen, but not past the mirror of its left margin.
        cfg = Config(min_font_px=12)
        block = make_block(box=(97, 540, 537, 44), lines=((97, 540, 537, 44),), line_height=44,
                           translation="Thôi không còn cách nào khác. Chúng ta đi trước khi trời tối nhé, Sera.")
        size = (1280, 720)
        lay = layout_block(block, cfg, size, growth_bounds([block], size)[block.id])
        self.assertTrue(lay.fit.grown)
        self.assertLessEqual(lay.fit.text_rect.right(), 1280 - 97 + 1)
        self.assertFalse(lay.fit.elided)
        self.assertGreaterEqual(lay.fit.font_px, 24)
        # Text on the right half keeps the region edge as its limit.
        right = dataclasses.replace(block, box=(700, 540, 300, 44), lines=((700, 540, 300, 44),))
        lay3 = layout_block(right, cfg, size, growth_bounds([right], size)[right.id])
        self.assertLessEqual(lay3.fit.text_rect.right(), 1280)


# --- native windows (windows platform only) ---------------------------------------------------

class NativeWindowTests(unittest.TestCase):
    """Real HWNDs on the user's desktop: a transparent, click-through, capture-excluded window."""

    def setUp(self):
        if os.environ.get("SCREEN_TRANSLATOR_NATIVE_TESTS") != "1" or platform() != "windows":
            self.skipTest("needs the windows platform: run via WindowsPlatformSubprocessTests")
        from app.capture import primary_monitor
        mon = primary_monitor()
        self.region = Region(mon.bounds.left + 160, mon.bounds.top + 160, 480, 200)
        self.block = make_block(box=(20, 20, 420, 60), lines=((20, 20, 420, 60),), bg=(255, 0, 255),
                                fg=(255, 255, 255), line_height=60, translation="Thử nghiệm overlay")

    def test_overlay_native_window(self):
        from app.overlay.affinity import get_display_affinity
        from app.overlay.overlay_window import OverlayWindow

        fg_before = _user32.GetForegroundWindow()
        ov = OverlayWindow(Config(box_alpha=255, show_border=True))
        try:
            ov.set_state(OverlayState(seq=1, region=self.region, blocks=(self.block,), status="running"))
            pump(400)
            hwnd = int(ov.winId())
            self.assertTrue(_user32.IsWindowVisible(hwnd))
            self.assertEqual(exstyle_flags(hwnd), set(EX_FLAGS))  # LAYERED|TRANSPARENT|TOPMOST|TOOLWINDOW|NOACTIVATE
            self.assertEqual(get_display_affinity(hwnd), 0x11)
            self.assertTrue(ov.exclusion_ok())
            self.assertEqual(_user32.GetForegroundWindow(), fg_before)  # showing it did not take focus
            self.assertEqual(window_rect(hwnd), self.region)  # 100 % scale: exact

            # guarded: the native window is destroyed, then recreated excluded
            ov.set_guarded(True)
            pump(100)
            self.assertFalse(ov.isVisible())
            self.assertEqual(int(ov.internalWinId()), 0)
            self.assertFalse(_user32.IsWindow(hwnd))
            ov.set_guarded(False)
            pump(300)
            hwnd2 = int(ov.internalWinId())
            self.assertTrue(hwnd2 and _user32.IsWindowVisible(hwnd2))
            self.assertEqual(get_display_affinity(hwnd2), 0x11)
            self.assertTrue(ov.exclusion_ok())
            self.assertEqual(exstyle_flags(hwnd2), set(EX_FLAGS))
            self.assertEqual(_user32.GetForegroundWindow(), fg_before)
        finally:
            ov.shutdown()
            pump(50)

    def test_dxcam_does_not_see_the_overlay(self):
        try:
            from app.capture import create_backend
        except Exception as e:  # pragma: no cover
            self.skipTest(f"capture unavailable: {e}")
        from app.overlay.overlay_window import OverlayWindow

        used: list[str] = []

        def grab() -> np.ndarray:
            # A fresh duplication's first frame is the full current desktop (a cached one may be stale).
            t0 = time.perf_counter()
            with create_backend("dxcam") as cap:
                frame = None
                for _ in range(40):
                    frame = cap.grab(self.region)
                    if frame is not None and frame.timestamp >= t0:
                        used.append("dxcam")
                        return frame.image
                    time.sleep(0.02)
            # Desktop Duplication delivers nothing while the display is asleep or the session is
            # locked; check the same invariant through GDI so the test still means something.
            with create_backend("mss") as cap:
                frame = cap.grab(self.region)
            self.assertIsNotNone(frame)
            used.append("mss")
            return frame.image

        ov = OverlayWindow(Config(box_alpha=255, show_border=True))
        try:
            baseline = magenta_px(grab())
            ov.set_state(OverlayState(seq=1, region=self.region, blocks=(self.block,), status="running"))
            pump(400)
            snap = rgba(ov.snapshot())
            painted = int(((snap[..., 0] > 215) & (snap[..., 1] < 40) & (snap[..., 2] > 215)).sum())
            self.assertGreater(painted, 10000)  # the overlay really draws a big magenta box
            excluded = magenta_px(grab())
            self.assertLessEqual(excluded, baseline, "overlay pixels leaked into the capture")
            # control: with exclusion off the same capture must see it (the check is not vacuous)
            ov._maintain_timer.stop()
            ov._exclusion.set_exclude(False)
            pump(300)
            visible = magenta_px(grab())
            self.assertGreater(visible, baseline + 5000)
            print(f"\n    capture ({'/'.join(dict.fromkeys(used))}) magenta px: baseline={baseline} "
                  f"excluded={excluded} exclusion-off={visible} (painted in snapshot={painted})", file=sys.stderr)
        finally:
            if ov._exclusion is not None:
                ov._exclusion.set_exclude(True)
            ov.shutdown()
            pump(50)

    def test_selector_windows_are_capture_excluded(self):
        from app.overlay.affinity import get_display_affinity
        from app.overlay.geometry import screen_maps
        from app.overlay.region_selector import RegionSelector, _map_for_screen, _ShadeWindow

        sel = RegionSelector()
        maps = screen_maps()
        windows = [_ShadeWindow(sel, s, _map_for_screen(s, maps)) for s in QGuiApplication.screens()]
        try:  # never shown (showing would activate a full-screen window and steal focus)
            for w in windows:
                self.assertEqual(get_display_affinity(int(w.winId())), 0x11)
                self.assertEqual(QRect(w.geometry()), w.screen().geometry())
        finally:
            for w in windows:
                w.deleteLater()
            pump(20)


@unittest.skipUnless(os.environ.get("QT_SCALE_FACTOR") == "1.5" and os.environ.get("SCREEN_TRANSLATOR_NATIVE_TESTS") == "1",
                     "needs QT_SCALE_FACTOR=1.5 and the windows platform: run via WindowsPlatformSubprocessTests")
class ScaledGeometryTests(unittest.TestCase):
    """Qt 6 per-screen mapping checked against where Windows actually puts the window."""

    def test_window_lands_on_physical_region(self):
        from app.capture import primary_monitor
        from app.overlay.overlay_window import OverlayWindow

        qapp()
        self.assertEqual(QGuiApplication.platformName(), "windows")
        self.assertAlmostEqual(QGuiApplication.primaryScreen().devicePixelRatio(), 1.5)
        mon = primary_monitor()
        o = (mon.bounds.left, mon.bounds.top)
        cfg = Config(box_alpha=255, show_border=False)
        block = make_block(box=(100, 50, 200, 40), lines=((100, 50, 200, 40),), bg=(255, 0, 255), translation="Thử")
        for region, exact in [(Region(o[0] + 300, o[1] + 150, 600, 300), True),
                              (Region(o[0] + 301, o[1] + 151, 401, 201), False)]:
            with self.subTest(region=region):
                ov = OverlayWindow(cfg)
                try:
                    ov.set_state(OverlayState(seq=1, region=region, blocks=(block,), status="running"))
                    pump(300)
                    actual = window_rect(int(ov.winId()))
                    self.assertEqual(actual, logical_to_physical(ov.geometry()))  # our model of Qt's rounding
                    if exact:
                        self.assertEqual(actual, region)
                    self.assertLessEqual(actual.left, region.left)
                    self.assertLessEqual(actual.top, region.top)
                    self.assertGreaterEqual(actual.right, region.right)
                    self.assertGreaterEqual(actual.bottom, region.bottom)
                    self.assertLessEqual(actual.width - region.width, 2)
                    # painting is in physical px: the magenta fill lands where the layout put it
                    lay = layout_block(block, cfg, (region.width, region.height))
                    want = QRectF()
                    for f in lay.fill_rects:
                        want = want.united(f)
                    snap = rgba(ov.snapshot())
                    ys, xs = np.nonzero((snap[..., 0] > 215) & (snap[..., 1] < 40) & (snap[..., 2] > 215))
                    dx, dy = region.left - actual.left, region.top - actual.top
                    self.assertAlmostEqual(int(xs.min()), want.left() + dx, delta=2)
                    self.assertAlmostEqual(int(ys.min()), want.top() + dy, delta=2)
                    self.assertAlmostEqual(int(xs.max()) + 1, want.right() + dx, delta=2)
                    self.assertAlmostEqual(int(ys.max()) + 1, want.bottom() + dy, delta=2)
                finally:
                    ov.shutdown()
                    pump(50)


# --- child processes -------------------------------------------------------------------------

@unittest.skipIf(sys.platform != "win32", "Windows only")
@unittest.skipIf(os.environ.get("SCREEN_TRANSLATOR_SKIP_GUI_TESTS") == "1", "SCREEN_TRANSLATOR_SKIP_GUI_TESTS=1")
class WindowsPlatformSubprocessTests(unittest.TestCase):
    """Runs the font/HWND test classes in child processes that use Qt's windows platform."""

    def _run(self, classes: list[str], **env_extra: str) -> None:
        env = dict(os.environ)
        env.pop("QT_SCALE_FACTOR", None)
        env.update(QT_QPA_PLATFORM="windows", SCREEN_TRANSLATOR_NATIVE_TESTS="1", PYTHONIOENCODING="utf-8",
                   **env_extra)
        cmd = [sys.executable, "-m", "unittest", "-v", *[f"tests.test_overlay.{c}" for c in classes]]
        proc = subprocess.run(cmd, cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=180)
        out = proc.stdout + proc.stderr
        if os.environ.get("SCREEN_TRANSLATOR_VERBOSE_CHILD") == "1":
            print(out, file=sys.stderr)
        self.assertEqual(proc.returncode, 0, out[-6000:])
        self.assertNotIn("skipped", out, out[-3000:])  # everything must really run in the child

    def test_render_and_native_window_tests(self):
        if platform() == "windows" and os.environ.get("SCREEN_TRANSLATOR_NATIVE_TESTS") == "1":
            self.skipTest("already running on the windows platform")
        self._run(["RenderTests", "NativeWindowTests", "OverlayWindowLogicTests", "HotkeyManagerTests",
                   "GameGuardTests", "TrayTests"])

    def test_qt_scale_factor_15(self):
        if os.environ.get("QT_SCALE_FACTOR") == "1.5":
            self.skipTest("already running with QT_SCALE_FACTOR=1.5")
        self._run(["ScaledGeometryTests"], QT_SCALE_FACTOR="1.5")


if __name__ == "__main__":
    unittest.main()
