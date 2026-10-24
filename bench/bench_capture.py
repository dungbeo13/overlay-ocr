"""Capture latency benchmark: dxcam vs WGC monitor capture vs mss on typical translation regions.

Regions on the primary monitor: subtitle strip (full width x 200 at the bottom), dialog box
800x300 (centered), full monitor. For each backend and region it times N grabs back-to-back and
N grabs at a fixed poll interval (default 150 ms) and reports latency p50/p95, how many grabs
returned a new frame (fresh_pct, fresh_p50_ms = latency of those grabs only, since dxcam serves
cached pixels when nothing changed), the age of the returned pixels, and this process's CPU use
(100 % = one logical core, only for blocks lasting >= 1 s; WGC's capture thread runs in-process so
its frame-copy cost is included).

By default a helper process flickers a 48x48 square (two close grays) inside the subtitle strip so
dxcam/WGC keep delivering new frames, as over video or a game; --no-animate measures a static desktop.
Run: python -m bench.bench_capture [--quick]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

from bench.common import percentile, print_table, save_results, summarize_ms

ROOT = Path(__file__).resolve().parents[1]
WARMUP_GRABS = 10  # also lets dxcam's recent-region union shrink to the measured region
MIN_CPU_WINDOW_S = 1.0


def regions_for(primary) -> dict:
    from app.capture import Region
    l, t, w, h = primary.left, primary.top, primary.width, primary.height
    return {
        "subtitle_strip": Region(l, t + h - 200, w, 200),
        "dialog_800x300": Region(l + (w - 800) // 2, t + (h - 300) // 2, 800, 300),
        "full_primary": primary,
    }


def _cpu_s(proc: psutil.Process) -> float:
    ct = proc.cpu_times()
    return ct.user + ct.system


def run_block(backend, region, n: int, interval_s: float, last_ts: float | None) -> tuple[dict, float | None]:
    proc = psutil.Process()
    lat, fresh_lat, ages = [], [], []
    none = fresh = 0
    c0 = _cpu_s(proc)
    w0 = next_t = time.perf_counter()
    for _ in range(n):
        if interval_s:
            delay = next_t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            next_t += interval_s
        t0 = time.perf_counter()
        f = backend.grab(region)
        t1 = time.perf_counter()
        lat.append(t1 - t0)
        if f is None:
            none += 1
            continue
        ages.append(t1 - f.timestamp)
        if f.timestamp != last_ts:
            fresh += 1
            fresh_lat.append(t1 - t0)
            last_ts = f.timestamp
    wall = time.perf_counter() - w0
    cpu = _cpu_s(proc) - c0
    stats = {
        **summarize_ms(lat),
        "fresh_pct": round(100 * fresh / n, 1),
        "fresh_p50_ms": round(percentile([x * 1000 for x in fresh_lat], 50), 2) if fresh_lat else None,
        "age_p50_ms": round(percentile([x * 1000 for x in ages], 50), 2) if ages else None,
        "age_p95_ms": round(percentile([x * 1000 for x in ages], 95), 2) if ages else None,
        "none": none,
        # Process CPU time advances in ~15.6 ms scheduler ticks: too coarse for short blocks.
        "cpu_pct": round(100 * cpu / wall, 1) if wall >= MIN_CPU_WINDOW_S else None,
        "wall_s": round(wall, 3),
    }
    return stats, last_ts


def bench_backend(name: str, regions: dict, n: int, poll_n: int, poll_s: float, idle_s: float) -> dict:
    from app.capture import create_backend

    out: dict = {"backend": name}
    proc = psutil.Process()
    t0 = time.perf_counter()
    be = create_backend(name)
    t1 = time.perf_counter()
    try:
        first_region = next(iter(regions.values()))
        be.grab(first_region)  # starts lazy sessions (WGC) / the duplication (dxcam)
        t2 = time.perf_counter()
        out["init_ms"] = round((t1 - t0) * 1000, 1)
        out["first_grab_ms"] = round((t2 - t1) * 1000, 1)
        c0, w0 = _cpu_s(proc), time.perf_counter()
        time.sleep(idle_s)  # background cost with no grabs (WGC copies every delivered frame)
        out["idle_cpu_pct"] = round(100 * (_cpu_s(proc) - c0) / (time.perf_counter() - w0), 1)
        out["blocks"] = []
        for rname, region in regions.items():
            last_ts = None
            for _ in range(WARMUP_GRABS):
                f = be.grab(region)
                last_ts = f.timestamp if f is not None else last_ts
            for pace, count, interval in (("burst", n, 0.0), (f"poll{round(poll_s * 1000)}ms", poll_n, poll_s)):
                if count <= 0:
                    continue
                stats, last_ts = run_block(be, region, count, interval, last_ts)
                out["blocks"].append({"region": rname, "size": f"{region.width}x{region.height}", "pace": pace, **stats})
        if hasattr(be, "border_setting_accepted"):
            out["border_setting_accepted"] = be.border_setting_accepted
        if hasattr(be, "refresh_count"):
            out["dxcam_forced_refreshes"] = be.refresh_count
        if hasattr(be, "frame_count"):
            out["wgc_frames_delivered"] = be.frame_count
    finally:
        be.close()
    return out


# --- animator helper process ---------------------------------------------------------------

def run_animator(x: int, y: int, seconds: float) -> None:
    """Flicker a small square at physical (x, y) on the primary monitor until killed."""
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QColor, QPainter
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication([])
    dpr = app.primaryScreen().devicePixelRatio()
    flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
             | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus)

    class Square(QWidget):
        def __init__(self) -> None:
            super().__init__(None, flags)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            self.tick = 0

        def paintEvent(self, _event) -> None:
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(0x40, 0x40, 0x40) if self.tick % 2 else QColor(0x48, 0x48, 0x48))
            p.end()

    w = Square()
    origin = app.primaryScreen().geometry().topLeft()
    w.setGeometry(origin.x() + round(x / dpr), origin.y() + round(y / dpr), round(48 / dpr), round(48 / dpr))
    w.show()

    def step() -> None:
        w.tick += 1
        w.update()

    timer = QTimer()
    timer.setTimerType(Qt.TimerType.PreciseTimer)
    timer.timeout.connect(step)
    timer.start(8)
    QTimer.singleShot(int(seconds * 1000), app.quit)
    print("ready", flush=True)
    app.exec()


def start_animator(x: int, y: int, max_seconds: float) -> subprocess.Popen | None:
    proc = subprocess.Popen(
        [sys.executable, "-m", "bench.bench_capture", "--_animator", f"{x},{y},{max_seconds}"],
        cwd=ROOT, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True,
    )
    ready = threading.Event()
    threading.Thread(target=lambda: (proc.stdout.readline(), ready.set()), daemon=True).start()
    if not ready.wait(10):
        print("warning: animator did not start; measuring a static desktop", file=sys.stderr)
        proc.kill()
        return None
    time.sleep(0.2)
    return proc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--backends", default="dxcam,wgc,mss")
    ap.add_argument("--n", type=int, default=None, help="back-to-back grabs per region (default 200, quick 30)")
    ap.add_argument("--poll-n", type=int, default=None, help="polled grabs per region (default = n, quick 15)")
    ap.add_argument("--poll-ms", type=float, default=150.0)
    ap.add_argument("--idle-s", type=float, default=None, help="idle CPU window per backend (default 1.0, quick 0.5)")
    ap.add_argument("--no-animate", action="store_true", help="do not keep the desktop changing")
    ap.add_argument("--cv2-threads", type=int, default=None,
                    help="cv2.setNumThreads(N); OpenCV's default pool spins after each call and inflates CPU%%")
    ap.add_argument("--quick", action="store_true", help="short smoke run (<60 s)")
    ap.add_argument("--_animator", default=None, help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args._animator:
        x, y, secs = args._animator.split(",")
        run_animator(int(x), int(y), float(secs))
        return 0

    from app.capture import primary_monitor
    from app.winutil import enable_per_monitor_dpi_awareness

    enable_per_monitor_dpi_awareness()
    import cv2
    if args.cv2_threads is not None:
        cv2.setNumThreads(args.cv2_threads)
    n = args.n if args.n is not None else (30 if args.quick else 200)
    poll_n = args.poll_n if args.poll_n is not None else (15 if args.quick else n)
    idle_s = args.idle_s if args.idle_s is not None else (0.5 if args.quick else 1.0)
    backends = [b.strip() for b in args.backends.split(",") if b.strip()]
    mon = primary_monitor()
    regions = regions_for(mon.bounds)

    animator = None
    if not args.no_animate:
        est = len(backends) * (len(regions) * (poll_n * args.poll_ms / 1000 + n * 0.05) + 5) + 30
        animator = start_animator(mon.bounds.left + 40, mon.bounds.bottom - 150, est)

    results, rows, init_rows = [], [], []
    try:
        for name in backends:
            print(f"[{name}] running...", flush=True)
            try:
                r = bench_backend(name, regions, n, poll_n, args.poll_ms / 1000, idle_s)
            except Exception as e:
                r = {"backend": name, "error": repr(e)}
                print(f"[{name}] failed: {e!r}", file=sys.stderr)
            results.append(r)
            init_rows.append({k: r.get(k, "") for k in ("backend", "init_ms", "first_grab_ms", "idle_cpu_pct", "error")})
            for b in r.get("blocks", []):
                rows.append({"backend": name, **b})
    finally:
        if animator is not None:
            animator.kill()
            animator.wait(5)

    path = save_results("bench_capture", {
        "params": {"n": n, "poll_n": poll_n, "poll_ms": args.poll_ms, "idle_s": idle_s, "warmup_grabs": WARMUP_GRABS,
                   "animated_desktop": animator is not None, "quick": args.quick, "cv2_threads": cv2.getNumThreads(),
                   "logical_cpus": psutil.cpu_count(), "cpu_pct_note": "100 = one logical core"},
        "primary_monitor": {"device": mon.device, "bounds": [mon.bounds.left, mon.bounds.top, mon.bounds.width, mon.bounds.height],
                            "dpi": mon.dpi},
        "regions": {k: [v.left, v.top, v.width, v.height] for k, v in regions.items()},
        "backends": results,
    })
    print()
    print_table(init_rows, ["backend", "init_ms", "first_grab_ms", "idle_cpu_pct", "error"])
    print()
    if rows:
        print_table(rows, ["backend", "region", "size", "pace", "n", "p50_ms", "p95_ms", "fresh_pct",
                           "fresh_p50_ms", "age_p50_ms", "none", "cpu_pct"])
    print(f"\nanimated desktop: {animator is not None}; results -> {path}")
    return 0 if all("error" not in r for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
