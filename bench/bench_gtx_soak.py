"""Soak test of GoogleGtxTranslator at a fixed batch rate.

Each tick (1/--rps s) dispatches one batch (default 20 items from the eval segments, alternating
EN/JA) with small random variations (numbers, name prefixes) so the endpoint keeps seeing new
text. Batches may overlap (up to --max-inflight) because one request can take longer than a tick;
the engine's own token bucket (rate=--rps, burst 2) still caps HTTP requests, retries included.
Every batch and every HTTP attempt is logged; a status line prints every 10 s and a summary at
the end. Stops early after --max-fail consecutive failed batches (e.g. the IP got blocked).
Ctrl+C stops and saves partial results.

  python -m bench.bench_gtx_soak --quick                     # 45 s
  python -m bench.bench_gtx_soak --rps 1 --batch 20 --duration 1800
"""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import random
import sys
import time
from collections import Counter
from dataclasses import asdict

from app.translate import GoogleGtxTranslator
from app.translate.google_gtx import GtxRequestLog
from app.translate.textnorm import has_kana, is_passthrough
from bench.common import percentile, print_table, save_results, summarize_ms
from bench.eval_translate import SEGMENTS_CSV, load_segments

_BATCH_ID: contextvars.ContextVar[int | None] = contextvars.ContextVar("batch_id", default=None)
EN_NAMES = ["Alex", "Mira", "Ken", "Rosa", "Theo", "Lina", "Omar", "Ivy"]
JA_NAMES = ["ハルト", "ユイ", "ソウタ", "アオイ", "レン", "ミオ", "カイト", "サクラ"]


def vary(text: str, lang: str, rng: random.Random) -> str:
    """Small realistic change: a counter, a tag, or a speaker name."""
    n = rng.randint(2, 999)
    if lang == "ja":
        name = rng.choice(JA_NAMES)
        options = [f"{text}（{n}）", f"【{n}】{text}", f"{name}「{text}」", f"{text} ×{n}"]
    else:
        name = rng.choice(EN_NAMES)
        options = [f"{text} ({n})", f"[{n}] {text}", f"{name}: {text}", f"{text} x{n}"]
    return rng.choice(options)


class Soak:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.rng = random.Random(args.seed)
        segs = load_segments(SEGMENTS_CSV)
        self.pool = {lang: [s["source"] for s in segs if s["lang"] == lang] for lang in ("en", "ja")}
        self.http: list[dict] = []
        self.batches: list[dict] = []
        self.engine = GoogleGtxTranslator(rate=args.rps, burst=2, on_request=self._on_request)
        self.late = 0  # ticks delayed because --max-inflight batches were still running
        self.consecutive_fail = 0
        self.t0 = time.perf_counter()

    def _on_request(self, log: GtxRequestLog) -> None:
        # runs inside the batch's task, so the ContextVar tells which batch this attempt belongs to
        self.http.append({"t": round(time.perf_counter() - self.t0, 3), "batch": _BATCH_ID.get(),
                          **asdict(log), "latency_s": round(log.latency_s, 4)})

    def make_batch(self, lang: str) -> list[str]:
        pool = self.pool[lang]
        picks = [self.rng.choice(pool) for _ in range(self.args.batch)]
        return [vary(t, lang, self.rng) for t in picks]

    async def one_batch(self, i: int) -> None:
        _BATCH_ID.set(i)
        lang = "en" if i % 2 == 0 else "ja"
        texts = self.make_batch(lang)
        rec = {"batch": i, "t": round(time.perf_counter() - self.t0, 3), "lang": lang, "items": len(texts),
               "chars": sum(map(len, texts)), "ok": False, "error": None}
        t = time.perf_counter()
        try:
            out = await self.engine.translate_batch(texts, lang, "vi")
            rec["ok"] = True
            rec["passthrough"] = sum(is_passthrough(s, o) for s, o in zip(texts, out))
            rec["kana_residue"] = sum(has_kana(o) for o in out) if lang == "ja" else 0
            rec["sample"] = [texts[0], out[0]]
        except Exception as exc:  # noqa: BLE001 - the soak test records every failure kind
            rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["latency_ms"] = round((time.perf_counter() - t) * 1000, 1)
        attempts = [a for a in self.http if a["batch"] == i]
        rec["statuses"] = [a["status"] for a in attempts]
        rec["attempts"] = len(attempts)
        rec["mismatch"] = any(a["mismatch"] for a in attempts)
        self.batches.append(rec)
        self.consecutive_fail = 0 if rec["ok"] else self.consecutive_fail + 1
        if not rec["ok"]:
            print(f"  batch {i} failed: {rec['error']}", flush=True)

    def status_line(self) -> str:
        ok = [b for b in self.batches if b["ok"]]
        lat = [b["latency_ms"] for b in ok]
        codes = Counter(a["status"] for a in self.http)
        return (f"[{time.perf_counter() - self.t0:6.0f}s] batches={len(self.batches)} ok={len(ok)} "
                f"fail={len(self.batches) - len(ok)} http={len(self.http)} 429={codes.get(429, 0)} "
                f"p50={percentile(lat, 50):.0f}ms p95={percentile(lat, 95):.0f}ms "
                f"mismatch={sum(b['mismatch'] for b in self.batches)} "
                f"passthrough={sum(b.get('passthrough', 0) for b in ok)}")

    async def _reporter(self) -> None:
        while True:
            await asyncio.sleep(10)
            print(self.status_line(), flush=True)

    async def run(self) -> bool:
        """Dispatch one batch per tick (at most --max-inflight running). True if stopped early."""
        interval = 1.0 / self.args.rps
        next_send = time.perf_counter()
        inflight: set[asyncio.Task] = set()
        reporter = asyncio.create_task(self._reporter())
        stopped = False
        i = 0
        try:
            while time.perf_counter() - self.t0 < self.args.duration:
                if self.consecutive_fail >= self.args.max_fail:
                    print(f"stopping: {self.consecutive_fail} consecutive failed batches")
                    stopped = True
                    break
                delay = next_send - time.perf_counter()
                if delay > 0:
                    await asyncio.sleep(delay)
                if len(inflight) >= self.args.max_inflight:
                    self.late += 1  # endpoint slower than the target rate allows
                    await asyncio.wait(inflight, return_when=asyncio.FIRST_COMPLETED)
                    next_send = time.perf_counter()  # re-anchor instead of bursting to catch up
                task = asyncio.create_task(self.one_batch(i))
                inflight.add(task)
                task.add_done_callback(inflight.discard)
                i += 1
                next_send += interval
            if inflight:
                await asyncio.wait(inflight, timeout=60)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None:
                current.uncancel()
            print("interrupted; saving partial results")
            stopped = True
        finally:
            reporter.cancel()
            for task in inflight:
                task.cancel()
            await asyncio.gather(reporter, *inflight, return_exceptions=True)
            await self.engine.aclose()
        return stopped

    def summary(self, stopped_early: bool) -> dict:
        elapsed = time.perf_counter() - self.t0
        ok = [b for b in self.batches if b["ok"]]
        codes = Counter("network" if a["status"] is None else str(a["status"]) for a in self.http)
        http_ok = [a["latency_s"] for a in self.http if a["status"] == 200]
        errors = Counter(b["error"].split(":")[0] for b in self.batches if b["error"])
        return {
            "elapsed_s": round(elapsed, 1),
            "stopped_early": stopped_early,
            "batches": len(self.batches),
            "ok_batches": len(ok),
            "failed_batches": len(self.batches) - len(ok),
            "achieved_batches_per_s": round(len(self.batches) / elapsed, 3) if elapsed else 0,
            "late_ticks": self.late,
            "items": sum(b["items"] for b in self.batches),
            "batch_latency": summarize_ms([b["latency_ms"] / 1000 for b in ok]) if ok else None,
            "http_latency_200": summarize_ms(http_ok) if http_ok else None,
            "http_status_counts": dict(codes),
            "http_429": codes.get("429", 0),
            "mismatch_batches": sum(b["mismatch"] for b in self.batches),
            "passthrough_items": sum(b.get("passthrough", 0) for b in ok),
            "ja_kana_residue_items": sum(b.get("kana_residue", 0) for b in ok),
            "exceptions": dict(errors),
            "engine_stats": self.engine.stats.as_dict(),
        }


async def main_async(args: argparse.Namespace) -> None:
    soak = Soak(args)
    print(f"gtx soak: rps={args.rps} batch={args.batch} duration={args.duration}s")
    stopped = await soak.run()
    summary = soak.summary(stopped)
    bl = summary["batch_latency"] or {}
    hl = summary["http_latency_200"] or {}
    rows = [
        {"metric": "batches ok/total", "value": f"{summary['ok_batches']}/{summary['batches']}"},
        {"metric": "batch latency p50/p95 ms", "value": f"{bl.get('p50_ms', '-')}/{bl.get('p95_ms', '-')}"},
        {"metric": "http 200 latency p50/p95 ms", "value": f"{hl.get('p50_ms', '-')}/{hl.get('p95_ms', '-')}"},
        {"metric": "http status counts", "value": summary["http_status_counts"]},
        {"metric": "429 responses", "value": summary["http_429"]},
        {"metric": "retries", "value": summary["engine_stats"]["retries"]},
        {"metric": "mismatch batches", "value": summary["mismatch_batches"]},
        {"metric": "fallback items", "value": summary["engine_stats"]["fallback_items"]},
        {"metric": "passthrough items", "value": summary["passthrough_items"]},
        {"metric": "ja outputs with kana", "value": summary["ja_kana_residue_items"]},
        {"metric": "exceptions", "value": summary["exceptions"] or "-"},
        {"metric": "achieved batches/s", "value": summary["achieved_batches_per_s"]},
    ]
    print()
    print_table(rows, ["metric", "value"])
    path = save_results(args.name, {"args": vars(args), "summary": summary,
                                    "batches": soak.batches, "requests": soak.http})
    print(f"\nsaved {path}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rps", type=float, default=1.0, help="batches (requests) per second")
    ap.add_argument("--batch", type=int, default=20, help="items per batch")
    ap.add_argument("--duration", type=float, default=1800.0, help="seconds")
    ap.add_argument("--quick", action="store_true", help="45 s smoke run")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--max-fail", type=int, default=20, help="stop after this many consecutive failed batches")
    ap.add_argument("--max-inflight", type=int, default=4, help="concurrent batches allowed while keeping the rate")
    args = ap.parse_args()
    if args.quick:
        args.duration = 45.0
    args.name = "gtx_soak_quick" if args.quick else "gtx_soak"
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
