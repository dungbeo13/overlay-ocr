"""Probe: is a PySide6 overlay with WDA_EXCLUDEFROMCAPTURE invisible to dxcam, WGC and mss?

Shows an opaque magenta rectangle (default 400x200 physical px) inside a translucent, topmost,
click-through tool window (the overlay's final window flags) on the primary monitor and counts
the magenta pixels each capture backend sees over the window:

  baseline        window created but hidden      -> background magenta noise (should be 0)
  a_off           affinity OFF                    -> magenta MUST be seen (else the probe is invalid)
  b_on            affinity ON                     -> zero magenta expected
  c_recreate_on   setWindowFlags() toggled twice (hides the window, may recreate the HWND),
                  re-shown; the CaptureExclusion filter re-applies on Show -> zero magenta expected
  c2_destroy_recreate_on
                  QWidget.destroy() + show(): a guaranteed NEW HWND (starts at WDA_NONE; Qt 6.12's
                  setWindowFlags keeps the HWND, so c alone never tests this). Only the filter's
                  WinIdChange/Show re-apply can keep it excluded -> zero magenta expected
  c_control_off   affinity OFF on the re-shown window -> magenta again (proves c was not vacuous)

During c and c2 a dxcam poller in a thread records every new frame to catch a transient leak.
Also records DPI awareness, monitors (bounds, DPI, scale, HDR) and the window's styles.
Every capture uses a fresh backend instance so its first frame is guaranteed current.

Run: python -m bench.probe_affinity [--save-images]
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
import threading
import time
import traceback
from ctypes import wintypes
from dataclasses import asdict

import numpy as np

from bench.common import RESULTS_DIR, print_table, save_results

FINAL_FLAGS_DOC = "FramelessWindowHint|Tool|WindowStaysOnTopHint|WindowTransparentForInput|WindowDoesNotAcceptFocus"

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.GetDpiAwarenessContextForProcess.argtypes = [wintypes.HANDLE]
_user32.GetDpiAwarenessContextForProcess.restype = ctypes.c_void_p
_user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
_user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
_user32.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
_user32.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_user32.AreDpiAwarenessContextsEqual.restype = wintypes.BOOL
_dwmapi = ctypes.WinDLL("dwmapi")
_dwmapi.DwmFlush.restype = ctypes.c_long

GWL_STYLE, GWL_EXSTYLE = -16, -20
_EX_FLAGS = {"TOPMOST": 0x8, "TRANSPARENT": 0x20, "TOOLWINDOW": 0x80, "LAYERED": 0x80000,
             "NOREDIRECTIONBITMAP": 0x200000, "NOACTIVATE": 0x8000000}
_PMV2 = ctypes.c_void_p(-4)


# --- Win32 helpers -------------------------------------------------------------------------

def window_rect(hwnd: int):
    from app.capture import Region
    rc = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(rc))
    return Region(rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)


def window_styles(hwnd: int) -> dict:
    ex = _user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
    style = _user32.GetWindowLongPtrW(hwnd, GWL_STYLE) & 0xFFFFFFFF
    return {"style": hex(style), "exstyle": hex(ex), "ex_flags": [k for k, v in _EX_FLAGS.items() if ex & v]}


def dpi_awareness() -> dict:
    proc = _user32.GetDpiAwarenessContextForProcess(None)
    thread = _user32.GetThreadDpiAwarenessContext()
    names = {-1: "invalid", 0: "unaware", 1: "system", 2: "per_monitor"}
    return {
        "process_awareness": names.get(_user32.GetAwarenessFromDpiAwarenessContext(proc), "?"),
        "process_is_pmv2": bool(_user32.AreDpiAwarenessContextsEqual(proc, _PMV2)),
        "thread_is_pmv2": bool(_user32.AreDpiAwarenessContextsEqual(thread, _PMV2)),
    }


def dwm_flush(times: int = 2) -> None:
    for _ in range(times):
        _dwmapi.DwmFlush()


# --- HDR / advanced color via DisplayConfig --------------------------------------------------

class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _PathSource(ctypes.Structure):
    _fields_ = [("adapterId", _LUID), ("id", ctypes.c_uint32), ("modeInfoIdx", ctypes.c_uint32),
                ("statusFlags", ctypes.c_uint32)]


class _Rational(ctypes.Structure):
    _fields_ = [("Numerator", ctypes.c_uint32), ("Denominator", ctypes.c_uint32)]


class _PathTarget(ctypes.Structure):
    _fields_ = [("adapterId", _LUID), ("id", ctypes.c_uint32), ("modeInfoIdx", ctypes.c_uint32),
                ("outputTechnology", ctypes.c_uint32), ("rotation", ctypes.c_uint32),
                ("scaling", ctypes.c_uint32), ("refreshRate", _Rational),
                ("scanLineOrdering", ctypes.c_uint32), ("targetAvailable", wintypes.BOOL),
                ("statusFlags", ctypes.c_uint32)]


class _PathInfo(ctypes.Structure):
    _fields_ = [("sourceInfo", _PathSource), ("targetInfo", _PathTarget), ("flags", ctypes.c_uint32)]


class _ModeInfo(ctypes.Structure):
    _fields_ = [("infoType", ctypes.c_uint32), ("id", ctypes.c_uint32), ("adapterId", _LUID),
                ("info", ctypes.c_ubyte * 48)]


class _DevInfoHeader(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("size", ctypes.c_uint32), ("adapterId", _LUID),
                ("id", ctypes.c_uint32)]


class _SourceName(ctypes.Structure):
    _fields_ = [("header", _DevInfoHeader), ("viewGdiDeviceName", wintypes.WCHAR * 32)]


class _AdvancedColor(ctypes.Structure):
    _fields_ = [("header", _DevInfoHeader), ("value", ctypes.c_uint32), ("colorEncoding", ctypes.c_uint32),
                ("bitsPerColorChannel", ctypes.c_uint32)]


def advanced_color_info() -> dict[str, dict]:
    """GDI device name -> advanced color state (HDR on = enabled and not just ACM wide color)."""
    u = ctypes.WinDLL("user32")
    u.GetDisplayConfigBufferSizes.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
    u.QueryDisplayConfig.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(_PathInfo),
                                     ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(_ModeInfo), ctypes.c_void_p]
    u.DisplayConfigGetDeviceInfo.argtypes = [ctypes.POINTER(_DevInfoHeader)]
    for f in (u.GetDisplayConfigBufferSizes, u.QueryDisplayConfig, u.DisplayConfigGetDeviceInfo):
        f.restype = ctypes.c_long
    qdc_only_active = 2
    n_paths, n_modes = ctypes.c_uint32(), ctypes.c_uint32()
    if u.GetDisplayConfigBufferSizes(qdc_only_active, ctypes.byref(n_paths), ctypes.byref(n_modes)) != 0:
        return {}
    paths, modes = (_PathInfo * n_paths.value)(), (_ModeInfo * n_modes.value)()
    if u.QueryDisplayConfig(qdc_only_active, ctypes.byref(n_paths), paths, ctypes.byref(n_modes), modes, None) != 0:
        return {}
    out = {}
    for p in paths[: n_paths.value]:
        src = _SourceName()
        src.header.type, src.header.size = 1, ctypes.sizeof(_SourceName)  # GET_SOURCE_NAME
        src.header.adapterId, src.header.id = p.sourceInfo.adapterId, p.sourceInfo.id
        adv = _AdvancedColor()
        adv.header.type, adv.header.size = 9, ctypes.sizeof(_AdvancedColor)  # GET_ADVANCED_COLOR_INFO
        adv.header.adapterId, adv.header.id = p.targetInfo.adapterId, p.targetInfo.id
        if u.DisplayConfigGetDeviceInfo(ctypes.byref(src.header)) or u.DisplayConfigGetDeviceInfo(ctypes.byref(adv.header)):
            continue
        v = adv.value
        out[src.viewGdiDeviceName] = {
            "advanced_color_supported": bool(v & 1), "advanced_color_enabled": bool(v & 2),
            "wide_color_enforced": bool(v & 4), "advanced_color_force_disabled": bool(v & 8),
            "hdr_on": bool(v & 2) and not (v & 4), "bits_per_channel": adv.bitsPerColorChannel,
            "refresh_hz": round(p.targetInfo.refreshRate.Numerator / max(1, p.targetInfo.refreshRate.Denominator), 2),
        }
    return out


# --- measurement ---------------------------------------------------------------------------

def magenta_count(img_bgr: np.ndarray, tol: int) -> int:
    b, g, r = img_bgr[..., 0], img_bgr[..., 1], img_bgr[..., 2]
    return int(np.count_nonzero((b >= 255 - tol) & (g <= tol) & (r >= 255 - tol)))


def capture_all(region, backends: list[str], tol: int, tag: str, save_images: bool) -> dict:
    """Grab `region` with a fresh instance of each backend; returns per-backend magenta counts."""
    import cv2
    from app.capture import create_backend

    out = {}
    for name in backends:
        t0 = time.perf_counter()
        try:
            with create_backend(name) as be:
                frame = None
                for _ in range(25):
                    frame = be.grab(region)
                    if frame is not None and frame.timestamp >= t0:
                        break
                    time.sleep(0.02)
                extra = {"border_setting_accepted": be.border_setting_accepted} if name == "wgc" else {}
            if frame is None:
                out[name] = {"error": "no frame"}
                continue
            out[name] = {
                "magenta_px": magenta_count(frame.image, tol),
                "fresh": frame.timestamp >= t0,
                "shape": list(frame.image.shape),
                "frame_region": asdict(frame.region),
                "capture_ms": round((time.perf_counter() - t0) * 1000, 1),
                **extra,
            }
            if save_images:
                img_dir = RESULTS_DIR / "probe_affinity_images"
                img_dir.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(img_dir / f"{tag}_{name}.png"), frame.image)
        except Exception as e:
            out[name] = {"error": repr(e)}
    return out


class TransientMonitor(threading.Thread):
    """Polls a region with dxcam in a thread and records magenta in every new frame."""

    def __init__(self, region, tol: int) -> None:
        super().__init__(daemon=True, name="transient-monitor")
        self.region, self.tol = region, tol
        self.stop_event, self.ready = threading.Event(), threading.Event()
        self.frames, self.leak_frames, self.max_magenta, self.error = 0, 0, 0, None

    def run(self) -> None:
        from app.capture import create_backend
        try:
            with create_backend("dxcam") as be:
                last_ts = None
                while not self.stop_event.is_set():
                    f = be.grab(self.region)
                    if f is not None and f.timestamp != last_ts:
                        last_ts = f.timestamp
                        self.frames += 1
                        n = magenta_count(f.image, self.tol)
                        self.max_magenta = max(self.max_magenta, n)
                        self.leak_frames += n > 0
                        self.ready.set()
                    time.sleep(0.002)
        except Exception as e:
            self.error = repr(e)
        finally:
            self.ready.set()

    def finish(self) -> dict:
        self.stop_event.set()
        self.join(timeout=5)
        return {"frames": self.frames, "frames_with_magenta": self.leak_frames,
                "max_magenta_px": self.max_magenta, "error": self.error}


# --- main ----------------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--width", type=int, default=400, help="magenta rect width, physical px")
    ap.add_argument("--height", type=int, default=200, help="magenta rect height, physical px")
    ap.add_argument("--x", type=int, default=200, help="window left, physical px from primary monitor origin")
    ap.add_argument("--y", type=int, default=200, help="window top, physical px from primary monitor origin")
    ap.add_argument("--tol", type=int, default=40, help="per-channel tolerance for magenta")
    ap.add_argument("--hold-ms", type=int, default=300, help="settle time after each change")
    ap.add_argument("--backends", default="dxcam,wgc,mss")
    ap.add_argument("--save-images", action="store_true", help="write each capture to bench/results/probe_affinity_images")
    ap.add_argument("--timeout", type=float, default=20.0, help="hard exit after this many seconds")
    ap.add_argument("--quick", action="store_true", help="accepted for CLI symmetry; the probe is always short")
    args = ap.parse_args()
    backends = [b.strip() for b in args.backends.split(",") if b.strip()]

    def _abort() -> None:
        print(f"probe_affinity: watchdog fired after {args.timeout}s, aborting", file=sys.stderr, flush=True)
        os._exit(3)

    watchdog = threading.Timer(args.timeout, _abort)
    watchdog.daemon = True
    watchdog.start()

    from PySide6.QtCore import QEventLoop, QPoint, QRect, Qt
    from PySide6.QtGui import QColor, QPainter
    from PySide6.QtWidgets import QApplication, QWidget

    from app import qt_gc
    from app.capture import enum_monitors
    from app.overlay.affinity import WDA_EXCLUDEFROMCAPTURE, apply_to_widget, get_display_affinity

    app = QApplication(sys.argv)  # Qt makes the process PMv2 here, before dxcam / mss load
    qt_gc.install(app)  # as in app.main: TransientMonitor's thread never runs a gc collection beside the widgets
    flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
             | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus)

    class MagentaOverlay(QWidget):
        def __init__(self, rect: QRect) -> None:
            super().__init__(None, flags)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            self._rect = rect

        def paintEvent(self, _event) -> None:
            p = QPainter(self)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            p.fillRect(self.rect(), Qt.GlobalColor.transparent)
            p.fillRect(self._rect, QColor(255, 0, 255))
            p.end()

    def settle(seconds: float | None = None) -> None:
        end = time.perf_counter() + (args.hold_ms / 1000 if seconds is None else seconds)
        while time.perf_counter() < end:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
            time.sleep(0.005)
        dwm_flush()

    t_start = time.perf_counter()
    awareness = dpi_awareness()
    try:
        hdr = advanced_color_info()
    except Exception as e:
        hdr = {"error": repr(e)}
    monitors = []
    for m in enum_monitors():
        d = asdict(m)
        d["scale"] = m.scale
        d["advanced_color"] = hdr.get(m.device) if isinstance(hdr, dict) else None
        monitors.append(d)
    primary_mon = next(m for m in enum_monitors() if m.primary)
    screen = app.primaryScreen()
    dpr = screen.devicePixelRatio()
    qt_screens = [{"name": s.name(), "geometry_logical": s.geometry().getRect(), "dpr": s.devicePixelRatio()}
                  for s in app.screens()]

    margin = 16
    w_l, h_l = round(args.width / dpr), round(args.height / dpr)
    expected = round(w_l * dpr) * round(h_l * dpr)
    origin = screen.geometry().topLeft()
    pos = origin + QPoint(round(args.x / dpr), round(args.y / dpr))

    overlay = MagentaOverlay(QRect(margin, margin, w_l, h_l))
    overlay.setGeometry(pos.x(), pos.y(), w_l + 2 * margin, h_l + 2 * margin)

    scenarios: dict[str, dict] = {}
    info: dict = {}
    monitor: TransientMonitor | None = None
    try:
        excl = apply_to_widget(overlay, exclude=False)  # creates the HWND, affinity WDA_NONE
        hwnd = int(overlay.winId())
        region = window_rect(hwnd)
        info["hwnd_initial"] = hex(hwnd)
        info["capture_region"] = asdict(region)

        scenarios["baseline"] = {"captures": capture_all(region, backends, args.tol, "baseline", args.save_images)}

        overlay.show()
        settle()
        region_shown = window_rect(hwnd)
        info["region_after_show"] = asdict(region_shown)
        info["styles_initial"] = window_styles(hwnd)
        if region_shown != region:
            print(f"note: window rect moved after show: {region} -> {region_shown}")
            region = region_shown

        # a) affinity OFF: the probe must see the overlay.
        scenarios["a_off"] = {"affinity": get_display_affinity(hwnd), "visible": bool(_user32.IsWindowVisible(hwnd)),
                              "captures": capture_all(region, backends, args.tol, "a_off", args.save_images)}

        # b) affinity ON.
        res = excl.set_exclude(True)
        settle()
        scenarios["b_on"] = {"set_result": asdict(res), "affinity": get_display_affinity(hwnd),
                             "visible": bool(_user32.IsWindowVisible(hwnd)),
                             "captures": capture_all(region, backends, args.tol, "b_on", args.save_images)}

        # c) toggle click-through off/on via setWindowFlags (hides the window, may recreate the HWND),
        #    then re-show; the filter re-applies on Show. Auto re-apply is suspended during the flag
        #    changes so we can see whether affinity survives on its own.
        monitor = TransientMonitor(region, args.tol)
        monitor.start()
        monitor.ready.wait(2.0)
        excl.auto_reapply = False
        overlay.setWindowFlags(flags & ~Qt.WindowType.WindowTransparentForInput)
        def native_state(h: int) -> dict:
            # Qt may report internalWinId()==0 here while the old HWND still exists (it is reused).
            alive = bool(_user32.IsWindow(hwnd))
            return {"internal_win_id": hex(h), "widget_visible": overlay.isVisible(),
                    "original_hwnd_alive": alive,
                    "original_hwnd_visible": bool(_user32.IsWindowVisible(hwnd)) if alive else None,
                    "original_hwnd_affinity": get_display_affinity(hwnd) if alive else None,
                    "current_hwnd_affinity": get_display_affinity(h) if h else None}

        mid = native_state(int(overlay.internalWinId()))
        overlay.setWindowFlags(flags)
        before_reshow = native_state(int(overlay.internalWinId()))
        affinity_before_reshow = before_reshow["current_hwnd_affinity"]
        if affinity_before_reshow is None:
            affinity_before_reshow = before_reshow["original_hwnd_affinity"]
        excl.auto_reapply = True
        n_hist = len(excl.history)
        overlay.show()
        settle()
        hwnd_c = int(overlay.winId())
        reapplies = [{"trigger": t, "hwnd": hex(h), **asdict(r)} for t, h, r in excl.history[n_hist:]]
        affinity_after = get_display_affinity(hwnd_c)
        manual_fallback = None
        if affinity_after != WDA_EXCLUDEFROMCAPTURE:
            manual_fallback = asdict(excl.apply("manual_fallback"))
            settle()
            affinity_after = get_display_affinity(hwnd_c)
        settle(0.2)
        transient = monitor.finish()
        region_c = window_rect(hwnd_c)
        scenarios["c_recreate_on"] = {
            "hwnd_before": hex(hwnd), "after_first_setWindowFlags": mid, "before_reshow": before_reshow,
            "hwnd_after": hex(hwnd_c), "hwnd_changed": hwnd_c != hwnd,
            "affinity_survived_flags_change": affinity_before_reshow,
            "reapplied_by_filter": reapplies, "manual_fallback": manual_fallback,
            "affinity": affinity_after, "visible": bool(_user32.IsWindowVisible(hwnd_c)),
            "styles": window_styles(hwnd_c), "region": asdict(region_c), "transient_dxcam": transient,
            "captures": capture_all(region_c, backends, args.tol, "c_recreate_on", args.save_images),
        }

        # c2) real native-window recreation: destroy() drops the HWND, show() creates a new one that
        #     starts at WDA_NONE. No manual fallback here: the filter alone must re-apply.
        monitor = TransientMonitor(region_c, args.tol)
        monitor.start()
        monitor.ready.wait(2.0)
        n_hist = len(excl.history)
        overlay.destroy()
        after_destroy = {"internal_win_id": hex(int(overlay.internalWinId())),
                         "old_hwnd_alive": bool(_user32.IsWindow(hwnd_c))}
        overlay.show()
        settle()
        hwnd_r = int(overlay.winId())
        affinity_r = get_display_affinity(hwnd_r)
        settle(0.2)
        transient = monitor.finish()
        region_r = window_rect(hwnd_r)
        scenarios["c2_destroy_recreate_on"] = {
            "hwnd_before": hex(hwnd_c), "after_destroy": after_destroy, "hwnd_after": hex(hwnd_r),
            "hwnd_changed": hwnd_r != hwnd_c,
            "reapplied_by_filter": [{"trigger": t, "hwnd": hex(h), **asdict(r)} for t, h, r in excl.history[n_hist:]],
            "affinity": affinity_r, "visible": bool(_user32.IsWindowVisible(hwnd_r)),
            "styles": window_styles(hwnd_r), "region": asdict(region_r), "transient_dxcam": transient,
            "captures": capture_all(region_r, backends, args.tol, "c2_destroy_recreate_on", args.save_images),
        }

        # control: affinity OFF again on the (recreated) window -> magenta must be back.
        res = excl.set_exclude(False)
        settle()
        scenarios["c_control_off"] = {"set_result": asdict(res), "affinity": get_display_affinity(hwnd_r),
                                      "visible": bool(_user32.IsWindowVisible(hwnd_r)),
                                      "captures": capture_all(region_r, backends, args.tol, "c_control_off", args.save_images)}
    except Exception:
        info["error"] = traceback.format_exc()
        print(info["error"], file=sys.stderr)
    finally:
        if monitor is not None and monitor.is_alive():
            monitor.finish()  # an exception mid-scenario must not leave the dxcam poller running
        overlay.close()
        overlay.deleteLater()
        app.processEvents()

    # --- verdicts --------------------------------------------------------------------------
    rows, verdicts = [], {}
    for name in backends:
        base = scenarios.get("baseline", {}).get("captures", {}).get(name, {}).get("magenta_px", 0) or 0
        status = {}
        for scen in ("a_off", "b_on", "c_recreate_on", "c2_destroy_recreate_on", "c_control_off"):
            if scen not in scenarios:
                continue
            cap = scenarios[scen]["captures"].get(name, {})
            n = cap.get("magenta_px")
            want_visible = scen in ("a_off", "c_control_off")
            if n is None:
                ok, note = False, cap.get("error", "no data")
            elif want_visible:
                ok, note = n >= 0.9 * expected, "overlay must be seen"
            else:
                ok, note = n <= base, "overlay must be hidden"
            if scen == "c2_destroy_recreate_on" and not scenarios[scen]["hwnd_changed"]:
                ok, note = False, note + " (HWND was not recreated: test is vacuous)"
            if not cap.get("fresh", True):
                note += " (stale frame)"
            status[scen] = ok
            cap["pass"] = ok
            rows.append({"scenario": scen, "backend": name, "magenta_px": n, "expected": expected if want_visible else f"<={base}",
                         "result": "PASS" if ok else "FAIL", "note": note})
        valid = status.get("a_off", False) and status.get("c_control_off", False)
        excluded = (status.get("b_on", False) and status.get("c_recreate_on", False)
                    and status.get("c2_destroy_recreate_on", False))
        verdicts[name] = ("EXCLUDED" if excluded else "NOT EXCLUDED") if valid else "INVALID PROBE (overlay not seen with affinity off)"

    elapsed = time.perf_counter() - t_start
    results = {
        "params": vars(args) | {"window_flags": FINAL_FLAGS_DOC, "attributes": "WA_TranslucentBackground|WA_ShowWithoutActivating",
                                "expected_magenta_px": expected, "qt_dpr": dpr, "logical_rect": [w_l, h_l], "margin_logical": margin},
        "dpi_awareness": awareness,
        "monitors": monitors,
        "primary_device": primary_mon.device,
        "qt_screens": qt_screens,
        "window": info,
        "scenarios": scenarios,
        "verdicts": verdicts,
        "all_pass": all(v == "EXCLUDED" for v in verdicts.values()),
        "elapsed_s": round(elapsed, 1),
    }
    path = save_results("probe_affinity", results)

    print(f"DPI awareness: {awareness}")
    for m in monitors:
        print(f"monitor {m['index']} {m['device']} bounds={tuple(m['bounds'].values())} dpi={m['dpi']} "
              f"scale={m['scale']:.2f} primary={m['primary']} advanced_color={m['advanced_color']}")
    c = scenarios.get("c_recreate_on", {})
    print(f"setWindowFlags: hwnd_changed={c.get('hwnd_changed')} affinity_survived_flags_change="
          f"{c.get('affinity_survived_flags_change')} reapplied_by={[r['trigger'] for r in c.get('reapplied_by_filter', [])]} "
          f"transient_dxcam={c.get('transient_dxcam')}")
    c2 = scenarios.get("c2_destroy_recreate_on", {})
    print(f"destroy+show: hwnd {c2.get('hwnd_before')} -> {c2.get('hwnd_after')} changed={c2.get('hwnd_changed')} "
          f"affinity={c2.get('affinity')} reapplied_by={[r['trigger'] for r in c2.get('reapplied_by_filter', [])]} "
          f"transient_dxcam={c2.get('transient_dxcam')}")
    print()
    print_table(rows, ["scenario", "backend", "magenta_px", "expected", "result", "note"])
    print()
    print_table([{"backend": k, "verdict": v} for k, v in verdicts.items()], ["backend", "verdict"])
    print(f"\nOVERALL: {'PASS' if results['all_pass'] else 'FAIL'}  ({elapsed:.1f}s)  -> {path}")
    watchdog.cancel()
    return 0 if results["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
