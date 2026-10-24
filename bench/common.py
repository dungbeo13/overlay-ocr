"""Shared helpers for Phase 0 benchmarks: timing summaries and result files.

Run benchmarks from the project root as modules, e.g. `python -m bench.bench_ocr`.
"""
from __future__ import annotations

import json
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"
DATA_DIR = Path(__file__).parent / "data"


def percentile(values: list[float], p: float) -> float:
    """Linear-interpolated percentile, p in [0, 100]."""
    if not values:
        return float("nan")
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summarize_ms(samples_s: list[float]) -> dict:
    """Summarize durations given in seconds as milliseconds."""
    ms = [x * 1000 for x in samples_s]
    return {
        "n": len(ms),
        "p50_ms": round(percentile(ms, 50), 2),
        "p95_ms": round(percentile(ms, 95), 2),
        "mean_ms": round(statistics.fmean(ms), 2) if ms else float("nan"),
        "max_ms": round(max(ms), 2) if ms else float("nan"),
    }


class Stopwatch:
    """`with Stopwatch() as sw: ...` then read `sw.elapsed` (seconds)."""

    def __enter__(self) -> "Stopwatch":
        self._t0 = time.perf_counter()
        self.elapsed = 0.0
        return self

    def __exit__(self, *exc) -> None:
        self.elapsed = time.perf_counter() - self._t0


def machine_info() -> dict:
    info = {
        "python": sys.version.split()[0],
        "windows_build": sys.getwindowsversion().build,
        "cpu": platform.processor(),
    }
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        info["gpu"] = out
    except (OSError, subprocess.SubprocessError):
        info["gpu"] = None
    return info


def save_results(name: str, data: dict) -> Path:
    """Write bench/results/<name>.json with machine info attached; returns the path."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{name}.json"
    payload = {"machine": machine_info(), "created": time.strftime("%Y-%m-%d %H:%M:%S"), **data}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def print_table(rows: list[dict], columns: list[str]) -> None:
    """Print rows as a fixed-width table (for console summaries)."""
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in columns}
    print("  ".join(c.ljust(widths[c]) for c in columns))
    print("  ".join("-" * widths[c] for c in columns))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in columns))
