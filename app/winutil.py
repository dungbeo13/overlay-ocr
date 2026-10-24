"""Small Win32 helpers shared by capture, OCR and overlay code."""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

# DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
_PMV2 = ctypes.c_void_p(-4)


class _PowerThrottlingState(ctypes.Structure):
    _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]


def set_power_throttling(enabled: bool) -> bool:
    """Opt this process in/out of EcoQoS execution-speed throttling.

    A throttled background process is parked on E-cores at low clocks: ONNX OCR measured ~5x
    slower while another app (a game) had focus. The app calls this with False at startup.
    """
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    state = _PowerThrottlingState(1, 0x1, 0x1 if enabled else 0x0)  # PROCESS_POWER_THROTTLING_EXECUTION_SPEED
    return bool(k32.SetProcessInformation(k32.GetCurrentProcess(), 4, ctypes.byref(state), ctypes.sizeof(state)))


def enable_per_monitor_dpi_awareness() -> bool:
    """Make this process Per-Monitor DPI Aware v2 so all coordinates are physical pixels.

    Call before creating any window or capture session. Qt 6 does this itself when
    QApplication is created, so non-Qt scripts (benchmarks) are the main callers.
    Returns True if the process is PMv2 after the call.
    """
    user32 = ctypes.windll.user32
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetProcessDpiAwarenessContext(_PMV2)  # fails harmlessly if already set
    user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    current = user32.GetThreadDpiAwarenessContext()
    return bool(user32.AreDpiAwarenessContextsEqual(current, _PMV2))


def windows_build() -> int:
    return sys.getwindowsversion().build


# The app's AppUserModelID: the taskbar groups our windows under it (not under pythonw.exe / Python) and
# takes their name and icon from the Start Menu shortcut that carries the same ID (tools/install_shortcut.py).
APP_USER_MODEL_ID = "ScreenTranslator.App"


def set_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> bool:
    """SetCurrentProcessExplicitAppUserModelID; call before the first window is shown."""
    try:
        shell32 = ctypes.windll.shell32
        shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]
        shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
        return shell32.SetCurrentProcessExplicitAppUserModelID(app_id) == 0
    except (AttributeError, OSError):
        return False
