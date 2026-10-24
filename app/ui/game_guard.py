"""Anti-cheat guard: notices when a blocked game owns the foreground window.

Only window-manager information is used: GetForegroundWindow -> GetWindowThreadProcessId ->
OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) -> QueryFullProcessImageNameW (the access level
Task Manager uses; no memory reads, no injection). Names are cached per (HWND, PID) for the last
NAME_CACHE_SIZE windows, so a game is opened once per window, not every second and not again on
every alt-tab back to it. If OpenProcess is refused (protected or elevated process) the name
comes from a Toolhelp process snapshot, which opens nothing.

An out-of-context WinEvent hook (EVENT_SYSTEM_FOREGROUND, no DLL is loaded into other processes)
triggers an immediate check on focus changes; the ~1 s poll remains the source of truth.
"""
from __future__ import annotations

import ctypes
import logging
import os
from collections.abc import Callable, Iterable
from ctypes import wintypes

from PySide6.QtCore import QObject, QTimer, Signal

from app.config import Config

log = logging.getLogger(__name__)

MODES = ("pause", "warn", "off")
NAME_CACHE_SIZE = 64  # foreground windows whose process name is remembered

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.GetForegroundWindow.argtypes = []
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD)]
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TH32CS_SNAPPROCESS = 0x2
_INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
_EVENT_SYSTEM_FOREGROUND = 0x0003
_WINEVENT_OUTOFCONTEXT, _WINEVENT_SKIPOWNPROCESS = 0x0000, 0x0002


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


_kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_kernel32.Process32FirstW.restype = wintypes.BOOL
_kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_kernel32.Process32NextW.restype = wintypes.BOOL

_WINEVENTPROC = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG,
                                   wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
_user32.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, _WINEVENTPROC,
                                    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
_user32.SetWinEventHook.restype = wintypes.HANDLE
_user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
_user32.UnhookWinEvent.restype = wintypes.BOOL


def _image_path_by_handle(pid: int) -> str | None:
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return buf.value or None
    finally:
        _kernel32.CloseHandle(handle)


def _image_name_by_handle(pid: int) -> str | None:
    path = _image_path_by_handle(pid)
    return (os.path.basename(path) or None) if path else None


def _image_name_by_snapshot(pid: int) -> str | None:
    snap = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == _INVALID_HANDLE_VALUE:
        return None
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = _kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.th32ProcessID == pid:
                return entry.szExeFile or None
            ok = _kernel32.Process32NextW(snap, ctypes.byref(entry))
        return None
    finally:
        _kernel32.CloseHandle(snap)


def process_image_name(pid: int) -> str | None:
    """Exe basename of `pid`; None if it cannot be determined (never raises)."""
    if pid <= 0:
        return None
    try:
        return _image_name_by_handle(pid) or _image_name_by_snapshot(pid)
    except OSError:
        return None


def process_image_path(pid: int) -> str | None:
    """Full image path of `pid` when OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) allows it, else the
    basename from a Toolhelp snapshot (opens nothing); None if neither works (never raises). The same call
    as process_image_name: the path only tells the game lookup which FILES to read (app.lookup.game_id)."""
    if pid <= 0:
        return None
    try:
        return _image_path_by_handle(pid) or _image_name_by_snapshot(pid)
    except OSError:
        return None


def foreground_window_pid() -> tuple[int, int] | None:
    """(HWND, PID) of the foreground window, None when there is none (e.g. secure desktop)."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None
    return int(hwnd), int(pid.value)


def _normalize(name: str) -> str:
    return os.path.basename(name.strip().strip('"').replace("/", "\\")).lower()


def matches(exe: str | None, blocked: Iterable[str]) -> bool:
    """Case-insensitive exe-basename match; a blocked entry without ".exe" also matches the stem."""
    if not exe:
        return False
    name = _normalize(exe)
    stem = name[:-4] if name.endswith(".exe") else name
    for entry in blocked:
        b = _normalize(entry) if isinstance(entry, str) else ""
        if b and (b == name or (not b.endswith(".exe") and b == stem)):
            return True
    return False


Reader = Callable[[], "tuple[int, str | None] | None"]


class GameGuard(QObject):
    """Polls the foreground process and reports blocked games.

    Signals:
        changed(guarded, exe): mode "pause" only; emitted on every change of (guarded, exe).
        warn(exe): mode "warn"; once per exe for the lifetime of the guard.
        foreground_changed(exe): any mode, while the guard runs; the exe basename of a new foreground
            process (never our own process; not repeated while the same exe stays in front).
            `foreground_path` then holds its full image path when Windows gave one ("" otherwise).
    Mode and list are read from `config` on every check, so tray changes apply at once (call
    refresh() to apply them immediately). Unknown modes behave like "pause".
    """

    changed = Signal(bool, str)
    warn = Signal(str)
    foreground_changed = Signal(str)

    def __init__(self, config: Config, interval_ms: int = 1000, parent: QObject | None = None, *,
                 reader: Reader | None = None, use_event_hook: bool = True) -> None:
        super().__init__(parent)
        self._config = config
        self._reader = reader or self._read_foreground
        self._use_hook = use_event_hook
        self._timer = QTimer(self)
        self._timer.setInterval(max(50, int(interval_ms)))
        self._timer.timeout.connect(self.check)
        self._guarded = False
        self._exe = ""
        self._warned: set[str] = set()
        self._own_pid = os.getpid()
        self._name_cache: dict[tuple[int, int], str] = {}  # (hwnd, pid) -> exe name, oldest first
        self._last_external: str | None = None  # last foreground exe that was not our process
        self._last_emitted = ""  # lower-case exe of the last foreground_changed
        self._hook = None
        self._hook_proc = None
        self.last_foreground: str | None = None  # diagnostics
        self.foreground_path = ""  # full image path of the last foreign foreground exe (local only: game lookup)

    # --- public API ------------------------------------------------------------------------

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()
            self._install_hook()
        self.check()

    def stop(self) -> None:
        self._timer.stop()
        self._remove_hook()

    def is_running(self) -> bool:
        return self._timer.isActive()

    def current(self) -> tuple[bool, str]:
        return self._guarded, self._exe

    def refresh(self) -> tuple[bool, str]:
        return self.check()

    @staticmethod
    def foreground_process_name() -> str | None:
        """Exe basename of the process owning the foreground window, None if unknown."""
        fg = foreground_window_pid()
        return process_image_name(fg[1]) if fg is not None else None

    matches = staticmethod(matches)

    def check(self) -> tuple[bool, str]:
        """Read the foreground process once and update the state."""
        try:
            fg = self._reader()
        except Exception:
            log.warning("foreground process lookup failed", exc_info=True)
            fg = None
        if fg is None:
            return self.current()  # no foreground window (lock screen, UAC, alt-tab): keep the state
        pid, exe = fg
        return self.evaluate(exe, pid)

    def evaluate(self, exe: str | None, pid: int | None = None) -> tuple[bool, str]:
        """Apply the guard rules to a foreground exe name (public for tests)."""
        if pid is not None and pid == self._own_pid:
            # Our tray menu / selector never counts: judge by the last other foreground window, so
            # a mode change made from the tray menu while a game is behind it applies at once.
            exe = self._last_external
        elif exe is not None:
            self._last_external = exe
            name = os.path.basename(exe)
            self.foreground_path = exe if name != exe else ""
            if name and name.lower() != self._last_emitted:
                self._last_emitted = name.lower()
                self.foreground_changed.emit(name)
        self.last_foreground = exe
        mode = (self._config.guard_mode or "pause").lower()
        if mode == "off":
            self._set(False, "")
            return self.current()
        if exe is None:
            return self.current()  # name unknown: keep the state rather than flapping
        hit = matches(exe, self._config.blocked_processes)
        name = os.path.basename(exe)
        if mode == "warn":
            if hit and name.lower() not in self._warned:
                self._warned.add(name.lower())
                log.info("anti-cheat game in foreground (warn only): %s", name)
                self.warn.emit(name)
            self._set(False, "")
        else:
            self._set(hit, name if hit else "")
        return self.current()

    # --- internals -------------------------------------------------------------------------

    def _set(self, guarded: bool, exe: str) -> None:
        if (guarded, exe) != (self._guarded, self._exe):
            self._guarded, self._exe = guarded, exe
            log.info("game guard: %s", f"guarded ({exe})" if guarded else "clear")
            self.changed.emit(guarded, exe)

    def _read_foreground(self) -> tuple[int, str | None] | None:
        fg = foreground_window_pid()
        if fg is None:
            return None
        hwnd, pid = fg
        if pid == self._own_pid:
            return pid, None
        return pid, self._cached_name(hwnd, pid, process_image_path)  # full path: evaluate() uses the basename

    def _cached_name(self, hwnd: int, pid: int, lookup: Callable[[int], str | None] = process_image_name) -> str | None:
        key = (hwnd, pid)
        name = self._name_cache.pop(key, None)  # re-inserted below: most recent last
        if name is None:
            name = lookup(pid)
            if name is None:
                return None  # not cached: try again on the next check
        self._name_cache[key] = name
        while len(self._name_cache) > NAME_CACHE_SIZE:
            del self._name_cache[next(iter(self._name_cache))]
        return name

    def _install_hook(self) -> None:
        if not self._use_hook or self._hook:
            return

        def _on_event(_hook, _event, _hwnd, _id_object, _id_child, _thread, _time) -> None:
            QTimer.singleShot(0, self, self.check)

        self._hook_proc = _WINEVENTPROC(_on_event)
        self._hook = _user32.SetWinEventHook(_EVENT_SYSTEM_FOREGROUND, _EVENT_SYSTEM_FOREGROUND, None,
                                             self._hook_proc, 0, 0,
                                             _WINEVENT_OUTOFCONTEXT | _WINEVENT_SKIPOWNPROCESS)
        if not self._hook:
            log.info("foreground WinEvent hook unavailable; polling only")
            self._hook_proc = None

    def _remove_hook(self) -> None:
        if self._hook:
            _user32.UnhookWinEvent(self._hook)
        self._hook = None
        self._hook_proc = None
