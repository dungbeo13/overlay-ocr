"""The LLM tier on FRESH translations of labelled lines: pronoun-role errors with the role post-edit off and on.

For every labelled line (bench.eval_roles_heldout.load_lines; with --scenes, the bench scene lines
that have a role-tagged "ref"), the SOURCE is translated from scratch by the real model through
LocalLlmTranslator.translate_screen - the speaker label + the line, with the minimal context of
bench.roles_llm (the two characters and their pronouns) - once with the role post-edit fully OFF
(repair_roles and reask_roles False; fix_pronouns, which only swaps tôi / bạn for the pair's words,
stays as in the app) and once with the app's defaults ON. Each final output is judged against the
line's "ref" (pronoun_roles.check_line(..., expected=ref)); a line without one is judged from the
source alone ("noref"), or with --derive-refs against a reference built from its labels (dev data).
The labelled "translation" itself is not used.

Reported per model: lines with a role error off vs on (overall, per source group, per "cat"), what
the source alone shows (what the app can see), re-ask rate (lines asked again / answered) and how
many re-asked answers were taken, lines the repair rewrote, latency off / on and the extra per line
(paired), and EVERY line that got worse (more issues on than off): read those.

With a reference, the headline is also given by the reference ALONE (basis "ref": its tagged slots and
subject-verb contexts, none of the source-only checks that decide the re-ask and whether its answer is
taken), and lines without a reference are counted apart (judged by the app's own checker).

The two settings run as two passes over all lines (not line by line), so llama-server's prompt
cache favours neither. Decoding is greedy, but llama-server is not bit-exact between a prompt served
from its cache and one computed again: the model's first answer differs between the passes on some
lines (the 1.8B on ~5% of them). So the "on" pass also records its first answer (before any
post-edit; RecordingTranslator) and reports "same first answer" numbers as well: that answer with
the post-edit off (fix_pronouns only, as the off pass) vs what the app shows. Those isolate what the
role post-edit does; the off pass vs the on pass is the whole system as two separate runs.

    python -m bench.eval_roles_e2e [--models quality,light] [--dev] [--scenes all|a,b] [--scenes-only]
                                   [--derive-refs] [--limit N] [--show] [--show-favour] [file.json ...]

--show-favour prints every benefactive line (off / on / ref): the judge undercounts the role errors of an
answer reworded away from the reference's anchors there, so a person reads them.

--scenes adds the scene lines to the folder's (or the files'); --scenes-only measures them alone.
Results: bench/results/eval_roles_e2e.json (the held-out folder), eval_roles_e2e_dev.json (--dev),
eval_roles_e2e_scenes.json (the held-out folder with --scenes, or --scenes-only), eval_roles_e2e_files.json
(files given).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path

from app.translate.contextual import ScreenResult
from app.translate.llm_local import LocalLlmTranslator
from app.translate.llm_prompt import finalize_result
from bench.common import percentile, save_results
from bench.eval_roles_heldout import DEV_DIR, FAVOUR, GROUPS, HELDOUT_DIR, derive_ref, judge, load_lines
from bench.roles_llm import (
    LINE_ID,
    BlockedGame,
    blocked_game,
    check_guard,
    line_context,
    line_items,
    line_lang,
    model_path,
    running_server,
    scene_lines,
)

SETTINGS = ("off", "on")


class RecordingTranslator(LocalLlmTranslator):
    """The app's translator, keeping the model's first answer of the last screen (`first_raw`: before
    any post-edit, the glossary retry merged in), that answer post-edited with fix_pronouns only
    (`first_fixed`: what the off pass would show for the same answer) and as the app post-edits it
    before any re-ask (`first_shown`: repair_roles too, when on)."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.first_raw = self.first_fixed = self.first_shown = None

    async def translate_screen(self, items, src, tgt, ctx) -> ScreenResult:
        self.first_raw = self.first_fixed = self.first_shown = None
        return await super().translate_screen(items, src, tgt, ctx)

    def _finalize(self, merged, items, ctx, src) -> ScreenResult:
        out = super()._finalize(merged, items, ctx, src)
        if self.first_raw is None:  # the first call is the first answer's (a role re-ask's trial comes later)
            opt = self.prompt_options
            self.first_raw, self.first_shown = merged, out
            self.first_fixed = finalize_result(merged, items, ctx, fix=opt.fix_pronouns, label_terms=opt.label_terms,
                                               repair=False, src=src)
        return out


def _issues(found) -> list[str] | None:
    return None if found is None else [f"{i.found}:{i.anchor}/{i.basis}" for i in found]


async def run_pass(tr: RecordingTranslator, lines: list[dict]) -> list[dict]:
    rows = []
    for n, ln in enumerate(lines, 1):
        check_guard(n)
        t0 = time.perf_counter()
        r = await tr.translate_screen(line_items(ln), line_lang(ln), "vi", line_context(ln))
        dt = time.perf_counter() - t0
        out = r.translations.get(LINE_ID)
        def first_of(res: ScreenResult | None) -> str | None:
            return res.translations.get(LINE_ID) if res else None

        fixed, shown = first_of(tr.first_fixed), first_of(tr.first_shown)
        rows.append({"out": out, "latency_ms": round(dt * 1000), "reask": tr.last_role_reask.get(LINE_ID),
                     "candidate": tr.last_role_candidates.get(LINE_ID), "first": first_of(tr.first_raw),
                     "first_fixed": fixed,
                     "repaired": shown is not None and shown != fixed,  # repair_roles rewrote the first answer
                     "issues": _issues(judge(ln, out)), "issues_noref": _issues(judge(ln, out, source_only=True)),
                     "issues_first": _issues(judge(ln, fixed)),
                     # the reference alone (none of the checks the app's re-ask is decided by): the independent number
                     "issues_ref": _issues(judge(ln, out, ref_only=True)) if ln["ref"] else None,
                     "issues_first_ref": _issues(judge(ln, fixed, ref_only=True)) if ln["ref"] else None})
    return rows


def _rate(n: int, d: int) -> float | None:
    return round(n / d, 3) if d else None


def _lat(ms: list[float]) -> dict:
    return {"n": len(ms), "p50_ms": round(percentile(ms, 50)) if ms else None,
            "p95_ms": round(percentile(ms, 95)) if ms else None, "mean_ms": round(sum(ms) / len(ms)) if ms else None}


def _more(a: list | None, b: list | None) -> bool:
    """`b` (issues of a text, None: no text) has more role issues than `a`."""
    return b is not None and (a is None or len(b) > len(a))


def summarize(lines: list[dict], off: list[dict], on: list[dict]) -> tuple[dict, list[dict]]:
    """(summary, per-line rows) of one model. "off" / "on": the two passes; "first": the on pass's own
    first answer with the post-edit off (fix_pronouns only), the same answer the app post-edited."""
    rows = []
    for ln, a, b in zip(lines, off, on):
        rows.append({"id": ln["id"], "file": ln["file"], "cat": ln["cat"], "group": ln["group"],
                     FAVOUR: ln.get(FAVOUR, False), "judged_by": "ref" if ln["ref"] else "source", "source": ln["source"], "ref": ln["ref"],
                     "speaker": ln["speaker"], "listener": ln["listener"], "own": ln["own"], "other": ln["other"],
                     "off": a["out"], "on": b["out"], "first": b["first_fixed"], "first_raw": b["first"],
                     "issues_off": a["issues"], "issues_on": b["issues"], "issues_first": b["issues_first"],
                     "noref_off": a["issues_noref"], "noref_on": b["issues_noref"],
                     "ref_off": a["issues_ref"], "ref_on": b["issues_ref"], "ref_first": b["issues_first_ref"],
                     "reask": b["reask"],
                     "reask_answer": b["candidate"], "repaired": b["repaired"],
                     "first_differs": a["out"] != b["first_fixed"],  # the model answered the passes differently
                     "latency_off_ms": a["latency_ms"], "latency_on_ms": b["latency_ms"],
                     "worse": _more(a["issues"], b["issues"]), "better": _more(b["issues"], a["issues"]),
                     "worse_same_first": _more(b["issues_first"], b["issues"]),
                     "better_same_first": _more(b["issues"], b["issues_first"])})
    answered = [r for r in rows if r["off"] is not None and r["on"] is not None]

    def errors(rs: list[dict], key: str) -> int:
        return sum(bool(r[key]) for r in rs)

    def block(rs: list[dict]) -> dict:
        return {"lines": len(rs), "errors_off": errors(rs, "issues_off"), "errors_on": errors(rs, "issues_on"),
                "errors_first": errors(rs, "issues_first"),
                "rate_off": _rate(errors(rs, "issues_off"), len(rs)), "rate_on": _rate(errors(rs, "issues_on"), len(rs)),
                "rate_first": _rate(errors(rs, "issues_first"), len(rs)),
                "asked": sum(r["reask"] not in (None, "over the cap") for r in rs),
                "worse": sum(r["worse"] for r in rs), "better": sum(r["better"] for r in rs),
                "worse_same_first": sum(r["worse_same_first"] for r in rs),
                "better_same_first": sum(r["better_same_first"] for r in rs)}

    asked = [r for r in answered if r["reask"] not in (None, "over the cap")]
    extra = [r["latency_on_ms"] - r["latency_off_ms"] for r in answered]
    with_ref = [r for r in answered if r["judged_by"] == "ref"]
    source_only = [r for r in answered if r["judged_by"] == "source"]
    summary = {
        "lines": len(rows), "answered": len(answered),
        "missing_off": sum(r["off"] is None for r in rows), "missing_on": sum(r["on"] is None for r in rows),
        **{k: v for k, v in block(answered).items() if k != "lines"},
        "noref_errors_off": errors(answered, "noref_off"), "noref_errors_on": errors(answered, "noref_on"),
        "judged_by_source_only": len(source_only),
        # lines with a reference, judged by it alone (basis "ref"): not by the checks that decide the re-ask
        "ref_lines": len(with_ref), "ref_errors_off": errors(with_ref, "ref_off"),
        "ref_errors_first": errors(with_ref, "ref_first"), "ref_errors_on": errors(with_ref, "ref_on"),
        "ref_judge_errors_off": errors(with_ref, "issues_off"), "ref_judge_errors_on": errors(with_ref, "issues_on"),
        "source_only_errors_off": errors(source_only, "issues_off"),
        "source_only_errors_on": errors(source_only, "issues_on"),
        "reask_rate": _rate(len(asked), len(answered)),
        "reask_outcomes": dict(Counter(r["reask"] for r in asked)),
        "changed": sum(r["off"] != r["on"] for r in answered),
        "first_differs": sum(r["first_differs"] for r in answered),
        "post_edited": sum(r["first"] != r["on"] for r in answered),
        "repaired": sum(r["repaired"] for r in answered),
        "latency_off": _lat([r["latency_off_ms"] for r in answered]),
        "latency_on": _lat([r["latency_on_ms"] for r in answered]),
        "extra_ms_per_line": _lat(extra),
        "extra_ms_reasked": _lat([r["latency_on_ms"] - r["latency_off_ms"] for r in asked]),
        # FAVOUR: the benefactive lines (who did a favour for whom), across the groups
        "groups": {**{g: block([r for r in answered if r["group"] == g]) for g in GROUPS},
                   FAVOUR: block([r for r in answered if r[FAVOUR]])},
        "cats": {c: block([r for r in answered if r["cat"] == c]) for c in sorted({r["cat"] for r in answered})},
    }
    return summary, rows


def print_summary(kind: str, s: dict) -> None:
    print(f"[{kind}] {s['answered']}/{s['lines']} lines answered (missing off {s['missing_off']}, on {s['missing_on']})")
    print(f"  role errors, two passes: off {s['errors_off']} ({s['rate_off']}), on {s['errors_on']} ({s['rate_on']}); "
          f"better {s['better']}, WORSE {s['worse']}; from the source alone off {s['noref_errors_off']}, "
          f"on {s['noref_errors_on']}" + (f"; {s['judged_by_source_only']} lines without a ref"
                                          if s["judged_by_source_only"] else ""))
    if s["ref_lines"]:
        print(f"  {s['ref_lines']} lines with a ref: judge off {s['ref_judge_errors_off']} -> on {s['ref_judge_errors_on']}; "
              f"by the ref ALONE (independent of the app's checks) off {s['ref_errors_off']}, same first "
              f"{s['ref_errors_first']} -> on {s['ref_errors_on']}")
    if s["judged_by_source_only"]:
        print(f"  {s['judged_by_source_only']} lines without a ref (judged by the app's own checker): off "
              f"{s['source_only_errors_off']} -> on {s['source_only_errors_on']}")
    print(f"  same first answer (the on pass's, post-edit off vs on): {s['errors_first']} ({s['rate_first']}) -> "
          f"{s['errors_on']}; better {s['better_same_first']}, WORSE {s['worse_same_first']}; the passes' first "
          f"answers differed on {s['first_differs']} lines")
    print(f"  asked again {s['asked']} (rate {s['reask_rate']}): {s['reask_outcomes']}; post-edited {s['post_edited']} "
          f"(repaired {s['repaired']})")
    lo, ln, ex, er = s["latency_off"], s["latency_on"], s["extra_ms_per_line"], s["extra_ms_reasked"]
    print(f"  latency p50 off {lo['p50_ms']} ms, on {ln['p50_ms']} ms; extra per line mean {ex['mean_ms']} ms "
          f"(p95 {ex['p95_ms']}); extra on lines asked again p50 {er['p50_ms']} ms (n={er['n']})")
    print("  per group (errors off -> same first -> on / lines): " + ", ".join(
        f"{g} {d['errors_off']}->{d['errors_first']}->{d['errors_on']}/{d['lines']}"
        for g, d in s["groups"].items() if d["lines"]))
    print("  per cat (errors off -> on / lines): " + ", ".join(
        f"{c or '-'} {d['errors_off']}->{d['errors_on']}/{d['lines']}" for c, d in s["cats"].items()))


def print_rows(kind: str, rows: list[dict], show: bool, favour_rows: bool = False) -> None:
    """The lines that got worse (always), those changed or wrong (`show`), and every benefactive line
    (`favour_rows`: the judge undercounts a reworded answer's role errors there, so read them)."""
    for r in rows:
        bad = r["worse"] or r["worse_same_first"]
        if not (bad or (show and (r["better"] or r["better_same_first"] or r["first"] != r["on"] or r["issues_on"]))
                or (favour_rows and r.get(FAVOUR))):
            continue
        tag = "WORSE" if bad else "better" if r["better"] or r["better_same_first"] else \
            "changed" if r["first"] != r["on"] else "error" if r["issues_on"] else "favour"
        print(f"  [{kind}] {tag} {r['id']} [{r['group']}|{r['cat']}] {r['speaker']}>{r['listener']} "
              f"({r['own']}/{r['other']}): {r['source']}\n      off: {r['off']}  {r['issues_off']}"
              + (f"\n      first: {r['first']}  {r['issues_first']}" if r["first_differs"] else "")
              + f"\n      on : {r['on']}  {r['issues_on']}  (re-ask: {r['reask']})"
              + (f"\n      re-ask answer: {r['reask_answer']}" if r["reask_answer"] and r["reask_answer"] != r["on"] else "")
              + (f"\n      ref: {r['ref']}" if r["ref"] else ""))


async def run_model(kind: str, path: Path, lines: list[dict], show: bool, favour_rows: bool = False) -> dict:
    passes: dict[str, list[dict]] = {}
    async with running_server(kind, path) as (url, model):
        for setting in SETTINGS:
            tr = RecordingTranslator(url, model)
            if setting == "off":
                tr.prompt_options = replace(tr.prompt_options, repair_roles=False, reask_roles=False)
            try:
                t0 = time.perf_counter()
                passes[setting] = await run_pass(tr, lines)
                print(f"[{kind}] pass {setting}: {len(lines)} lines in {time.perf_counter() - t0:.0f} s, "
                      f"{tr.requests} requests ({tr.role_retries} role re-asks)")
            finally:
                await tr.aclose()
    summary, rows = summarize(lines, passes["off"], passes["on"])
    print_summary(kind, summary)
    print_rows(kind, rows, show, favour_rows)
    return {"file": path.name, "model": model, "summary": summary, "rows": rows}


def collect_lines(files: list[Path], dev: bool, scenes: str, scenes_only: bool) -> tuple[list[Path], list[dict]]:
    """(files, lines) to measure: the files given, else the held-out (or with `dev` the development) folder,
    plus the bench scene lines with a ref named by `scenes` ("all" or names); `scenes_only`: those alone."""
    folder = DEV_DIR if dev else HELDOUT_DIR
    if scenes_only and not scenes:
        scenes = "all"
    paths = files or ([] if scenes_only else sorted(folder.glob("*.json")))
    lines = load_lines(paths)
    if scenes:
        extra = scene_lines([s.strip() for s in scenes.split(",") if s.strip()])
        print(f"{len(lines)} labelled lines from {len(paths)} files, plus {len(extra)} bench scene lines with a ref")
        lines += extra
    return paths, lines


async def main_async(args: argparse.Namespace) -> int:
    folder = DEV_DIR if args.dev else HELDOUT_DIR
    paths, lines = collect_lines(args.files, args.dev, args.scenes, args.scenes_only)
    if not lines:
        print(f"no labelled lines (folder {folder})")
        return 1
    if args.limit:
        lines = lines[:args.limit]
    derived = 0
    for ln in lines:
        if not ln["ref"] and args.derive_refs and ln.get("translation") and ln.get("label") in ("ok", "error"):
            ln["ref"] = derive_ref(ln)
            derived += bool(ln["ref"])
    print(f"{len(lines)} lines; refs: {sum(bool(ln['ref']) for ln in lines)} ({derived} derived from the labels)")
    models: dict[str, dict] = {}
    for kind in [k.strip() for k in args.models.split(",") if k.strip()]:
        path = model_path(kind)
        if path is None:
            print(f"[{kind}] not installed, skipped")
            continue
        game = blocked_game()
        if game:
            print(f"a blocked game ({game}) is in the foreground: not loading the GPU now")
            return 2
        try:
            models[kind] = await run_model(kind, path, lines, args.show, args.show_favour)
        except BlockedGame as exc:
            print(exc)
            return 2
    if not models:
        return 1
    name = ("eval_roles_e2e_files" if args.files else "eval_roles_e2e_dev" if args.dev
            else "eval_roles_e2e_scenes" if args.scenes or args.scenes_only else "eval_roles_e2e")
    out = save_results(name, {"files": [p.name for p in paths], "scenes": args.scenes or args.scenes_only,
                              "lines": len(lines),
                              "refs_derived": derived, "models": models})
    print(f"\nsaved {out}")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*", type=Path, help=f"labelled JSON files (default: {HELDOUT_DIR}/*.json)")
    ap.add_argument("--dev", action="store_true", help=f"the development sets in {DEV_DIR} instead")
    ap.add_argument("--scenes", default="", help="also bench scene lines with a ref: comma-separated names, or all")
    ap.add_argument("--scenes-only", action="store_true", help="only the bench scene lines (--scenes, default all)")
    ap.add_argument("--models", default="quality,light", help="comma-separated model kinds (quality, light)")
    ap.add_argument("--derive-refs", action="store_true", help="lines without a ref: build one from the labels")
    ap.add_argument("--limit", type=int, default=0, help="only the first N lines")
    ap.add_argument("--show", action="store_true", help="print every changed or wrong line, not only the worse ones")
    ap.add_argument("--show-favour", action="store_true",
                    help="also print every benefactive line (off / on / ref) for a human check of the judge")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
