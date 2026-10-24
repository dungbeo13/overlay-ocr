"""Settings window ("Cài đặt"): what otherwise lives only in data/config.json.

Non-modal and capture-excluded like every window of the app: the dialog, its combo lists, the font
list, tooltips, message boxes and the window picker never reach our own OCR. The dialog edits a copy
of the saved Config. Lưu validates it (hotkeys: syntax, duplicates, combos that Windows or typing
needs) and hands it to `apply` (App.apply_settings), which applies at once what it can (hotkeys are
re-registered; overlay style, LLM mode / model, anti-cheat mode and list, poll interval…), saves
config.json and names what only a restart applies (OCR device, capture backend): the dialog then stays
open with a "Khởi động lại app" button while any saved setting differs from what runs. "Mặc định" (after a
question) refills every tab with the defaults; nothing is saved before Lưu.

The window may stay open while the user plays: a change made elsewhere meanwhile (tray, hotkey, automatic
profile switch) reaches it through sync_field(), so Lưu never puts back the value the window was opened
with. A saved hotkey that worked before (e.g. a lone F9 in config.json) is reported but does not block
saving until it is changed here.

While a hotkey field records, `hotkey_capture(True)` asks the app to release its global hotkeys:
otherwise pressing Ctrl+Alt+R in the field would open the region selector instead of reaching it.

The "Tra cứu game" tab (game lookup, app.lookup) edits asking about new games, the sources and SearXNG; its
buttons (Claude request, import, "Cài SearXNG trên máy…", "Quên các game đã hỏi") are signals the app acts on at
once, and "Kiểm tra" probes the SearXNG address on a worker thread.
"""
from __future__ import annotations

import copy
import logging
import os
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, fields

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetrics, QGuiApplication, QKeyEvent, QLinearGradient,
                           QPainter, QPen)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFontComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.config import CONFIG_PATH, DATA_DIR, DEFAULT_BLOCKED_PROCESSES, PROJECT_ROOT, Config, Hotkeys
from app.overlay.affinity import CaptureExclusion, apply_to_widget
from app.pipeline.types import Block
from app.ui.glossary_editor import WindowPicker, _PopupExcluder, _signals_blocked, ask, ask_discard
from app.ui.hotkeys import format_combo, parse_combo

log = logging.getLogger(__name__)

LANG_CHOICES = [("ja", "Tiếng Nhật"), ("en", "Tiếng Anh")]
LLM_MODE_CHOICES = [("refine", "Google trước, LLM tinh chỉnh (khuyên dùng)"), ("only", "Chỉ LLM (chờ bản LLM)"),
                    ("off", "Tắt (chỉ dùng Google)")]
MODEL_CHOICES = [("", "Tự động (7B nếu đủ VRAM, không thì 1.8B)"), ("light", "Nhẹ 1.8B (~2 GB VRAM)"),
                 ("quality", "Chất lượng 7B (~5,3 GB VRAM)")]
GUARD_CHOICES = [("pause", "Tạm dừng khi bạn đang chơi game đó (khuyên dùng)"), ("warn", "Chỉ cảnh báo, vẫn dịch"),
                 ("off", "Tắt (không kiểm tra)")]
GUARD_HINTS = {
    "pause": "Khi bạn đang chơi một game trong danh sách dưới đây (cửa sổ game đang được chọn), app ngừng chụp "
             "màn hình, ẩn bản dịch và tự chạy lại khi bạn chuyển sang cửa sổ khác.",
    "warn": "App chỉ báo một lần cho mỗi game rồi vẫn tiếp tục chụp và dịch. Dùng khi bạn chắc game không cấm.",
    "off": "Không kiểm tra game nào. Không nên dùng với game online có anti-cheat.",
}
DEVICE_CHOICES = [("cuda", "GPU (CUDA): nhanh, ít tốn CPU"), ("cpu", "CPU")]
CAPTURE_CHOICES = [("auto", "Tự động (dxcam, chuyển sang GDI khi cần)"), ("dxcam", "dxcam (Desktop Duplication)"),
                   ("mss", "GDI (mss)"), ("wgc", "Windows Graphics Capture")]
HOTKEY_LABELS = {
    "select_region": "Chọn vùng dịch",
    "toggle_pause": "Tạm dừng / tiếp tục",
    "toggle_overlay": "Ẩn / hiện bản dịch",
    "switch_lang": "Đổi ngôn ngữ nguồn (Nhật ↔ Anh)",
    "quit": "Thoát",
}
RESTART_FIELDS = {"ocr_device": "Thiết bị OCR", "capture_backend": "Cách chụp màn hình"}
OVERLAY_FIELDS = ("font_family", "min_font_px", "box_alpha", "show_border")
SETTINGS_FIELDS = ("src_lang", "llm_mode", "llm_model", "llm_history_lines", "llm_auto_terms",
                   "llm_release_after_guard_s", "font_family", "min_font_px", "box_alpha", "show_border", "hotkeys",
                   "guard_mode", "blocked_processes", "poll_ms", "ocr_device", "capture_backend",
                   # TRA CỨU GAME (the "lookup" tab)
                   "lookup_ask_new_games", "lookup_sources", "searxng_url", "searxng_managed", "searxng_public_fallback")
TABS = ("general", "translate", "display", "hotkeys", "guard", "advanced")
TAB_TITLES = {"general": "Chung", "translate": "Dịch", "display": "Hiển thị", "hotkeys": "Phím tắt",
              "guard": "Anti-cheat", "advanced": "Nâng cao"}
LOOKUP_TAB = ("lookup", "Tra cứu game")  # added after TABS (SettingsDialog._add_tab)
LOOKUP_SOURCE_CHOICES = [("steam", "Steam"), ("wikidata", "Wikidata"), ("vndb", "VNDB (visual novel)"),
                         ("wiki", "Wiki game (Fandom, wiki.gg)"), ("web", "Tìm web qua SearXNG")]
LOOKUP_HINT = ("Khi bạn mở một game chưa có hồ sơ, app chỉ hiện một thông báo nhỏ ở khay, không mở cửa sổ đè lên game. "
               "Chưa gửi gì lên mạng cho tới khi bạn bấm Tra cứu, và chỉ gửi tên game.")
SEARXNG_PUBLIC_HINT = ("Mặc định tắt, và chỉ dùng khi máy bạn chưa có SearXNG nào. Máy chủ công cộng thấy tên game bạn tìm "
                       "và địa chỉ IP của bạn. Lần thử ngày 2026-10-25 chưa có máy chủ công cộng nào cho app dùng (đều chặn "
                       "truy cập tự động), nên lựa chọn này đang bị khóa.")
SEARXNG_INSTALL_HINT = "Cài một lần (tải khoảng 100 MB, môi trường Python riêng); chỉ chạy trên 127.0.0.1."
MSG_BAD_SEARXNG_URL = "Địa chỉ SearXNG phải bắt đầu bằng http:// hoặc https://"

MSG_SAVED = "Đã lưu."
MSG_RESTART = "Đã lưu. Cần khởi động lại app để áp dụng: {what}."
MSG_DEFAULTS = "Đã điền giá trị mặc định. Bấm Lưu để áp dụng."
MSG_CAPTURE = "Nhấn tổ hợp phím mới… (Esc: hủy)"
MSG_BAD_KEY = "Phím này không dùng làm phím tắt được"
HOTKEY_TIP = "Bấm vào ô rồi nhấn tổ hợp phím mới. Esc: hủy · Backspace: xóa phím tắt"
SAMPLE_SOURCE = "ごめんね。一緒に探してくれる？"
SAMPLE_TEXT = "Em xin lỗi nhé. Anh giúp em tìm cùng được không?"
LLM_HINT = "Model chạy trên GPU của bạn (llama-server, chỉ nghe trên 127.0.0.1)."
LLM_INSTALL_HINT = "Cài LLM một lần (tải vài GB): mở PowerShell, dán lệnh dưới đây, chờ xong rồi mở lại app."
DEFAULTS_QUESTION = ("Đặt lại mọi cài đặt về mặc định? (ngôn ngữ, LLM, hiển thị, phím tắt, danh sách game anti-cheat…)"
                     "\n\nChưa có gì được lưu cho tới khi bạn bấm Lưu.")
# A spin box's value from config.json: (minimum, maximum, default). Out-of-range values that worked before
# widen the range instead of being clamped (the window would otherwise be "changed" as soon as it opens).
_SPIN_DEFAULTS = {"llm_history_lines": 12, "llm_release_after_guard_s": 30, "min_font_px": 12, "poll_ms": 150}


# --- hotkeys -----------------------------------------------------------------------------------------

_MOD_TOKENS = ((Qt.KeyboardModifier.ControlModifier, "ctrl"), (Qt.KeyboardModifier.AltModifier, "alt"),
               (Qt.KeyboardModifier.ShiftModifier, "shift"), (Qt.KeyboardModifier.MetaModifier, "win"))
_MODIFIER_KEYS = {Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift, Qt.Key.Key_Meta, Qt.Key.Key_AltGr,
                  Qt.Key.Key_Super_L, Qt.Key.Key_Super_R, Qt.Key.Key_Hyper_L, Qt.Key.Key_Hyper_R, Qt.Key.Key_CapsLock}
_VK_TOKENS = {
    0x20: "space", 0x09: "tab", 0x0D: "enter", 0x1B: "esc", 0x08: "backspace", 0x2D: "insert", 0x2E: "delete",
    0x24: "home", 0x23: "end", 0x21: "pageup", 0x22: "pagedown", 0x25: "left", 0x26: "up", 0x27: "right",
    0x28: "down", 0x13: "pause", 0x2C: "printscreen", 0x91: "scrolllock", 0x90: "numlock",
    0xBA: "semicolon", 0xBB: "equal", 0xBC: "comma", 0xBD: "minus", 0xBE: "period", 0xBF: "slash",
    0xC0: "backquote", 0xDB: "[", 0xDC: "backslash", 0xDD: "]", 0xDE: "quote",
    0x6A: "multiply", 0x6B: "add", 0x6D: "subtract", 0x6E: "decimal", 0x6F: "divide",
}
_QT_TOKENS = {
    Qt.Key.Key_Space: "space", Qt.Key.Key_Tab: "tab", Qt.Key.Key_Return: "enter", Qt.Key.Key_Enter: "enter",
    Qt.Key.Key_Escape: "esc", Qt.Key.Key_Backspace: "backspace", Qt.Key.Key_Insert: "insert",
    Qt.Key.Key_Delete: "delete", Qt.Key.Key_Home: "home", Qt.Key.Key_End: "end", Qt.Key.Key_PageUp: "pageup",
    Qt.Key.Key_PageDown: "pagedown", Qt.Key.Key_Left: "left", Qt.Key.Key_Up: "up", Qt.Key.Key_Right: "right",
    Qt.Key.Key_Down: "down", Qt.Key.Key_Pause: "pause", Qt.Key.Key_Print: "printscreen",
    Qt.Key.Key_ScrollLock: "scrolllock", Qt.Key.Key_NumLock: "numlock", Qt.Key.Key_Semicolon: "semicolon",
    Qt.Key.Key_Equal: "equal", Qt.Key.Key_Plus: "plus", Qt.Key.Key_Comma: "comma", Qt.Key.Key_Minus: "minus",
    Qt.Key.Key_Period: "period", Qt.Key.Key_Slash: "slash", Qt.Key.Key_QuoteLeft: "backquote",
    Qt.Key.Key_BracketLeft: "[", Qt.Key.Key_Backslash: "backslash", Qt.Key.Key_BracketRight: "]",
    Qt.Key.Key_Apostrophe: "quote",
}
_QT_KEYPAD = {Qt.Key.Key_Asterisk: "multiply", Qt.Key.Key_Plus: "add", Qt.Key.Key_Minus: "subtract",
              Qt.Key.Key_Period: "decimal", Qt.Key.Key_Comma: "decimal", Qt.Key.Key_Slash: "divide"}
# Keys a global hotkey may use without Ctrl / Alt / Win: nothing types or plays with them.
_LONE_OK = {0x13, 0x91} | set(range(0x7C, 0x88))  # Pause, Scroll Lock, F13-F24
_RESERVED = {
    "ctrl+alt+delete": "màn hình bảo mật của Windows", "ctrl+shift+esc": "Task Manager", "ctrl+esc": "menu Start",
    "alt+tab": "chuyển cửa sổ", "alt+shift+tab": "chuyển cửa sổ", "alt+esc": "chuyển cửa sổ",
    "alt+f4": "đóng cửa sổ", "alt+space": "menu cửa sổ", "printscreen": "chụp màn hình",
    "win+l": "khóa máy", "win+d": "hiện desktop", "win+e": "File Explorer", "win+r": "hộp Run",
    "win+tab": "Task View", "win+x": "menu Win+X", "win+i": "Settings", "win+s": "tìm kiếm", "win+a": "Quick Settings",
    "win+v": "lịch sử clipboard", "win+shift+s": "Snipping Tool", "win+g": "Xbox Game Bar",
    "win+alt+r": "Game Bar quay video", "win+alt+g": "Game Bar ghi lại", "win+alt+printscreen": "Game Bar chụp",
    "win+printscreen": "chụp màn hình", "win+period": "bảng emoji", "win+semicolon": "bảng emoji",
    "win+space": "đổi bộ gõ",
}


def _canon(combo: str) -> tuple[int, int] | None:
    try:
        return parse_combo(combo)
    except ValueError:
        return None


_RESERVED_CANON = {_canon(k): v for k, v in _RESERVED.items()}


def key_token(key: int, native_vk: int = 0, keypad: bool = False) -> str | None:
    """The hotkeys.parse_combo name of a pressed key: from the Windows virtual-key code when Qt gives
    one (layout independent), else from the Qt key. None for keys a hotkey cannot use."""
    vk = int(native_vk or 0)
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        return chr(vk).lower()
    if 0x70 <= vk <= 0x87:
        return f"f{vk - 0x6F}"
    if 0x60 <= vk <= 0x69:
        return f"numpad{vk - 0x60}"
    if vk in _VK_TOKENS:
        return _VK_TOKENS[vk]
    k = Qt.Key(key) if not isinstance(key, Qt.Key) else key
    v = int(k.value)
    if keypad:
        if Qt.Key.Key_0.value <= v <= Qt.Key.Key_9.value:
            return f"numpad{v - Qt.Key.Key_0.value}"
        if k in _QT_KEYPAD:
            return _QT_KEYPAD[k]
    if Qt.Key.Key_A.value <= v <= Qt.Key.Key_Z.value or Qt.Key.Key_0.value <= v <= Qt.Key.Key_9.value:
        return chr(v).lower()
    if Qt.Key.Key_F1.value <= v <= Qt.Key.Key_F24.value:
        return f"f{v - Qt.Key.Key_F1.value + 1}"
    return _QT_TOKENS.get(k)


def combo_text(modifiers: Qt.KeyboardModifier, token: str) -> str:
    """"ctrl+alt+r" style (the order format_combo shows)."""
    return "+".join([name for flag, name in _MOD_TOKENS if modifiers & flag] + [token])


_MOD_DISPLAY = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}


def _held_text(modifiers: Qt.KeyboardModifier) -> str:
    """"Ctrl+Alt+…" while only modifiers are held, "" when none is."""
    parts = [_MOD_DISPLAY[name] for flag, name in _MOD_TOKENS if modifiers & flag]
    return "+".join(parts) + "+…" if parts else ""


def hotkey_problem(combo: str) -> str | None:
    """Why `combo` cannot be a global hotkey (Vietnamese), None when it can. "" (no hotkey) is fine."""
    if not combo:
        return None
    parsed = _canon(combo)
    if parsed is None:
        return f"“{combo}” không phải phím tắt hợp lệ"
    mods, vk = parsed
    what = _RESERVED_CANON.get(parsed)
    if what:
        return f"{format_combo(combo)} là phím của Windows ({what})"
    if not mods & 0x000B and vk not in _LONE_OK:  # no Ctrl / Alt / Win
        return f"{format_combo(combo)} cần thêm Ctrl, Alt hoặc Win (nếu không, phím đó sẽ bị chặn trong mọi app và game)"
    return None


def hotkey_conflicts(hotkeys: dict[str, str]) -> dict[str, str]:
    """name -> problem for every hotkey that is invalid, reserved or used twice."""
    out: dict[str, str] = {}
    seen: dict[tuple[int, int], str] = {}
    for name, combo in hotkeys.items():
        problem = hotkey_problem(combo)
        if problem:
            out[name] = problem
            continue
        parsed = _canon(combo) if combo else None
        if parsed is None:
            continue
        if parsed in seen:
            other = seen[parsed]
            out[name] = f"{format_combo(combo)} trùng với “{HOTKEY_LABELS.get(other, other)}”"
            out.setdefault(other, f"{format_combo(combo)} trùng với “{HOTKEY_LABELS.get(name, name)}”")
        else:
            seen[parsed] = name
    return {name: out[name] for name in hotkeys if name in out}  # in row order


def same_combo(a: str, b: str) -> bool:
    if (a or "") == (b or ""):
        return True
    ca, cb = _canon(a) if a else None, _canon(b) if b else None
    return ca is not None and ca == cb


def settings_diff(old: Config, new: Config) -> list[str]:
    """The SETTINGS_FIELDS whose values differ (hotkeys compared as key combos)."""
    out = []
    for name in SETTINGS_FIELDS:
        a, b = getattr(old, name), getattr(new, name)
        if name == "hotkeys":
            if not all(same_combo(getattr(a, f.name), getattr(b, f.name)) for f in fields(Hotkeys)):
                out.append(name)
        elif a != b:
            out.append(name)
    return out


@dataclass
class ApplyResult:
    """What App.apply_settings did with a saved Config."""

    restart: list[str] = field(default_factory=list)  # labels of saved settings a restart applies
    errors: list[str] = field(default_factory=list)  # Vietnamese messages (e.g. a hotkey another app holds)
    config: Config | None = None  # the settings now saved (a refused hotkey keeps its old combo)


def _save_only(config: Config) -> ApplyResult:
    config.save(CONFIG_PATH)
    return ApplyResult(config=copy.deepcopy(config))


class ShortcutStartup:
    """"Khởi động cùng Windows": a shortcut in the Startup folder (tools/install_shortcut.py)."""

    @staticmethod
    def _module():
        try:
            from tools import install_shortcut
        except ImportError:
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.append(str(PROJECT_ROOT))
            from tools import install_shortcut
        return install_shortcut

    def enabled(self) -> bool | None:
        """None when it cannot be told (no tools package, Startup folder unreadable)."""
        try:
            return bool(self._module().startup_enabled())
        except Exception:
            log.warning("cannot read the Startup shortcut", exc_info=True)
            return None

    def set_enabled(self, enabled: bool) -> None:
        self._module().set_startup(enabled)


# --- widgets ---------------------------------------------------------------------------------------

class HotkeyEdit(QLineEdit):
    """Records the next key combination pressed into it (only into this field: it is our own window
    receiving the keys). Esc cancels, Enter keeps the current combo, Backspace / Delete clears it."""

    combo_changed = Signal(str)
    capturing_changed = Signal(bool)

    def __init__(self, combo: str = "", parent: QWidget | None = None) -> None:
        self._combo = combo or ""
        self._capturing = False
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # Tab does not start a recording by accident
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setToolTip(HOTKEY_TIP)
        self._refresh()

    def combo(self) -> str:
        return self._combo

    def set_combo(self, combo: str, *, emit: bool = False) -> None:
        combo = combo or ""
        changed = combo != self._combo
        self._combo = combo
        self._refresh()
        if changed and emit:
            self.combo_changed.emit(combo)

    def is_capturing(self) -> bool:
        return self._capturing

    def start_capture(self) -> None:
        self._set_capturing(True)

    def stop_capture(self) -> None:
        self._set_capturing(False)

    def _set_capturing(self, on: bool) -> None:
        if on != self._capturing:
            self._capturing = on
            self._refresh()
            self.capturing_changed.emit(on)

    def _refresh(self, partial: str = "") -> None:
        if self._capturing:
            self.setPlaceholderText(MSG_CAPTURE)
            self.setText(partial)
        else:
            self.setPlaceholderText("(không dùng)")
            self.setText(format_combo(self._combo) if self._combo else "")

    def focusInEvent(self, e) -> None:
        super().focusInEvent(e)
        self._set_capturing(True)

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        self._set_capturing(False)

    def event(self, e: QEvent) -> bool:
        if self._capturing and e.type() == QEvent.Type.ShortcutOverride:
            e.accept()  # every key comes to keyPressEvent: no button / dialog shortcut takes it
            return True
        return super().event(e)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if not self._capturing:
            self.start_capture()
        key = e.key()
        mods = e.modifiers()
        held = mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
                       | Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.MetaModifier)
        if key in _MODIFIER_KEYS:
            self._refresh(_held_text(mods))
            return
        if not held and key in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._finish()
            return
        if not held and key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            self.set_combo("", emit=True)
            self._finish()
            return
        token = key_token(key, e.nativeVirtualKey(), bool(mods & Qt.KeyboardModifier.KeypadModifier))
        if token is None:
            self._refresh("")
            self.setPlaceholderText(MSG_BAD_KEY)
            return
        self.set_combo(combo_text(mods, token), emit=True)
        self._finish()

    def keyReleaseEvent(self, e: QKeyEvent) -> None:
        if self._capturing and e.key() in _MODIFIER_KEYS:
            self._refresh(_held_text(e.modifiers()))  # the modifiers still held
            return
        super().keyReleaseEvent(e)

    def _finish(self) -> None:
        self._set_capturing(False)
        self.clearFocus()


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setEnabled(False)  # the grey "secondary text" look the glossary editor uses
    return label


def _spin(low: int, high: int, suffix: str, *, step: int = 1, special: str = "") -> QSpinBox:
    box = QSpinBox()
    box.setRange(low, high)
    box.setSingleStep(step)
    box.setSuffix(suffix)
    if special:
        box.setSpecialValueText(special)  # shown at the minimum
    box.setMaximumWidth(170)
    return box


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _set_spin(box: QSpinBox, value, default: int) -> None:
    """Show a config.json value; one outside the box's range widens it (kept as it is, not clamped)."""
    v = _int(value, default)
    if v < box.minimum():
        box.setMinimum(v)
    if v > box.maximum():
        box.setMaximum(v)
    box.setValue(v)


def _names(value) -> list[str]:
    """blocked_processes as a list of names whatever config.json holds (null, one string, a list)."""
    if isinstance(value, str):
        return [value]
    try:
        return [str(v) for v in value or ()]
    except TypeError:
        return []


class OverlayPreview(QWidget):
    """The Hiển thị tab's preview: a small fake game scene (a light visual-novel text box with a Japanese
    line) with the translation painted over it the way the overlay paints it (app.overlay.render: the
    box colour sampled from the scene, a readable text colour, the font, min size, opacity and border)."""

    SCENE_BG = (238, 246, 250)  # BGR of the text box: cream
    SCENE_FG = (40, 48, 70)  # BGR of its text: dark brown

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = Config()
        self.setMinimumHeight(118)

    def set_config(self, config: Config) -> None:
        self._config = config
        self.update()

    def paintEvent(self, _event) -> None:
        from app.overlay.render import paint_block, paint_border

        w, h = self.width(), self.height()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        sky = QLinearGradient(0, 0, 0, h)
        sky.setColorAt(0.0, QColor(118, 164, 212))
        sky.setColorAt(1.0, QColor(196, 216, 236))
        p.fillRect(self.rect(), sky)
        box = QRectF(10, h * 0.30, w - 20, h * 0.70 - 8)
        b, g, r = self.SCENE_BG
        p.setBrush(QColor(r, g, b))
        p.setPen(QPen(QColor(150, 118, 86), 2))
        p.drawRoundedRect(box, 8, 8)
        font = QFont("Yu Gothic UI")
        font.setPixelSize(20)
        fm = QFontMetrics(font)
        x, top = int(box.left() + 16), int(box.top() + 14)
        b, g, r = self.SCENE_FG
        p.setFont(font)
        p.setPen(QColor(r, g, b))
        p.drawText(QPointF(x, top + fm.ascent()), SAMPLE_SOURCE)
        line = (x, top, fm.horizontalAdvance(SAMPLE_SOURCE), fm.height())
        block = Block(id=1, box=line, lines=(line,), source=SAMPLE_SOURCE, bg=self.SCENE_BG, fg=self.SCENE_FG,
                      line_height=fm.height(), translation=SAMPLE_TEXT)
        try:
            paint_block(p, block, self._config, (w, h))
            if self._config.show_border:
                paint_border(p, (w, h))
        except Exception:  # a font the system lacks must not break the window
            log.warning("overlay preview failed", exc_info=True)
        p.end()


def _combo_box(choices: list[tuple[str, str]]) -> QComboBox:
    box = QComboBox()
    for key, label in choices:
        box.addItem(label, key)
    return box


def _select(box: QComboBox, key, extra_label: str | None = None) -> None:
    """Select the item whose data is `key`; an unknown key is added (shown as `extra_label`)."""
    i = box.findData(key)
    if i < 0 and key not in (None, ""):
        box.addItem(extra_label or str(key), key)
        i = box.count() - 1
    with _signals_blocked(box):
        box.setCurrentIndex(max(i, 0))


# --- the dialog -------------------------------------------------------------------------------------

class SettingsDialog(QDialog):
    """Edit the saved settings; see the module docstring."""

    saved = Signal(object)  # the Config that was saved
    show_guide_requested = Signal()
    open_data_dir_requested = Signal()
    restart_requested = Signal()
    hotkey_capture = Signal(bool)  # a hotkey field is recording (the app pauses its global hotkeys)
    # TRA CỨU GAME tab: actions the app carries out at once (not on Lưu)
    lookup_request_requested = Signal()  # "Tạo yêu cầu tra cứu cho Claude…"
    import_profile_requested = Signal(str)  # "file" | "clipboard"
    searxng_install_requested = Signal()
    forget_games_requested = Signal()  # confirmed by the user

    def __init__(self, config: Config, *, apply: Callable[[Config], ApplyResult] | None = None,
                 startup: ShortcutStartup | None = None, llm_status: str = "", hotkeys_disabled: bool = False,
                 windows: Callable[[], list[tuple[str, str]]] | None = None, llm_models: Sequence[str] | None = None,
                 llm_server: bool | None = None, searxng_status: Callable[[], tuple[str, bool]] | None = None,
                 lookup_games: int | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = copy.deepcopy(config)  # what is saved now
        self._apply = apply or _save_only
        self._searxng_status = searxng_status  # () -> (Vietnamese status, installed); None = read it from disk
        self._lookup_games = lookup_games
        self._startup = startup if startup is not None else ShortcutStartup()
        self._startup_saved: bool | None = None
        self._windows = windows  # window list for the picker (tests); None = the real one
        self._hotkeys_disabled = hotkeys_disabled
        self._llm_models = llm_models  # what is installed (tests); None = look on disk once
        self._llm_server = llm_server
        self._restart_pending: list[str] = []
        self.ask_before_discard = True  # tests turn the "discard changes?" / "defaults?" questions off

        self.setWindowTitle("Cài đặt — Screen Translator")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setSizeGripEnabled(True)
        self.resize(680, 540)

        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self._tab_index: dict[str, int] = {}
        builders = {"general": self._build_general_tab, "translate": self._build_translate_tab,
                    "display": self._build_display_tab, "hotkeys": self._build_hotkeys_tab,
                    "guard": self._build_guard_tab, "advanced": self._build_advanced_tab}
        for key in TABS:
            self._tab_index[key] = self.tabs.addTab(builders[key](), TAB_TITLES[key])
        # ---- TRA CỨU GAME: the "Tra cứu game" tab (sources, SearXNG, Claude request / import) --------------
        # Its widgets are filled in load() and written into `cfg` in collect() (also marked "TRA CỨU GAME");
        # its Config fields are listed in SETTINGS_FIELDS, so apply_settings sees the change.
        self._add_tab(LOOKUP_TAB[0], self._build_lookup_tab(), LOOKUP_TAB[1])
        # ----------------------------------------------------------------------------------------------
        root.addWidget(self.tabs, 1)

        bottom = QHBoxLayout()
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        bottom.addWidget(self.status_label, 1)
        self.restart_button = QPushButton("Khởi động lại app")
        self.restart_button.setVisible(False)
        self.restart_button.clicked.connect(self._on_restart_clicked)
        bottom.addWidget(self.restart_button)
        self.buttons = QDialogButtonBox()
        self.defaults_button = self.buttons.addButton("Mặc định", QDialogButtonBox.ButtonRole.ResetRole)
        self.save_button = self.buttons.addButton("Lưu", QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button = self.buttons.addButton("Hủy", QDialogButtonBox.ButtonRole.RejectRole)
        self.defaults_button.setToolTip("Điền lại giá trị mặc định cho mọi tab (hỏi trước; chưa lưu cho tới khi bấm "
                                        "Lưu). Không đổi “Khởi động cùng Windows”.")
        self.save_button.setDefault(True)
        self.defaults_button.clicked.connect(self._on_defaults_clicked)
        self.buttons.accepted.connect(self._on_save_clicked)
        self.buttons.rejected.connect(self.reject)
        bottom.addWidget(self.buttons)
        root.addLayout(bottom)

        self.load(self._config)
        self._load_startup()
        self.set_llm_status(llm_status)
        # Before anything is shown: this dialog and all its popups never reach the OCR.
        self._popups = _PopupExcluder(self)
        self._exclusion: CaptureExclusion = apply_to_widget(self)

    def _add_tab(self, key: str, page: QWidget, title: str) -> None:
        """Add a tab after the built-in ones (for later tabs such as "Tra cứu game")."""
        self._tab_index[key] = self.tabs.addTab(page, title)

    # --- tabs ------------------------------------------------------------------------------------

    def _build_general_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        form = QFormLayout()
        self.lang_combo = _combo_box(LANG_CHOICES)
        form.addRow("Ngôn ngữ nguồn:", self.lang_combo)
        lay.addLayout(form)
        lay.addWidget(_hint("Ở chế độ tiếng Nhật, chữ chỉ có ký tự Latin (menu, phụ đề tiếng Anh) vẫn được dịch như "
                            "tiếng Anh. Đổi nhanh bằng phím tắt “Đổi ngôn ngữ nguồn”."))
        lay.addSpacing(10)
        self.startup_check = QCheckBox("Khởi động cùng Windows")
        lay.addWidget(self.startup_check)
        lay.addWidget(_hint("App tự mở khi bạn đăng nhập Windows, chỉ hiện biểu tượng 文 ở khay hệ thống."))
        lay.addSpacing(10)
        row = QHBoxLayout()
        self.guide_button = QPushButton("Hướng dẫn nhanh…")
        self.guide_button.clicked.connect(self.show_guide_requested)
        row.addWidget(self.guide_button)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        return page

    def _build_translate_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        form = QFormLayout()
        self.llm_mode_combo = _combo_box(LLM_MODE_CHOICES)
        form.addRow("Dịch bằng LLM:", self.llm_mode_combo)
        self.model_combo = _combo_box(MODEL_CHOICES)
        form.addRow("Mô hình:", self.model_combo)
        self.llm_status_label = QLabel()
        self.llm_status_label.setWordWrap(True)
        form.addRow("Trạng thái:", self.llm_status_label)
        self.history_spin = _spin(0, 50, " câu")
        form.addRow("Câu thoại gần đây gửi kèm:", self.history_spin)
        self.release_spin = _spin(-1, 3600, " giây", special="Không tắt")
        self.release_spin.setToolTip("Khi bạn chơi game có anti-cheat, app tắt LLM sau từng này giây để game có thêm "
                                     "VRAM; LLM tự bật lại khi cần.")
        form.addRow("Tắt LLM để giải phóng VRAM khi đang chơi game có anti-cheat, sau:", self.release_spin)
        lay.addLayout(form)
        self.auto_terms_check = QCheckBox("Tự thêm tên riêng / thuật ngữ do LLM đề xuất vào hồ sơ (nền vàng, chờ bạn "
                                          "xác nhận)")
        lay.addWidget(self.auto_terms_check)
        lay.addSpacing(6)
        lay.addWidget(_hint("Câu thoại gần đây giúp LLM giữ đúng người nói và cách xưng hô; nhiều hơn thì chậm hơn một "
                            "chút. Mô hình đổi xong được tải lại ngay."))
        lay.addWidget(_hint(LLM_HINT))
        self.llm_install_label = QLabel(LLM_INSTALL_HINT)
        self.llm_install_label.setWordWrap(True)
        lay.addWidget(self.llm_install_label)
        from app.ui.first_run import setup_command

        self.llm_command_label = QLabel(setup_command())
        self.llm_command_label.setWordWrap(True)
        self.llm_command_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.llm_command_label.setStyleSheet("QLabel { font-family: Consolas, 'Cascadia Mono', monospace; "
                                             "background: rgba(127, 127, 127, 40); padding: 6px; border-radius: 4px; }")
        lay.addWidget(self.llm_command_label)
        row = QHBoxLayout()
        self.llm_copy_button = QPushButton("Sao chép lệnh")
        self.llm_copy_button.clicked.connect(self._copy_llm_command)
        row.addWidget(self.llm_copy_button)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        return page

    def _build_display_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        form = QFormLayout()
        self.font_combo = QFontComboBox()
        self.font_combo.setWritingSystem(QFontDatabase.WritingSystem.Vietnamese)  # fonts with all the diacritics
        self.font_combo.currentFontChanged.connect(lambda _f: self._update_preview())
        form.addRow("Phông chữ bản dịch:", self.font_combo)
        self.min_font_spin = _spin(6, 40, " px")
        self.min_font_spin.valueChanged.connect(lambda _v: self._update_preview())
        form.addRow("Cỡ chữ nhỏ nhất:", self.min_font_spin)
        alpha_row = QHBoxLayout()
        self.alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self.alpha_slider.setRange(0, 255)
        self.alpha_slider.valueChanged.connect(lambda _v: self._update_preview())
        self.alpha_label = QLabel()
        self.alpha_label.setMinimumWidth(48)
        alpha_row.addWidget(self.alpha_slider, 1)
        alpha_row.addWidget(self.alpha_label)
        form.addRow("Độ che chữ gốc:", alpha_row)
        lay.addLayout(form)
        self.border_check = QCheckBox("Hiện khung viền quanh vùng dịch")
        self.border_check.toggled.connect(lambda _on: self._update_preview())
        lay.addWidget(self.border_check)
        lay.addSpacing(8)
        lay.addWidget(QLabel("Xem trước (một khung thoại giả, vẽ như bản dịch thật):"))
        self.preview = OverlayPreview()
        lay.addWidget(self.preview)
        lay.addWidget(_hint("Độ che chữ gốc: 0 % = trong suốt, 100 % = che kín chữ gốc. Màu ô che lấy theo màu khung "
                            "thoại của game. Chữ tự co lại cho vừa khung nhưng không nhỏ hơn cỡ chữ nhỏ nhất. Áp dụng "
                            "ngay khi lưu."))
        lay.addStretch(1)
        return page

    def _build_hotkeys_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(_hint("Bấm vào một ô rồi nhấn tổ hợp phím mới (ví dụ Ctrl+Alt+R). Esc: hủy · Backspace: xóa phím "
                            "tắt đó. Phím tắt dùng được cả khi đang ở trong game."))
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        self.hotkey_edits: dict[str, HotkeyEdit] = {}
        for row, f in enumerate(fields(Hotkeys)):
            name = f.name
            grid.addWidget(QLabel(HOTKEY_LABELS.get(name, name) + ":"), row, 0)
            edit = HotkeyEdit()
            edit.combo_changed.connect(lambda _c: self._check_hotkeys())
            edit.capturing_changed.connect(self._on_capturing_changed)
            grid.addWidget(edit, row, 1)
            clear = QPushButton("Xóa phím")
            clear.setToolTip("Không dùng phím tắt cho việc này")
            clear.clicked.connect(lambda _checked=False, e=edit: e.set_combo("", emit=True))
            grid.addWidget(clear, row, 2)
            self.hotkey_edits[name] = edit
        lay.addLayout(grid)
        row = QHBoxLayout()
        self.hotkey_defaults_button = QPushButton("Đặt lại phím tắt mặc định")
        self.hotkey_defaults_button.clicked.connect(self._reset_hotkeys)
        row.addWidget(self.hotkey_defaults_button)
        row.addStretch(1)
        lay.addLayout(row)
        self.hotkey_error_label = QLabel()
        self.hotkey_error_label.setWordWrap(True)
        self.hotkey_error_label.setStyleSheet("color: #b71c1c;")
        lay.addWidget(self.hotkey_error_label)
        if self._hotkeys_disabled:
            lay.addWidget(_hint("App đang chạy với --no-hotkeys: phím tắt được lưu và có tác dụng từ lần chạy sau."))
        lay.addStretch(1)
        return page

    def _build_guard_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        form = QFormLayout()
        self.guard_combo = _combo_box(GUARD_CHOICES)
        self.guard_combo.currentIndexChanged.connect(lambda _i: self._update_guard_hint())
        form.addRow("Chế độ:", self.guard_combo)
        lay.addLayout(form)
        self.guard_hint = _hint("")
        lay.addWidget(self.guard_hint)
        lay.addSpacing(6)
        lay.addWidget(QLabel("Game có anti-cheat (tên tệp .exe):"))
        body = QHBoxLayout()
        self.blocked_list = QListWidget()
        self.blocked_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        body.addWidget(self.blocked_list, 1)
        side = QVBoxLayout()
        self.pick_window_button = QPushButton("Thêm từ cửa sổ đang mở…")
        self.pick_window_button.clicked.connect(self.pick_windows)
        self.remove_blocked_button = QPushButton("Xóa")
        self.remove_blocked_button.clicked.connect(self.remove_selected_blocked)
        self.restore_blocked_button = QPushButton("Khôi phục danh sách mặc định")
        self.restore_blocked_button.clicked.connect(self.restore_default_blocked)
        for b in (self.pick_window_button, self.remove_blocked_button, self.restore_blocked_button):
            side.addWidget(b)
        side.addStretch(1)
        body.addLayout(side)
        lay.addLayout(body, 1)
        manual = QHBoxLayout()
        self.blocked_edit = QLineEdit()
        self.blocked_edit.setPlaceholderText("hoặc gõ tên tệp, ví dụ: game.exe")
        self.blocked_edit.returnPressed.connect(self._add_typed_blocked)
        self.add_blocked_button = QPushButton("Thêm")
        self.add_blocked_button.clicked.connect(self._add_typed_blocked)
        manual.addWidget(self.blocked_edit, 1)
        manual.addWidget(self.add_blocked_button)
        lay.addLayout(manual)
        lay.addWidget(_hint("App chỉ đọc tên tệp .exe của cửa sổ đang được chọn; không đọc bộ nhớ, không can thiệp "
                            "vào game."))
        return page

    def _build_advanced_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        form = QFormLayout()
        self.poll_spin = _spin(50, 2000, " ms", step=10)
        form.addRow("Chu kỳ chụp vùng dịch:", self.poll_spin)
        self.device_combo = _combo_box(DEVICE_CHOICES)
        form.addRow("Thiết bị OCR:", self._with_restart_note(self.device_combo))
        self.capture_combo = _combo_box(CAPTURE_CHOICES)
        form.addRow("Cách chụp màn hình:", self._with_restart_note(self.capture_combo))
        lay.addLayout(form)
        lay.addWidget(_hint("Chu kỳ nhỏ hơn thì bắt chữ mới nhanh hơn nhưng tốn CPU hơn (mặc định 150 ms). GPU (CUDA) "
                            "nhanh gấp khoảng 6 lần CPU. Đổi thiết bị OCR hoặc cách chụp cần khởi động lại app."))
        lay.addSpacing(10)
        row = QHBoxLayout()
        self.data_dir_button = QPushButton("Mở thư mục dữ liệu")
        self.data_dir_button.setToolTip(str(DATA_DIR))
        self.data_dir_button.clicked.connect(self.open_data_dir_requested)
        row.addWidget(self.data_dir_button)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(_hint("Thư mục dữ liệu chứa cài đặt (config.json), hồ sơ game, cache bản dịch và nhật ký."))
        lay.addStretch(1)
        return page

    def _build_lookup_tab(self) -> QWidget:
        """TRA CỨU GAME: asking about new games, the sources, SearXNG, the Claude request and import."""
        page = QWidget()
        lay = QVBoxLayout(page)
        self.lookup_ask_check = QCheckBox("Hỏi khi gặp game mới (mỗi game một lần)")
        lay.addWidget(self.lookup_ask_check)
        lay.addWidget(_hint(LOOKUP_HINT))
        grid = QGridLayout()
        grid.addWidget(QLabel("Nguồn dữ liệu:"), 0, 0, Qt.AlignmentFlag.AlignTop)
        self.lookup_source_checks: dict[str, QCheckBox] = {}
        for i, (key, label) in enumerate(LOOKUP_SOURCE_CHOICES):
            box = QCheckBox(label)
            grid.addWidget(box, i // 3, 1 + i % 3)
            self.lookup_source_checks[key] = box
        grid.setColumnStretch(3, 1)
        lay.addLayout(grid)
        group = QGroupBox("SearXNG: tìm trang Việt hóa và wiki trên web (tùy chọn)")
        form = QFormLayout(group)
        row = QHBoxLayout()
        self.searxng_url_edit = QLineEdit()
        row.addWidget(self.searxng_url_edit, 1)
        self.searxng_check_button = QPushButton("Kiểm tra")
        self.searxng_check_button.setToolTip("Hỏi địa chỉ này xem SearXNG có trả lời không (không tìm gì)")
        self.searxng_check_button.clicked.connect(self.check_searxng)
        row.addWidget(self.searxng_check_button)
        form.addRow("Địa chỉ:", row)
        self.searxng_status_label = QLabel()
        self.searxng_status_label.setWordWrap(True)
        form.addRow("Trạng thái:", self.searxng_status_label)
        row = QHBoxLayout()
        self.searxng_install_button = QPushButton("Cài SearXNG trên máy…")
        self.searxng_install_button.clicked.connect(self.searxng_install_requested)
        row.addWidget(self.searxng_install_button)
        row.addWidget(_hint(SEARXNG_INSTALL_HINT), 1)
        form.addRow(row)
        self.searxng_managed_check = QCheckBox("App tự bật / tắt SearXNG (chỉ chạy khi đang tra cứu, tắt sau 10 phút "
                                               "không dùng)")
        form.addRow(self.searxng_managed_check)
        self.searxng_public_check = QCheckBox("Dùng máy chủ SearXNG công cộng khi máy không có")
        try:
            from app.lookup.searxng import PUBLIC_INSTANCES
        except Exception:  # noqa: BLE001 - a partial install: nothing public to use
            PUBLIC_INSTANCES = []
        self.searxng_public_check.setEnabled(bool(PUBLIC_INSTANCES))  # no tested public server today
        form.addRow(self.searxng_public_check)
        form.addRow(_hint(SEARXNG_PUBLIC_HINT))
        lay.addWidget(group)
        row = QHBoxLayout()
        self.lookup_request_button = QPushButton("Tạo yêu cầu cho Claude…")
        self.lookup_request_button.setToolTip("Một đoạn yêu cầu để bạn tự dán cho Claude; câu trả lời nhập lại bằng "
                                              "“Nhập hồ sơ từ file…” hoặc “Dán từ clipboard”")
        self.lookup_request_button.clicked.connect(self.lookup_request_requested)
        self.import_file_button = QPushButton("Nhập hồ sơ từ file…")
        self.import_file_button.clicked.connect(lambda: self.import_profile_requested.emit("file"))
        self.import_clip_button = QPushButton("Dán từ clipboard")
        self.import_clip_button.clicked.connect(lambda: self.import_profile_requested.emit("clipboard"))
        for b in (self.lookup_request_button, self.import_file_button, self.import_clip_button):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.forget_games_button = QPushButton("Quên các game đã hỏi")
        self.forget_games_button.setToolTip("App sẽ hỏi lại về mọi game chưa có hồ sơ")
        self.forget_games_button.clicked.connect(self._on_forget_games)
        row.addWidget(self.forget_games_button)
        self.lookup_games_label = _hint("")
        row.addWidget(self.lookup_games_label, 1)
        lay.addLayout(row)
        lay.addStretch(1)
        self.set_lookup_info(self._lookup_games)
        scroll = QScrollArea()  # the tab is taller than the others: it scrolls instead of clipping
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # labels wrap instead
        scroll.setWidget(page)
        for w in (scroll, scroll.viewport(), page):  # drawn like the other tabs' pages (setWidget turns this on)
            w.setAutoFillBackground(False)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        return scroll

    @staticmethod
    def _with_restart_note(widget: QWidget) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(widget, 1)
        row.addWidget(_hint("(cần khởi động lại)"))
        return holder

    # --- load / collect --------------------------------------------------------------------------

    def load(self, config: Config) -> None:
        """Fill every tab from `config` (does not change what is saved). Values of the wrong type in a
        hand-edited config.json fall back to the defaults; numbers outside a box's range are kept."""
        c = config
        _select(self.lang_combo, c.src_lang)
        _select(self.llm_mode_combo, c.llm_mode)
        model = str(c.llm_model or "").strip()
        _select(self.model_combo, "" if model.lower() == "auto" else model, f"Tệp: {model}")
        _set_spin(self.history_spin, c.llm_history_lines, _SPIN_DEFAULTS["llm_history_lines"])
        release = _int(c.llm_release_after_guard_s, _SPIN_DEFAULTS["llm_release_after_guard_s"])
        _set_spin(self.release_spin, max(-1, release), 30)  # any negative value means "never" to the app
        self.auto_terms_check.setChecked(bool(c.llm_auto_terms))
        family = str(c.font_family or Config().font_family)
        with _signals_blocked(self.font_combo):
            self.font_combo.setCurrentFont(QFont(family))
            if self.font_combo.currentText() != family:
                self.font_combo.setEditText(family)  # a family Qt does not list: keep the name
        _set_spin(self.min_font_spin, c.min_font_px, _SPIN_DEFAULTS["min_font_px"])
        self.alpha_slider.setValue(max(0, min(255, _int(c.box_alpha, Config().box_alpha))))
        self.border_check.setChecked(bool(c.show_border))
        for name, edit in self.hotkey_edits.items():
            edit.set_combo(str(getattr(c.hotkeys, name, "") or ""))
        _select(self.guard_combo, c.guard_mode)
        self.blocked_list.clear()
        self.add_blocked(_names(c.blocked_processes))
        _set_spin(self.poll_spin, c.poll_ms, _SPIN_DEFAULTS["poll_ms"])
        _select(self.device_combo, c.ocr_device)
        _select(self.capture_combo, c.capture_backend)
        # TRA CỨU GAME: the lookup tab's widgets.
        self.lookup_ask_check.setChecked(bool(c.lookup_ask_new_games))
        sources = _names(c.lookup_sources)
        for key, box in self.lookup_source_checks.items():
            box.setChecked(key in sources)
        self.searxng_url_edit.setText(str(c.searxng_url or ""))
        self.searxng_managed_check.setChecked(bool(c.searxng_managed))
        self.searxng_public_check.setChecked(bool(c.searxng_public_fallback))
        self.refresh_searxng_status()
        self._update_preview()
        self._update_guard_hint()
        self._check_hotkeys()

    def collect(self) -> tuple[Config, list[str]]:
        """The edited settings (a copy of the saved Config with this dialog's fields replaced) and the
        problems that prevent saving (empty: OK)."""
        cfg = copy.deepcopy(self._config)
        cfg.src_lang = self.lang_combo.currentData()
        cfg.llm_mode = self.llm_mode_combo.currentData()
        cfg.llm_model = self.model_combo.currentData() or ""
        cfg.llm_history_lines = self.history_spin.value()
        cfg.llm_release_after_guard_s = self.release_spin.value()
        cfg.llm_auto_terms = self.auto_terms_check.isChecked()
        family = self.font_combo.currentFont().family() if self.font_combo.currentIndex() >= 0 else ""
        cfg.font_family = (self.font_combo.currentText() or family or self._config.font_family).strip()
        cfg.min_font_px = self.min_font_spin.value()
        cfg.box_alpha = self.alpha_slider.value()
        cfg.show_border = self.border_check.isChecked()
        hk = copy.deepcopy(self._config.hotkeys)
        for name, edit in self.hotkey_edits.items():
            combo = edit.combo()
            if not same_combo(combo, getattr(hk, name)):  # keep the saved spelling of an unchanged combo
                setattr(hk, name, combo)
        cfg.hotkeys = hk
        cfg.guard_mode = self.guard_combo.currentData()
        cfg.blocked_processes = self.blocked_processes()
        cfg.poll_ms = self.poll_spin.value()
        cfg.ocr_device = self.device_combo.currentData()
        cfg.capture_backend = self.capture_combo.currentData()
        # TRA CỨU GAME: the lookup tab's values (a saved value shown unchanged is kept exactly as saved).
        cfg.lookup_ask_new_games = self.lookup_ask_check.isChecked()
        cfg.lookup_sources = self._collect_sources()
        url = self.searxng_url_edit.text().strip()
        cfg.searxng_url = self._config.searxng_url if url == str(self._config.searxng_url or "").strip() else url
        cfg.searxng_managed = self.searxng_managed_check.isChecked()
        cfg.searxng_public_fallback = self.searxng_public_check.isChecked()
        # A saved combo that is not changed here blocks nothing (it worked before, e.g. a lone F9 the user
        # put in config.json): it is shown in red, but only changed rows must be fixed before saving.
        errors = [f"{HOTKEY_LABELS.get(n, n)}: {p}" for n, p in self.hotkey_problems().items()
                  if not same_combo(self.hotkey_edits[n].combo(), getattr(self._config.hotkeys, n, ""))]
        if url and url != str(self._config.searxng_url or "").strip() and not url.lower().startswith(("http://",
                                                                                                    "https://")):
            errors.append(MSG_BAD_SEARXNG_URL)
        return cfg, errors

    def _collect_sources(self) -> list[str]:
        """The checked sources in the saved order (unknown entries of config.json are kept), new ones last."""
        old = _names(self._config.lookup_sources)
        checked = {k for k, box in self.lookup_source_checks.items() if box.isChecked()}
        out = [s for s in old if s in checked or s not in self.lookup_source_checks]
        out += [k for k in self.lookup_source_checks if k in checked and k not in out]
        return out

    def hotkey_problems(self) -> dict[str, str]:
        return hotkey_conflicts({name: edit.combo() for name, edit in self.hotkey_edits.items()})

    def is_dirty(self) -> bool:
        cfg, _errors = self.collect()
        if settings_diff(self._config, cfg):
            return True
        return self._startup_saved is not None and self.startup_check.isChecked() != self._startup_saved

    def saved_config(self) -> Config:
        return copy.deepcopy(self._config)

    def sync_field(self, name: str, value) -> None:
        """`name` changed outside this window (tray, hotkey, automatic profile switch, the guide): it becomes
        what Lưu compares with, and the window shows it unless the user has changed that field here (then
        the user's choice stays and Lưu applies it)."""
        if name not in ("src_lang", "llm_mode", "llm_model", "guard_mode", "searxng_url", "searxng_managed"):
            return
        shown, _errors = self.collect()
        touched = getattr(shown, name) != getattr(self._config, name)
        setattr(self._config, name, copy.deepcopy(value))
        if touched:
            return
        if name == "src_lang":
            _select(self.lang_combo, value)
        elif name == "llm_mode":
            _select(self.llm_mode_combo, value)
            self.set_llm_status(self._llm_status)
        elif name == "llm_model":
            model = str(value or "").strip()
            _select(self.model_combo, "" if model.lower() == "auto" else model, f"Tệp: {model}")
        elif name == "searxng_url":  # TRA CỨU GAME: filled in by the app after "Cài SearXNG trên máy…"
            self.searxng_url_edit.setText(str(value or ""))
            self.refresh_searxng_status()
        elif name == "searxng_managed":
            self.searxng_managed_check.setChecked(bool(value))
        else:
            _select(self.guard_combo, value)
            self._update_guard_hint()

    # --- actions ---------------------------------------------------------------------------------

    def save(self) -> bool:
        """Validate, apply and save. False (reason in the status line) when nothing was saved or part of
        it was refused; the dialog then stays open."""
        cfg, errors = self.collect()
        if errors:
            self.set_tab(LOOKUP_TAB[0] if errors == [MSG_BAD_SEARXNG_URL] else "hotkeys")
            more = f" (và {len(errors) - 2} lỗi khác)" if len(errors) > 2 else ""
            self._set_status(" ".join(errors[:2]) + more, error=True)
            return False
        problems: list[str] = []
        want_startup = self.startup_check.isChecked()
        if self._startup_saved is not None and want_startup != self._startup_saved:
            try:
                self._startup.set_enabled(want_startup)
                self._startup_saved = want_startup
            except Exception as e:
                log.warning("changing the Startup shortcut failed", exc_info=True)
                problems.append(f"Không đổi được “Khởi động cùng Windows”: {e}")
                with _signals_blocked(self.startup_check):
                    self.startup_check.setChecked(bool(self._startup_saved))
        try:
            result = self._apply(cfg) or ApplyResult()
        except Exception as e:
            log.exception("applying settings failed")
            self._set_status(f"Không lưu được: {e}", error=True)
            return False
        self._config = copy.deepcopy(result.config if result.config is not None else cfg)
        problems += result.errors
        for name, edit in self.hotkey_edits.items():  # a refused hotkey shows the combo still in use
            saved = getattr(self._config.hotkeys, name, "")
            if not same_combo(edit.combo(), saved):
                edit.set_combo(saved)
        self._check_hotkeys()
        # every save reports all saved settings that differ from what runs: back to the running value, no restart
        self._restart_pending = list(dict.fromkeys(result.restart))
        self.restart_button.setVisible(bool(self._restart_pending))
        self.set_llm_status(self._llm_status)
        if problems:
            self._set_status(" ".join(problems), error=True)
        elif self._restart_pending:
            self._set_status(MSG_RESTART.format(what=", ".join(self._restart_pending)))
        else:
            self._set_status(MSG_SAVED)
        log.info("settings saved%s", f" (restart needed: {', '.join(self._restart_pending)})"
                 if self._restart_pending else "")
        self.saved.emit(copy.deepcopy(self._config))
        return not problems

    def reset_defaults(self) -> None:
        """Every tab back to the defaults (Config()); saved only by Lưu. "Khởi động cùng Windows" is not a
        setting of config.json (a shortcut in the Startup folder) and stays as it is."""
        self.load(Config())
        self._set_status(MSG_DEFAULTS)

    def set_tab(self, key: str | None) -> None:
        if key in self._tab_index:
            self.tabs.setCurrentIndex(self._tab_index[key])

    def current_tab(self) -> str:
        i = self.tabs.currentIndex()
        return next((k for k, v in self._tab_index.items() if v == i), "")

    def set_llm_status(self, text: str) -> None:
        """The app's LLM status line; the install command shows when the model or llama.cpp is missing or
        broken (first_run.llm_summary decides, as in the guide)."""
        self._llm_status = text or ""
        shown = " ".join((text or "").split()).removeprefix("LLM: ").removeprefix("LLM ") or "chưa chạy"
        self.llm_status_label.setText(shown[:1].upper() + shown[1:])
        from app.ui.first_run import llm_summary

        try:
            if self._llm_models is None or self._llm_server is None:
                from app.llm.models import find_llama_server, list_models

                if self._llm_models is None:
                    self._llm_models = [p.name for p in list_models()]
                if self._llm_server is None:
                    self._llm_server = find_llama_server() is not None
            state = llm_summary(self._config.llm_mode, self._llm_status, models=self._llm_models,
                                server_found=self._llm_server)[0]
        except Exception:
            log.warning("cannot tell whether the LLM is installed", exc_info=True)
            state = ""
        show = state in ("missing", "error")
        for w in (self.llm_install_label, self.llm_command_label, self.llm_copy_button):
            w.setVisible(show)

    def llm_install_shown(self) -> bool:
        return not self.llm_command_label.isHidden()

    def blocked_processes(self) -> list[str]:
        return [self.blocked_list.item(i).text() for i in range(self.blocked_list.count())]

    def add_blocked(self, names) -> int:
        have = {p.lower() for p in self.blocked_processes()}
        added = 0
        for name in names:
            exe = _clean_exe(name)
            if exe and exe.lower() not in have:
                have.add(exe.lower())
                self.blocked_list.addItem(exe)
                added += 1
        return added

    def remove_selected_blocked(self) -> None:
        rows = sorted((self.blocked_list.row(i) for i in self.blocked_list.selectedItems()), reverse=True)
        for row in rows:
            self.blocked_list.takeItem(row)

    def restore_default_blocked(self) -> None:
        self.add_blocked(DEFAULT_BLOCKED_PROCESSES)

    def pick_windows(self) -> None:
        picker = self.make_window_picker()
        if picker.exec() == QDialog.DialogCode.Accepted:
            self.add_blocked(picker.selected())
        picker.deleteLater()

    def make_window_picker(self) -> WindowPicker:
        windows = self._windows() if self._windows is not None else None
        picker = WindowPicker(self.blocked_processes(), self, windows=windows)
        picker.setWindowTitle("Thêm game có anti-cheat")
        label = picker.findChild(QLabel)
        if label is not None:
            label.setText("Chọn game có anti-cheat. Khi bạn đang chơi game đó, app sẽ tạm dừng (hoặc cảnh báo):")
            label.setWordWrap(True)
        return picker

    def startup_state(self) -> bool | None:
        return self._startup_saved

    def exclusion(self) -> CaptureExclusion:
        return self._exclusion

    def popup_exclusions(self) -> int:
        return self._popups.count

    def restart_pending(self) -> list[str]:
        return list(self._restart_pending)

    # --- TRA CỨU GAME tab ---------------------------------------------------------------------------

    def set_lookup_info(self, asked_games: int | None) -> None:
        """How many games the app has asked about (lookup_state.json); None: unknown (not shown)."""
        self._lookup_games = asked_games
        self.lookup_games_label.setText("" if asked_games is None else
                                        "Chưa hỏi về game nào." if asked_games == 0 else
                                        f"Đã hỏi về {asked_games} game.")
        self.forget_games_button.setEnabled(asked_games != 0)

    def refresh_searxng_status(self) -> None:
        """The status line, the install button and the address placeholder (no network)."""
        from app.lookup import searxng

        try:
            if self._searxng_status is not None:
                text, installed = self._searxng_status()
            else:
                svc = searxng.SearxngService(str(self._config.searxng_url or ""), bool(self._config.searxng_managed))
                text, installed = svc.status_text(), svc.is_installed()
        except Exception:
            log.warning("cannot read the SearXNG status", exc_info=True)
            text, installed = "Không đọc được trạng thái SearXNG", False
        self.searxng_status_label.setText(text)
        self.searxng_install_button.setText("Cài lại / sửa SearXNG…" if installed else "Cài SearXNG trên máy…")
        url = self.installed_searxng_url() if installed else ""
        self.searxng_url_edit.setPlaceholderText(f"{url} (bản cài trên máy)" if url else
                                                 "để trống: dùng bản cài trên máy khi đã cài")

    @staticmethod
    def installed_searxng_url() -> str:
        from app.lookup import searxng

        info = searxng.install_info() if searxng.installed() else None
        return f"http://127.0.0.1:{info.get('port') or searxng.DEFAULT_PORT}" if info else ""

    def check_searxng(self) -> None:
        """"Kiểm tra": probe the address (or the installed instance) on a worker thread; sends no search."""
        url = self.searxng_url_edit.text().strip() or self.installed_searxng_url()
        if not url:
            self.searxng_status_label.setText("Chưa có địa chỉ nào để kiểm tra: cài SearXNG trên máy hoặc gõ địa chỉ.")
            return
        if not url.lower().startswith(("http://", "https://")):
            self.searxng_status_label.setText(MSG_BAD_SEARXNG_URL)
            return
        self.searxng_check_button.setEnabled(False)
        self.searxng_status_label.setText(f"Đang kiểm tra {url}…")
        bridge = _ProbeBridge()
        bridge.done.connect(self._on_searxng_checked)
        bridge.done.connect(bridge.deleteLater)

        def work() -> None:
            from app.lookup import searxng

            try:
                ok = searxng.probe(url)
            except Exception:
                log.warning("SearXNG probe failed", exc_info=True)
                ok = False
            bridge.done.emit(url, ok)

        threading.Thread(target=work, name="searxng-probe", daemon=True).start()

    def _on_searxng_checked(self, url: str, ok: bool) -> None:
        self.searxng_check_button.setEnabled(True)
        if ok:
            self.searxng_status_label.setText(f"SearXNG trả lời ở {url}.")
        elif self.searxng_managed_check.isChecked() and self.installed_searxng_url():
            self.searxng_status_label.setText(f"SearXNG đang tắt ({url}); app tự bật khi tra cứu.")
        else:
            self.searxng_status_label.setText(f"Không có SearXNG nào trả lời ở {url}.")

    def _on_forget_games(self) -> None:
        if self.ask_before_discard and not ask(self, "Quên các game đã hỏi?",
                                               "App sẽ hỏi lại (một lần) về mọi game chưa có hồ sơ khi bạn mở chúng.",
                                               "Quên", "Không", destructive=False):
            return
        self.forget_games_requested.emit()

    # --- internals -------------------------------------------------------------------------------

    def _load_startup(self) -> None:
        state = self._startup.enabled()
        self._startup_saved = state
        with _signals_blocked(self.startup_check):
            self.startup_check.setChecked(bool(state))
        self.startup_check.setEnabled(state is not None)
        if state is None:
            self.startup_check.setToolTip("Không đọc được thư mục Startup của Windows")

    def _add_typed_blocked(self) -> None:
        if self.add_blocked([self.blocked_edit.text()]):
            self.blocked_edit.clear()

    def _reset_hotkeys(self) -> None:
        defaults = Hotkeys()
        for name, edit in self.hotkey_edits.items():
            edit.set_combo(getattr(defaults, name))
        self._check_hotkeys()

    def _check_hotkeys(self) -> None:
        problems = self.hotkey_problems()
        for name, edit in self.hotkey_edits.items():
            edit.setStyleSheet("QLineEdit { background: #ffcdd2; color: #6e0000; }" if name in problems else "")
            edit.setToolTip(problems.get(name, HOTKEY_TIP))
        self.hotkey_error_label.setText("\n".join(f"{HOTKEY_LABELS.get(n, n)}: {p}" for n, p in problems.items()))

    def _on_capturing_changed(self, _on: bool) -> None:
        self.hotkey_capture.emit(any(e.is_capturing() for e in self.hotkey_edits.values()))

    def _update_guard_hint(self) -> None:
        self.guard_hint.setText(GUARD_HINTS.get(self.guard_combo.currentData(), ""))

    def _update_preview(self) -> None:
        alpha = self.alpha_slider.value()
        self.alpha_label.setText(f"{round(alpha * 100 / 255)} %")
        cfg = Config(font_family=self.font_combo.currentText() or str(self._config.font_family),
                     min_font_px=self.min_font_spin.value(), box_alpha=alpha, show_border=self.border_check.isChecked())
        self.preview.set_config(cfg)

    def _copy_llm_command(self) -> None:
        from app.ui.first_run import setup_command

        QGuiApplication.clipboard().setText(setup_command())
        self.llm_copy_button.setText("Đã sao chép")

    def _on_defaults_clicked(self) -> None:
        if self.ask_before_discard and not ask(self, "Đặt lại mặc định?", DEFAULTS_QUESTION, "Đặt lại", "Không",
                                               destructive=False):
            return
        self.reset_defaults()

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self.status_label.setStyleSheet("color: #b71c1c;" if error else "")
        self.status_label.setText(text)

    def _on_save_clicked(self) -> None:
        if self.save() and not self._restart_pending:
            self.accept()

    def _on_restart_clicked(self) -> None:
        if self.is_dirty() and not self.save():
            return
        self.restart_requested.emit()

    def showEvent(self, event) -> None:
        self._popups.install()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._popups.remove()
        for edit in self.hotkey_edits.values():
            edit.stop_capture()

    def reject(self) -> None:
        if self.ask_before_discard and self.is_dirty() and not ask_discard(self):
            return
        super().reject()


def _clean_exe(text: str) -> str:
    return os.path.basename(str(text).strip().strip('"').replace("/", "\\")).strip()


class _ProbeBridge(QObject):
    """"Kiểm tra" result from the probe thread (parented to the application, not to the window)."""

    done = Signal(str, bool)

    def __init__(self) -> None:
        super().__init__(QCoreApplication.instance())
