"""Tray icon, global hotkeys and the anti-cheat game guard (PySide6, Win32 via ctypes)."""
from .game_guard import GameGuard, foreground_window_pid, matches, process_image_name
from .hotkeys import HotkeyManager, format_combo, parse_combo
from .tray import TrayController, make_icon

__all__ = [
    "GameGuard",
    "HotkeyManager",
    "TrayController",
    "foreground_window_pid",
    "format_combo",
    "make_icon",
    "matches",
    "parse_combo",
    "process_image_name",
]
