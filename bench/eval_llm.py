"""Evaluate the contextual LLM tier (LocalLlmTranslator on the local llama-server) per installed model.

Scenes (bench/data/translate/scenarios/*.json) run line by line as the app would: each screen is
the speaker label (role "name") plus the dialogue line, with the scene's glossary + characters and
the history of the lines translated so far (config.llm_history_lines). A scenario with "screen"
instead of "lines" is one screen of several items (UI labels, an injection attempt). Automatic
checks per line: glossary names / terms rendered exactly (case-insensitive), the speaker label
rendered as the glossary name, no Japanese left, Vietnamese pronouns (word-boundary regex) inside
the scene's expected set for that speaker -> listener pair, injection lines translated rather than
obeyed; per request: JSON parse failures and retries; per screen: latency p50/p95.

Pronoun ROLES (app.translate.pronoun_roles): a pair check passes "Anh xin lỗi nhé" from a sister who
calls herself "em", so each line is also checked for a pronoun in the wrong role (the speaker
referred to with the listener's word or the reverse) - from the source's speech acts (apology,
thanks, request, offer) and, where the line has a role-tagged "ref" ("{S:Em} xin lỗi {L:anh}"),
against the reference too. "role_errors" = lines with such an issue (with the reference),
"errors_noref" = what the source alone shows (what an app-side repair could see). Scenes and lines
carry a "split" ("tune" / "test"): tune on one half, report on the other; "probe" scenes hold the hard
cases the checker was fixed on (reported speech, a demand next to a refusal, ...), reported apart.
Not part of "clean".
--recheck re-runs the role check over the translations stored in eval_llm.json (no model, no write).

The plain eval set (eval_segments.csv) also runs one line per screen without any context, next to
Google's output from bench/results/eval_translate.csv (no new Google traffic for it). Google's
translation of the scene lines is fetched once per scene and cached in eval_llm_gtx.json.

Output: bench/results/eval_llm.json (latest details + a history of run summaries, for before/after
comparisons) and eval_llm.csv (one row per line, <model>_score columns left empty to rate 1-5; a
sheet that already holds scores is never overwritten).

  python -m bench.eval_llm [--models light,quality] [--scenes a,b] [--no-segments] [--no-gtx]
                           [--shape auto|chat|native] [--lang en|vi] [--no-listener] [--no-address]
                           [--no-history-translations] [--inline-glossary] [--fix-pronouns 0|1]
                           [--address-style roles|pair] [--role-repair 0|1] [--role-reask 0|1]
                           [--label TEXT] [--show]
  python -m bench.eval_llm --recheck [--scenes a,b]
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
import time
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path

import app.translate.llm_local as llm_local
from app.config import LLM_HOME, Config
from app.llm.models import RECOMMENDED, recommended_model
from app.llm.server import LlamaServer
from app.translate.contextual import Character, HistoryLine, ScreenItem, Term, TranslationContext
from app.translate.llm_local import LocalLlmTranslator
from app.translate.llm_prompt import PromptOptions
from app.translate.pronoun_roles import check_line, parse_ref
from bench.common import DATA_DIR, RESULTS_DIR, percentile, print_table, save_results
from bench.eval_translate import SEGMENTS_CSV, chrf, load_segments

SCENARIOS_DIR = DATA_DIR / "translate" / "scenarios"
GTX_CACHE = RESULTS_DIR / "eval_llm_gtx.json"
_JA = re.compile(r"[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]")
MAX_RUNS_KEPT = 40

# Vietnamese personal pronouns / address words. Compounds that are not a pronoun use (classifier
# "con", "cô đơn", third person "anh ấy", plurals "chúng tôi", ...) are blanked out first.
PRONOUNS = ("quý khách", "tiền bối", "senpai", "tôi", "tao", "tớ", "mày", "cậu", "anh", "em", "chị", "ông", "bà",
            "cháu", "con", "cô", "chú", "bác", "ta", "ngươi", "bạn", "ngài", "nhóc")
NOT_PRONOUNS = (
    "chúng ta", "chúng tôi", "chúng mình", "chúng em", "chúng tớ", "chúng con", "chúng cháu", "bọn mình", "bọn tao",
    "bọn tớ", "bọn em", "bọn mày", "tụi mình", "tụi tao", "tụi mày", "tụi em", "các bạn", "các em", "các cháu",
    "anh ấy", "chị ấy", "cô ấy", "em ấy", "cậu ấy", "ông ấy", "bà ấy", "bạn ấy", "con bé", "con gái", "con trai",
    "con người", "con đường", "con vật", "con bọ", "con cánh cứng", "con này", "con đó", "con kia", "con nào",
    "con mèo", "con chó", "con sâu", "con số", "con tàu", "con dao", "bà con", "ông trời", "ông bà", "cô đơn",
    "cô gái", "cậu bé", "em bé", "em gái", "em trai", "anh trai", "chị gái", "anh hùng", "tiếng anh", "bạn bè",
    "bạn trai", "bạn gái", "bác sĩ", "chú ý", "chú thích", "ta thấy", "thật ta",
)
_NOT_RE = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(p) for p in sorted(NOT_PRONOUNS, key=len, reverse=True))
                     + r")(?!\w)", re.IGNORECASE)
_PRON_RE = re.compile(r"(?<!\w)(" + "|".join(re.escape(p) for p in PRONOUNS) + r")(?!\w)", re.IGNORECASE)
_ACK = {"ok", "okay", "đã hiểu", "hiểu rồi", "rõ", "rõ rồi", "vâng", "được", "đồng ý", "了解", "liễu giải"}


def detect_pronouns(text: str) -> list[str]:
    """Vietnamese pronouns / address words used in `text` (lowercase, in order)."""
    return [m.group(1).lower() for m in _PRON_RE.finditer(_NOT_RE.sub(" ", text or ""))]


# --- scenarios ---------------------------------------------------------------------------------

def load_scenarios(names: list[str] | None = None) -> list[dict]:
    out = []
    for path in sorted(SCENARIOS_DIR.glob("*.json")):
        sc = json.loads(path.read_text(encoding="utf-8"))
        if not names or sc["name"] in names:
            out.append(sc)
    return out


def scenario_context(sc: dict, history: tuple[HistoryLine, ...] = ()) -> TranslationContext:
    terms = tuple(Term(t["source"], t["target"], t.get("kind", "term"), t.get("note", "")) for t in sc.get("glossary", []))
    chars = tuple(Character(c["name"], c.get("name_vi", ""), c.get("gender", ""), c.get("self_pronoun", ""),
                            c.get("notes", "")) for c in sc.get("characters", []))
    return TranslationContext(profile=sc["name"], style=sc.get("style", ""), terms=terms, characters=chars,
                              history=history, version=sc["name"])


def listener_of(sc: dict, line: dict) -> str | None:
    if line.get("to"):
        return line["to"]
    others = [c["name"] for c in sc.get("characters", []) if c["name"] != line.get("speaker")]
    return others[0] if len(others) == 1 else None


def glossary_pairs(sc: dict) -> list[tuple[str, str, bool]]:
    """(source, rendering, is a person's name)."""
    pairs = [(c["name"], c["name_vi"], True) for c in sc.get("characters", []) if c.get("name_vi")]
    return pairs + [(t["source"], t["target"], t.get("kind") == "person") for t in sc.get("glossary", [])]


def _similar_present(target: str, out: str) -> str | None:
    """A capitalised word sequence in `out` that looks like a variant of `target` (Genzo for Genzou)."""
    from rapidfuzz import fuzz  # noqa: PLC0415
    words = re.findall(r"[A-ZÀ-Ỹ][\wÀ-ỹ'-]*", out)
    n = max(1, len(target.split()))
    for i in range(len(words) - n + 1):
        cand = " ".join(words[i:i + n])
        if cand.casefold() != target.casefold() and fuzz.ratio(cand.casefold(), target.casefold()) >= 70:
            return cand
    return None


def line_pair(sc: dict, line: dict) -> tuple[list[str], list[str]] | None:
    """(speaker's own words, words for the listener) of a scene line, from the scene's "pronouns"."""
    exp = sc.get("pronouns", {}).get(f"{line.get('speaker')}>{listener_of(sc, line)}")
    return (exp.get("self", []), exp.get("other", [])) if exp else None


def line_split(sc: dict, line: dict) -> str:
    return line.get("split") or sc.get("split") or "unsplit"


def role_issues(sc: dict, line: dict, out: str | None) -> tuple[list[dict], list[dict]] | None:
    """(issues against the line's reference, issues the source alone shows) of one translated line;
    None when the line has no pronoun pair or no translation (not judged)."""
    pair = line_pair(sc, line)
    if not out or pair is None:
        return None

    def run(ref: str | None) -> list[dict]:
        return [asdict(i) for i in check_line(line["text"], out, *pair, sc["lang"], expected=ref)]

    return run(line.get("ref")), run(None)


def role_fields(sc: dict, line: dict, out: str | None) -> dict:
    """The role columns of a scene row."""
    res = role_issues(sc, line, out)
    return {"split": line_split(sc, line), "role_checked": res is not None,
            "role_issues": res[0] if res else [], "role_issues_noref": res[1] if res else []}


def role_stats(rows: list[dict]) -> dict:
    """Role errors of scene rows, overall ("all") and per split: lines judged, lines with an issue
    (with the reference / from the source alone) and issues."""
    out: dict[str, dict] = {}
    for r in rows:
        if not r.get("role_checked"):
            continue
        for key in ("all", r.get("split") or "unsplit"):
            s = out.setdefault(key, {"lines": 0, "errors": 0, "errors_noref": 0, "issues": 0})
            s["lines"] += 1
            s["errors"] += bool(r["role_issues"])
            s["errors_noref"] += bool(r["role_issues_noref"])
            s["issues"] += len(r["role_issues"])
    return out


def role_error_list(scene_results: list[dict]) -> list[dict]:
    """Every line with a role issue: source, translation and what is wrong."""
    return [{"scene": sc["name"], "n": r["n"], "split": r.get("split"), "speaker": r.get("speaker"),
             "listener": r.get("listener"), "source": r["source"], "out": r["out"],
             "issues": [f"{i['kind']} '{i['found']}' -> '{i['expected_word']}' [{i['basis']}]: {i['message']}"
                        for i in r["role_issues"]]}
            for sc in scene_results for r in sc["rows"] if r.get("role_issues")]


# "probe": hard cases the checker's rules were fixed on (roles_probe_*), kept apart so that tune / test
# stay comparable with earlier runs; "all" includes them.
ROLE_SPLITS = ("all", "tune", "test", "probe")


def _role_cell(stats: dict, key: str) -> str:
    s = stats.get(key)
    return f"{s['errors']}/{s['lines']}" if s else "-"


SOFT_ISSUES = ("name_dropped:",)  # a name used as a form of address rendered as the pronoun


def check_output(sc: dict, source: str, out: str | None, *, speaker: str | None = None, role: str = "dialog",
                 injection: bool = False) -> list[str]:
    """Automatic issues of one translated item (empty list = clean). A person's name that is absent
    is "name_dropped" (soft) unless a variant spelling of it is there (then a glossary error)."""
    if not out:
        return ["missing"]
    issues = []
    low = out.casefold()
    present: list[str] = []
    for src, tgt, person in sorted(glossary_pairs(sc), key=lambda p: len(p[0]), reverse=True):
        if src not in source or any(src in longer for longer in present):
            continue
        present.append(src)
        if tgt.casefold() in low:
            continue
        variant = _similar_present(tgt, out) if person else None
        if person and variant is None:
            issues.append(f"name_dropped:{src}->{tgt}")
        else:
            issues.append(f"glossary:{src}->{tgt}" + (f" (wrote {variant})" if variant else ""))
    if _JA.search(out):
        issues.append("japanese_left")
    if speaker:
        exp = sc.get("pronouns", {}).get(f"{speaker}>{listener_of(sc, {'speaker': speaker})}")
        if exp:
            allowed = {p.lower() for p in exp.get("self", []) + exp.get("other", [])}
            bad = sorted({p for p in detect_pronouns(out) if p not in allowed})
            if bad:
                issues.append("pronoun:" + "/".join(bad))
    if injection and (len(out) < 15 or out.strip(" .!。").casefold() in _ACK):
        issues.append("followed_injection")
    if role == "ui" and len(out) > 3 * len(source) + 12:
        issues.append("ui_too_long")
    return issues


# --- parse-failure accounting (wraps the translator's parser) -----------------------------------

class ParseStats:
    def __init__(self):
        self.requests = 0
        self.json_failures = 0  # the answer was not valid JSON at all
        self.incomplete = 0  # valid JSON but some requested ids missing / rejected
        self.raw: list[str] = []

    def install(self):
        orig = llm_local.parse_response

        def spy(text, items, ctx=None):
            self.requests += 1
            try:
                json.loads(text)
            except ValueError:
                self.json_failures += 1
            res = orig(text, items, ctx)
            if len(res.translations) < len(items):
                self.incomplete += 1
                self.raw.append(text[:600])
            return res

        llm_local.parse_response = spy
        return orig


# --- running --------------------------------------------------------------------------------------

def _name_box() -> tuple[int, int, int, int]:
    return (80, 600, 140, 34)


def _line_box() -> tuple[int, int, int, int]:
    return (80, 640, 1100, 90)


async def run_scene(tr: LocalLlmTranslator, sc: dict, history_n: int) -> dict:
    """Translate the scene as the app would; returns per-line rows and the scene summary."""
    lang = sc["lang"]
    rows = []
    lat = []
    proposed: list[dict] = []
    if "screen" in sc:
        items = [ScreenItem(i + 1, it["text"], (80, 60 + 50 * i, 400, 40), it.get("role", "dialog"))
                 for i, it in enumerate(sc["screen"])]
        t0 = time.perf_counter()
        res = await tr.translate_screen(items, lang, "vi", scenario_context(sc))
        lat.append(time.perf_counter() - t0)
        for it, spec in zip(items, sc["screen"]):
            out = res.translations.get(it.id)
            declined = tr.skip_reason(it) if out is None else None  # the app keeps Google's translation
            rows.append({"n": it.id, "item_id": it.id, "role": it.role_hint, "speaker": None, "listener": None,
                         "source": it.text, "out": out, "declined": declined,
                         "issues": [] if declined else
                         check_output(sc, it.text, out, role=it.role_hint, injection=spec.get("injection", False))})
        proposed += [{"source": t.source, "target": t.target, "kind": t.kind} for t in res.new_terms]
    else:
        history: list[HistoryLine] = []
        name_ids = {c["name"]: 1000 + i for i, c in enumerate(sc.get("characters", []))}
        for n, line in enumerate(sc["lines"], 1):
            sp = line["speaker"]
            items = [ScreenItem(name_ids.get(sp, 999), sp, _name_box(), "name"),
                     ScreenItem(n, line["text"], _line_box(), "dialog")]
            ctx = scenario_context(sc, tuple(history[-history_n:]) if history_n > 0 else ())
            t0 = time.perf_counter()
            res = await tr.translate_screen(items, lang, "vi", ctx)
            lat.append(time.perf_counter() - t0)
            out = res.translations.get(n)
            name_out = res.translations.get(items[0].id)
            vi_name = next((c["name_vi"] for c in sc.get("characters", []) if c["name"] == sp), sp)
            issues = check_output(sc, line["text"], out, speaker=sp)
            if (name_out or "").casefold() != vi_name.casefold():
                issues.append(f"name_label:{name_out!r}")
            if res.speakers.get(n) != sp:
                issues.append(f"speaker:{res.speakers.get(n)!r}")
            rows.append({"n": n, "item_id": n, "role": "dialog", "speaker": sp, "listener": listener_of(sc, line),
                         "source": line["text"], "out": out, "name_out": name_out, "issues": issues,
                         "pronouns": detect_pronouns(out or ""), "latency_ms": round(lat[-1] * 1000),
                         "timings": {k: tr.last_timings.get(k) for k in ("cache_n", "prompt_n", "predicted_n")},
                         # the role re-ask of this line (LocalLlmTranslator.last_role_reask) and what it answered
                         "reask": tr.last_role_reask.get(n), "reask_answer": tr.last_role_candidates.get(n),
                         **role_fields(sc, line, out)})
            proposed += [{"source": t.source, "target": t.target, "kind": t.kind, "line": n} for t in res.new_terms]
            history.append(HistoryLine(sp, line["text"], out or ""))
    pairs: dict[str, Counter] = {}
    for r in rows:
        if r.get("speaker"):
            pairs.setdefault(f"{r['speaker']}>{r['listener']}", Counter()).update(r.get("pronouns", []))
    consistency = {}
    for pair, cnt in pairs.items():
        exp = sc.get("pronouns", {}).get(pair, {})
        allowed = {p.lower() for p in exp.get("self", []) + exp.get("other", [])}
        consistency[pair] = {"used": dict(cnt), "outside_expected": sorted(p for p in cnt if allowed and p not in allowed),
                             "consistent": not allowed or all(p in allowed for p in cnt)}
    expect_terms = sc.get("expect_new_terms", [])
    declined = [r for r in rows if r.get("declined")]
    rows_answered = [r for r in rows if not r.get("declined")]
    summary = {
        "lines": len(rows_answered),
        "declined": len(declined),  # left to Google by design (the 1.8B: menus, long text)
        "clean": sum(not [i for i in r["issues"] if not i.startswith(SOFT_ISSUES)] for r in rows_answered),
        "strict_clean": sum(not r["issues"] for r in rows_answered),
        "missing": sum("missing" in r["issues"] for r in rows),
        "glossary_misses": sum(i.startswith(("glossary:", "name_label:")) for r in rows for i in r["issues"]),
        "names_dropped": sum(i.startswith("name_dropped:") for r in rows for i in r["issues"]),
        "japanese_left": sum("japanese_left" in r["issues"] for r in rows),
        "pronoun_violations": sum(any(i.startswith("pronoun:") for i in r["issues"]) for r in rows),
        "speaker_errors": sum(any(i.startswith("speaker:") for i in r["issues"]) for r in rows),
        "followed_injection": sum("followed_injection" in r["issues"] for r in rows),
        "pairs_consistent": sum(v["consistent"] for v in consistency.values()),
        "pairs": len(consistency),
        "expected_new_terms_found": [t for t in expect_terms if any(p["source"] == t for p in proposed)],
        "latency_ms": [round(x * 1000) for x in lat],
        "roles": role_stats(rows),
    }
    return {"name": sc["name"], "lang": sc["lang"], "rows": rows, "consistency": consistency,
            "new_terms": proposed, "summary": summary}


async def run_segments(tr: LocalLlmTranslator, segments: list[dict], gtx: dict[str, str] | None = None) -> list[dict]:
    """One line per screen without context. "chrf" scores the model's answers; "chrf_shown" what the
    app shows: Google's translation where the model declines the line (skip_reason)."""
    rows = []
    empty = TranslationContext(profile="eval", version="none")
    for seg in segments:
        role = "ui" if seg["category"] == "ui" else "dialog"
        item = ScreenItem(1, seg["source"], (0, 0, 800, 40), role)
        t0 = time.perf_counter()
        res = await tr.translate_screen([item], seg["lang"], "vi", empty)
        dt = time.perf_counter() - t0
        out = res.translations.get(1)
        declined = tr.skip_reason(item) if out is None else None
        shown = out if out is not None else (gtx or {}).get(seg["id"]) if declined else None
        ref = seg.get("reference_vi", "").strip()
        rows.append({"id": seg["id"], "lang": seg["lang"], "category": seg["category"], "source": seg["source"],
                     "reference_vi": ref, "out": out, "declined": declined, "latency_ms": round(dt * 1000),
                     "japanese_left": bool(out and _JA.search(out)), "missing": not out and not declined,
                     "chrf": round(chrf(out or "", ref), 1) if ref and not declined else None,
                     "chrf_shown": round(chrf(shown or "", ref), 1) if ref else None})
    return rows


def _lat_summary(ms: list[float]) -> dict:
    return {"n": len(ms), "p50_ms": round(percentile(ms, 50)), "p95_ms": round(percentile(ms, 95)),
            "max_ms": round(max(ms)) if ms else None}


async def run_model(kind: str, path: Path, scenes: list[dict], segments: list[dict], options: PromptOptions | None,
                    history_n: int, gtx_segs: dict[str, str] | None = None) -> dict:
    server = LlamaServer(path, log_path=LLM_HOME / "logs" / f"eval-{path.stem}.log")
    print(f"[{kind}] {path.name}: starting llama-server")
    t0 = time.perf_counter()
    url = await asyncio.to_thread(server.start)
    print(f"[{kind}] ready in {time.perf_counter() - t0:.1f} s at {url}")
    stats = ParseStats()
    orig = stats.install()
    tr = LocalLlmTranslator(url, server.model_name)
    if options is not None:
        tr.prompt_options = options
    try:
        scene_results = []
        for sc in scenes:
            r = await run_scene(tr, sc, history_n)
            s = r["summary"]
            print(f"  {sc['name']:18} clean {s['clean']}/{s['lines']} (strict {s['strict_clean']})  glossary "
                  f"{s['glossary_misses']}  names dropped {s['names_dropped']}  pronoun {s['pronoun_violations']}  "
                  f"ja {s['japanese_left']}  pairs ok {s['pairs_consistent']}/{s['pairs']}  "
                  f"roles {_role_cell(s['roles'], 'all')}  p50 {percentile(s['latency_ms'], 50):.0f} ms")
            scene_results.append(r)
        seg_rows = await run_segments(tr, segments, gtx_segs) if segments else []
    finally:
        llm_local.parse_response = orig
        await tr.aclose()
        await asyncio.to_thread(server.stop)
    screen_ms = [x for r in scene_results for x in r["summary"]["latency_ms"]]
    tot = {k: sum(r["summary"][k] for r in scene_results) for k in
           ("lines", "declined", "clean", "strict_clean", "missing", "glossary_misses", "names_dropped",
            "japanese_left", "pronoun_violations", "speaker_errors", "followed_injection", "pairs_consistent", "pairs")}
    seg_chrf = [r["chrf"] for r in seg_rows if r["chrf"] is not None]
    seg_shown = [r["chrf_shown"] for r in seg_rows if r["chrf_shown"] is not None]
    roles = role_stats([r for sc in scene_results for r in sc["rows"]])
    reasked = [{"scene": sc["name"], "n": r["n"], "speaker": r["speaker"], "source": r["source"], "out": r["out"],
                "reask": r["reask"], "reask_answer": r["reask_answer"]}
               for sc in scene_results for r in sc["rows"] if r.get("reask")]
    summary = {**tot, "role_errors": roles.get("all", {}).get("errors", 0), "roles": roles,
               "json_failures": stats.json_failures, "incomplete_answers": stats.incomplete,
               "requests": stats.requests, "retries": tr.retries, "role_retries": tr.role_retries,
               "role_retry_items": tr.role_retry_items, "role_retry_taken": tr.role_retry_taken,
               "role_reask_lines": reasked,
               "scene_latency": _lat_summary(screen_ms),
               "segments": {"n": len(seg_rows), "missing": sum(r["missing"] for r in seg_rows),
                            "declined": sum(bool(r["declined"]) for r in seg_rows),
                            "japanese_left": sum(r["japanese_left"] for r in seg_rows),
                            "chrf_mean": round(sum(seg_chrf) / len(seg_chrf), 1) if seg_chrf else None,
                            "chrf_n": len(seg_chrf),
                            "chrf_shown_mean": round(sum(seg_shown) / len(seg_shown), 1) if seg_shown else None,
                            "latency": _lat_summary([r["latency_ms"] for r in seg_rows]) if seg_rows else None}}
    return {"file": path.name, "model": server.model_name, "options": asdict(tr.prompt_options), "summary": summary,
            "scenes": scene_results, "segments": seg_rows, "incomplete_raw": stats.raw[:10],
            "role_errors": role_error_list(scene_results)}


# --- Google reference ---------------------------------------------------------------------------

async def gtx_for_scenes(scenes: list[dict], enabled: bool) -> dict[str, list[str]]:
    """Google's translation of every scene line (one batch request per scene, cached on disk)."""
    try:
        cache = json.loads(GTX_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    todo = []
    for sc in scenes:
        texts = [ln["text"] for ln in sc.get("lines", [])] or [it["text"] for it in sc.get("screen", [])]
        key = sc["name"]
        if cache.get(key, {}).get("texts") != texts:
            todo.append((sc, texts))
    if todo and enabled:
        from app.translate.google_gtx import GoogleGtxTranslator  # noqa: PLC0415
        from app.translate.base import TranslationError  # noqa: PLC0415
        eng = GoogleGtxTranslator()
        try:
            for sc, texts in todo:
                try:
                    out = await eng.translate_batch(texts, sc["lang"], "vi")
                except TranslationError as exc:
                    print(f"  gtx failed for {sc['name']}: {exc}")
                    continue
                cache[sc["name"]] = {"texts": texts, "vi": out}
        finally:
            await eng.aclose()
        GTX_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return {k: v["vi"] for k, v in cache.items()}


def gtx_for_segments() -> dict[str, str]:
    sheet = RESULTS_DIR / "eval_translate.csv"
    if not sheet.exists():
        return {}
    with sheet.open(encoding="utf-8-sig", newline="") as f:
        return {row["id"]: row.get("gtx_vi", "") for row in csv.DictReader(f)}


# --- output ----------------------------------------------------------------------------------------

def _sheet_has_scores(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            return any(v.strip() for row in csv.DictReader(f) for k, v in row.items() if k and k.endswith("_score") and v)
    except (OSError, csv.Error, UnicodeDecodeError):
        return True


def write_sheet(path: Path, scenes: list[dict], models: dict[str, dict], gtx_scenes: dict[str, list[str]],
                gtx_segs: dict[str, str], segments: list[dict]) -> Path:
    if _sheet_has_scores(path):
        path = path.with_name(f"{path.stem}.{time.strftime('%Y%m%d-%H%M%S')}{path.suffix}")
        print(f"note: existing sheet has scores; writing {path.name} instead")
    kinds = list(models)
    cols = ["set", "scene", "split", "n", "role", "speaker", "listener", "expected_pronouns", "source", "reference_vi",
            "gtx_vi", "gtx_score"]
    for k in kinds:
        cols += [f"{k}_vi", f"{k}_issues", f"{k}_roles", f"{k}_score"]
    cols += ["gtx_chrf"] + [f"{k}_chrf" for k in kinds]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for si, sc in enumerate(scenes):
            g = gtx_scenes.get(sc["name"], [])
            n_rows = len(sc.get("lines") or sc.get("screen") or [])
            for i in range(n_rows):
                spec = (sc.get("lines") or sc.get("screen"))[i]
                sp = spec.get("speaker")
                lis = listener_of(sc, spec) if sp else None
                exp = sc.get("pronouns", {}).get(f"{sp}>{lis}", {})
                row = {"set": "scene", "scene": sc["name"], "split": line_split(sc, spec), "n": i + 1,
                       "role": spec.get("role", "dialog"), "speaker": sp or "", "listener": lis or "",
                       "expected_pronouns": ("self " + "/".join(exp.get("self", [])) + "; other "
                                             + "/".join(exp.get("other", []))) if exp else "",
                       "source": spec["text"], "reference_vi": parse_ref(spec["ref"])[0] if spec.get("ref") else "",
                       "gtx_vi": g[i] if i < len(g) else "", "gtx_score": ""}
                for k in kinds:
                    r = models[k]["scenes"][si]["rows"][i]
                    row[f"{k}_vi"] = r["out"] or ""
                    row[f"{k}_issues"] = "; ".join(r["issues"]) or (
                        f"declined ({r['declined']}): Google kept" if r.get("declined") else "")
                    row[f"{k}_roles"] = "; ".join(f"{x['kind']} '{x['found']}' -> '{x['expected_word']}' [{x['basis']}]"
                                                  for x in r.get("role_issues", []))
                    row[f"{k}_score"] = ""
                w.writerow(row)
        for j, seg in enumerate(segments):
            ref = seg.get("reference_vi", "").strip()
            g = gtx_segs.get(seg["id"], "")
            row = {"set": "segment", "scene": seg["id"], "split": "", "n": "", "role": seg["category"], "speaker": "",
                   "listener": "",
                   "expected_pronouns": "", "source": seg["source"], "reference_vi": ref, "gtx_vi": g, "gtx_score": "",
                   "gtx_chrf": f"{chrf(g, ref):.1f}" if ref and g else ""}
            for k in kinds:
                r = models[k]["segments"][j] if j < len(models[k]["segments"]) else {}
                row[f"{k}_vi"] = r.get("out") or ""
                row[f"{k}_issues"] = "; ".join(x for x, bad in (("missing", r.get("missing")),
                                                                (f"declined ({r.get('declined')}): Google kept",
                                                                 r.get("declined")),
                                                                ("japanese_left", r.get("japanese_left"))) if bad)
                row[f"{k}_score"] = ""
                row[f"{k}_chrf"] = "" if r.get("chrf") is None else f"{r['chrf']:.1f}"
            w.writerow(row)
    return path


def gtx_scene_rows(scenes: list[dict], gtx_scenes: dict[str, list[str]]) -> list[dict]:
    """Google's translation of the scene lines as rows with the role columns (a reference point)."""
    rows = []
    for sc in scenes:
        g = gtx_scenes.get(sc["name"], [])
        for n, line in enumerate(sc.get("lines", []), 1):
            out = g[n - 1] if n - 1 < len(g) else None
            rows.append({"n": n, "speaker": line["speaker"], "listener": listener_of(sc, line), "source": line["text"],
                         "out": out, **role_fields(sc, line, out)})
    return rows


def print_roles(models: dict[str, dict], gtx_roles: dict | None = None) -> None:
    """Role errors per model and split, then every line with one."""
    print("\npronoun roles (lines with an issue / lines judged; noref = from the source alone):")
    for kind, m in models.items():
        st = m["summary"]["roles"]
        print(f"  {kind}: " + ", ".join(f"{k} {_role_cell(st, k)} (noref {st[k]['errors_noref']}, issues "
                                        f"{st[k]['issues']})" for k in ROLE_SPLITS if k in st))
    if gtx_roles:
        print("  gtx: " + ", ".join(f"{k} {_role_cell(gtx_roles, k)} (noref {gtx_roles[k]['errors_noref']})"
                                    for k in ROLE_SPLITS if k in gtx_roles))
    for kind, m in models.items():
        for e in m.get("role_errors", []):
            print(f"  [{kind} {e['split']}] {e['scene']}#{e['n']} {e['speaker']}>{e['listener']}: {e['source']}\n"
                  f"      -> {e['out']}" + "".join(f"\n      ! {x}" for x in e["issues"]))


def recheck(scene_names: list[str] | None) -> int:
    """The role check over the translations stored in eval_llm.json, against the current scenes and
    checker (no model, nothing written). Rows whose source no longer matches the scene are skipped."""
    try:
        data = json.loads((RESULTS_DIR / "eval_llm.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"no stored results: {exc}")
        return 1
    scenes = {sc["name"]: sc for sc in load_scenarios(scene_names)}
    models: dict[str, dict] = {}
    for kind, m in data.get("models", {}).items():
        results = []
        for scr in m.get("scenes", []):
            sc = scenes.get(scr["name"])
            if not sc or "lines" not in sc:
                continue
            rows = []
            for r in scr["rows"]:
                line = sc["lines"][r["n"] - 1] if 0 < r["n"] <= len(sc["lines"]) else None
                if line is None or line["text"] != r["source"]:
                    continue
                rows.append({**r, **role_fields(sc, line, r.get("out"))})
            results.append({"name": sc["name"], "rows": rows})
        rows_all = [r for sc in results for r in sc["rows"]]
        models[kind] = {"summary": {"roles": role_stats(rows_all)}, "role_errors": role_error_list(results)}
    print(f"stored run: {data.get('latest', {}).get('created')} {data.get('latest', {}).get('label', '')!r}")
    print_roles(models, role_stats(gtx_scene_rows(list(scenes.values()), data.get("gtx_scenes", {}))))
    return 0


def _previous_runs() -> list[dict]:
    try:
        return json.loads((RESULTS_DIR / "eval_llm.json").read_text(encoding="utf-8")).get("runs", [])
    except (OSError, ValueError):
        return []


async def main_async(args: argparse.Namespace) -> int:
    from app.ui.game_guard import GameGuard, matches  # noqa: PLC0415
    blocked = Config.load().blocked_processes
    exe = GameGuard.foreground_process_name()
    if matches(exe, blocked):
        print(f"a blocked game ({exe}) is in the foreground: not loading the GPU now, try again later")
        return 2
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)  # ignored by git: missing on a fresh clone (GTX cache, CSV)
    scenes = load_scenarios([s.strip() for s in args.scenes.split(",")] if args.scenes else None)
    segments = [] if args.no_segments else load_segments(SEGMENTS_CSV)
    options = None
    if (args.shape != "auto" or args.lang != "en" or args.no_listener or args.inline_glossary or args.no_address
            or args.no_history_translations or args.fix_pronouns is not None):
        options = PromptOptions(shape=args.shape if args.shape != "auto" else "chat", lang=args.lang,
                                listener=not args.no_listener, address=not args.no_address,
                                history_translations=not args.no_history_translations,
                                inline_glossary=args.inline_glossary, fix_pronouns=bool(args.fix_pronouns))
    roles = {k: (v if k == "address_style" else bool(v)) for k, v in
             (("address_style", args.address_style), ("repair_roles", args.role_repair),
              ("reask_roles", args.role_reask)) if v is not None}
    kinds = [k.strip() for k in args.models.split(",") if k.strip()]
    gtx_scenes = await gtx_for_scenes(load_scenarios(), not args.no_gtx)
    gtx_segs = gtx_for_segments()
    models: dict[str, dict] = {}
    for kind in kinds:
        path = recommended_model(kind)
        if path is None or path.name not in RECOMMENDED.get(kind, ()):
            print(f"[{kind}] not installed, skipped")
            continue
        exe = GameGuard.foreground_process_name()
        if matches(exe, blocked):
            print(f"a blocked game ({exe}) came to the foreground: stopping")
            break
        opt = options
        if args.shape == "auto" and options is not None:
            opt = replace(options, shape=PromptOptions.for_model(path.stem).shape)
        if roles:
            opt = replace(opt or PromptOptions.for_model(path.stem), **roles)
        models[kind] = await run_model(kind, path, scenes, segments, opt, args.history, gtx_segs)
    if not models:
        return 1
    rows = []
    for kind, m in models.items():
        s = m["summary"]
        rows.append({"model": kind, "shape": m["options"].get("shape"), "lang": m["options"].get("lang"),
                     "clean": f"{s['clean']}/{s['lines']}", "strict": s["strict_clean"],
                     "glossary": s["glossary_misses"], "name_drop": s["names_dropped"],
                     "pronoun": s["pronoun_violations"], "roles": _role_cell(s["roles"], "all"),
                     "role_tune": _role_cell(s["roles"], "tune"), "role_test": _role_cell(s["roles"], "test"),
                     "role_probe": _role_cell(s["roles"], "probe"),
                     "ja_left": s["japanese_left"],
                     "pairs_ok": f"{s['pairs_consistent']}/{s['pairs']}", "speaker": s["speaker_errors"],
                     "inject": s["followed_injection"], "json_fail": s["json_failures"],
                     "incomplete": s["incomplete_answers"], "retries": s["retries"], "role_retries": s["role_retries"],
                     "declined": s["declined"] + s["segments"]["declined"],
                     "p50": s["scene_latency"]["p50_ms"], "p95": s["scene_latency"]["p95_ms"],
                     "seg_chrF": s["segments"]["chrf_mean"], "seg_chrF_shown": s["segments"]["chrf_shown_mean"],
                     "seg_ja": s["segments"]["japanese_left"]})
    print()
    print_table(rows, list(rows[0]))
    gtx_ref = [chrf(gtx_segs.get(seg["id"], ""), seg["reference_vi"]) for seg in segments
               if seg.get("reference_vi", "").strip() and gtx_segs.get(seg["id"])]
    if gtx_ref:
        print(f"gtx chrF on the same segments: {sum(gtx_ref) / len(gtx_ref):.1f} (n={len(gtx_ref)})")
    gtx_roles = role_stats(gtx_scene_rows(scenes, gtx_scenes))
    print_roles(models, gtx_roles)
    print("\nlines asked again for their pronoun roles (read each one):")
    for kind, m in models.items():
        for x in m["summary"]["role_reask_lines"]:
            answer = f"\n      re-ask answer: {x['reask_answer']}" if x["reask_answer"] not in (None, x["out"]) else ""
            print(f"  [{kind}] {x['scene']}#{x['n']} {x['speaker']}: {x['source']}\n      -> {x['out']}  "
                  f"({x['reask']}){answer}")
    if args.show:
        for kind, m in models.items():
            print(f"\n=== {kind}")
            for sc in m["scenes"]:
                print(f"--- {sc['name']}")
                for r in sc["rows"]:
                    flag = f"   [!] {', '.join(r['issues'])}" if r["issues"] else ""
                    roles = "".join(f"\n     [role] {i['message']}" for i in r.get("role_issues", []))
                    print(f"  {r.get('speaker') or r.get('role')}: {r['source']}\n     -> {r['out']}{flag}{roles}")
                if sc["new_terms"]:
                    print(f"  new_terms: {sc['new_terms']}")
    sheet = write_sheet(RESULTS_DIR / "eval_llm.csv", scenes, models, gtx_scenes, gtx_segs, segments)
    run_summary = {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "label": args.label,
                   "args": {k: v for k, v in vars(args).items() if k != "label"},
                   "models": {k: {"file": m["file"], "options": m["options"], **m["summary"]} for k, m in models.items()},
                   "gtx_segments_chrf_mean": round(sum(gtx_ref) / len(gtx_ref), 1) if gtx_ref else None,
                   "gtx_roles": gtx_roles}
    runs = (_previous_runs() + [run_summary])[-MAX_RUNS_KEPT:]
    path = save_results("eval_llm", {"latest": run_summary, "runs": runs, "models": models,
                                     "gtx_scenes": gtx_scenes, "sheet": str(sheet)})
    print(f"\nsheet: {sheet}\nresults: {path}")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--models", default="light,quality", help="comma-separated model kinds (light, quality)")
    ap.add_argument("--scenes", default="", help="comma-separated scenario names (default: all)")
    ap.add_argument("--no-segments", action="store_true", help="skip the plain eval_segments.csv lines")
    ap.add_argument("--no-gtx", action="store_true", help="no Google request for uncached scene lines")
    ap.add_argument("--shape", choices=["auto", "chat", "native"], default="auto",
                    help="prompt shape (auto = PromptOptions.for_model)")
    ap.add_argument("--lang", choices=["en", "vi"], default="en", help="language of the instructions")
    ap.add_argument("--no-listener", action="store_true", help="do not add the guessed listener to lines")
    ap.add_argument("--no-address", action="store_true", help="no per-line pronoun pair from the characters")
    ap.add_argument("--no-history-translations", action="store_true", help="history lines without their Vietnamese")
    ap.add_argument("--inline-glossary", action="store_true", help="write glossary renderings into the source text")
    ap.add_argument("--fix-pronouns", type=int, choices=[0, 1], default=None,
                    help="swap fallback pronouns for the characters' pair afterwards (default: per model)")
    ap.add_argument("--address-style", choices=["roles", "pair"], default=None,
                    help="how a line's pronoun pair is given (default: pair)")
    ap.add_argument("--role-repair", type=int, choices=[0, 1], default=None,
                    help="rewrite pronouns in a wrong role afterwards (default: 1)")
    ap.add_argument("--role-reask", type=int, choices=[0, 1], default=None,
                    help="ask once more for role issues the repair leaves (default: 1)")
    ap.add_argument("--history", type=int, default=Config().llm_history_lines, help="history lines per screen")
    ap.add_argument("--label", default="", help="free text stored with this run's summary")
    ap.add_argument("--show", action="store_true", help="print every scene line")
    ap.add_argument("--recheck", action="store_true",
                    help="only re-run the pronoun-role check over the stored eval_llm.json translations")
    args = ap.parse_args()
    if args.recheck:
        return recheck([s.strip() for s in args.scenes.split(",")] if args.scenes else None)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
