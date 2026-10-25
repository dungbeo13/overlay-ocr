"""Tests for the windowless launcher, Settings window, first-run guide, single-instance guard and the
shortcut installer (app.ui.settings_dialog, app.ui.first_run, app.single_instance, app.main wiring,
tools/install_shortcut.py, run_app.pyw).

Run: python -m unittest tests.test_settings_ui -v

The logic tests run on Qt's offscreen platform in this process; keys are sent to our own widgets with
QCoreApplication.sendEvent (no OS input), dialogs are driven through their methods and signals. Shortcuts
are written into temporary folders, never the real Desktop / Start Menu / Startup. The native classes
(capture-exclusion readback on real HWNDs, previews rendered with QWidget.grab into
bench/results/preview/, and a real windowless launch of run_app.pyw with a temporary data directory,
quit through the single-instance socket) run in a child process on the windows platform
(WindowsPlatformSubprocessTests), skipped while an anti-cheat game owns the foreground or with
SCREEN_TRANSLATOR_SKIP_GUI_TESTS=1.
"""
from __future__ import annotations

import copy
import ctypes
import importlib.machinery
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from ctypes import wintypes
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent, QShowEvent
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from app.config import Config, Hotkeys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PREVIEW_DIR = PROJECT_ROOT / "bench" / "results" / "preview"
NATIVE = os.environ.get("SCREEN_TRANSLATOR_NATIVE_TESTS") == "1"
HK_A, HK_B = "ctrl+alt+shift+f21", "ctrl+alt+shift+f22"  # unused combos (test_overlay uses F23)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetForegroundWindow.restype = wintypes.HWND
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_WM_CLOSE = 0x0010


def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication([sys.argv[0]])
    from app import qt_gc

    qt_gc.install(app)  # as in app.main: worker threads never run a gc collection (Qt wrappers in the garbage)
    return app


def on_windows_platform() -> bool:
    qapp()
    return QGuiApplication.platformName() == "windows"


def pump(ms: int) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
        time.sleep(0.003)


def pump_until(pred, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
        time.sleep(0.005)
    return bool(pred())


def flush_deletes() -> None:
    """Run pending deleteLater()s (capture-excluded widgets must not survive their test)."""
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    pump(10)


def press(widget, key, mods=Qt.KeyboardModifier.NoModifier, vk: int = 0, kind=QEvent.Type.KeyPress) -> QKeyEvent:
    """Deliver a key event to our own widget (QCoreApplication.sendEvent: no OS input involved)."""
    ev = QKeyEvent(kind, key, mods, 0, vk, 0)
    QCoreApplication.sendEvent(widget, ev)
    return ev


class FakeStartup:
    def __init__(self, enabled: bool | None = False, fail: Exception | None = None) -> None:
        self.state, self.fail, self.calls = enabled, fail, []

    def enabled(self):
        return self.state

    def set_enabled(self, on: bool) -> None:
        self.calls.append(on)
        if self.fail is not None:
            raise self.fail
        self.state = on


def sample_config() -> Config:
    return Config(src_lang="en", region=[5, 6, 700, 200], llm_mode="only", llm_model="quality", llm_history_lines=7,
                  llm_auto_terms=False, llm_release_after_guard_s=90, font_family="Arial", min_font_px=15,
                  box_alpha=200, show_border=False, guard_mode="warn", blocked_processes=["A.exe", "OnlineGame.exe"],
                  poll_ms=220, ocr_device="cpu", capture_backend="mss", profile="Blue Archive", first_run_done=True,
                  hotkeys=Hotkeys("ctrl+alt+r", "ctrl+alt+p", "", "ctrl+shift+f9", "ctrl+alt+q"))


def ui_fields(cfg: Config) -> dict:
    from app.ui.settings_dialog import SETTINGS_FIELDS
    return {name: copy.deepcopy(getattr(cfg, name)) for name in SETTINGS_FIELDS}


# --- hotkey helpers (no widgets) ------------------------------------------------------------------

class HotkeyLogicTests(unittest.TestCase):
    def test_key_token(self):
        from app.ui.settings_dialog import key_token
        K = Qt.Key
        self.assertEqual(key_token(K.Key_R), "r")
        self.assertEqual(key_token(K.Key_7), "7")
        self.assertEqual(key_token(K.Key_F9), "f9")
        self.assertEqual(key_token(K.Key_7, keypad=True), "numpad7")
        self.assertEqual(key_token(K.Key_Plus, keypad=True), "add")
        self.assertEqual(key_token(K.Key_PageDown), "pagedown")
        self.assertEqual(key_token(K.Key_BracketLeft), "[")
        self.assertEqual(key_token(K.Key_Exclam, native_vk=0x31), "1")  # Shift+1 on a US layout: the key, not "!"
        self.assertEqual(key_token(K.Key_unknown, native_vk=0xBA), "semicolon")
        self.assertEqual(key_token(K.Key_unknown, native_vk=0x7B), "f12")
        self.assertIsNone(key_token(K.Key_VolumeUp))

    def test_every_token_parses(self):
        from app.ui.hotkeys import parse_combo
        from app.ui.settings_dialog import _QT_KEYPAD, _QT_TOKENS, _VK_TOKENS
        for token in {*_VK_TOKENS.values(), *_QT_TOKENS.values(), *_QT_KEYPAD.values()}:
            parse_combo(f"ctrl+alt+{token}")  # raises on a name hotkeys.py does not know

    def test_hotkey_problem(self):
        from app.ui.settings_dialog import hotkey_problem
        self.assertIsNone(hotkey_problem(""))  # no hotkey: allowed
        self.assertIsNone(hotkey_problem("ctrl+alt+r"))
        self.assertIsNone(hotkey_problem("win+shift+f5"))
        self.assertIsNone(hotkey_problem("f13"))  # rare keys may stand alone
        self.assertIn("cần thêm Ctrl, Alt hoặc Win", hotkey_problem("shift+a"))
        self.assertIn("cần thêm Ctrl, Alt hoặc Win", hotkey_problem("f5"))
        self.assertIn("phím của Windows", hotkey_problem("alt+f4"))
        self.assertIn("phím của Windows", hotkey_problem("Win+Shift+S"))
        self.assertIn("không phải phím tắt hợp lệ", hotkey_problem("ctrl+nonsense"))

    def test_conflicts(self):
        from app.ui.settings_dialog import hotkey_conflicts
        got = hotkey_conflicts({"select_region": "ctrl+alt+r", "toggle_pause": "Ctrl+Alt+R", "toggle_overlay": "",
                                "switch_lang": "", "quit": "alt+f4"})
        self.assertEqual(list(got), ["select_region", "toggle_pause", "quit"])  # in row order
        self.assertIn("trùng với “Tạm dừng / tiếp tục”", got["select_region"])
        self.assertIn("trùng với “Chọn vùng dịch”", got["toggle_pause"])
        self.assertEqual(hotkey_conflicts({"a": "", "b": ""}), {})  # two empty fields are no clash

    def test_settings_diff(self):
        from app.ui.settings_dialog import settings_diff
        a = Config()
        b = copy.deepcopy(a)
        b.hotkeys.select_region = "Ctrl+Alt+R"  # same combo, other spelling
        b.region = [1, 2, 3, 4]  # not a Settings field
        self.assertEqual(settings_diff(a, b), [])
        b.hotkeys.quit = "ctrl+alt+x"
        b.box_alpha = 10
        b.ocr_device = "cpu"
        self.assertEqual(settings_diff(a, b), ["box_alpha", "hotkeys", "ocr_device"])


# --- the key-capture field ------------------------------------------------------------------------

class HotkeyEditTests(unittest.TestCase):
    def setUp(self):
        qapp()
        self.addCleanup(flush_deletes)
        from app.ui.settings_dialog import HotkeyEdit
        self.edit = HotkeyEdit("ctrl+alt+r")
        self.addCleanup(self.edit.deleteLater)
        self.changed: list[str] = []
        self.capturing: list[bool] = []
        self.edit.combo_changed.connect(self.changed.append)
        self.edit.capturing_changed.connect(self.capturing.append)

    def test_records_a_combo(self):
        e = self.edit
        self.assertEqual(e.text(), "Ctrl+Alt+R")
        e.start_capture()
        self.assertEqual((e.text(), e.placeholderText()), ("", "Nhấn tổ hợp phím mới… (Esc: hủy)"))
        mods = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
        # A modifier key's own event carries the state before it (QKeyEvent.modifiers() flips its bit).
        press(e, Qt.Key.Key_Control)
        self.assertEqual(e.text(), "Ctrl+…")
        press(e, Qt.Key.Key_Alt, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(e.text(), "Ctrl+Alt+…")  # what is held so far
        press(e, Qt.Key.Key_Alt, mods, kind=QEvent.Type.KeyRelease)
        self.assertEqual(e.text(), "Ctrl+…")  # Alt let go again
        press(e, Qt.Key.Key_Alt, Qt.KeyboardModifier.ControlModifier)
        override = press(e, Qt.Key.Key_K, mods, kind=QEvent.Type.ShortcutOverride)
        self.assertTrue(override.isAccepted())  # no dialog / button shortcut takes the key
        press(e, Qt.Key.Key_K, mods, vk=0x4B)
        self.assertEqual(e.combo(), "ctrl+alt+k")
        self.assertEqual(e.text(), "Ctrl+Alt+K")
        self.assertEqual(self.changed, ["ctrl+alt+k"])
        self.assertEqual(self.capturing, [True, False])  # one combo per recording
        self.assertFalse(e.is_capturing())

    def test_native_key_and_keypad(self):
        e = self.edit
        e.start_capture()
        press(e, Qt.Key.Key_Exclam, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, vk=0x31)
        self.assertEqual(e.combo(), "ctrl+shift+1")
        e.start_capture()
        press(e, Qt.Key.Key_5, Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.KeypadModifier)
        self.assertEqual(e.combo(), "alt+numpad5")
        e.start_capture()
        press(e, Qt.Key.Key_F7, Qt.KeyboardModifier.MetaModifier)
        self.assertEqual(e.combo(), "win+f7")

    def test_escape_cancels_backspace_clears(self):
        e = self.edit
        e.start_capture()
        press(e, Qt.Key.Key_Escape)
        self.assertEqual(e.combo(), "ctrl+alt+r")
        self.assertEqual(e.text(), "Ctrl+Alt+R")
        self.assertFalse(e.is_capturing())
        e.start_capture()
        press(e, Qt.Key.Key_Return)  # Enter keeps the combo too
        self.assertEqual(e.combo(), "ctrl+alt+r")
        e.start_capture()
        press(e, Qt.Key.Key_Backspace)
        self.assertEqual(e.combo(), "")
        self.assertEqual((e.text(), e.placeholderText()), ("", "(không dùng)"))
        self.assertEqual(self.changed, [""])

    def test_unusable_key(self):
        e = self.edit
        e.start_capture()
        press(e, Qt.Key.Key_VolumeUp, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(e.combo(), "ctrl+alt+r")
        self.assertTrue(e.is_capturing())
        self.assertEqual(e.placeholderText(), "Phím này không dùng làm phím tắt được")


# --- the Settings dialog (offscreen) ---------------------------------------------------------------

class _DialogFixture(unittest.TestCase):
    def setUp(self):
        qapp()
        self.addCleanup(flush_deletes)
        self.tmp = Path(tempfile.mkdtemp(prefix="st-settings-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.config_path = self.tmp / "config.json"
        self.applied: list[Config] = []
        self.result_patch = None  # callable(cfg) -> ApplyResult, overrides the default saving one
        self.startup = FakeStartup()

    def apply(self, cfg: Config):
        from app.ui.settings_dialog import ApplyResult
        self.applied.append(copy.deepcopy(cfg))
        if self.result_patch is not None:
            return self.result_patch(cfg)
        cfg.save(self.config_path)
        return ApplyResult(config=copy.deepcopy(cfg))

    def dialog(self, config: Config | None = None, **kw):
        from app.ui.settings_dialog import SettingsDialog
        kw.setdefault("startup", self.startup)
        d = SettingsDialog(config or sample_config(), apply=self.apply, **kw)
        d.ask_before_discard = False
        self.addCleanup(d.deleteLater)
        return d


class SettingsDialogTests(_DialogFixture):
    def test_tabs_and_labels_are_vietnamese(self):
        from app.ui.settings_dialog import TABS
        d = self.dialog()
        self.assertEqual([d.tabs.tabText(i) for i in range(d.tabs.count())],
                         ["Chung", "Dịch", "Hiển thị", "Phím tắt", "Anti-cheat", "Nâng cao", "Tra cứu game"])
        self.assertEqual((d.save_button.text(), d.cancel_button.text(), d.defaults_button.text()),
                         ("Lưu", "Hủy", "Mặc định"))
        self.assertIn("Cài đặt", d.windowTitle())
        english = {"save", "cancel", "default", "settings", "apply", "ok", "close", "browse", "reset"}
        from PySide6.QtWidgets import QAbstractButton
        for w in d.findChildren(QLabel) + d.findChildren(QAbstractButton):
            words = {x.strip(".…:()").lower() for x in w.text().split()}
            self.assertFalse(words & english, w.text())
        for key in TABS:
            d.set_tab(key)
            self.assertEqual(d.current_tab(), key)
        src = (PROJECT_ROOT / "app" / "ui" / "settings_dialog.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(src.count("TRA CỨU GAME"), 3)  # the marked room for the later tab

    def test_loads_and_round_trips(self):
        cfg = sample_config()
        d = self.dialog(cfg)
        self.assertEqual(d.lang_combo.currentData(), "en")
        self.assertEqual(d.llm_mode_combo.currentData(), "only")
        self.assertEqual(d.model_combo.currentData(), "quality")
        self.assertEqual((d.history_spin.value(), d.release_spin.value()), (7, 90))
        self.assertFalse(d.auto_terms_check.isChecked())
        self.assertEqual((d.min_font_spin.value(), d.alpha_slider.value()), (15, 200))
        self.assertEqual(d.alpha_label.text(), "78 %")
        self.assertFalse(d.border_check.isChecked())
        self.assertEqual(d.hotkey_edits["toggle_pause"].text(), "Ctrl+Alt+P")
        self.assertEqual(d.hotkey_edits["toggle_overlay"].combo(), "")
        self.assertEqual(d.guard_combo.currentData(), "warn")
        self.assertEqual(d.blocked_processes(), ["A.exe", "OnlineGame.exe"])
        self.assertEqual((d.poll_spin.value(), d.device_combo.currentData(), d.capture_combo.currentData()),
                         (220, "cpu", "mss"))
        got, errors = d.collect()
        self.assertEqual(errors, [])
        self.assertEqual(ui_fields(got), ui_fields(cfg))
        self.assertEqual((got.region, got.profile, got.first_run_done), (cfg.region, cfg.profile, True))
        self.assertFalse(d.is_dirty())

    def test_edit_everything_and_save(self):
        d = self.dialog()
        saved: list[Config] = []
        d.saved.connect(saved.append)
        d.lang_combo.setCurrentIndex(d.lang_combo.findData("ja"))
        d.llm_mode_combo.setCurrentIndex(d.llm_mode_combo.findData("refine"))
        d.model_combo.setCurrentIndex(d.model_combo.findData(""))
        d.history_spin.setValue(20)
        d.release_spin.setValue(-1)
        self.assertEqual(d.release_spin.text(), "Không tắt")
        d.auto_terms_check.setChecked(True)
        d.font_combo.setEditText("Tahoma")
        d.min_font_spin.setValue(18)
        d.alpha_slider.setValue(255)
        d.border_check.setChecked(True)
        d.hotkey_edits["toggle_overlay"].set_combo("ctrl+alt+h", emit=True)
        d.guard_combo.setCurrentIndex(d.guard_combo.findData("pause"))
        d.blocked_list.item(0).setSelected(True)
        d.remove_selected_blocked()
        d.blocked_edit.setText('"C:\\Games\\Nikke\\nikke.exe"')
        d._add_typed_blocked()
        d.poll_spin.setValue(300)
        d.device_combo.setCurrentIndex(d.device_combo.findData("cuda"))
        d.capture_combo.setCurrentIndex(d.capture_combo.findData("auto"))
        self.assertTrue(d.is_dirty())
        self.assertTrue(d.save(), d.status_label.text())
        got = Config.load(self.config_path)
        self.assertEqual((got.src_lang, got.llm_mode, got.llm_model, got.llm_history_lines, got.llm_release_after_guard_s,
                          got.llm_auto_terms), ("ja", "refine", "", 20, -1, True))
        self.assertEqual((got.font_family, got.min_font_px, got.box_alpha, got.show_border), ("Tahoma", 18, 255, True))
        self.assertEqual(got.hotkeys.toggle_overlay, "ctrl+alt+h")
        self.assertEqual(got.hotkeys.switch_lang, "ctrl+shift+f9")  # untouched
        self.assertEqual((got.guard_mode, got.blocked_processes), ("pause", ["OnlineGame.exe", "nikke.exe"]))
        self.assertEqual((got.poll_ms, got.ocr_device, got.capture_backend), (300, "cuda", "auto"))
        self.assertEqual((got.region, got.profile, got.first_run_done), ([5, 6, 700, 200], "Blue Archive", True))
        self.assertEqual(d.status_label.text(), "Đã lưu.")
        self.assertFalse(d.is_dirty())
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].min_font_px, 18)

    def test_defaults(self):
        self.startup.state = True
        d = self.dialog()
        self.assertTrue(d.startup_check.isChecked())
        d._on_defaults_clicked()  # the button (its question is off in tests)
        self.assertEqual(d.status_label.text(), "Đã điền giá trị mặc định. Bấm Lưu để áp dụng.")
        self.assertTrue(d.startup_check.isChecked())  # the Startup shortcut is not a config.json setting: kept
        self.assertEqual(self.applied, [])  # nothing saved yet
        got, errors = d.collect()
        self.assertEqual(errors, [])
        want = ui_fields(Config())
        have = ui_fields(got)
        if not on_windows_platform():  # offscreen Qt lists no fonts: the family is kept by name
            want.pop("font_family")
            have.pop("font_family")
        self.assertEqual(have, want)
        self.assertEqual(got.region, [5, 6, 700, 200])  # not a Settings field: untouched
        self.assertTrue(d.save())
        self.assertEqual(Config.load(self.config_path).blocked_processes, Config().blocked_processes)
        self.assertEqual(self.startup.calls, [])

    def test_duplicate_hotkey_blocks_save(self):
        d = self.dialog()
        d.set_tab("general")
        d.hotkey_edits["toggle_pause"].set_combo("ctrl+alt+q", emit=True)
        self.assertIn("trùng với “Thoát”", d.hotkey_error_label.text())
        self.assertIn("#ffcdd2", d.hotkey_edits["quit"].styleSheet())
        self.assertFalse(d.save())
        self.assertEqual(self.applied, [])
        self.assertEqual(d.current_tab(), "hotkeys")
        self.assertIn("trùng với", d.status_label.text())
        d.hotkey_edits["toggle_pause"].set_combo("shift+p", emit=True)
        self.assertFalse(d.save())
        self.assertIn("cần thêm Ctrl, Alt hoặc Win", d.status_label.text())
        d.hotkey_edits["toggle_pause"].set_combo("ctrl+alt+p", emit=True)
        self.assertEqual(d.hotkey_error_label.text(), "")
        self.assertTrue(d.save())

    def test_refused_hotkey_and_restart(self):
        from app.ui.settings_dialog import ApplyResult

        def refuse(cfg):
            kept = copy.deepcopy(cfg)
            kept.hotkeys.select_region = "ctrl+alt+r"  # what App.apply_settings returns: the old combo stays
            return ApplyResult(restart=["Thiết bị OCR"], errors=["Chọn vùng dịch: Ctrl+Alt+M đang được ứng dụng khác sử dụng"],
                               config=kept)

        self.result_patch = refuse
        d = self.dialog()
        restarts: list[bool] = []
        d.restart_requested.connect(lambda: restarts.append(True))
        d.hotkey_edits["select_region"].set_combo("ctrl+alt+m", emit=True)
        d.device_combo.setCurrentIndex(d.device_combo.findData("cuda"))
        self.assertFalse(d.save())
        self.assertIn("đang được ứng dụng khác sử dụng", d.status_label.text())
        self.assertEqual(d.hotkey_edits["select_region"].combo(), "ctrl+alt+r")  # shows the combo still in use
        self.assertEqual(d.restart_pending(), ["Thiết bị OCR"])
        self.assertFalse(d.restart_button.isHidden())
        self.assertEqual(d.restart_button.text(), "Khởi động lại app")
        self.result_patch = lambda cfg: ApplyResult(restart=["Thiết bị OCR"], config=copy.deepcopy(cfg))
        d.history_spin.setValue(3)
        self.assertTrue(d.save())
        self.assertEqual(d.status_label.text(), "Đã lưu. Cần khởi động lại app để áp dụng: Thiết bị OCR.")
        d._on_save_clicked()  # Lưu with a restart pending keeps the window open
        self.assertEqual(d.result(), 0)
        d.restart_button.click()  # our own button, clicked programmatically
        self.assertEqual(restarts, [True])
        # the device set back to what runs: the next save reports no restart, the notice and button go
        self.result_patch = lambda cfg: ApplyResult(restart=[], config=copy.deepcopy(cfg))
        d.device_combo.setCurrentIndex(d.device_combo.findData("cpu"))
        self.assertTrue(d.save())
        self.assertEqual((d.restart_pending(), d.status_label.text()), ([], "Đã lưu."))
        self.assertTrue(d.restart_button.isHidden())

    def test_unchanged_saved_hotkey_and_odd_values_do_not_block(self):
        """config.json values that worked before: a lone F9, numbers outside the boxes' ranges, wrong types."""
        cfg = sample_config()
        cfg.hotkeys.toggle_overlay = "f9"  # no Ctrl / Alt / Win, but registered fine before
        cfg.poll_ms, cfg.llm_release_after_guard_s, cfg.llm_history_lines, cfg.min_font_px = 30, 7200, 80, 48
        d = self.dialog(cfg)
        self.assertIn("cần thêm Ctrl, Alt hoặc Win", d.hotkey_error_label.text())  # reported...
        self.assertFalse(d.is_dirty())  # ...and nothing changed by just opening the window
        self.assertEqual((d.poll_spin.value(), d.release_spin.value(), d.history_spin.value(), d.min_font_spin.value()),
                         (30, 7200, 80, 48))
        d.history_spin.setValue(20)
        self.assertTrue(d.save(), d.status_label.text())  # ...but it does not block other changes
        got = Config.load(self.config_path)
        self.assertEqual((got.hotkeys.toggle_overlay, got.poll_ms, got.llm_history_lines), ("f9", 30, 20))
        d.hotkey_edits["toggle_overlay"].set_combo("f8", emit=True)  # changed here: now it must be fixed
        self.assertFalse(d.save())
        bad = Config(poll_ms="fast", llm_release_after_guard_s=None, blocked_processes=None, min_font_px="x")
        b = self.dialog(bad)  # a hand-edited config.json with wrong types still opens
        self.assertEqual((b.poll_spin.value(), b.release_spin.value(), b.min_font_spin.value()), (150, 30, 12))
        self.assertEqual(b.blocked_processes(), [])

    def test_changes_made_elsewhere_are_not_reverted(self):
        d = self.dialog()  # sample: src_lang en, llm_mode only, guard warn
        d.sync_field("src_lang", "ja")  # e.g. the switch_lang hotkey while the window is open
        self.assertEqual(d.lang_combo.currentData(), "ja")
        d.guard_combo.setCurrentIndex(d.guard_combo.findData("pause"))  # the user's own choice here...
        d.sync_field("guard_mode", "off")  # ...is kept over a tray change
        self.assertEqual(d.guard_combo.currentData(), "pause")
        d.sync_field("llm_mode", "off")
        self.assertEqual(d.llm_mode_combo.currentData(), "off")
        d.min_font_spin.setValue(20)
        self.assertTrue(d.save())
        got = Config.load(self.config_path)
        self.assertEqual((got.src_lang, got.llm_mode, got.guard_mode, got.min_font_px), ("ja", "off", "pause", 20))

    def test_llm_install_command_only_when_missing(self):
        from app.ui.first_run import setup_command
        d = self.dialog(llm_models=[], llm_server=False)
        self.assertTrue(d.llm_install_shown())
        self.assertEqual(d.llm_command_label.text(), setup_command())
        self.assertIn("-m tools.setup_llm", d.llm_command_label.text())
        d.llm_copy_button.click()
        self.assertEqual(QGuiApplication.clipboard().text(), setup_command())
        ok = self.dialog(llm_models=["Hy-MT2-7B-Q4_K_M.gguf"], llm_server=True, llm_status="LLM: sẵn sàng (7B)")
        self.assertFalse(ok.llm_install_shown())
        ok.set_llm_status("LLM lỗi: server đã dừng")
        self.assertTrue(ok.llm_install_shown())

    def test_discard_and_defaults_questions_are_vietnamese(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication as _QA
        d = self.dialog()
        d.ask_before_discard = True
        d.history_spin.setValue(1)
        seen: list[list[str]] = []

        def answer(choice: str):
            for w in _QA.topLevelWidgets():
                if isinstance(w, QMessageBox) and w.isVisible():
                    seen.append([b.text() for b in w.buttons()])
                    next(b for b in w.buttons() if b.text() == choice).click()
                    return
            QTimer.singleShot(20, lambda: answer(choice))

        QTimer.singleShot(20, lambda: answer("Tiếp tục sửa"))
        d.reject()  # kept open
        QTimer.singleShot(20, lambda: answer("Không"))
        d._on_defaults_clicked()  # not reset
        self.assertEqual(d.history_spin.value(), 1)
        self.assertEqual(sorted(seen[0]), ["Bỏ thay đổi", "Tiếp tục sửa"])
        self.assertEqual(sorted(seen[1]), ["Không", "Đặt lại"])
        d.ask_before_discard = False

    def test_startup_checkbox(self):
        d = self.dialog()
        self.assertFalse(d.startup_check.isChecked())
        self.assertTrue(d.startup_check.isEnabled())
        d.startup_check.setChecked(True)
        self.assertTrue(d.is_dirty())
        self.assertTrue(d.save())
        self.assertEqual(self.startup.calls, [True])
        self.assertTrue(d.startup_state())
        self.assertTrue(d.save())  # unchanged: not touched again
        self.assertEqual(self.startup.calls, [True])
        broken = FakeStartup(False, fail=OSError("Access denied"))
        d2 = self.dialog(startup=broken)
        d2.startup_check.setChecked(True)
        self.assertFalse(d2.save())
        self.assertIn("Không đổi được “Khởi động cùng Windows”: Access denied", d2.status_label.text())
        self.assertFalse(d2.startup_check.isChecked())  # back to the real state
        unknown = self.dialog(startup=FakeStartup(None))
        self.assertFalse(unknown.startup_check.isEnabled())

    def test_capture_signal_and_cancel(self):
        d = self.dialog()
        seen: list[bool] = []
        d.hotkey_capture.connect(seen.append)
        d.hotkey_edits["quit"].start_capture()
        d.hotkey_edits["quit"].stop_capture()
        self.assertEqual(seen, [True, False])
        d.history_spin.setValue(1)
        d.reject()  # ask_before_discard is off in tests
        self.assertEqual(self.applied, [])

    def test_blocked_list_and_window_picker(self):
        d = self.dialog(windows=lambda: [("onlinegame.exe", "Online Game"), ("Game.exe", "Some game")])
        self.assertEqual(d.add_blocked(["a.EXE", "C:/x/New.exe", " "]), 1)  # case-insensitive, path stripped
        d.restore_default_blocked()
        self.assertIn("VALORANT-Win64-Shipping.exe", d.blocked_processes())
        picker = d.make_window_picker()
        self.addCleanup(picker.deleteLater)
        self.assertEqual(picker.windowTitle(), "Thêm game có anti-cheat")
        self.assertIn("Chọn game có anti-cheat", picker.findChild(QLabel).text())
        self.assertIn("(đã có)", picker.list.item(0).text())  # OnlineGame.exe is already listed
        picker.list.item(1).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(d.add_blocked(picker.selected()), 1)
        self.assertEqual(d.blocked_processes()[-1], "Game.exe")

    def test_guard_hint_and_llm_status(self):
        d = self.dialog(llm_status="LLM: sẵn sàng (Chất lượng 7B)")
        self.assertEqual(d.llm_status_label.text(), "Sẵn sàng (Chất lượng 7B)")
        d.guard_combo.setCurrentIndex(d.guard_combo.findData("pause"))
        self.assertIn("ngừng chụp màn hình", d.guard_hint.text())
        d.set_llm_status("")
        self.assertEqual(d.llm_status_label.text(), "Chưa chạy")
        odd = self.dialog(Config(llm_model="Hy-MT2-7B-Q4_K_M.gguf"))
        self.assertEqual(odd.model_combo.currentText(), "Tệp: Hy-MT2-7B-Q4_K_M.gguf")
        self.assertEqual(odd.collect()[0].llm_model, "Hy-MT2-7B-Q4_K_M.gguf")  # kept, not turned into "quality"


# --- first-run guide (offscreen) --------------------------------------------------------------------

class FirstRunTests(unittest.TestCase):
    def setUp(self):
        qapp()
        self.addCleanup(flush_deletes)

    def guide(self, config: Config | None = None, **kw):
        from app.ui.first_run import FirstRunGuide
        kw.setdefault("profiles", ["default", "Blue Archive"])
        kw.setdefault("llm_models", ["Hy-MT2-7B-Q4_K_M.gguf"])
        kw.setdefault("llm_server", True)
        g = FirstRunGuide(config or Config(), **kw)
        self.addCleanup(g.deleteLater)
        return g

    def test_should_show(self):
        from app.ui.first_run import should_show
        self.assertTrue(should_show(False))
        self.assertFalse(should_show(True))
        self.assertFalse(should_show(False, automated=True))  # --exit-after runs (e2e) never show it
        self.assertTrue(should_show(True, forced=True))
        self.assertTrue(should_show(True, automated=True, forced=True))

    def test_navigation_and_dismiss(self):
        g = self.guide()
        done: list[bool] = []
        g.dismissed.connect(done.append)
        self.assertEqual((g.step(), g.step_count()), (0, 6))
        self.assertEqual(g.step_label.text(), "Bước 1/6")
        self.assertFalse(g.back_button.isEnabled())
        for i in range(1, 6):
            g.next()
            self.assertEqual(g.step(), i)
        self.assertEqual(g.next_button.text(), "Xong")
        self.assertTrue(g.skip_button.isHidden())
        g.back()
        self.assertEqual((g.step(), g.next_button.text()), (4, "Tiếp"))
        g.go(5)
        g.next()
        self.assertEqual(done, [True])
        g2 = self.guide()
        g2.dismissed.connect(lambda c: done.append(("skip", c)))
        g2.skip()
        g2.reject()
        self.assertEqual(done, [True, ("skip", False)])  # once
        g3 = self.guide()
        g3.dismissed.connect(lambda c: done.append(("quiet", c)))
        g3.close_quietly()
        self.assertEqual(len(done), 2)

    def test_actions_are_signals(self):
        g = self.guide(Config(src_lang="ja"), active_profile="Blue Archive")
        got: list = []
        g.set_lang.connect(lambda c: got.append(("lang", c)))
        g.set_profile.connect(lambda n: got.append(("profile", n)))
        g.new_profile.connect(lambda: got.append(("new",)))
        g.edit_glossary.connect(lambda: got.append(("edit",)))
        g.select_region.connect(lambda: got.append(("region",)))
        g.open_settings.connect(lambda: got.append(("settings",)))
        self.assertTrue(g.lang_buttons["ja"].isChecked())
        g.lang_buttons["en"].setChecked(True)
        self.assertEqual(g.profile_combo.currentText(), "Blue Archive")
        self.assertEqual(g.profile_combo.itemText(0), "Mặc định")
        g.profile_combo.activated.emit(0)
        g.new_profile_button.click()
        g.edit_profile_button.click()
        g.settings_button.click()
        g.show()
        g.region_button.click()
        self.assertFalse(g.isVisible())  # out of the way of the dimmed screen
        g.region_finished([10, 20, 640, 180])
        self.assertTrue(g.isVisible())
        self.assertIn("640 × 180 px", g.region_label.text())
        self.assertEqual(got, [("lang", "en"), ("profile", "default"), ("new",), ("edit",), ("settings",), ("region",)])
        g.set_lang_checked("ja")  # from the app (hotkey): no signal back
        self.assertEqual(got[-1], ("region",))
        g.close_quietly()

    def test_region_and_hotkeys(self):
        g = self.guide(Config(region=None, hotkeys=Hotkeys(quit="")))
        self.assertIn("Chưa chọn vùng", g.region_label.text())
        self.assertEqual(g.region_button.text(), "Chọn vùng ngay")
        self.assertEqual(g.hotkey_labels["select_region"].text(), "Ctrl+Alt+R")
        self.assertEqual(g.hotkey_labels["quit"].text(), "(không có)")
        g.region_finished(None)  # cancelled, not started from here: nothing happens
        self.assertIn("Chưa chọn vùng", g.region_label.text())

    def test_llm_summary(self):
        from app.ui.first_run import llm_summary, setup_command
        models = ["Hy-MT2-1.8B-Q6_K.gguf"]
        self.assertEqual(llm_summary("off", "", models=models, server_found=True)[0], "off")
        state, text = llm_summary("refine", "", models=[], server_found=False)
        self.assertEqual(state, "missing")
        self.assertIn("Chưa cài model và llama.cpp", text)
        self.assertEqual(llm_summary("refine", "", models=models, server_found=False)[1][:16], "Chưa cài llama.c")
        self.assertEqual(llm_summary("refine", "LLM: đang tải model…", models=models, server_found=True)[0], "loading")
        state, text = llm_summary("only", "LLM: sẵn sàng (Nhẹ 1.8B)", models=models, server_found=True)
        self.assertEqual(state, "ready")
        self.assertTrue(text.startswith("Sẵn sàng (Nhẹ 1.8B). "), text)
        self.assertEqual(llm_summary("refine", "LLM lỗi: server đã dừng", models=models, server_found=True)[0], "error")
        state, text = llm_summary("refine", "LLM: chưa chạy", models=models, server_found=True)
        self.assertEqual(state, "installed")
        self.assertIn("Nhẹ 1.8B", text)
        self.assertIn("-m tools.setup_llm", setup_command())
        g = self.guide(llm_models=[], llm_server=False)
        self.assertEqual(g.llm_state, "missing")
        self.assertFalse(g.command_label.isHidden())
        g.set_llm_mode("off")
        g.set_llm_status("")
        self.assertEqual(g.llm_state, "off")
        self.assertTrue(g.command_label.isHidden())


# --- tray entries (offscreen) ---------------------------------------------------------------------

class TrayEntryTests(unittest.TestCase):
    def setUp(self):
        qapp()
        self.addCleanup(flush_deletes)
        from app.ui.tray import TrayController
        self.tray = TrayController(Config())
        for m in (self.tray.menu(), self.tray._lang_menu, self.tray._guard_menu, self.tray._profile_menu,
                  self.tray._llm_menu):
            self.addCleanup(m.deleteLater)
        self.addCleanup(self.tray.deleteLater)

    def test_entries_and_signals(self):
        texts = self.tray.action_texts()
        i = texts.index("Cài đặt…")
        self.assertEqual(texts[i:i + 4], ["Cài đặt…", "Hướng dẫn nhanh…", "Mở thư mục dữ liệu", "Thoát\tCtrl+Alt+Q"])
        self.assertIn("Chọn vùng dịch\tCtrl+Alt+R", texts)
        got: list[str] = []
        self.tray.open_settings.connect(lambda: got.append("settings"))
        self.tray.open_guide.connect(lambda: got.append("guide"))
        self.tray._settings_action.trigger()
        self.tray._guide_action.trigger()
        self.assertEqual(got, ["settings", "guide"])

    def test_icon_pixmap(self):
        from app.ui.tray import icon_pixmap, make_icon
        for size in (16, 48, 256):
            pm = icon_pixmap(size)
            self.assertEqual((pm.width(), pm.height()), (size, size))
        self.assertGreaterEqual(len(make_icon().availableSizes()), 7)


# --- App wiring (fake pipeline, like tests.test_app) ------------------------------------------------

from tests import test_app as _ta  # noqa: E402  (its AppTestBase: App + fake pipeline + fake llama-server)


class FakeHotkeys:
    """HotkeyManager stand-in: `taken` combos are held by "another app"."""

    def __init__(self, taken=()) -> None:
        self.taken = set(taken)
        self.reg: dict[str, str] = {}
        self.errors: dict[str, str] = {}
        self.log: list[tuple] = []

    def register(self, name: str, combo: str) -> bool:
        self.log.append(("register", name, combo))
        self.reg.pop(name, None)
        if combo in self.taken or combo in self.reg.values():
            self.errors[name] = f"{combo} đang được ứng dụng khác sử dụng"
            return False
        self.reg[name] = combo
        self.errors.pop(name, None)
        return True

    def unregister(self, name: str) -> None:
        self.log.append(("unregister", name))
        self.reg.pop(name, None)

    def unregister_all(self) -> None:
        self.log.append(("unregister_all",))
        self.reg.clear()

    def registered(self) -> dict[str, str]:
        return dict(self.reg)

    def last_errors(self) -> dict[str, str]:
        return dict(self.errors)

    def close(self) -> None:
        self.reg.clear()


class AppSettingsTests(_ta.AppTestBase):
    def setUp(self):
        super().setUp()
        self.addCleanup(flush_deletes)

    def new(self, **changes) -> Config:
        cfg = copy.deepcopy(self.app._persisted)
        for k, v in changes.items():
            setattr(cfg, k, v)
        return cfg

    def test_live_apply(self):
        app = self.app
        res = app.apply_settings(self.new(font_family="Tahoma", min_font_px=16, box_alpha=128, show_border=False,
                                          llm_history_lines=3, llm_auto_terms=False, poll_ms=300,
                                          blocked_processes=["x.exe"], llm_release_after_guard_s=5))
        self.assertEqual((res.restart, res.errors), ([], []))
        c = app.config
        self.assertEqual((c.font_family, c.min_font_px, c.box_alpha, c.show_border), ("Tahoma", 16, 128, False))
        self.assertEqual((c.llm_history_lines, c.llm_auto_terms, c.blocked_processes), (3, False, ["x.exe"]))
        self.assertIs(app.overlay._config, c)  # the overlay paints from this object: style applies on repaint
        self.assertIn(("set_poll_ms", 300), self.fake.calls)
        saved = self.saved_config()
        self.assertEqual((saved["font_family"], saved["box_alpha"], saved["poll_ms"], saved["blocked_processes"]),
                         ("Tahoma", 128, 300, ["x.exe"]))
        self.assertEqual(saved["region"], [1, 2, 3, 4])  # the persisted region, not the --region override
        self.assertEqual(res.config.font_family, "Tahoma")

    def test_llm_and_language_and_guard(self):
        app = self.app
        app.apply_settings(self.new(llm_mode="off", src_lang="en", guard_mode="warn", llm_model="light"))
        self.assertIn(("set_llm_mode", "off"), self.fake.calls)
        self.assertIn(("set_src_lang", "en"), self.fake.calls)
        self.assertFalse(app.llm.wanted)
        self.assertEqual((app.config.llm_model, app.config.guard_mode), ("light", "warn"))
        self.assertIsNotNone(app.guard)
        self.assertTrue(app.tray._model_actions["light"].isChecked())
        saved = self.saved_config()
        self.assertEqual((saved["llm_mode"], saved["src_lang"], saved["guard_mode"], saved["llm_model"]),
                         ("off", "en", "warn", "light"))
        app.apply_settings(self.new(llm_model="My-Model.gguf"))
        self.assertEqual(app.config.llm_model, "My-Model.gguf")
        self.assertFalse(any(a.isChecked() for a in app.tray._model_actions.values()))

    def test_restart_fields(self):
        app = self.app
        res = app.apply_settings(self.new(ocr_device="cpu", capture_backend="mss"))
        self.assertEqual(res.restart, ["Thiết bị OCR", "Cách chụp màn hình"])
        self.assertEqual((app.config.ocr_device, app.config.capture_backend), ("cuda", "auto"))  # this run keeps them
        self.assertEqual((self.saved_config()["ocr_device"], self.saved_config()["capture_backend"]), ("cpu", "mss"))
        res = app.apply_settings(self.new(ocr_device="cuda"))  # back to what is running: only the capture left
        self.assertEqual(res.restart, ["Cách chụp màn hình"])
        res = app.apply_settings(self.new(min_font_px=21))  # any later save still names it
        self.assertEqual(res.restart, ["Cách chụp màn hình"])
        res = app.apply_settings(self.new(capture_backend="auto"))
        self.assertEqual(res.restart, [])

    def test_tray_and_hotkey_changes_survive_a_settings_save(self):
        """The window stays open while the user plays: Lưu must not put back what the tray / hotkeys changed."""
        app = self.app
        app.open_settings()
        dlg = app._settings
        app._on_hotkey("switch_lang")  # ja -> en while the window is open
        app.set_llm_mode("off")  # tray
        app.set_guard_mode("warn")  # tray
        self.assertEqual(dlg.lang_combo.currentData(), "en")
        dlg.min_font_spin.setValue(19)  # the only change made in the window
        self.assertTrue(dlg.save(), dlg.status_label.text())
        self.assertEqual((app.config.src_lang, app.config.llm_mode, app.config.guard_mode, app.config.min_font_px),
                         ("en", "off", "warn", 19))
        self.assertEqual(self.calls("set_src_lang"), [("set_src_lang", "en")])  # not set back to "ja"
        self.assertFalse(app.llm.wanted)
        saved = self.saved_config()
        self.assertEqual((saved["src_lang"], saved["llm_mode"], saved["guard_mode"]), ("en", "off", "warn"))
        dlg.ask_before_discard = False
        dlg.close()

    def test_hotkeys_without_manager_are_only_saved(self):
        res = self.app.apply_settings(self.new(hotkeys=Hotkeys(select_region="ctrl+alt+m")))
        self.assertEqual(res.errors, [])
        self.assertEqual(self.saved_config()["hotkeys"]["select_region"], "ctrl+alt+m")

    def test_settings_window_from_tray(self):
        app = self.app
        app.tray._settings_action.trigger()
        dlg = app._settings
        self.assertIsNotNone(dlg)
        self.assertTrue(dlg.isVisible())
        self.assertEqual(dlg.saved_config().region, [1, 2, 3, 4])  # edits the saved settings
        app.open_settings("guard")
        self.assertIs(app._settings, dlg)  # one window
        self.assertEqual(dlg.current_tab(), "guard")
        dlg.min_font_spin.setValue(20)
        self.assertTrue(dlg.save())
        self.assertEqual(app.config.min_font_px, 20)
        dlg.show_guide_requested.emit()
        self.assertIsNotNone(app._guide)
        self.assertEqual(app.instance_status()["settings_open"], True)
        dlg.ask_before_discard = False
        dlg.close()
        flush_deletes()
        self.assertIsNone(app._settings)

    def test_first_run_flag(self):
        app = self.app
        self.assertFalse(app._persisted.first_run_done)
        app.tray._guide_action.trigger()
        guide = app._guide
        self.assertTrue(guide.isVisible())
        guide.lang_buttons["en"].setChecked(True)
        self.assertEqual(app.config.src_lang, "en")
        guide.set_profile.emit("default")
        app.select_region()
        app.selector.simulate_press(100, 100)
        app.selector.simulate_release(500, 300)
        self.assertIn("400 × 200 px", guide.region_label.text())
        guide.skip()
        self.assertTrue(self.saved_config()["first_run_done"])
        self.assertTrue(app.config.first_run_done)

    def test_guide_closed_by_quitting_is_not_seen(self):
        self.app.show_guide()
        self.app.shutdown()
        self.assertFalse(self.saved_config().get("first_run_done", False))

    def test_instance_commands(self):
        app = self.app
        app.on_instance_command("settings")
        self.assertIsNotNone(app._settings)
        app.on_instance_command("guide")
        self.assertIsNotNone(app._guide)
        with mock.patch.object(app.tray, "popup_menu") as popup, mock.patch.object(app.tray, "show_message") as msg:
            app.on_instance_command("show")
        popup.assert_called_once()
        self.assertIn("khay hệ thống", msg.call_args[0][1])
        st = app.instance_status()
        for key in ("pid", "exe", "console", "tray_visible", "tray_available", "profile", "llm", "guide_open"):
            self.assertIn(key, st)
        self.assertEqual(st["pid"], os.getpid())
        json.dumps(st)  # the "ping" reply is JSON
        with mock.patch.object(app, "quit") as quit_:
            app.on_instance_command("quit")
            self.assertTrue(pump_until(lambda: quit_.called, 3.0))  # after the reply has gone out
        quit_.assert_called_once()

    def test_restart(self):
        from app.main import restart_command
        cmd = restart_command(["C:\\Python312\\pythonw.exe", "C:\\st\\run_app.pyw", "--lang", "en",
                               "--wait-for-instance=5"], executable="C:\\venv\\Scripts\\pythonw.exe")
        self.assertEqual(cmd, ["C:\\venv\\Scripts\\pythonw.exe", "C:\\st\\run_app.pyw", "--wait-for-instance", "30"])
        # the restart applies what Settings saved: overrides of Settings fields and of this run only are dropped
        cmd = restart_command(["py.exe", "-m", "app", "--capture", "mss", "--device=cpu", "--exit-after", "60",
                               "--first-run", "--no-llm", "--profile", "Blue Archive", "--no-hotkeys",
                               "--region=-1920,0,800,600"], executable="C:\\venv\\Scripts\\python.exe")
        self.assertEqual(cmd, ["C:\\venv\\Scripts\\python.exe", "-m", "app", "--profile", "Blue Archive", "--no-hotkeys",
                               "--region=-1920,0,800,600", "--wait-for-instance", "30"])
        with mock.patch("app.main.subprocess.Popen") as popen, mock.patch.object(self.app, "quit") as quit_:
            self.app.restart()
        self.assertEqual(popen.call_args[0][0][-2:], ["--wait-for-instance", "30"])
        self.assertEqual(popen.call_args[1]["cwd"], str(PROJECT_ROOT))
        quit_.assert_called_once()
        with mock.patch("app.main.subprocess.Popen", side_effect=OSError("nope")), \
                mock.patch.object(self.app, "quit") as quit_, mock.patch.object(self.app.tray, "show_message") as msg:
            self.app.restart()
        quit_.assert_not_called()
        self.assertEqual(msg.call_args[0][0], "Không khởi động lại được")

    def test_report_startup_error(self):
        from app import main as app_main
        with mock.patch.object(app_main, "show_error_box") as box, self.assertLogs("app", "CRITICAL") as logs:
            app_main.report_startup_error(RuntimeError("dxcam hỏng"), Path("C:/x/app.log"), show=True)
        self.assertIn("startup failed", logs.output[0])
        title, text, details = box.call_args[0]
        self.assertEqual(text, "Screen Translator không khởi động được.")
        self.assertIn("dxcam hỏng", details)
        self.assertIn("Chi tiết trong nhật ký", details)
        with mock.patch.object(app_main, "show_error_box") as box, self.assertLogs("app", "CRITICAL"):
            app_main.report_startup_error(RuntimeError("x"), None, show=False)
        box.assert_not_called()


class AppHotkeyApplyTests(_ta.AppTestBase):
    def setUp(self):
        super().setUp()
        self.addCleanup(flush_deletes)
        self.app.args.no_hotkeys = False

    def test_reregister_refuse_and_swap(self):
        app = self.app
        hk = FakeHotkeys(taken={"ctrl+alt+m"})
        app.hotkeys = hk
        app._register_all_hotkeys()
        self.assertEqual(hk.registered()["select_region"], "ctrl+alt+r")
        new = copy.deepcopy(app._persisted)
        new.hotkeys.toggle_pause = "ctrl+alt+p"  # free: re-registered
        new.hotkeys.select_region = "ctrl+alt+m"  # held by another app: refused, the old one stays
        res = app.apply_settings(new)
        self.assertEqual(hk.registered()["toggle_pause"], "ctrl+alt+p")
        self.assertEqual(hk.registered()["select_region"], "ctrl+alt+r")
        self.assertEqual(len(res.errors), 1)
        self.assertIn("Chọn vùng dịch: ctrl+alt+m đang được ứng dụng khác sử dụng", res.errors[0])
        self.assertEqual(res.config.hotkeys.select_region, "ctrl+alt+r")
        saved = self.saved_config()["hotkeys"]
        self.assertEqual((saved["select_region"], saved["toggle_pause"]), ("ctrl+alt+r", "ctrl+alt+p"))
        self.assertIn("Tạm dừng\tCtrl+Alt+P", app.tray.action_texts())
        swap = copy.deepcopy(app._persisted)  # two actions trade combos
        swap.hotkeys.select_region, swap.hotkeys.quit = "ctrl+alt+q", "ctrl+alt+r"
        self.assertEqual(app.apply_settings(swap).errors, [])
        self.assertEqual((hk.registered()["select_region"], hk.registered()["quit"]), ("ctrl+alt+q", "ctrl+alt+r"))

    def test_refused_combo_never_leaves_a_duplicate(self):
        """select_region takes toggle_pause's old combo while toggle_pause's new one is refused: toggle_pause
        goes back to its old combo, so select_region goes back too (no combo saved twice, none lost)."""
        app = self.app
        hk = FakeHotkeys(taken={"ctrl+alt+x"})
        app.hotkeys = hk
        app._register_all_hotkeys()
        new = copy.deepcopy(app._persisted)
        new.hotkeys.select_region, new.hotkeys.toggle_pause = "ctrl+alt+t", "ctrl+alt+x"
        res = app.apply_settings(new)
        self.assertEqual((hk.registered()["select_region"], hk.registered()["toggle_pause"]), ("ctrl+alt+r", "ctrl+alt+t"))
        saved = self.saved_config()["hotkeys"]
        self.assertEqual((saved["select_region"], saved["toggle_pause"]), ("ctrl+alt+r", "ctrl+alt+t"))
        self.assertEqual(len(res.errors), 2, res.errors)
        self.assertIn("Tạm dừng / tiếp tục: ctrl+alt+x đang được ứng dụng khác sử dụng", res.errors[0])
        self.assertIn("Chọn vùng dịch: giữ phím cũ", res.errors[1])
        combos = [c for c in saved.values() if c]
        self.assertEqual(len(combos), len(set(combos)))

    def test_suspend_while_recording(self):
        app = self.app
        hk = FakeHotkeys()
        app.hotkeys = hk
        app._register_all_hotkeys()
        app.open_settings("hotkeys")
        dlg = app._settings
        dlg.hotkey_edits["quit"].start_capture()  # what clicking into the field does
        self.assertEqual(hk.registered(), {})  # Ctrl+Alt+R now reaches the field, not the region selector
        dlg.hotkey_edits["quit"].stop_capture()
        self.assertEqual(len(hk.registered()), 5)
        dlg.hotkey_edits["quit"].start_capture()
        dlg.ask_before_discard = False
        dlg.close()  # closing the window ends the recording
        self.assertEqual(len(hk.registered()), 5)

    def test_real_hotkey_manager(self):
        """RegisterHotKey for real, with F21 / F22 combos nothing uses."""
        from app.ui.hotkeys import HotkeyManager
        app = self.app
        app.config.hotkeys = Hotkeys(HK_A, "", "", "", "")
        app._persisted.hotkeys = copy.deepcopy(app.config.hotkeys)
        app.hotkeys = HotkeyManager(app)
        other = HotkeyManager()
        self.addCleanup(other.close)
        self.assertEqual(app._register_all_hotkeys(), {})
        self.assertEqual(app.hotkeys.registered(), {"select_region": HK_A})
        new = copy.deepcopy(app._persisted)
        new.hotkeys.select_region = HK_B
        self.assertEqual(app.apply_settings(new).errors, [])
        self.assertEqual(app.hotkeys.registered(), {"select_region": HK_B})
        self.assertTrue(other.register("x", HK_A))  # A is free again
        back = copy.deepcopy(app._persisted)
        back.hotkeys.select_region = HK_A  # now held by "another app"
        res = app.apply_settings(back)
        self.assertEqual(len(res.errors), 1)
        self.assertIn("Chọn vùng dịch:", res.errors[0])
        self.assertEqual(app.hotkeys.registered(), {"select_region": HK_B})  # the old one keeps working
        self.assertEqual(self.saved_config()["hotkeys"]["select_region"], HK_B)


# --- single instance --------------------------------------------------------------------------------

class SingleInstanceTests(unittest.TestCase):
    def setUp(self):
        qapp()
        self.addCleanup(flush_deletes)
        from app.single_instance import instance_key
        self.key = instance_key(Path(tempfile.gettempdir()) / f"st-test-{os.getpid()}-{time.monotonic_ns()}")

    def test_key(self):
        from app.single_instance import instance_key
        self.assertEqual(instance_key("C:\\Data\\X"), instance_key("c:/data/x"))
        self.assertNotEqual(instance_key("C:\\Data\\X"), instance_key("C:\\Data\\Y"))
        self.assertTrue(instance_key("C:\\Data").startswith("ScreenTranslator-"))

    def test_lock(self):
        from app.single_instance import InstanceLock
        first, second = InstanceLock(self.key), InstanceLock(self.key)
        self.addCleanup(first.release)
        self.addCleanup(second.release)
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        self.assertFalse(second.held)
        threading.Timer(0.3, first.release).start()  # the old instance exits (a restart)
        t0 = time.monotonic()
        self.assertTrue(second.acquire(wait_s=5.0))
        self.assertLess(time.monotonic() - t0, 3.0)
        with InstanceLock(self.key) as third:
            self.assertFalse(third.acquire())

    def test_socket_commands(self):
        from app.single_instance import InstanceServer, send_command
        server = InstanceServer(self.key, status=lambda: {"pid": 1, "tray_visible": True})
        self.addCleanup(server.deleteLater)
        self.addCleanup(server.close)
        self.assertTrue(server.listen())
        got: list[str] = []
        server.command.connect(got.append)
        replies: dict[str, str | None] = {}

        def client():  # blocking client on its own thread; the server needs this thread's event loop
            for cmd in ("ping", "show", "QUIT", "rm -rf"):
                replies[cmd] = send_command(self.key, cmd, timeout_s=3.0)

        t = threading.Thread(target=client)
        t.start()
        self.assertTrue(pump_until(lambda: not t.is_alive(), 15.0))
        self.assertEqual(json.loads(replies["ping"]), {"pid": 1, "tray_visible": True})
        self.assertEqual((replies["show"], replies["QUIT"], replies["rm -rf"]), ("ok", "ok", "error"))
        pump(30)
        self.assertEqual(got, ["ping", "show", "quit"])
        server.close()
        nobody: list = []
        t = threading.Thread(target=lambda: nobody.append(send_command(self.key, "ping", timeout_s=0.5)))
        t.start()
        self.assertTrue(pump_until(lambda: not t.is_alive(), 5.0))
        self.assertEqual(nobody, [None])

    def _hand_over_in_thread(self, lock, **kw):
        from app import main as app_main
        out: list = []
        t = threading.Thread(target=lambda: out.append(app_main._hand_over(lock, False, **kw)))
        t.start()
        return t, out

    def test_second_launch_waits_for_a_slow_start(self):
        """The first instance holds the lock but its socket comes seconds later (cold disk at sign-in): the
        second launch keeps asking instead of reporting a hang."""
        from app.single_instance import InstanceLock, InstanceServer, pipe_exists
        first, second = InstanceLock(self.key), InstanceLock(self.key)
        self.addCleanup(first.release)
        self.addCleanup(second.release)
        self.assertTrue(first.acquire())
        self.assertFalse(pipe_exists(self.key))
        t, out = self._hand_over_in_thread(second, wait_s=20)
        pump(2500)  # the first one is still "starting"
        self.assertTrue(t.is_alive())
        server = InstanceServer(self.key)
        self.addCleanup(server.deleteLater)
        self.addCleanup(server.close)
        got: list[str] = []
        server.command.connect(got.append)
        self.assertTrue(server.listen())
        self.assertTrue(pipe_exists(self.key))
        self.assertTrue(pump_until(lambda: not t.is_alive(), 15.0))
        self.assertEqual(out, [0])
        pump(30)
        self.assertEqual(got, ["show"])
        self.assertFalse(second.held)

    def test_second_launch_starts_when_the_first_has_exited(self):
        from app.single_instance import InstanceLock
        first, second = InstanceLock(self.key), InstanceLock(self.key)
        self.addCleanup(first.release)
        self.addCleanup(second.release)
        self.assertTrue(first.acquire())
        t, out = self._hand_over_in_thread(second, wait_s=20)
        threading.Timer(1.0, first.release).start()  # the old one was just quitting
        self.assertTrue(pump_until(lambda: not t.is_alive(), 15.0))
        self.assertEqual(out, [None])  # main() goes on with a normal start
        self.assertTrue(second.held)

    def test_hung_instance_is_reported(self):
        """A socket that exists but never answers (the running app is hung): the Vietnamese box."""
        from app import main as app_main
        from app.single_instance import InstanceLock, InstanceServer
        first, second = InstanceLock(self.key), InstanceLock(self.key)
        self.addCleanup(first.release)
        self.addCleanup(second.release)
        self.assertTrue(first.acquire())
        server = InstanceServer(self.key)
        self.addCleanup(server.deleteLater)
        self.addCleanup(server.close)
        self.assertTrue(server.listen())
        t0 = time.monotonic()
        with mock.patch.object(app_main, "HANDOVER_HUNG_TRIES", 1), \
                mock.patch.object(app_main, "show_error_box") as box:
            rc = app_main._hand_over(second, True, wait_s=30)  # this thread does not run the server's events
        self.assertEqual(rc, 1)
        self.assertLess(time.monotonic() - t0, 15)  # not the whole 30 s: a pipe that never answers is a hang
        self.assertIn("không phản hồi", box.call_args[0][1])
        self.assertNotIn("đã đang", box.call_args[0][1])


# --- shortcuts (temporary folders only) ---------------------------------------------------------------

class ShortcutTests(unittest.TestCase):
    def setUp(self):
        qapp()
        from tools import install_shortcut as sc
        self.sc = sc
        self.dir = Path(tempfile.mkdtemp(prefix="st-lnk-"))  # %TEMP%: short enough for IShellLink
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.icon = self.dir / "app.ico"

    def test_ico_file(self):
        self.assertTrue(self.sc.write_icon(self.icon))
        data = self.icon.read_bytes()
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        self.assertEqual((reserved, kind, count), (0, 1, len(self.sc.ICO_SIZES)))
        sizes = []
        for i in range(count):
            w, h, _c, _r, planes, bits, size, offset = struct.unpack("<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
            sizes.append(w or 256)
            self.assertEqual((w, planes, bits), (h, 1, 32))
            self.assertEqual(data[offset:offset + 8], b"\x89PNG\r\n\x1a\n")
            self.assertLessEqual(offset + size, len(data))
        self.assertEqual(sizes, list(self.sc.ICO_SIZES))
        self.assertFalse(self.sc.write_icon(self.icon))  # same drawing: file untouched

    def test_install_read_back_idempotent_remove(self):
        sc = self.sc
        for loc in ("desktop", "menu", "startup"):
            folder = self.dir / loc
            path, status = sc.install(loc, folder=folder, icon=self.icon)
            self.assertEqual(status, "created")
            self.assertEqual(path, folder / "Screen Translator.lnk")
            info = sc.read_shortcut(path)
            self.assertEqual(Path(info.target).name.lower(), "pythonw.exe")
            self.assertTrue(Path(info.target).is_file())
            self.assertEqual(info.arguments, f'"{PROJECT_ROOT / "run_app.pyw"}"')
            self.assertEqual(Path(info.working_dir), PROJECT_ROOT)
            self.assertEqual((Path(info.icon), info.icon_index), (self.icon, 0))
            self.assertIn("Nhật / Anh → Việt", info.description)
            self.assertEqual(info.app_id, "ScreenTranslator.App")  # the taskbar groups the app's windows under it
            self.assertTrue(info.runs_this_app())
            self.assertEqual(sc.install(loc, folder=folder, icon=self.icon)[1], "unchanged")
            self.assertTrue(sc.is_installed(loc, folder=folder))
        self.assertTrue(self.icon.is_file())
        self.assertTrue(sc.startup_enabled(self.dir / "startup"))
        sc.set_startup(False, self.dir / "startup")
        self.assertFalse(sc.startup_enabled(self.dir / "startup"))
        self.assertFalse(sc.remove("startup", folder=self.dir / "startup"))  # already gone
        sc.set_startup(True, self.dir / "startup")
        self.assertTrue(sc.startup_enabled(self.dir / "startup"))
        # someone else's shortcut with the same name is left alone
        foreign = sc.ShortcutInfo("C:\\Windows\\notepad.exe", "", "C:\\", "", 0, "x")
        sc.write_shortcut(self.dir / "desktop" / "Screen Translator.lnk", foreign)
        self.assertFalse(sc.is_installed("desktop", folder=self.dir / "desktop"))
        self.assertFalse(sc.remove("desktop", folder=self.dir / "desktop"))
        self.assertEqual(sc.install("desktop", folder=self.dir / "desktop", icon=self.icon)[1], "updated")
        self.assertTrue(sc.remove("desktop", folder=self.dir / "desktop"))

    def test_cli(self):
        import contextlib
        import io
        args = [f"--{loc}-dir={self.dir / loc}" for loc in ("desktop", "menu", "startup")] + [f"--icon={self.icon}"]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.sc.main(args), 0)
        text = out.getvalue()
        self.assertIn("Desktop: đã tạo", text)
        self.assertIn("Start Menu: đã tạo", text)
        self.assertNotIn("Startup", text.split("Shortcut trỏ")[0])  # only with --startup
        self.assertFalse((self.dir / "startup").exists())
        self.assertIn("đã vẽ", text)  # no icon yet: drawn
        self.icon.write_bytes(b"kept as it is")  # the committed assets/app.ico is not redrawn on every run
        with contextlib.redirect_stdout(io.StringIO()) as out2:
            self.assertEqual(self.sc.main([*args, "--startup"]), 0)
        self.assertIn("Desktop: đã có, không đổi", out2.getvalue())
        self.assertIn("Startup (khởi động cùng Windows): đã tạo", out2.getvalue())
        self.assertEqual(self.icon.read_bytes(), b"kept as it is")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.sc.main([*args, "--redraw-icon"]), 0)
        self.assertEqual(self.icon.read_bytes()[:4], b"\x00\x00\x01\x00")  # an .ico again
        with contextlib.redirect_stdout(io.StringIO()) as out3:
            self.assertEqual(self.sc.main([*args, "--check"]), 0)
        self.assertIn("run_app.pyw", out3.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as out4:
            self.assertEqual(self.sc.main([*args, "--remove", "--startup"]), 0)
        self.assertEqual(out4.getvalue().count("đã gỡ"), 3)
        self.assertEqual(list(self.dir.glob("*/*.lnk")), [])

    def test_install_reads_back_what_windows_stored(self):
        """IShellLink silently cuts a working directory past 259 characters: install() must not report
        success for such a link (the Settings checkbox would keep a broken Startup shortcut)."""
        sc = self.sc
        long_dir = self.dir / ("x" * 120) / ("y" * 120) / "z"
        with mock.patch.object(sc, "PROJECT_ROOT", long_dir):
            with self.assertRaises(OSError) as ctx:
                sc.install("startup", folder=self.dir / "startup", icon=self.icon)
        self.assertIn("đọc lại không khớp (", str(ctx.exception))
        self.assertIn("working_dir", str(ctx.exception))
        self.assertIn("260 ký tự", str(ctx.exception))
        st = __import__("app.ui.settings_dialog", fromlist=["ShortcutStartup"]).ShortcutStartup()
        with mock.patch.object(sc, "known_folder", lambda loc: self.dir / "startup2"), \
                mock.patch.object(sc, "ICON_PATH", self.icon), mock.patch.object(sc, "PROJECT_ROOT", long_dir):
            with self.assertRaises(OSError):
                st.set_enabled(True)  # what the Settings window reports as "Không đổi được ..."

    def test_moved_startup_shortcut_is_updated_too(self):
        """After the project folder moved, re-creating the shortcuts also fixes an existing Startup one."""
        import contextlib
        import io
        sc = self.sc
        old = sc.wanted(icon=self.icon)
        moved = sc.ShortcutInfo(old.target, '"D:\\Cũ\\screen-translator\\run_app.pyw"', "D:\\Cũ\\screen-translator",
                                old.icon, 0, old.description, old.app_id)
        sc.write_shortcut(self.dir / "startup" / "Screen Translator.lnk", moved)
        args = [f"--{loc}-dir={self.dir / loc}" for loc in ("desktop", "menu", "startup")] + [f"--icon={self.icon}"]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(sc.main(args), 0)
        self.assertIn("Startup (khởi động cùng Windows): đã cập nhật", out.getvalue())
        self.assertTrue(sc.read_shortcut(self.dir / "startup" / "Screen Translator.lnk").runs_this_app())

    def test_known_folders(self):
        for loc in ("desktop", "menu", "startup"):
            self.assertTrue(self.sc.known_folder(loc).is_absolute())
        self.assertEqual(self.sc.known_folder("startup").name, "Startup")

    def test_settings_startup_adapter(self):
        from app.ui.settings_dialog import ShortcutStartup
        folder = self.dir / "startup"
        with mock.patch.object(self.sc, "known_folder", lambda loc: folder), \
                mock.patch.object(self.sc, "ICON_PATH", self.icon):
            st = ShortcutStartup()
            self.assertFalse(st.enabled())
            st.set_enabled(True)
            self.assertTrue(st.enabled())
            self.assertTrue((folder / "Screen Translator.lnk").is_file())
            st.set_enabled(False)
            self.assertFalse(st.enabled())


# --- run_app.pyw (imported, not run) ------------------------------------------------------------------

def load_run_app():
    path = PROJECT_ROOT / "run_app.pyw"
    loader = importlib.machinery.SourceFileLoader("run_app_under_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class RunAppTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_run_app()
        self.home = Path(tempfile.mkdtemp(prefix="st-runapp-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        import logging
        self.addCleanup(logging.captureWarnings, False)  # run() routes warnings to the log

    def test_import_failure_is_logged_and_shown(self):
        boxes: list[tuple[str, str]] = []
        with mock.patch.dict(os.environ, {"SCREEN_TRANSLATOR_HOME": str(self.home)}), \
                mock.patch.dict(sys.modules, {"app.main": None}), \
                mock.patch.object(self.mod, "_message_box", lambda t, x: boxes.append((t, x))):
            self.assertEqual(self.mod.run([]), 1)
        self.assertEqual(os.getcwd(), str(PROJECT_ROOT))  # runs from the project folder
        log = (self.home / "logs" / "startup-error.log").read_text(encoding="utf-8")
        self.assertIn("không khởi động được", log)
        self.assertIn("import of app.main halted", log)  # the traceback
        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0][0], "Screen Translator: lỗi khởi động")
        self.assertIn("thiếu hoặc hỏng thư viện", boxes[0][1])
        self.assertIn(str(self.home), boxes[0][1])

    def test_main_called_windowless(self):
        calls: list = []
        fake = mock.MagicMock(main=lambda argv, windowless: calls.append((argv, windowless)) or 0)
        with mock.patch.dict(sys.modules, {"app.main": fake}):
            self.assertEqual(self.mod.run(["--lang", "en"]), 0)
        self.assertEqual(calls, [(["--lang", "en"], True)])


class LaunchPolishTests(unittest.TestCase):
    """Window icon / taskbar id, Qt's own texts in Vietnamese, the error box's buttons, the crash log, the
    tray's left click, the guide's Enter key."""

    def setUp(self):
        qapp()
        self.addCleanup(flush_deletes)

    def test_qt_texts_are_vietnamese(self):
        from PySide6.QtWidgets import QDialogButtonBox, QLineEdit, QPlainTextEdit
        from app.ui import qt_vi
        qt_vi.install(QApplication.instance())
        self.assertIs(qt_vi.install(), qt_vi.install())  # once
        edit, plain = QLineEdit(), QPlainTextEdit()
        self.addCleanup(edit.deleteLater)
        self.addCleanup(plain.deleteLater)
        for w in (edit, plain):
            menu = w.createStandardContextMenu()
            texts = [a.text().split("\t")[0] for a in menu.actions() if a.text()]
            menu.deleteLater()
            self.assertEqual(texts[:6], ["&Hoàn tác", "&Làm lại", "Cắ&t", "&Sao chép", "&Dán", "Xóa"], texts)
            self.assertIn("Chọn tất cả", texts)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.addCleanup(bb.deleteLater)
        self.assertEqual(sorted(b.text() for b in bb.buttons()), ["Hủy", "OK"])
        self.assertEqual(QCoreApplication.translate("Khác", "Hello"), "Hello")  # anything else unchanged

    def test_error_box_buttons(self):
        from PySide6.QtCore import QTimer
        from app import main as app_main
        seen: list = []

        def click(text: str):
            for w in QApplication.topLevelWidgets():
                if isinstance(w, QMessageBox) and w.isVisible():
                    seen.append(sorted(b.text() for b in w.buttons()))
                    next(b for b in w.buttons() if b.text() == text).click()
                    return
            QTimer.singleShot(20, lambda: click(text))

        QTimer.singleShot(20, lambda: click("Sao chép chi tiết"))
        self.assertEqual(app_main.show_error_box("Screen Translator: lỗi", "Không khởi động được.", "dxcam hỏng"),
                         "copy")
        self.assertEqual(seen, [["Mở thư mục nhật ký", "Sao chép chi tiết", "Đóng"]])
        self.assertIn("dxcam hỏng", QGuiApplication.clipboard().text())
        QTimer.singleShot(20, lambda: click("Đóng"))
        self.assertEqual(app_main.show_error_box("t", "x"), "close")

    def test_crash_log_of_the_last_run_is_kept(self):
        import faulthandler
        from app import main as app_main
        tmp = Path(tempfile.mkdtemp(prefix="st-crash-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "crash.log").write_text("Windows fatal exception: access violation\n", encoding="utf-8")
        was = faulthandler.is_enabled()
        with mock.patch.object(app_main, "LOG_DIR", tmp):
            app_main._enable_crash_log()
        try:
            self.assertIn("access violation", (tmp / "crash.log.1").read_text(encoding="utf-8"))
            self.assertEqual((tmp / "crash.log").read_text(encoding="utf-8"), "")
        finally:
            faulthandler.disable()
            app_main._enable_crash_log.file.close()
            if was:
                faulthandler.enable()

    def test_window_icon_and_taskbar_id(self):
        from app.winutil import APP_USER_MODEL_ID, set_app_user_model_id
        src = (PROJECT_ROOT / "app" / "main.py").read_text(encoding="utf-8")
        main_src = src[src.index("def main("):]
        self.assertIn("qapp.setWindowIcon(make_icon())", main_src)
        self.assertLess(main_src.index("set_app_user_model_id()  #"), main_src.index("qapp = QApplication.instance()"))
        self.assertEqual(APP_USER_MODEL_ID, "ScreenTranslator.App")
        self.assertTrue(callable(set_app_user_model_id))

    def test_tray_left_click_opens_the_menu(self):
        from PySide6.QtWidgets import QSystemTrayIcon
        from app.ui.tray import TrayController
        tray = TrayController(Config())
        for m in (tray.menu(), tray._lang_menu, tray._guard_menu, tray._profile_menu, tray._llm_menu):
            self.addCleanup(m.deleteLater)
        self.addCleanup(tray.deleteLater)
        got: list[str] = []
        tray.select_region.connect(lambda: got.append("region"))
        tray._on_activated(QSystemTrayIcon.ActivationReason.Trigger)  # a left click: the menu, a moment later
        self.assertTrue(tray._click_timer.isActive())
        self.assertGreater(tray._click_timer.interval(), QApplication.doubleClickInterval())
        tray._on_activated(QSystemTrayIcon.ActivationReason.DoubleClick)  # ...unless it was a double click
        self.assertFalse(tray._click_timer.isActive())
        self.assertEqual(got, ["region"])

    def test_guide_enter_goes_to_the_next_step(self):
        from PySide6.QtWidgets import QPushButton
        from app.ui.first_run import FirstRunGuide
        g = FirstRunGuide(Config(), profiles=["default"], llm_models=[], llm_server=False)
        self.addCleanup(g.deleteLater)
        done: list[bool] = []
        g.dismissed.connect(done.append)
        self.assertTrue(g.next_button.isDefault())
        self.assertFalse(any(b.autoDefault() for b in g.findChildren(QPushButton)))
        g.show()
        pump(20)
        press(g, Qt.Key.Key_Return)
        self.assertEqual(g.step(), 1)
        self.assertEqual(done, [])  # Enter on the first screen does not skip the guide any more
        g.go(3)
        g._on_select_region()
        g.region_finished([0, 0, 640, 180])
        press(g, Qt.Key.Key_Return)
        self.assertEqual(g.step(), 4)  # after choosing the region, Enter goes on (not "Quay lại")
        self.assertIn("Đã chọn vùng (640 × 180 px)", g.region_label.text())
        self.assertNotIn("góc trên trái", g.region_label.text())
        g.close_quietly()


# --- native (windows platform only) -------------------------------------------------------------------

class NativeSettingsTests(_DialogFixture):
    def setUp(self):
        if not NATIVE or not on_windows_platform():
            self.skipTest("needs the windows platform: run via WindowsPlatformSubprocessTests")
        super().setUp()

    def test_windows_and_popups_capture_excluded(self):
        from app.overlay.affinity import get_display_affinity
        from app.ui.first_run import FirstRunGuide
        d = self.dialog(windows=lambda: [("a.exe", "A")])
        self.assertEqual(get_display_affinity(int(d.winId())), 0x11)
        self.assertTrue(d.exclusion().last_result.excluded)
        d._popups.install()  # what showEvent does
        popups = [d.font_combo.view().window(), d.lang_combo.view().window(), d.guard_combo.view().window()]
        box = QMessageBox(QMessageBox.Icon.Question, "Bỏ thay đổi?", "Đóng mà không lưu?", parent=d)
        tip = QLabel("tooltip", None, Qt.WindowType.ToolTip)
        self.addCleanup(tip.deleteLater)
        for w in [*popups, box, tip]:
            QCoreApplication.sendEvent(w, QShowEvent())  # synthetic: nothing appears or grabs input
        for w in [*popups, box, tip]:
            self.assertEqual(get_display_affinity(int(w.winId())), 0x11, w)
        d._popups.remove()
        picker = d.make_window_picker()
        self.addCleanup(picker.deleteLater)
        self.assertEqual(get_display_affinity(int(picker.winId())), 0x11)
        g = FirstRunGuide(Config(), profiles=["default"], llm_models=[], llm_server=False)
        self.addCleanup(g.deleteLater)
        self.assertEqual(get_display_affinity(int(g.winId())), 0x11)
        g._popups.install()
        QCoreApplication.sendEvent(g.profile_combo.view().window(), QShowEvent())
        self.assertEqual(get_display_affinity(int(g.profile_combo.view().window().winId())), 0x11)
        g._popups.remove()

    def test_render_previews(self):
        """Every tab and step as PNG in bench/results/preview/ (QWidget.grab; nothing is shown)."""
        from app.ui.first_run import FirstRunGuide
        from app.ui.settings_dialog import TABS
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        cfg = Config(blocked_processes=[*Config().blocked_processes[:6], "OnlineGame.exe"])
        cfg.hotkeys.quit = "ctrl+alt+r"  # shows the duplicate warning on the Phím tắt tab
        d = self.dialog(cfg, llm_status="LLM: sẵn sàng (Chất lượng 7B)")
        d.resize(680, 540)
        for key in TABS:
            d.set_tab(key)
            d.layout().activate()
            pump(20)
            img = d.grab()
            self.assertGreaterEqual(img.width(), 600)
            self.assertTrue(img.save(str(PREVIEW_DIR / f"settings_{key}.png")))
        g = FirstRunGuide(Config(region=[100, 900, 1280, 300]), profiles=["default", "Blue Archive"],
                          active_profile="Blue Archive", llm_models=[], llm_server=False)
        self.addCleanup(g.deleteLater)
        for i in range(g.step_count()):
            g.go(i)
            g.layout().activate()
            pump(20)
            img = g.grab()
            self.assertGreaterEqual(img.width(), 500)
            self.assertTrue(img.save(str(PREVIEW_DIR / f"first_run_{i + 1}.png")))
        print(f"\n    previews: {PREVIEW_DIR / 'settings_general.png'} …", file=sys.stderr)


def _windows_of(pids: set[int]) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []

    def visit(hwnd, _lp):
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and _user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            _user32.GetWindowTextW(hwnd, buf, 256)
            found.append((int(hwnd), buf.value))
        return True

    _user32.EnumWindows(_WNDENUMPROC(visit), 0)
    return found


class LauncherIntegrationTests(unittest.TestCase):
    """run_app.pyw under the venv's pythonw.exe for real, with a temporary data directory and no LLM
    model: windowless, tray icon up, a second launch hands over, quit through the socket."""

    ARGS = ["--no-hotkeys", "--llm-mode", "off", "--device", "cpu", "--exit-after", "85"]

    def setUp(self):
        if not NATIVE or not on_windows_platform():
            self.skipTest("needs the windows platform: run via WindowsPlatformSubprocessTests")
        from tools.install_shortcut import pythonw_path
        self.home = Path(tempfile.mkdtemp(prefix="st-launch-"))
        (self.home / "llm").mkdir()
        self.data = self.home / "data"
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("QT_QPA_PLATFORM", "SCREEN_TRANSLATOR_NATIVE_TESTS", "PYTHONIOENCODING")}
        self.env.update(SCREEN_TRANSLATOR_HOME=str(self.data), SCREEN_TRANSLATOR_LLM_HOME=str(self.home / "llm"))
        self.pythonw = pythonw_path()
        self.procs: list[subprocess.Popen] = []
        self.addCleanup(shutil.rmtree, self.home, True)
        self.addCleanup(self._kill_all)

    def launch(self, *args: str) -> subprocess.Popen:
        p = subprocess.Popen([str(self.pythonw), str(PROJECT_ROOT / "run_app.pyw"), *args],
                             cwd=str(self.home), env=self.env)  # any working directory: it moves to the project
        self.procs.append(p)
        return p

    def _kill_all(self) -> None:
        import psutil
        for p in self.procs:
            if p.poll() is None:
                try:
                    for c in psutil.Process(p.pid).children(recursive=True):
                        c.kill()
                except psutil.Error:
                    pass
                p.kill()
                p.wait(10)

    def log_text(self) -> str:
        try:
            return (self.data / "logs" / "app.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def ping(self, key: str, timeout_s: float = 2.0):
        from app.single_instance import send_command
        reply = send_command(key, "ping", timeout_s=timeout_s)
        return json.loads(reply) if reply else None

    def test_windowless_start_second_launch_and_quit(self):
        import psutil
        from app.single_instance import instance_key, send_command
        key = instance_key(self.data)
        first = self.launch(*self.ARGS)
        status = None
        end = time.monotonic() + 60
        while status is None and time.monotonic() < end and first.poll() is None:
            status = self.ping(key)
            time.sleep(0.3)
        self.assertIsNotNone(status, f"no answer; exit {first.poll()}\n{self.log_text()[-3000:]}")
        self.assertEqual(status["exe"].lower(), "pythonw.exe")
        self.assertIs(status["console"], False)  # GetConsoleWindow() == 0 inside the app
        self.assertTrue(status["tray_visible"])
        app_proc = psutil.Process(status["pid"])
        self.assertEqual(app_proc.name().lower(), "pythonw.exe")
        self.assertNotIn("conhost.exe", [c.name().lower() for c in app_proc.children(recursive=True)])
        self.assertIn("windowless", self.log_text())
        from app import __version__
        self.assertIn(f"Screen Translator {__version__} starting (", self.log_text())  # which build wrote the log
        # the shortcut double-clicked again: the new process hands over and exits
        second = self.launch(*self.ARGS)
        self.assertEqual(second.wait(40), 0)
        self.assertTrue(any("single-instance command: show" in line for line in self.log_text().splitlines()))
        self.assertIn("is already running", self.log_text())
        self.assertEqual(self.ping(key)["pid"], status["pid"])  # still the first one
        # "Khởi động lại app": quit, while a new launch waits for the lock
        self.assertEqual(send_command(key, "quit", timeout_s=3.0), "ok")
        third = self.launch(*self.ARGS, "--wait-for-instance", "40")
        self.assertEqual(first.wait(40), 0)
        status3 = None
        end = time.monotonic() + 60
        while status3 is None and time.monotonic() < end and third.poll() is None:
            status3 = self.ping(key)
            time.sleep(0.3)
        self.assertIsNotNone(status3, self.log_text()[-3000:])
        self.assertNotEqual(status3["pid"], status["pid"])
        self.assertEqual(send_command(key, "quit", timeout_s=3.0), "ok")
        self.assertEqual(third.wait(40), 0)
        self.assertIsNone(self.ping(key, timeout_s=0.5))
        self.assertGreaterEqual(self.log_text().count("shutdown complete"), 2)
        self.assertNotIn("Traceback", self.log_text())

    def test_bad_arguments_show_a_vietnamese_box(self):
        import psutil
        from app.overlay.affinity import get_display_affinity
        p = self.launch("--lang", "xx")
        hwnd, title = 0, ""
        end = time.monotonic() + 40
        while not hwnd and time.monotonic() < end and p.poll() is None:
            pids = {p.pid}
            try:
                pids |= {c.pid for c in psutil.Process(p.pid).children(recursive=True)}
            except psutil.Error:
                pass
            for h, t in _windows_of(pids):
                if "lỗi khởi động" in t:
                    hwnd, title = h, t
            time.sleep(0.2)
        self.assertTrue(hwnd, f"no message box; exit {p.poll()}")
        self.assertEqual(title, "Screen Translator: lỗi khởi động")
        affinity = get_display_affinity(hwnd)
        if affinity != -1:  # readable for another process's window on this build
            self.assertEqual(affinity, 0x11)
        _user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)  # our own child's window
        self.assertEqual(p.wait(20), 2)
        self.assertIn("invalid command line", self.log_text())


# --- child process ------------------------------------------------------------------------------------

@unittest.skipIf(sys.platform != "win32", "Windows only")
@unittest.skipIf(os.environ.get("SCREEN_TRANSLATOR_SKIP_GUI_TESTS") == "1", "SCREEN_TRANSLATOR_SKIP_GUI_TESTS=1")
class WindowsPlatformSubprocessTests(unittest.TestCase):
    """Runs the native classes (and the dialog logic again) in a child process on the windows platform."""

    def test_windows_platform(self):
        if NATIVE:
            self.skipTest("already running on the windows platform")
        from app.ui.game_guard import GameGuard, matches
        fg = GameGuard.foreground_process_name()
        if matches(fg, Config.load().blocked_processes):
            self.skipTest(f"an anti-cheat game ({fg}) is in the foreground")
        env = dict(os.environ)
        env.pop("QT_SCALE_FACTOR", None)
        env.update(QT_QPA_PLATFORM="windows", SCREEN_TRANSLATOR_NATIVE_TESTS="1", PYTHONIOENCODING="utf-8")
        classes = ["NativeSettingsTests", "LauncherIntegrationTests", "SettingsDialogTests", "HotkeyEditTests",
                   "FirstRunTests"]
        cmd = [sys.executable, "-m", "unittest", "-v", *[f"tests.test_settings_ui.{c}" for c in classes]]
        proc = subprocess.run(cmd, cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=400)
        out = proc.stdout + proc.stderr
        if os.environ.get("SCREEN_TRANSLATOR_VERBOSE_CHILD") == "1":
            print(out, file=sys.stderr)
        self.assertEqual(proc.returncode, 0, out[-6000:])
        self.assertNotIn("skipped", out, out[-3000:])


if __name__ == "__main__":
    unittest.main()
