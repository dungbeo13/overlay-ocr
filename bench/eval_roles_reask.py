"""What the app's pronoun-role re-ask does to the answers it asks again for, on labelled lines (real models).

Each labelled line (bench.eval_roles_heldout.load_lines: its "translation" is a model's first answer,
"label" ok / error, "wrong" the words in the wrong role, "ref" the right translation with role tags)
becomes a one-line screen (bench.roles_llm: the speaker label + the line, the two characters). The
labelled translation is INJECTED as the model's first answer and the app's own path runs on it:
LocalLlmTranslator.translate_screen -> finalize_result (fix_pronouns, repair_roles) -> _reask_roles,
which asks the real model again only where the app would. The final text is judged by
pronoun_roles.check_line(..., expected=ref) (bench.eval_roles_heldout.judge).

Two passes per model:
  app     every line, as the app runs (no request where it does not ask again). Error lines asked
          again: fixed (the judge finds nothing left) / better (fewer issues) / same / worse when the
          new answer was taken, else rejected by the guards (why: unchanged, no fewer role issues,
          changed more than the roles, dropped a glossary term, no answer, failed). Right lines asked
          again (a false re-ask): whether the text shown changed. Latency per re-ask.
  oracle  every error line, asked again with a perfect checker (an upper bound): the note names the
          labelled wrong words and the "fewer issues" test uses the reference; everything else (the
          note's wording, finalize, note_words, same_shape, the glossary test) is the app's.

A line without a "ref" is judged from the source alone ("noref"), or with --derive-refs against a
reference built from its labels (bench.eval_roles_heldout.derive_ref: dev data only; held-out sets
carry refs written by a person). The taken answers are also judged by the reference alone ("ref_only":
none of the source checks that decided the re-ask and its acceptance).

    python -m bench.eval_roles_reask [--models quality,light] [--dev] [--derive-refs] [--no-oracle]
                                     [--limit N] [--show] [file.json ...]

Results: bench/results/eval_roles_reask.json (the held-out folder), eval_roles_reask_dev.json (--dev),
eval_roles_reask_files.json (files given on the command line).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
import time
from collections import Counter
from pathlib import Path

import app.translate.llm_local as llm_local
from app.translate.contextual import ScreenResult
from app.translate.llm_local import LocalLlmTranslator
from app.translate.llm_prompt import finalize_result, role_note
from app.translate.pronoun_roles import parse_ref
from bench.common import percentile, save_results
from bench.eval_roles_heldout import DEV_DIR, FAVOUR, GROUPS, HELDOUT_DIR, derive_ref, judge, label_issues, load_lines
from bench.eval_translate import chrf
from bench.roles_llm import (
    LINE_ID,
    BlockedGame,
    InjectingTransport,
    blocked_game,
    check_guard,
    client_for,
    first_word,
    line_context,
    line_items,
    line_lang,
    model_path,
    running_server,
)

TAKEN = ("fixed", "better", "same", "worse", "unjudged")


@contextlib.contextmanager
def oracle_checker(ln: dict, first_shown: str):
    """The re-ask path of llm_local with a perfect checker for the line of `ln`: the labelled wrong
    words for the first answer as shown (`first_shown`, when the post-edit left it as labelled),
    the reference judge for any other text; every issue is worth asking again for."""
    own, other = first_word(ln["own"]), first_word(ln["other"])

    def issues_of(text: str) -> list:
        if text == first_shown and text == ln["translation"]:
            return label_issues(ln)
        return judge(ln, text) or []

    def line_fn(result, items, ctx, src="auto"):
        tr = result.translations.get(LINE_ID)
        found = issues_of(tr) if tr else []
        return {LINE_ID: (found, ln["speaker"], ln["listener"], own, other)} if found else {}

    def screen_fn(result, items, ctx, src="auto"):
        return {i: (found, role_note(found, sp, li, o, t, "themself", result.translations.get(i)))
                for i, (found, sp, li, o, t) in line_fn(result, items, ctx, src).items()}

    saved = (llm_local.screen_role_issues, llm_local.line_role_issues, llm_local.reask_issues)
    llm_local.screen_role_issues, llm_local.line_role_issues, llm_local.reask_issues = screen_fn, line_fn, list
    try:
        yield
    finally:
        llm_local.screen_role_issues, llm_local.line_role_issues, llm_local.reask_issues = saved


def shown_first(tr: LocalLlmTranslator, ln: dict, repair: bool = True) -> str:
    """The injected answer as the app shows it before any re-ask (fix_pronouns, repair_roles; without
    `repair`, fix_pronouns only: tôi / bạn -> the pair's words)."""
    opt = tr.prompt_options
    r = finalize_result(ScreenResult({LINE_ID: ln["translation"]}, {}), line_items(ln), line_context(ln),
                        fix=opt.fix_pronouns, label_terms=opt.label_terms, repair=opt.repair_roles and repair,
                        src=line_lang(ln))
    return r.translations.get(LINE_ID, ln["translation"])


async def run_line(tr: LocalLlmTranslator, transport: InjectingTransport, ln: dict, *, oracle: bool) -> dict:
    first = shown_first(tr, ln)
    swapped = shown_first(tr, ln, repair=False)  # tôi / bạn -> the pair's words only: no role repair
    transport.inject({LINE_ID: ln["translation"]}, {LINE_ID: ln["speaker"]})
    n0, s0, retries0 = transport.forwarded, transport.forwarded_s, tr.retries
    with oracle_checker(ln, first) if oracle else contextlib.nullcontext():
        t0 = time.perf_counter()
        r = await tr.translate_screen(line_items(ln), line_lang(ln), "vi", line_context(ln))
        dt = time.perf_counter() - t0
    out = r.translations.get(LINE_ID)
    outcome = tr.last_role_reask.get(LINE_ID)
    before, after = judge(ln, first) or [], judge(ln, out) or []
    # the reference alone (basis "ref"): none of the checks that decided the re-ask and its acceptance
    before_ref, after_ref = (judge(ln, first, ref_only=True) or [], judge(ln, out, ref_only=True) or []) \
        if ln["ref"] else (None, None)
    err = ln["label"] == "error"
    if outcome is None or outcome == "over the cap":
        verdict = "repaired" if first != swapped else "not asked"
    elif outcome != "taken":
        verdict = "rejected"
    elif err and not before:
        verdict = "unjudged"  # the judge does not see the labelled error either: it cannot say what changed
    else:
        verdict = "fixed" if not after else "better" if len(after) < len(before) else \
            "same" if len(after) == len(before) else "worse"
    # chrF against a reference written by a person (one derived from the labels is the first answer reworded)
    ref_plain = parse_ref(ln["ref"])[0] if ln["ref"] and not ln.get("ref_derived") else ""
    return {"id": ln["id"], "file": ln["file"], "cat": ln["cat"], "group": ln["group"], FAVOUR: ln.get(FAVOUR, False),
            "label": ln["label"],
            "judged_by": "ref" if ln["ref"] else "source", "source": ln["source"], "first": ln["translation"],
            "shown_first": first, "final": out, "reask": outcome, "verdict": verdict,
            "candidate": tr.last_role_candidates.get(LINE_ID),
            "issues_before": [f"{i.found}:{i.anchor}/{i.basis}" for i in before],
            "issues_after": [f"{i.found}:{i.anchor}/{i.basis}" for i in after],
            "ref_before": None if before_ref is None else [f"{i.found}:{i.anchor}" for i in before_ref],
            "ref_after": None if after_ref is None else [f"{i.found}:{i.anchor}" for i in after_ref],
            "changed": out != first, "error": err, "latency_ms": round(dt * 1000),
            "requests": transport.forwarded - n0, "request_ms": round((transport.forwarded_s - s0) * 1000),
            "parse_retry": tr.retries - retries0,
            "chrf_gain": round(chrf(out or "", ref_plain) - chrf(first, ref_plain), 1)
            if ref_plain and outcome == "taken" else None}


def summarize(rows: list[dict]) -> dict:
    """Counts of one pass: error lines by verdict (and rejection reason), right lines asked again,
    latency of the lines asked again."""
    errs, oks = [r for r in rows if r["error"]], [r for r in rows if not r["error"]]
    asked = [r for r in rows if r["verdict"] in (*TAKEN, "rejected")]
    lat = [r["request_ms"] for r in asked if r["requests"]]
    out = {"lines": len(rows), "errors": len(errs), "ok": len(oks),
           "errors_by_verdict": dict(Counter(r["verdict"] for r in errs)),
           "errors_rejected_why": dict(Counter(r["reask"] for r in errs if r["verdict"] == "rejected")),
           "errors_asked": sum(r["verdict"] in (*TAKEN, "rejected") for r in errs),
           "errors_fixed": sum(r["verdict"] == "fixed" for r in errs),
           "errors_worse": sum(r["verdict"] == "worse" for r in errs),
           "ok_asked": sum(r["verdict"] in (*TAKEN, "rejected") for r in oks),
           "ok_changed": sum(r["verdict"] in TAKEN for r in oks),
           # right lines the post-edit rewrote without asking (repair_roles): a false action as well
           "ok_repaired": sum(r["verdict"] == "repaired" for r in oks),
           "ok_worse": sum(r["verdict"] == "worse" for r in oks),
           "ok_by_verdict": dict(Counter(r["verdict"] for r in oks)),
           "reask_latency": {"n": len(lat), "p50_ms": round(percentile(lat, 50)) if lat else None,
                             "p95_ms": round(percentile(lat, 95)) if lat else None,
                             "mean_ms": round(sum(lat) / len(lat)) if lat else None},
           "parse_retries": sum(r["parse_retry"] for r in rows),
           "judged_by_source_only": sum(r["judged_by"] == "source" for r in rows)}
    # taken answers judged by the reference alone (lines with a ref): what changed, by a judge the app's
    # own checks have no part in
    taken = [r for r in rows if r["reask"] == "taken" and r["ref_before"] is not None]
    out["ref_only"] = {"taken": len(taken),
                       "errors_fixed": sum(r["error"] and bool(r["ref_before"]) and not r["ref_after"] for r in taken),
                       "errors_ref_sees": sum(r["error"] and bool(r["ref_before"]) for r in taken),
                       "worse": sum(len(r["ref_after"]) > len(r["ref_before"]) for r in taken),
                       "ok_worse": sum(not r["error"] and len(r["ref_after"]) > len(r["ref_before"]) for r in taken)}
    gains = [r["chrf_gain"] for r in asked if r["chrf_gain"] is not None]
    out["chrf_gain_taken_mean"] = round(sum(gains) / len(gains), 1) if gains else None
    def of(r: dict, g: str) -> bool:
        return r.get(FAVOUR, False) if g == FAVOUR else r["group"] == g

    out["groups"] = {g: {"errors": sum(of(r, g) for r in errs),
                         "asked": sum(of(r, g) and r["verdict"] in (*TAKEN, "rejected") for r in errs),
                         "fixed": sum(of(r, g) and r["verdict"] == "fixed" for r in errs),
                         "ok_asked": sum(of(r, g) and r["verdict"] in (*TAKEN, "rejected") for r in oks)}
                     for g in (*GROUPS, FAVOUR)}  # FAVOUR: the benefactive lines, across the groups
    return out


def print_summary(kind: str, name: str, s: dict) -> None:
    e = s["errors_by_verdict"]
    print(f"[{kind}] {name}: {s['lines']} lines ({s['errors']} errors, {s['ok']} ok)")
    print(f"  error lines asked again {s['errors_asked']}: fixed {e.get('fixed', 0)}, better {e.get('better', 0)}, "
          f"same {e.get('same', 0)}, worse {e.get('worse', 0)}, taken but not judgeable {e.get('unjudged', 0)}, "
          f"rejected {e.get('rejected', 0)} "
          f"{s['errors_rejected_why'] or ''}; repaired without asking {e.get('repaired', 0)}, "
          f"not asked {e.get('not asked', 0)}")
    print(f"  right lines asked again {s['ok_asked']}: changed {s['ok_changed']}, judged worse {s['ok_worse']}; "
          f"right lines REWRITTEN without asking (repair) {s['ok_repaired']}")
    ro = s["ref_only"]
    if ro["taken"]:
        print(f"  taken answers by the ref alone: {ro['taken']} taken; of the {ro['errors_ref_sees']} error lines the "
              f"ref sees, fixed {ro['errors_fixed']}; worse {ro['worse']} (on right lines {ro['ok_worse']})")
    lat = s["reask_latency"]
    if lat["n"]:
        print(f"  re-ask latency p50 {lat['p50_ms']} ms, p95 {lat['p95_ms']} ms, mean {lat['mean_ms']} ms (n={lat['n']})"
              + (f"; chrF gain of taken answers {s['chrf_gain_taken_mean']:+.1f}"
                 if s["chrf_gain_taken_mean"] is not None else ""))
    print("  per group (errors: asked / fixed; ok lines asked): " + ", ".join(
        f"{g} {d['asked']}/{d['fixed']} of {d['errors']}; {d['ok_asked']}" for g, d in s["groups"].items()))


def print_rows(rows: list[dict]) -> None:
    for r in rows:
        if r["verdict"] in ("not asked",) and not r["changed"] and r["shown_first"] == r["first"]:
            continue
        print(f"  {r['id']} [{r['label']}|{r['group']}] {r['verdict']} ({r['reask']}) {r['source']}\n"
              f"      first: {r['first']}" + (f"\n      shown: {r['shown_first']}" if r['shown_first'] != r['first'] else "")
              + f"\n      final: {r['final']}\n      issues {r['issues_before']} -> {r['issues_after']}")


async def run_model(kind: str, path: Path, lines: list[dict], oracle: bool, show: bool) -> dict:
    out: dict = {"file": path.name}
    async with running_server(kind, path) as (url, model):
        transport = InjectingTransport()
        client = client_for(transport)
        tr = LocalLlmTranslator(url, model, client=client)
        try:
            for name, todo, as_oracle in (("app", lines, False),
                                          ("oracle", [ln for ln in lines if ln["label"] == "error"], True)):
                if as_oracle and not oracle:
                    continue
                rows = []
                for n, ln in enumerate(todo, 1):
                    check_guard(n)
                    rows.append(await run_line(tr, transport, ln, oracle=as_oracle))
                out[name] = {"summary": summarize(rows), "rows": rows}
                print_summary(kind, name, out[name]["summary"])
                if show:
                    print_rows(rows)
        finally:
            await client.aclose()
            await transport.aclose()
        out["model"] = model
        out["options"] = {k: getattr(tr.prompt_options, k) for k in ("shape", "fix_pronouns", "repair_roles",
                                                                     "reask_roles")}
    return out


async def main_async(args: argparse.Namespace) -> int:
    folder = DEV_DIR if args.dev else HELDOUT_DIR
    paths = args.files or sorted(folder.glob("*.json"))
    if not paths:
        print(f"no labelled files in {folder}")
        return 1
    every = load_lines(paths)
    lines = [ln for ln in every if ln["translation"] and ln["label"] in ("ok", "error")]
    if len(lines) < len(every):
        print(f"{len(every) - len(lines)} lines skipped: no labelled translation (label ok / error) to inject")
    if not lines:
        return 1
    if args.limit:
        lines = lines[:args.limit]
    derived = 0
    for ln in lines:
        if not ln["ref"] and args.derive_refs:
            ln["ref"] = derive_ref(ln)
            ln["ref_derived"] = bool(ln["ref"])
            derived += bool(ln["ref"])
    print(f"{len(lines)} labelled lines from {len(paths)} files; refs: {sum(bool(ln['ref']) for ln in lines)}"
          f" ({derived} derived from the labels)")
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
            models[kind] = await run_model(kind, path, lines, not args.no_oracle, args.show)
        except BlockedGame as exc:
            print(exc)
            return 2
    if not models:
        return 1
    name = "eval_roles_reask_files" if args.files else "eval_roles_reask_dev" if args.dev else "eval_roles_reask"
    path = save_results(name, {"files": [p.name for p in paths], "lines": len(lines), "refs_derived": derived,
                               "models": models})
    print(f"\nsaved {path}")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*", type=Path, help=f"labelled JSON files (default: {HELDOUT_DIR}/*.json)")
    ap.add_argument("--dev", action="store_true", help=f"the development sets in {DEV_DIR} instead")
    ap.add_argument("--models", default="quality,light", help="comma-separated model kinds (quality, light)")
    ap.add_argument("--derive-refs", action="store_true", help="lines without a ref: build one from the labels")
    ap.add_argument("--no-oracle", action="store_true", help="skip the upper-bound pass")
    ap.add_argument("--limit", type=int, default=0, help="only the first N lines")
    ap.add_argument("--show", action="store_true", help="print every line asked again or changed")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
