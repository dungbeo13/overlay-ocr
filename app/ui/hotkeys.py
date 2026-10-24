"""Global hotkeys via RegisterHotKey (MOD_NOREPEAT) and a Qt native event filter.

Hotkeys are registered with hWnd=NULL, so Windows posts WM_HOTKEY to the registering (GUI)
thread's queue; Qt's event dispatcher hands it to native event filters. With PySide6 6.12 the
filter is called as nativeEventFilter(eventType: QByteArray, message: shiboken6.VoidPtr) and
must return a (handled: bool, result: int) tuple; int(message) is the address of the MSG.
No keyboard hooks, nothing is injected anywhere.
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, QThread, QTimer, Signal

log = logging.getLogger(__name__)

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x0008, 0x4000
WM_HOTKEY = 0x0312
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
_user32.RegisterHotKey.restype = wintypes.BOOL
_user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.UnregisterHotKey.restype = wintypes.BOOL
_MSG_MESSAGE_OFFSET = wintypes.MSG.message.offset

_MODIFIERS = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL, "ctl": MOD_CONTROL,
    "alt": MOD_ALT, "menu": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN, "windows": MOD_WIN, "super": MOD_WIN, "meta": MOD_WIN, "cmd": MOD_WIN,
}
_MOD_NAMES = ((MOD_CONTROL, "Ctrl"), (MOD_ALT, "Alt"), (MOD_SHIFT, "Shift"), (MOD_WIN, "Win"))

_KEYS: dict[str, int] = {
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
    "backspace": 0x08, "insert": 0x2D, "ins": 0x2D, "delete": 0x2E, "del": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "pause": 0x13, "break": 0x13, "printscreen": 0x2C, "prtsc": 0x2C, "print": 0x2C,
    "scrolllock": 0x91, "numlock": 0x90,
    ";": 0xBA, "semicolon": 0xBA, "=": 0xBB, "plus": 0xBB, "equal": 0xBB, ",": 0xBC, "comma": 0xBC,
    "-": 0xBD, "minus": 0xBD, ".": 0xBE, "period": 0xBE, "/": 0xBF, "slash": 0xBF,
    "`": 0xC0, "backquote": 0xC0, "grave": 0xC0, "tilde": 0xC0, "[": 0xDB, "\\": 0xDC, "backslash": 0xDC,
    "]": 0xDD, "'": 0xDE, "quote": 0xDE,
    "multiply": 0x6A, "add": 0x6B, "subtract": 0x6D, "decimal": 0x6E, "divide": 0x6F,
}
_KEY_NAMES = {0x20: "Space", 0x09: "Tab", 0x0D: "Enter", 0x1B: "Esc", 0x08: "Backspace", 0x2D: "Insert",
              0x2E: "Delete", 0x24: "Home", 0x23: "End", 0x21: "PageUp", 0x22: "PageDown", 0x25: "Left",
              0x26: "Up", 0x27: "Right", 0x28: "Down", 0x13: "Pause", 0x2C: "PrintScreen", 0x91: "ScrollLock",
              0x90: "NumLock", 0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/", 0xC0: "`",
              0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'", 0x6A: "Num*", 0x6B: "Num+", 0x6D: "Num-",
              0x6E: "Num.", 0x6F: "Num/"}


def _vk(token: str) -> int:
    if len(token) == 1 and ("a" <= token <= "z" or "0" <= token <= "9"):
        return ord(token.upper())
    if token in _KEYS:
        return _KEYS[token]
    if token.startswith("f") and token[1:].isdigit() and 1 <= int(token[1:]) <= 24:
        return 0x70 + int(token[1:]) - 1
    for prefix in ("numpad", "num"):
        if token.startswith(prefix) and token[len(prefix):].isdigit() and len(token) == len(prefix) + 1:
            return 0x60 + int(token[len(prefix):])
    raise ValueError(f"unknown key {token!r}")


def parse_combo(combo: str) -> tuple[int, int]:
    """'ctrl+alt+r' -> (MOD_CONTROL | MOD_ALT, 0x52). MOD_NOREPEAT is added at registration.

    Modifiers: ctrl, alt, shift, win (aliases control, super, meta, ...). Exactly one key:
    a-z, 0-9, f1-f24, numpad0-9, named keys (space, enter, esc, home, pageup, left, ...) or
    punctuation ("ctrl++" is ctrl+plus). Raises ValueError on anything else.
    """
    if not isinstance(combo, str):
        raise ValueError("hotkey must be a string")
    s = combo.strip().lower().replace(" ", "")
    if not s:
        raise ValueError("empty hotkey")
    if s.endswith("++"):
        s = s[:-2] + "+plus"
    elif s == "+":
        s = "plus"
    tokens = s.split("+")
    if any(not t for t in tokens):
        raise ValueError(f"malformed hotkey {combo!r}")
    mods, vk = 0, None
    for t in tokens:
        if t in _MODIFIERS:
            bit = _MODIFIERS[t]
            if mods & bit:
                raise ValueError(f"modifier repeated in {combo!r}")
            mods |= bit
        else:
            if vk is not None:
                raise ValueError(f"more than one key in {combo!r}")
            vk = _vk(t)
    if vk is None:
        raise ValueError(f"no key in {combo!r}")
    return mods, vk


def format_combo(combo: str) -> str:
    """Display form: 'ctrl+alt+r' -> 'Ctrl+Alt+R' (input returned unchanged if invalid)."""
    try:
        mods, vk = parse_combo(combo)
    except ValueError:
        return combo
    parts = [name for bit, name in _MOD_NAMES if mods & bit]
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        key = chr(vk)
    elif 0x70 <= vk <= 0x87:
        key = f"F{vk - 0x6F}"
    elif 0x60 <= vk <= 0x69:
        key = f"Num{vk - 0x60}"
    else:
        key = _KEY_NAMES.get(vk, hex(vk))
    return "+".join(parts + [key])


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, manager: "HotkeyManager") -> None:
        super().__init__()
        self._manager = manager
        self._last: tuple | None = None

    def nativeEventFilter(self, eventType, message):  # noqa: N802 (Qt API)
        try:
            addr = int(message)
            if not addr or ctypes.c_uint.from_address(addr + _MSG_MESSAGE_OFFSET).value != WM_HOTKEY:
                return False, 0
            msg = wintypes.MSG.from_address(addr)
            # The same MSG could reach us through two dispatch paths ("windows_dispatcher_MSG" and
            # "windows_generic_MSG"); only one was seen in practice, but never act twice on it.
            kind = bytes(eventType) if eventType is not None else b""
            key = (int(msg.time), int(msg.wParam), int(msg.lParam), int(msg.pt.x), int(msg.pt.y))
            if self._last is not None and self._last[0] == key and self._last[1] != kind:
                return True, 0
            self._last = (key, kind)
            return self._manager._on_wm_hotkey(int(msg.wParam)), 0
        except Exception:  # never let an exception escape into Qt's dispatcher
            log.exception("hotkey filter failed")
            return False, 0


class HotkeyManager(QObject):
    """Registers global hotkeys by name and emits `triggered(name)` (queued) when one is pressed.

    Create and use on the GUI thread: RegisterHotKey binds to the calling thread's queue.
    """

    triggered = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ids: dict[int, str] = {}  # hotkey id -> name
        self._names: dict[str, tuple[int, str]] = {}  # name -> (id, combo)
        self._errors: dict[str, str] = {}
        self._next_id = 1
        self._filter = _HotkeyFilter(self)
        app = QCoreApplication.instance()
        if app is None:
            raise RuntimeError("HotkeyManager needs a QApplication")
        app.installNativeEventFilter(self._filter)
        self._installed = True

    parse_combo = staticmethod(parse_combo)
    format_combo = staticmethod(format_combo)

    def register(self, name: str, combo: str) -> bool:
        """Register (or re-register) `name`. On failure the reason goes to last_errors(); never raises."""
        self._check_thread()
        self.unregister(name)
        try:
            mods, vk = parse_combo(combo)
        except ValueError as e:
            self._errors[name] = f"Phím tắt không hợp lệ '{combo}': {e}"
            log.warning("hotkey %s: %s", name, self._errors[name])
            return False
        hid = self._next_id
        self._next_id += 1
        if not _user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
            err = ctypes.get_last_error()
            if err == ERROR_HOTKEY_ALREADY_REGISTERED:
                self._errors[name] = f"{format_combo(combo)} đang được ứng dụng khác sử dụng"
            else:
                self._errors[name] = f"Không đăng ký được {format_combo(combo)} (lỗi Windows {err})"
            log.warning("hotkey %s: %s", name, self._errors[name])
            return False
        self._ids[hid] = name
        self._names[name] = (hid, combo)
        self._errors.pop(name, None)
        log.info("hotkey %s = %s", name, format_combo(combo))
        return True

    def unregister(self, name: str) -> None:
        entry = self._names.pop(name, None)
        if entry is not None:
            hid, _ = entry
            self._ids.pop(hid, None)
            _user32.UnregisterHotKey(None, hid)

    def unregister_all(self) -> None:
        for name in list(self._names):
            self.unregister(name)

    def registered(self) -> dict[str, str]:
        """name -> combo of the hotkeys currently registered."""
        return {name: combo for name, (_, combo) in self._names.items()}

    def last_errors(self) -> dict[str, str]:
        """name -> Vietnamese reason, for registrations that failed (cleared on success)."""
        return dict(self._errors)

    def close(self) -> None:
        """Unregister everything and remove the native event filter."""
        self.unregister_all()
        app = QCoreApplication.instance()
        if self._installed and app is not None:
            app.removeNativeEventFilter(self._filter)
        self._installed = False

    def handle_native_message(self, eventType, message) -> tuple[bool, int]:
        """Feed one native message (MSG address) through the filter; for tests."""
        return self._filter.nativeEventFilter(eventType, message)

    def _on_wm_hotkey(self, hid: int) -> bool:
        name = self._ids.get(hid)
        if name is None:
            return False
        # Queued: slots may open windows or menus; do not run them inside the native filter.
        QTimer.singleShot(0, self, lambda n=name: self.triggered.emit(n))
        return True

    def _check_thread(self) -> None:
        app = QCoreApplication.instance()
        if app is not None and QThread.currentThread() is not app.thread():
            raise RuntimeError("HotkeyManager must be used on the GUI thread")
