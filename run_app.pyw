"""Windowless entry point: what the Desktop / Start Menu shortcuts run, with no console window.

    <venv>\\Scripts\\pythonw.exe run_app.pyw [the options of python -m app]

Works from any working directory (it switches to the project folder). pythonw.exe has no console, so
standard output goes nowhere, Python warnings go to the log, and a failure is shown in a Vietnamese
message box and logged: by app.main once its logging is up, and here for anything earlier (a broken
PySide6 / OpenCV install, a syntax error), in data/logs/startup-error.log. A second launch while the app
runs opens the running app's tray menu instead (app.single_instance).
"""
from __future__ import annotations

import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "Screen Translator"


def _data_dir() -> str:
    return os.environ.get("SCREEN_TRANSLATOR_HOME") or os.path.join(ROOT, "data")


def _log_failure(text: str) -> str:
    """Append to <data>/logs/startup-error.log (or %TEMP% when that is not writable); returns the path."""
    for folder in (os.path.join(_data_dir(), "logs"), os.environ.get("TEMP") or ROOT):
        path = os.path.join(folder, "startup-error.log")
        try:
            os.makedirs(folder, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {APP_NAME} không khởi động được\n{text}\n")
            return path
        except OSError:
            continue
    return ""


def _message_box(title: str, text: str) -> None:
    """Qt message box (capture-excluded) when Qt still imports, else a plain Win32 one."""
    try:
        from app.main import show_error_box

        show_error_box(title, text.split("\n\n", 1)[0], text.split("\n\n", 1)[1] if "\n\n" in text else "")
        return
    except Exception:
        pass
    import ctypes

    ctypes.windll.user32.MessageBoxW(None, text, title, 0x10 | 0x10000)  # MB_ICONERROR | MB_SETFOREGROUND


def _quiet_stdio() -> None:
    """pythonw: no console. Writes to sys.stdout / sys.stderr would raise; send them nowhere."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115
    import logging

    logging.captureWarnings(True)


def run(argv: list[str] | None = None) -> int:
    os.chdir(ROOT)
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    _quiet_stdio()
    try:
        from app.main import main
    except Exception:
        detail = traceback.format_exc()
        path = _log_failure(detail)
        last = detail.strip().splitlines()[-1] if detail.strip() else "lỗi không rõ"
        _message_box(f"{APP_NAME}: lỗi khởi động",
                     f"{APP_NAME} không khởi động được (thiếu hoặc hỏng thư viện?).\n\n{last}"
                     + (f"\n\nChi tiết: {path}" if path else ""))
        return 1
    try:
        return main(sys.argv[1:] if argv is None else argv, windowless=True)
    except SystemExit as exc:  # argparse: the box was shown by app.main
        return exc.code if isinstance(exc.code, int) else 1
    except Exception:  # main() reports its own startup errors; this is a last resort
        detail = traceback.format_exc()
        path = _log_failure(detail)
        _message_box(f"{APP_NAME}: lỗi", f"{APP_NAME} gặp lỗi và phải dừng.\n\n{detail.strip().splitlines()[-1]}"
                     + (f"\n\nChi tiết: {path}" if path else ""))
        return 1


if __name__ == "__main__":
    sys.exit(run())
