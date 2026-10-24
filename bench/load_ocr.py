"""OCR load generator for the game-impact test.

Runs OCR every --interval seconds for --duration seconds, on a sample image or a live
screen region (mss), and prints per-second OCR latency and CPU use. Start your game,
open an FPS overlay (PresentMon, NVIDIA overlay/FrameView, RTSS), note the baseline FPS,
then run this and compare.

    python -m bench.load_ocr --config rapid-small-small-cpu-t8 --interval 0.25 --duration 60
    python -m bench.load_ocr --config rapid-small-small-cuda --region 0,1000,2560,440 --interval 0.5
    python -m bench.load_ocr --config winocr --lang ja --image bench/data/ocr/synthetic/game_dialog/ja_0.png
    python -m bench.load_ocr --priority below_normal --affinity 16-23   # e.g. E-cores only

Columns: runs = OCR passes finished in that second; proc_cores = process CPU time / wall time
(1.0 = one logical core fully busy); sys_cpu% = whole-machine CPU use.
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import numpy as np
import psutil

from app.winutil import enable_per_monitor_dpi_awareness
from bench.bench_ocr import SYN_DIR, _guess_lang, load_bgr, make_engine, parse_config, set_power_throttling
from bench.common import percentile, print_table, save_results

DEFAULT_IMAGE = SYN_DIR / "game_dialog" / "en_0.png"
PRIORITIES = {
    "idle": psutil.IDLE_PRIORITY_CLASS,
    "below_normal": psutil.BELOW_NORMAL_PRIORITY_CLASS,
    "normal": psutil.NORMAL_PRIORITY_CLASS,
    "above_normal": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
}


def parse_cpu_list(spec: str) -> list[int]:
    """'16-23' or '0,2,4-7' -> sorted logical CPU indices."""
    cpus: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            cpus.update(range(int(a), int(b) + 1))
        elif part:
            cpus.add(int(part))
    return sorted(cpus)


class ScreenSource:
    """Grabs a live region with mss (physical pixels; process is made per-monitor DPI aware)."""

    def __init__(self, left: int, top: int, width: int, height: int) -> None:
        import mss

        self._mss = mss.MSS()
        self.region = {"left": left, "top": top, "width": width, "height": height}

    def __call__(self) -> np.ndarray:
        shot = self._mss.grab(self.region)
        return np.ascontiguousarray(np.asarray(shot)[:, :, :3])  # BGRA -> BGR

    def close(self) -> None:
        self._mss.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="rapid-small-small-cpu-t8", help="engine config (see bench_ocr --list)")
    ap.add_argument("--lang", choices=["en", "ja"], help="default: from image name (ja_*), else en")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--image", type=Path, help="sample image (default: synthetic game_dialog/en_0.png)")
    src.add_argument("--region", help="live capture L,T,W,H in physical virtual-desktop pixels")
    src.add_argument("--monitor", type=int, help="live capture a whole monitor (mss index, 1 = first)")
    ap.add_argument("--interval", type=float, default=0.25, help="seconds between OCR starts (0 = back-to-back)")
    ap.add_argument("--duration", type=float, default=60.0, help="seconds to run")
    ap.add_argument("--quick", action="store_true", help="10 s run")
    ap.add_argument("--priority", choices=list(PRIORITIES), help="process priority class")
    ap.add_argument("--affinity", help="restrict to logical CPUs, e.g. 16-23 (E-cores on 13700KF)")
    ap.add_argument("--allow-throttling", action="store_true",
                    help="leave Windows EcoQoS throttling on (default: opt out, like a foreground app)")
    ap.add_argument("--name", default="load_ocr", help="results file name")
    args = ap.parse_args()
    duration = 10.0 if args.quick else args.duration

    enable_per_monitor_dpi_awareness()
    proc = psutil.Process()
    throttling_off = False if args.allow_throttling else set_power_throttling(False)
    if args.priority:
        proc.nice(PRIORITIES[args.priority])
    if args.affinity:
        proc.cpu_affinity(parse_cpu_list(args.affinity))

    screen: ScreenSource | None = None
    if args.region:
        left, top, width, height = (int(v) for v in args.region.split(","))
        screen = ScreenSource(left, top, width, height)
        source_desc = f"screen region {args.region}"
    elif args.monitor is not None:
        import mss

        with mss.MSS() as m:
            mon = m.monitors[args.monitor]
        screen = ScreenSource(mon["left"], mon["top"], mon["width"], mon["height"])
        source_desc = f"monitor {args.monitor} ({mon['width']}x{mon['height']})"
    image_path = args.image or DEFAULT_IMAGE
    lang = args.lang or (_guess_lang(image_path.stem, None, "en") if screen is None else "en")
    if screen is None:
        frame = load_bgr(image_path)
        source_desc = f"image {image_path} ({frame.shape[1]}x{frame.shape[0]})"

    engine = make_engine(parse_config(args.config), lang)
    engine.warmup(3)
    # The first passes at the real input size are slow (buffer allocation, cuDNN algo choice:
    # ~185 vs 28 ms on CUDA); keep them out of the measured load.
    for _ in range(2):
        engine.recognize(screen() if screen is not None else frame)
    print(f"engine {engine.name} [{lang}] on {source_desc}")
    if hasattr(engine, "providers"):
        print(f"providers: {engine.providers}")
    print(f"interval {args.interval}s, duration {duration}s, power throttling "
          f"{'off' if throttling_off else 'system default'}, priority {args.priority or 'unchanged'}, "
          f"affinity {args.affinity or 'all'}; Ctrl+C to stop\n")
    print(f"{'t_s':>5} {'runs':>4} {'p50_ms':>8} {'max_ms':>8} {'grab_ms':>7} {'boxes':>5} {'proc_cores':>10} {'sys_cpu%':>8}")

    ncpu = os.cpu_count() or 1
    psutil.cpu_percent(None)
    proc.cpu_percent(None)
    per_second: list[dict] = []
    all_lat: list[float] = []
    sec_lat: list[float] = []
    sec_grab: list[float] = []
    boxes = 0
    t0 = time.perf_counter()
    next_run = t0
    next_report = t0 + 1.0
    try:
        while True:
            now = time.perf_counter()
            if now - t0 >= duration:
                break
            if now >= next_run:
                if screen is not None:
                    g = time.perf_counter()
                    frame = screen()
                    sec_grab.append(time.perf_counter() - g)
                t = time.perf_counter()
                boxes = len(engine.recognize(frame))
                lat = time.perf_counter() - t
                sec_lat.append(lat)
                all_lat.append(lat)
                next_run = max(next_run + args.interval, time.perf_counter()) if args.interval > 0 else 0.0
                now = time.perf_counter()
            if now >= next_report:
                ms = [x * 1000 for x in sec_lat]
                row = {
                    "t_s": round(now - t0),
                    "runs": len(sec_lat),
                    "p50_ms": round(percentile(ms, 50), 1) if ms else None,
                    "max_ms": round(max(ms), 1) if ms else None,
                    "grab_ms": round(1000 * sum(sec_grab) / len(sec_grab), 1) if sec_grab else None,
                    "boxes": boxes,
                    "proc_cores": round(proc.cpu_percent(None) / 100, 2),
                    "sys_cpu": round(psutil.cpu_percent(None), 1),
                }
                per_second.append(row)
                print(f"{row['t_s']:>5} {row['runs']:>4} {row['p50_ms'] or '-':>8} {row['max_ms'] or '-':>8} "
                      f"{row['grab_ms'] or '-':>7} {boxes:>5} {row['proc_cores']:>10} {row['sys_cpu']:>8}")
                sec_lat, sec_grab = [], []
                next_report += 1.0
            wake = min(next_run, next_report) if args.interval > 0 else now
            delay = wake - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
    except KeyboardInterrupt:
        print("stopped")
    finally:
        if screen is not None:
            screen.close()
        if hasattr(engine, "close"):
            engine.close()

    elapsed = time.perf_counter() - t0
    ms = [x * 1000 for x in all_lat]
    cores = [r["proc_cores"] for r in per_second]
    summary = {
        "config": args.config, "engine": getattr(engine, "name", args.config), "lang": lang,
        "runs": len(ms), "rate_hz": round(len(ms) / elapsed, 2) if elapsed else 0,
        "p50_ms": round(percentile(ms, 50), 1), "p95_ms": round(percentile(ms, 95), 1),
        "max_ms": round(max(ms), 1) if ms else None,
        "avg_cores": round(sum(cores) / len(cores), 2) if cores else None,
        "avg_cpu_pct_of_machine": round(100 * sum(cores) / len(cores) / ncpu, 1) if cores else None,
    }
    print()
    print_table([summary], list(summary))
    path = save_results(args.name, {
        "args": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
        "source": source_desc, "interval_s": args.interval, "duration_s": round(elapsed, 1),
        "power_throttling_off": throttling_off, "providers": getattr(engine, "providers", None),
        "summary": summary, "per_second": per_second,
    })
    print(f"\nresults: {path}")


if __name__ == "__main__":
    main()
