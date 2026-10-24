"""OCR benchmark: latency (p50/p95) and character error rate per engine config, case and language.

Data:
  bench/data/ocr/synthetic/labels.json (+ images)   from `python -m bench.make_ocr_samples`
  bench/data/ocr/real/<usecase>/*.png|jpg            optional real screenshots; a same-name .txt
      (one line per text line, reading order) is the ground truth, otherwise latency only.
      Language: file stem prefix ja_/en_ (also ja-, jp, japanese, english), else guessed from the
      .txt, else --real-lang.

Config names (see --list):
  rapid-<det>-<rec>-<cpu|cuda>[-t<threads>][-spin][-max960|-min736][-cls][-exhaustive]
      -spin: enable ORT thread busy-waiting (off by default), -max960: det resize limit
      (rapidocr default is min736), -cls: 0/180 classifier, -exhaustive: cuDNN algo search
  winocr                       Windows.Media.Ocr, en-US for EN images, ja-JP for JA images

CER = Levenshtein(ref, hyp) / len(ref) over the reading-order concatenation of all lines
(NFKC, whitespace collapsed; Japanese compared without spaces), micro-averaged per group.
cpu_cores = process CPU time / wall time while that group ran (how many cores OCR kept busy).

    python -m bench.bench_ocr --quick
    python -m bench.bench_ocr --configs "rapid-small-*,winocr" --cases game_dialog,subtitle
"""
from __future__ import annotations

import argparse
import fnmatch
import gc
import json
import re
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from rapidfuzz.distance import Levenshtein

from app.ocr.base import OcrEngine, TextBox
from app.ocr.textnorm import is_cjk, join_lines, normalize_for_cer, reading_order
from app.winutil import enable_per_monitor_dpi_awareness, set_power_throttling
from bench.common import DATA_DIR, percentile, print_table, save_results

SYN_DIR = DATA_DIR / "ocr" / "synthetic"
REAL_DIR = DATA_DIR / "ocr" / "real"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}

DEFAULT_CONFIGS = [
    "rapid-tiny-small-cpu-t4",
    "rapid-tiny-small-cpu-t8",
    "rapid-small-small-cpu-t4",
    "rapid-small-small-cpu-t8",
    "rapid-small-small-cuda",
    "rapid-medium-medium-cpu-t8",
    "rapid-medium-medium-cuda",
    "winocr",
]
QUICK_CONFIGS = ["rapid-small-small-cpu-t8", "winocr"]
WINOCR_TAGS = {"en": "en-US", "ja": "ja-JP"}


# ----------------------------------------------------------------------------- process setup


def load_bgr(path: Path) -> np.ndarray:
    img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"cannot read image {path}")
    return np.ascontiguousarray(img)


def _err(exc: BaseException) -> str:
    lines = str(exc).strip().splitlines()
    return f"{type(exc).__name__}: {lines[0]}" if lines else type(exc).__name__


def _close(eng: object) -> None:
    try:
        if eng is not None and hasattr(eng, "close"):
            eng.close()
    except Exception:  # noqa: BLE001 - best effort; a broken engine must not stop the run
        pass


# ----------------------------------------------------------------------------- configs


def parse_config(name: str) -> dict:
    """'rapid-small-small-cpu-t8-nospin' -> RapidOcrEngine kwargs; 'winocr' -> Windows OCR."""
    if name == "winocr":
        return {"kind": "winocr"}
    parts = name.split("-")
    if parts[0] != "rapid" or len(parts) < 4:
        raise ValueError(f"bad config name {name!r} (expected rapid-<det>-<rec>-<cpu|cuda>[-opts] or winocr)")
    kw: dict = {"kind": "rapid", "det": parts[1], "rec": parts[2], "device": parts[3]}
    for p in parts[4:]:
        if p.startswith("t") and p[1:].isdigit():
            kw["threads"] = int(p[1:])
        elif p in ("spin", "nospin"):  # ORT thread-pool busy-waiting; engine default is off
            kw["allow_spinning"] = p == "spin"
        elif p == "cls":
            kw["use_cls"] = True
        elif p[:3] in ("max", "min") and p[3:].isdigit():
            kw["det_limit_type"], kw["det_limit_side_len"] = p[:3], int(p[3:])
        elif p in ("exhaustive", "heuristic"):
            kw["cudnn_algo"] = p.upper()
        else:
            raise ValueError(f"unknown option {p!r} in config {name!r}")
    if kw["device"] not in ("cpu", "cuda"):
        raise ValueError(f"device must be cpu or cuda in {name!r}")
    return kw


def select_configs(spec: str | None, quick: bool) -> list[str]:
    if not spec:
        return list(QUICK_CONFIGS if quick else DEFAULT_CONFIGS)
    out: list[str] = []
    for tok in (t.strip() for t in spec.split(",")):
        if not tok:
            continue
        if any(ch in tok for ch in "*?["):
            matches = [c for c in DEFAULT_CONFIGS if fnmatch.fnmatch(c, tok)]
            if not matches:
                raise SystemExit(f"--configs pattern {tok!r} matches no default config")
            out += matches
        else:
            parse_config(tok)  # validate custom names early
            out.append(tok)
    return list(dict.fromkeys(out))


def make_engine(cfg: dict, lang: str) -> OcrEngine:
    if cfg["kind"] == "winocr":
        from app.ocr.winocr import WindowsOcrEngine

        return WindowsOcrEngine(WINOCR_TAGS[lang])
    from app.ocr.rapid import RapidOcrEngine

    kw = {k: v for k, v in cfg.items() if k != "kind"}
    return RapidOcrEngine(lang=lang, **kw)


# ----------------------------------------------------------------------------- data


@dataclass
class Sample:
    key: str  # e.g. "synthetic/game_dialog/en_0.png"
    path: Path
    case: str
    lang: str
    gt: list[str] | None
    vertical: bool | None  # reading-order hint; None = auto-detect


def _guess_lang(stem: str, gt: list[str] | None, default: str) -> str:
    # "ja_menu", "en-hud", "japanese_menu" count; "jackbox_menu" / "enemy_list" do not.
    m = re.match(r"(ja|jp|japanese|en|english)(?![a-z])", stem.lower())
    if m:
        return "en" if m.group(1).startswith("en") else "ja"
    if gt and any(is_cjk(ch) for line in gt for ch in line):
        return "ja"
    return default


def load_samples(per_group: int | None, cases: set[str] | None, langs: set[str], use_real: bool,
                 real_lang: str) -> list[Sample]:
    samples: list[Sample] = []
    labels_path = SYN_DIR / "labels.json"
    if labels_path.exists():
        labels: dict[str, list[str]] = json.loads(labels_path.read_text("utf-8"))
        for rel, lines in labels.items():
            case, fname = rel.split("/", 1)
            lang = fname.split("_", 1)[0]
            path = SYN_DIR / rel
            if path.exists():
                samples.append(Sample(f"synthetic/{rel}", path, case, lang, lines, case == "vertical_ja"))
            else:
                print(f"warning: {path} listed in labels.json but missing")
    else:
        print(f"warning: {labels_path} not found - run `python -m bench.make_ocr_samples` first")
    if use_real and REAL_DIR.is_dir():
        for path in sorted(REAL_DIR.rglob("*")):
            if path.suffix.lower() not in IMAGE_EXTS:
                continue
            txt = path.with_suffix(".txt")
            gt = [ln.strip() for ln in txt.read_text("utf-8-sig").splitlines() if ln.strip()] if txt.exists() else None
            usecase = path.parent.relative_to(REAL_DIR).as_posix()
            usecase = "real" if usecase == "." else f"real/{usecase}"
            lang = _guess_lang(path.stem, gt, real_lang)
            samples.append(Sample(path.relative_to(DATA_DIR / "ocr").as_posix(), path, usecase, lang, gt, None))

    samples = [s for s in samples if s.lang in langs and (cases is None or s.case in cases)]
    if per_group is not None:
        counts: dict[tuple[str, str], int] = {}
        kept = []
        for s in samples:
            k = (s.case, s.lang)
            if counts.get(k, 0) < per_group:
                counts[k] = counts.get(k, 0) + 1
                kept.append(s)
        samples = kept
    return samples


def cer(gt: list[str], pred: list[TextBox], lang: str, vertical: bool | None) -> tuple[int, int, str]:
    ref = normalize_for_cer(join_lines(gt, lang), lang)
    hyp = normalize_for_cer(join_lines([b.text for b in reading_order(pred, vertical)], lang), lang)
    return Levenshtein.distance(ref, hyp), len(ref), hyp


# ----------------------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true", help="2 configs, 2 images per case/lang, 1 repeat")
    ap.add_argument("--configs", help="comma-separated config names or glob patterns over the default matrix")
    ap.add_argument("--cases", help="comma-separated cases (synthetic case names, or real/<usecase>)")
    ap.add_argument("--langs", default="en,ja")
    ap.add_argument("--images", type=int, help="max images per case/lang (default all; quick 2)")
    ap.add_argument("--repeat", type=int, help="timed runs per image (default 3; quick 1)")
    ap.add_argument("--repeat-budget", type=float, default=2.0,
                    help="stop repeating an image once its runs took this many seconds (keeps slow "
                         "configs such as medium on CPU to ~1 run per image)")
    ap.add_argument("--warmup", type=int, default=3, help="warm-up runs per engine on a real sample")
    ap.add_argument("--no-real", action="store_true", help="ignore bench/data/ocr/real")
    ap.add_argument("--real-lang", default="en", choices=["en", "ja"], help="lang for unlabeled real images")
    ap.add_argument("--allow-throttling", action="store_true",
                    help="leave Windows EcoQoS power throttling on (default: opt out for stable timings)")
    ap.add_argument("--name", help="results file name (default bench_ocr / bench_ocr_quick)")
    ap.add_argument("--list", action="store_true", help="list default configs and exit")
    args = ap.parse_args()

    if args.list:
        print("\n".join(DEFAULT_CONFIGS))
        return
    enable_per_monitor_dpi_awareness()
    throttling_off = False if args.allow_throttling else set_power_throttling(False)

    configs = select_configs(args.configs, args.quick)
    per_group = args.images if args.images is not None else (2 if args.quick else None)
    repeat = args.repeat if args.repeat is not None else (1 if args.quick else 3)
    langs = {x.strip() for x in args.langs.split(",") if x.strip()}
    cases = {x.strip() for x in args.cases.split(",")} if args.cases else None
    samples = load_samples(per_group, cases, langs, not args.no_real, args.real_lang)
    if not samples:
        raise SystemExit("no samples selected")
    images = {s.key: load_bgr(s.path) for s in samples}
    print(f"{len(samples)} images, configs: {', '.join(configs)}, repeat={repeat}, "
          f"power throttling {'off' if throttling_off else 'system default'}\n")

    groups: dict[tuple[str, str], list[Sample]] = {}
    for s in samples:
        groups.setdefault((s.case, s.lang), []).append(s)

    rows: list[dict] = []
    per_image: list[dict] = []
    config_info: dict[str, dict] = {}
    t_start = time.perf_counter()
    aborted: str | None = None
    try:
        for name in configs:
            cfg = parse_config(name)
            info: dict = {"kwargs": cfg, "engines": {}, "skipped": {}}
            config_info[name] = info
            engines: dict[str, OcrEngine] = {}
            for lang in sorted({s.lang for s in samples}):
                t0 = time.perf_counter()
                eng = None
                try:
                    eng = make_engine(cfg, lang)
                    init_s = time.perf_counter() - t0
                    warm_src = next(images[s.key] for s in samples if s.lang == lang)
                    t0 = time.perf_counter()
                    eng.warmup(1)
                    for _ in range(args.warmup):
                        eng.recognize(warm_src)
                except Exception as exc:  # noqa: BLE001 - ORT/EP/download/WinRT errors are plain Exceptions
                    reason = _err(exc)
                    info["skipped"][lang] = reason
                    print(f"[{name}] {lang}: skipped - {reason}")
                    _close(eng)
                    continue
                engines[lang] = eng
                einfo = {"name": eng.name, "init_s": round(init_s, 2), "warmup_s": round(time.perf_counter() - t0, 2)}
                if hasattr(eng, "providers"):
                    einfo["providers"] = eng.providers
                info["engines"][lang] = einfo
                print(f"[{name}] {lang}: {einfo}")

            for (case, lang), group in groups.items():
                eng = engines.get(lang)
                if eng is None:
                    rows.append({"config": name, "case": case, "lang": lang, "n": 0,
                                 "note": "skipped: " + info["skipped"].get(lang, "no engine")})
                    continue
                lat: list[float] = []
                dist = ref_len = labelled = 0  # labelled = images with a non-empty reference
                boxes_n = 0
                errors: list[str] = []
                cpu0, wall0 = time.process_time(), time.perf_counter()
                for s in group:
                    img = images[s.key]
                    times, pred = [], []
                    try:
                        for r in range(repeat):
                            t = time.perf_counter()
                            out = eng.recognize(img)
                            times.append(time.perf_counter() - t)
                            if r == 0:
                                pred = out
                            if sum(times) >= args.repeat_budget:
                                break
                        rec = {"config": name, "image": s.key, "case": case, "lang": lang,
                               "ms": [round(x * 1000, 2) for x in times], "boxes": len(pred)}
                        if s.gt is not None:
                            d, n, hyp = cer(s.gt, pred, lang, s.vertical)
                            if n:
                                dist, ref_len, labelled = dist + d, ref_len + n, labelled + 1
                            # empty reference (e.g. blank .txt): CER undefined, keep the prediction only
                            rec.update(cer=round(d / n, 4) if n else None, pred=hyp)
                        else:
                            rec["pred"] = [b.text for b in reading_order(pred)]
                    except Exception as exc:  # noqa: BLE001 - one failing image must not sink the config
                        err = _err(exc)
                        errors.append(f"{s.key}: {err}")
                        per_image.append({"config": name, "image": s.key, "case": case, "lang": lang, "error": err})
                        print(f"  [{name}] {s.key}: error - {err}")
                        continue
                    lat += times
                    boxes_n += len(pred)
                    per_image.append(rec)
                cpu_cores = (time.process_time() - cpu0) / max(1e-9, time.perf_counter() - wall0)
                ms = [x * 1000 for x in lat]
                ok = len(group) - len(errors)
                if errors:
                    info.setdefault("errors", []).extend(errors)
                row = {
                    "config": name, "case": case, "lang": lang, "n": ok,
                    "p50_ms": round(percentile(ms, 50), 1), "p95_ms": round(percentile(ms, 95), 1),
                    "cer": round(dist / ref_len, 4) if ref_len else "", "labelled": labelled,
                    "boxes": round(boxes_n / max(1, ok), 1), "cpu_cores": round(cpu_cores, 2),
                    "note": f"{len(errors)} failed: {errors[0]}" if errors else "",
                }
                rows.append(row)
                print(f"  {name:28s} {case:12s} {lang}  p50 {row['p50_ms']:8.1f} ms  p95 {row['p95_ms']:8.1f} ms"
                      f"  CER {row['cer']}  cores {row['cpu_cores']}")
            for eng in engines.values():
                _close(eng)
            engines.clear()
            gc.collect()
    except KeyboardInterrupt:
        aborted = "interrupted"
        print("\ninterrupted - saving partial results")
    except Exception as exc:  # noqa: BLE001 - unexpected bug: still save what was measured
        traceback.print_exc()
        aborted = _err(exc)
        print(f"\naborted ({aborted}) - saving partial results")

    # per-config overall summary (synthetic + real, labelled groups only for CER)
    summary = []
    for name in configs:
        cinfo = config_info.get(name, {})
        notes = [f"{k}: {v}" for k, v in cinfo.get("skipped", {}).items()]
        if cinfo.get("errors"):
            notes.append(f"{len(cinfo['errors'])} image(s) failed, e.g. {cinfo['errors'][0]}")
        recs = [r for r in per_image if r["config"] == name and "ms" in r]
        if not recs:
            summary.append({"config": name, "images": 0, "note": "; ".join(notes)})
            continue
        ms = [m for r in recs for m in r["ms"]]
        by_lang = {}
        for lang in ("en", "ja"):
            lr = [r for r in rows if r["config"] == name and r["lang"] == lang and r.get("cer") not in ("", None)]
            if lr:  # micro-average over images that have a non-empty reference
                tot = sum(r["labelled"] for r in lr)
                by_lang[lang] = round(sum(r["cer"] * r["labelled"] for r in lr) / tot, 4)
        summary.append({
            "config": name, "images": len(recs), "p50_ms": round(percentile(ms, 50), 1),
            "p95_ms": round(percentile(ms, 95), 1), "cer_en": by_lang.get("en", ""), "cer_ja": by_lang.get("ja", ""),
            "note": "; ".join(notes),
        })

    print()
    if rows:
        print_table(rows, ["config", "case", "lang", "n", "p50_ms", "p95_ms", "cer", "boxes", "cpu_cores", "note"])
    print("\nOverall (cer_* = mean of per-case CER, weighted by labelled image count):")
    print_table(summary, ["config", "images", "p50_ms", "p95_ms", "cer_en", "cer_ja", "note"])
    out_name = args.name or ("bench_ocr_quick" if args.quick else "bench_ocr")
    path = save_results(out_name, {
        "args": vars(args), "configs": configs, "repeat": repeat, "power_throttling_off": throttling_off,
        "elapsed_s": round(time.perf_counter() - t_start, 1), "aborted": aborted, "config_info": config_info,
        "summary": summary, "rows": rows, "images": per_image,
    })
    print(f"\nresults: {path}")


if __name__ == "__main__":
    main()
