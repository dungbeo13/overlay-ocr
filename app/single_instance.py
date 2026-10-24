"""One running app per data directory: a named mutex, plus a local socket the next launch talks to.

The first instance creates the mutex "Local\\<key>" (key = instance_key(DATA_DIR)) and listens on a
QLocalServer of the same name (a named pipe only this user can open). A second launch, e.g. the
Desktop shortcut clicked again, finds the mutex, sends "show" over the socket and exits; the running
app then shows its tray menu. Two copies would fight over config.json, the hotkeys and the GPU.

The key depends on the data directory, so a test run with its own SCREEN_TRANSLATOR_HOME (e2e_check)
never collides with the app the user has open.

Protocol: the client writes one command line and reads one reply line. Commands: show, settings,
guide, quit, ping (the reply is the app's status as JSON). Anything else is answered "error".
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)

COMMANDS = ("show", "settings", "guide", "quit", "ping")
MAX_LINE = 256
CLIENT_TIMEOUT_MS = 10_000
ERROR_ALREADY_EXISTS = 183
ERROR_PIPE_BUSY = 231
ASFW_ANY = 0xFFFFFFFF

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
_kernel32.WaitNamedPipeW.restype = wintypes.BOOL
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
_user32.AllowSetForegroundWindow.restype = wintypes.BOOL


def instance_key(data_dir: Path | str) -> str:
    """"ScreenTranslator-<12 hex>" for a data directory (case and spelling of the path normalised)."""
    norm = os.path.normcase(os.path.abspath(str(data_dir)))
    return "ScreenTranslator-" + hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


class InstanceLock:
    """The named mutex. acquire() is False while another process (or another InstanceLock) holds it."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.name = f"Local\\{key}"
        self._handle: int | None = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def try_acquire(self) -> bool:
        if self._handle is not None:
            return True
        handle = _kernel32.CreateMutexW(None, False, self.name)
        err = ctypes.get_last_error()
        if not handle:
            raise OSError(err, f"CreateMutexW({self.name}) failed")
        if err == ERROR_ALREADY_EXISTS:
            _kernel32.CloseHandle(handle)
            return False
        self._handle = int(handle)
        return True

    def acquire(self, wait_s: float = 0.0, poll_s: float = 0.1) -> bool:
        """Take the mutex, waiting up to `wait_s` for another instance to let go (a restart)."""
        deadline = time.monotonic() + max(0.0, wait_s)
        while True:
            if self.try_acquire():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(poll_s)

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            _kernel32.CloseHandle(handle)

    def __enter__(self) -> "InstanceLock":
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


class InstanceServer(QObject):
    """The running instance's end of the socket (GUI thread). `command(name)` is emitted for every
    valid command after its reply has been written; `status()` provides the "ping" answer."""

    command = Signal(str)

    def __init__(self, key: str, status: Callable[[], dict] | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self._status = status
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._on_connection)
        self.received: list[str] = []  # diagnostics / tests

    def listen(self) -> bool:
        if self._server.isListening():
            return True
        if self._server.listen(self.key):
            return True
        QLocalServer.removeServer(self.key)  # a stale socket (not used on Windows pipes, harmless)
        ok = self._server.listen(self.key)
        if not ok:
            log.warning("single-instance socket %s: %s", self.key, self._server.errorString())
        return ok

    def is_listening(self) -> bool:
        return self._server.isListening()

    def close(self) -> None:
        self._server.close()

    def _on_connection(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            if sock is None:
                return
            sock.readyRead.connect(lambda s=sock: self._on_ready(s))
            sock.disconnected.connect(sock.deleteLater)
            QTimer.singleShot(CLIENT_TIMEOUT_MS, sock, sock.abort)  # a client that never hangs up
            if sock.bytesAvailable():
                self._on_ready(sock)

    def _on_ready(self, sock: QLocalSocket) -> None:
        if not sock.canReadLine():
            if sock.bytesAvailable() > MAX_LINE:
                sock.abort()
            return
        line = bytes(sock.readLine(MAX_LINE)).decode("utf-8", "replace").strip().lower()
        cmd = line.split()[0] if line else ""
        if cmd not in COMMANDS:
            log.warning("single-instance socket: unknown command %r", line[:40])
            reply = "error"
        elif cmd == "ping":
            try:
                reply = json.dumps(self._status() if self._status else {"pid": os.getpid()}, ensure_ascii=False)
            except Exception:
                log.warning("single-instance status failed", exc_info=True)
                reply = json.dumps({"pid": os.getpid()})
        else:
            reply = "ok"
        sock.write((reply + "\n").encode("utf-8"))
        sock.flush()
        # The client hangs up once it has read the line: disconnecting here (DisconnectNamedPipe) would
        # throw away a reply it has not read yet.
        if cmd in COMMANDS:
            self.received.append(cmd)
            log.info("single-instance command: %s", cmd)
            self.command.emit(cmd)


def pipe_path(key: str) -> str:
    """The named pipe QLocalServer.listen(key) creates on Windows."""
    return "\\\\.\\pipe\\" + key


ERROR_FILE_NOT_FOUND = 2


def pipe_exists(key: str) -> bool:
    """Whether an instance listens on `key` (busy or not), without connecting to it: a second launch
    tells "still starting" (no pipe yet) from "hung" (a pipe that never answers)."""
    if _kernel32.WaitNamedPipeW(pipe_path(key), 1):
        return True
    return ctypes.get_last_error() != ERROR_FILE_NOT_FOUND  # ERROR_SEM_TIMEOUT: there, but all instances busy


def send_command(key: str, command: str, timeout_s: float = 2.0) -> str | None:
    """Send `command` to the instance listening on `key`; its reply line, or None when nobody
    answered in time. Plain pipe I/O: no Qt event loop needed (a second launch exits right after)."""
    path = pipe_path(key)
    deadline = time.monotonic() + timeout_s
    result: list[str | None] = []

    def talk() -> None:
        while True:
            try:
                pipe = open(path, "r+b", buffering=0)  # noqa: SIM115 - closed below
                break
            except OSError as e:
                left = int((deadline - time.monotonic()) * 1000)
                if getattr(e, "winerror", None) != ERROR_PIPE_BUSY or left <= 0:
                    result.append(None)  # nobody listens (ERROR_FILE_NOT_FOUND)
                    return
                _kernel32.WaitNamedPipeW(path, max(1, min(left, 500)))  # the server is between connections
        try:
            if command != "ping":
                # The running app may bring its menu / a window to the front: we were just launched by
                # the user, so we may hand that right on (Windows otherwise flashes the taskbar instead).
                _user32.AllowSetForegroundWindow(ASFW_ANY)
            pipe.write((command.strip() + "\n").encode("utf-8"))
            line = pipe.readline(64 * 1024)
            result.append(line.decode("utf-8", "replace").strip() if line else None)
        except OSError:
            result.append(None)
        finally:
            pipe.close()

    t = threading.Thread(target=talk, name="instance-client", daemon=True)
    t.start()
    t.join(max(0.0, deadline - time.monotonic()) + 0.05)
    return result[0] if result else None
