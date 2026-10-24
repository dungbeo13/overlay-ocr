"""Translate the eval segments with every available engine and write a rating sheet.

Output: bench/results/eval_translate.csv with id, lang, category, source, reference_vi and, per
engine, <engine>_vi, <engine>_score (left empty: rate 1-5) and <engine>_chrf (only when the row
has a reference), plus a JSON summary (latency per batch, mean chrF, skipped engines).
gtx always runs; azure/gemini run only when AZURE_TRANSLATOR_KEY / GEMINI_API_KEY are set.
A sheet that already holds scores is never overwritten (a timestamped file is written instead).

  python -m bench.eval_translate [--quick] [--engines gtx,azure,gemini] [--batch-size N] [--show]
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
import time
from collections import Counter
from pathlib import Path

from app.translate import AzureTranslator, GeminiTranslator, GoogleGtxTranslator, MissingApiKeyError
from app.translate.base import TranslationError, TranslatorEngine
from app.translate.textnorm import has_kana, is_passthrough
from bench.common import DATA_DIR, RESULTS_DIR, Stopwatch, print_table, save_results, summarize_ms

SEGMENTS_CSV = DATA_DIR / "translate" / "eval_segments.csv"
ALL_ENGINES = ("gtx", "azure", "gemini")


def chrf(hypothesis: str, reference: str, order: int = 6, beta: float = 2.0) -> float:
    """Sentence-level chrF (Popovic 2015): character n-grams 1..order with whitespace removed,
    precision and recall averaged over orders, then F-beta. Returns 0..100."""
    hyp = "".join(hypothesis.split())
    ref = "".join(reference.split())
    if not hyp or not ref:
        return 100.0 if hyp == ref else 0.0
    precisions, recalls = [], []
    for n in range(1, order + 1):
        h = Counter(hyp[i:i + n] for i in range(len(hyp) - n + 1))
        r = Counter(ref[i:i + n] for i in range(len(ref) - n + 1))
        if not h or not r:
            continue
        matches = sum((h & r).values())
        precisions.append(matches / sum(h.values()))
        recalls.append(matches / sum(r.values()))
    if not precisions:
        return 0.0
    p = sum(precisions) / len(precisions)
    r = sum(recalls) / len(recalls)
    if p + r == 0:
        return 0.0
    b2 = beta * beta
    return 100.0 * (1 + b2) * p * r / (b2 * p + r)


def load_segments(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [row for row in csv.DictReader(f) if row.get("source", "").strip()]


def make_engine(name: str) -> TranslatorEngine:
    if name == "gtx":
        return GoogleGtxTranslator()
    if name == "azure":
        return AzureTranslator()
    if name == "gemini":
        return GeminiTranslator()
    raise ValueError(f"unknown engine {name!r}")


async def run_engine(engine: TranslatorEngine, segments: list[dict], tgt: str, batch_size: int) -> dict:
    outputs: dict[str, str] = {}
    batches: list[dict] = []
    by_lang: dict[str, list[dict]] = {}
    for seg in segments:
        by_lang.setdefault(seg["lang"], []).append(seg)
    for lang, segs in by_lang.items():
        size = batch_size if batch_size > 0 else len(segs)
        for start in range(0, len(segs), size):
            chunk = segs[start:start + size]
            texts = [s["source"] for s in chunk]
            error = None
            with Stopwatch() as sw:
                try:
                    results = await engine.translate_batch(texts, lang, tgt)
                except TranslationError as exc:
                    error, results = f"{type(exc).__name__}: {exc}", [""] * len(texts)
            batches.append({"lang": lang, "items": len(texts), "chars": sum(map(len, texts)),
                            "latency_ms": round(sw.elapsed * 1000, 1), "error": error})
            for seg, out in zip(chunk, results):
                outputs[seg["id"]] = out
    return {"outputs": outputs, "batches": batches}


def _sheet_has_scores(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            return any(v.strip() for row in csv.DictReader(f) for k, v in row.items()
                       if k and k.endswith("_score") and v)
    except (OSError, csv.Error, UnicodeDecodeError):
        return True  # unreadable: do not risk overwriting it


def write_sheet(path: Path, segments: list[dict], engines: dict[str, dict]) -> Path:
    if _sheet_has_scores(path):
        path = path.with_name(f"{path.stem}.{time.strftime('%Y%m%d-%H%M%S')}{path.suffix}")
        print(f"note: existing sheet has scores; writing {path.name} instead")
    has_refs = any(s.get("reference_vi", "").strip() for s in segments)
    columns = ["id", "lang", "category", "source", "reference_vi"]
    for name in engines:
        columns += [f"{name}_vi", f"{name}_score"] + ([f"{name}_chrf"] if has_refs else [])
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # BOM so Excel shows Vietnamese correctly
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for seg in segments:
            ref = seg.get("reference_vi", "").strip()
            row = {k: seg.get(k, "") for k in ("id", "lang", "category", "source", "reference_vi")}
            for name, res in engines.items():
                hyp = res["outputs"].get(seg["id"], "")
                row[f"{name}_vi"] = hyp
                row[f"{name}_score"] = ""
                if has_refs:
                    row[f"{name}_chrf"] = f"{chrf(hyp, ref):.1f}" if ref and hyp else ""
            w.writerow(row)
    return path


async def main_async(args: argparse.Namespace) -> None:
    segments = load_segments(Path(args.segments))
    if args.quick:
        per_lang: dict[str, int] = {}
        quick = []
        for s in segments:
            if per_lang.get(s["lang"], 0) < 8:
                per_lang[s["lang"]] = per_lang.get(s["lang"], 0) + 1
                quick.append(s)
        segments = quick
    names = [n.strip() for n in args.engines.split(",") if n.strip()]
    ran: dict[str, dict] = {}
    summary: dict[str, dict] = {}
    for name in names:
        try:
            engine = make_engine(name)
        except MissingApiKeyError as exc:
            summary[name] = {"status": "skipped", "reason": f"{exc.env_var} not set"}
            continue
        try:
            res = await run_engine(engine, segments, args.tgt, args.batch_size)
        finally:
            await engine.aclose()
        ran[name] = res
        ok_lat = [b["latency_ms"] / 1000 for b in res["batches"] if not b["error"]]
        scored = [chrf(res["outputs"][s["id"]], s["reference_vi"]) for s in segments
                  if s.get("reference_vi", "").strip() and res["outputs"].get(s["id"])]
        passthrough = sum(is_passthrough(s["source"], res["outputs"].get(s["id"], "")) for s in segments)
        kana = sum(has_kana(res["outputs"].get(s["id"], "")) for s in segments if s["lang"] == "ja")
        errors = [b["error"] for b in res["batches"] if b["error"]]
        summary[name] = {
            "status": "error" if errors and not ok_lat else "ok",
            "model": getattr(engine, "model", ""),
            "items": len(segments),
            "batches": res["batches"],
            "latency": summarize_ms(ok_lat) if ok_lat else None,
            "chrf_mean": round(sum(scored) / len(scored), 2) if scored else None,
            "chrf_n": len(scored),
            "passthrough": passthrough,
            "ja_outputs_with_kana": kana,
            "errors": errors,
            "engine_stats": _stats(engine),
        }

    sheet = write_sheet(RESULTS_DIR / f"{args.name}.csv", segments, ran) if ran else None
    rows = []
    for name, s in summary.items():
        lat = s.get("latency") or {}
        rows.append({"engine": name, "status": s["status"], "items": s.get("items", ""),
                     "batches": len(s.get("batches", [])), "p50_ms": lat.get("p50_ms", ""),
                     "max_ms": lat.get("max_ms", ""), "chrF": s.get("chrf_mean", ""),
                     "n_ref": s.get("chrf_n", ""), "passthru": s.get("passthrough", ""),
                     "ja_kana": s.get("ja_outputs_with_kana", ""), "note": s.get("reason", "")})
    print_table(rows, ["engine", "status", "items", "batches", "p50_ms", "max_ms", "chrF", "n_ref",
                       "passthru", "ja_kana", "note"])
    if args.show and ran:
        for seg in segments:
            print(f"\n[{seg['id']}] {seg['source']}")
            for name, res in ran.items():
                print(f"  {name:>6}: {res['outputs'].get(seg['id'], '')}")
    path = save_results(args.name, {"args": vars(args), "segments": len(segments),
                                    "sheet": str(sheet) if sheet else None, "engines": summary})
    print(f"\nsheet: {sheet}\nsummary: {path}")


def _stats(engine: TranslatorEngine) -> dict:
    stats = getattr(engine, "stats", None)
    if stats is None:
        return {}
    return stats.as_dict() if hasattr(stats, "as_dict") else dict(stats)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engines", default=",".join(ALL_ENGINES), help="comma-separated subset of gtx,azure,gemini")
    ap.add_argument("--segments", default=str(SEGMENTS_CSV))
    ap.add_argument("--tgt", default="vi")
    ap.add_argument("--batch-size", type=int, default=0, help="items per request (0 = one batch per language)")
    ap.add_argument("--quick", action="store_true", help="first 8 segments per language")
    ap.add_argument("--show", action="store_true", help="print every translation")
    args = ap.parse_args()
    args.name = "eval_translate_quick" if args.quick else "eval_translate"
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
