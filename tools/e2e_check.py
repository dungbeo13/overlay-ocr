"""End-to-end check of the real app: demo window -> dxcam -> CUDA OCR -> live Google -> overlay.

Starts tools.demo_text_window, waits for its client rect, then runs `python -m app` on exactly that
region (--no-hotkeys --no-guard --exit-after N --debug-dump ...) with its own data directory
(SCREEN_TRANSLATOR_HOME), samples the app's CPU and RSS with psutil, and analyses the dump:

- per scene occurrence: the first published state in which every line of the scene is covered by
  translated blocks (OCR'd source fuzzy-matched to the scene's lines) and the latency from the
  moment the scene's text was complete on screen to that state reaching the GUI thread;
- self-capture: no OCR'd source contains Vietnamese-only letters or one of our translations, and
  the app log reports no capture-exclusion failure;
- exit code 0 within a few seconds of --exit-after; peak CPU% / RSS; number of published states.

Contextual LLM tier (--llm = the "llm" scene set in refine mode for 80 s; or --scenes llm, or
--llm-mode refine|only with any scene set): a glossary profile for the demo conversation (characters
with their forms of address, two terms; one speaker deliberately left out) is written to the run's
data dir and selected with --profile. Checks, from states.jsonl, the app's llm.jsonl (one line per
LLM screen result: speakers, new terms, dialogue-memory size), the profile file and the processes:
- tier order per dialogue block: refine = Google (tier 1) first where it arrives first, then the LLM
  (tier 2) in place, never back to tier 1, and every block reaches tier 2; only = no Google text
  while the LLM is up; no blank frame (a translated block shown empty again) on the replacement;
- glossary: speaker labels exactly as in the glossary, names and terms inside the lines kept;
- speaker attribution of every dialogue line the LLM translated; the dialogue memory grows;
- the unknown speaker's name lands in the profile file as an auto term;
- llama-server listens on 127.0.0.1 only, none outlives the app, VRAM back to the level before.
Reported: latency to the first translation and to tier 2 per page (p50/p95), LLM time per screen,
the forms of address used (model quality; role-aware: "Anh xin lỗi" from Sera, who calls herself
"em", is wrong - app.translate.pronoun_roles), Google's and the LLM's translation side by side, VRAM
before / peak / after, CPU of the app and of llama-server.
--no-model runs the app with an empty LLM home (no model installed): Google must still translate
everything, the log must say the model is missing, and no llama-server may start.

Results: bench/results/e2e/<run>/ (logs, dump, composites) and bench/results/e2e_mvp.json (latest
run per scene set / LLM mode). Exit code 1 if a check fails.

Run: python -m tools.e2e_check [--scenes ja|mixed|en|llm] [--lang ja] [--exit-after 48]
     [--llm] [--llm-mode off|refine|only] [--llm-model NAME] [--no-model]
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import psutil
from rapidfuzz import fuzz

from app.config import PROJECT_ROOT
from app.translate.llm_prompt import PromptOptions
from app.translate.pronoun_roles import check_line
from tools.demo_text_window import JA_TALK

RESULTS = PROJECT_ROOT / "bench" / "results"
LOADING_MESSAGE = "Đang tải OCR…"
_VI_ONLY = set("ăâđêôơưĂÂĐÊÔƠƯ")
_EXCLUSION_PROBLEMS = re.compile(r"exclusion (failed|LOST)|không ẩn được overlay", re.IGNORECASE)
_LOG_TS = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}) ")
_JA = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
MSG_NO_MODEL = "LLM: chưa có model"  # app.main.MSG_LLM_NO_MODEL (not imported: that pulls in the whole app)

# Glossary profile for the demo conversation (tools.demo_text_window.JA_TALK). ケンジ is left out on
# purpose: the LLM renders his name label and the app stores it in the profile as an auto term.
E2E_PROFILE = "E2E Demo"
E2E_CHARACTERS = (  # name, name_vi, gender, self_pronoun, notes
    ("セラ", "Sera", "nữ", "em", "Em gái của Ren. Gọi Ren là 'anh' (お兄ちゃん), xưng 'em'."),
    ("レン", "Ren", "nam", "anh", "Anh trai của Sera. Gọi Sera là 'em' (khi bực: 'mày'), xưng 'anh' (khi bực: 'tao')."),
)
E2E_TERMS = (("灯台", "ngọn hải đăng", "place"), ("井戸", "giếng", "place"))
E2E_STYLE = "Lời thoại tự nhiên, ngắn gọn, giữa hai anh em ruột."
UNKNOWN_SPEAKERS = tuple(dict.fromkeys(s for s, _ in JA_TALK if s not in {c[0] for c in E2E_CHARACTERS}))
TALK_SPEAKERS = frozenset(s for s, _ in JA_TALK)
# Forms of address between the siblings: expected pronouns, and ones that would be wrong for them.
ADDRESS_WORDS = {"セラ": ("anh", "em"), "レン": ("anh", "em", "tao", "mày")}
WRONG_ADDRESS = ("bạn", "cậu", "tôi", "tớ")
# Who each sibling's words stand for (own, the other's): "Anh xin lỗi" from Sera is the right pair in
# the wrong role (app.translate.pronoun_roles).
ADDRESS_ROLES = {"セラ": ("em", "anh"), "レン": (("anh", "tao"), ("em", "mày"))}
VRAM_TOLERANCE_MB = 300  # "after" may differ from "before" by other programs' allocations
ASLEEP_REFRESHES_PER_S = 10  # DWM vblanks per second below this: the monitor sleeps


class _Ratio(ctypes.Structure):
    _fields_ = [("num", ctypes.c_uint32), ("den", ctypes.c_uint32)]


class _DwmTimingInfo(ctypes.Structure):  # DWM_TIMING_INFO (dwmapi.h, packed)
    _pack_ = 1
    _fields_ = ([("cbSize", ctypes.c_uint32), ("rateRefresh", _Ratio), ("qpcRefreshPeriod", ctypes.c_uint64),
                 ("rateCompose", _Ratio), ("qpcVBlank", ctypes.c_uint64), ("cRefresh", ctypes.c_uint64),
                 ("cDXRefresh", ctypes.c_uint32), ("qpcCompose", ctypes.c_uint64), ("cFrame", ctypes.c_uint64),
                 ("cDXPresent", ctypes.c_uint32), ("cRefreshFrame", ctypes.c_uint64),
                 ("cFrameSubmitted", ctypes.c_uint64), ("cDXPresentSubmitted", ctypes.c_uint32),
                 ("cFrameConfirmed", ctypes.c_uint64), ("cDXPresentConfirmed", ctypes.c_uint32),
                 ("cRefreshConfirmed", ctypes.c_uint64), ("cDXRefreshConfirmed", ctypes.c_uint32),
                 ("cFramesLate", ctypes.c_uint64), ("cFramesOutstanding", ctypes.c_uint32)]
                + [(f"_u{i}", ctypes.c_uint64) for i in range(21)])  # cFrameDisplayed .. cBuffersEmpty


def display_refreshes_per_s(seconds: float = 0.5) -> float | None:
    """DWM vblank count per second: ~the refresh rate while the display is on, ~1 while it sleeps
    (Desktop Duplication then delivers no frames)."""
    def count() -> int | None:
        ti = _DwmTimingInfo()
        ti.cbSize = ctypes.sizeof(ti)
        return ti.cRefresh if ctypes.windll.dwmapi.DwmGetCompositionTimingInfo(None, ctypes.byref(ti)) == 0 else None
    a = count()
    time.sleep(seconds)
    b = count()
    return None if a is None or b is None else round((b - a) / seconds, 1)


def process_tree(proc: psutil.Process) -> list[psutil.Process]:
    """The process and its children (the venv's python.exe is a launcher that runs the real interpreter)."""
    try:
        return [proc, *proc.children(recursive=True)]
    except psutil.Error:
        return [proc]


def has_vietnamese(text: str) -> bool:
    """True if `text` has a letter only Vietnamese uses (ă â đ ê ô ơ ư or a precomposed tone mark)."""
    return any(ch in _VI_ONLY or 0x1EA0 <= ord(ch) <= 0x1EF9 for ch in unicodedata.normalize("NFC", text))


def norm(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split()).casefold()


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 3)


@dataclass
class Occurrence:
    scene: str
    cycle: int
    lang: str
    start_ts: float
    final_ts: float | None = None
    end_ts: float = float("inf")  # next scene's first frame
    lines: list[str] = field(default_factory=list)
    eligible: bool = False
    skip_reason: str = ""
    covered_seq: int | None = None
    latency_s: float | None = None
    exact_latency_s: float | None = None  # every line verbatim (normalised) in the translated sources
    retranslations: int = 0  # translated text changed after the first covering state (a visible blink)
    refinements: int = 0  # ...replaced by a better tier (tier 1 -> LLM): expected in "refine" mode
    blanks: int = 0  # a translated scene block shown without a translation again (an empty frame)
    tier1_latency_s: float | None = None  # every line covered by Google (quality 1) translations
    tier2_latency_s: float | None = None  # every line covered by LLM (quality 2) translations
    llm_eligible: bool = False  # eligible, and shown after the LLM server was ready
    best_score: float = 0.0
    translations: list[dict] = field(default_factory=list)
    extra_blocks: list[str] = field(default_factory=list)  # translated blocks not matching the scene
    tiers: dict[str, dict] = field(default_factory=dict)  # block source -> {"mt", "llm", "speaker"}
    # block id -> {"source", "q": qualities shown, in order (consecutive repeats dropped)}
    tier_seq: dict[int, dict] = field(default_factory=dict)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass  # a line cut off by a kill
    return out


def occurrences(demo: list[dict]) -> list[Occurrence]:
    occ: list[Occurrence] = []
    for rec in demo:
        if "scene" not in rec:
            continue
        cur = occ[-1] if occ else None
        if cur is None or (cur.scene, cur.cycle) != (rec["scene"], rec["cycle"]):
            if cur is not None:
                cur.end_ts = rec["ts"]
            cur = Occurrence(rec["scene"], rec["cycle"], rec.get("lang", ""), rec["ts"])
            occ.append(cur)
        if rec.get("final"):
            cur.final_ts = rec["ts"]
            cur.lines = list(rec["lines"])
    return occ


def coverage(lines: list[str], blocks: list[dict], threshold: float) -> tuple[bool, float]:
    """Every line fuzzy-found in the concatenated sources of the translated blocks? (ok, worst score)."""
    done = sorted((b for b in blocks if b.get("translation")), key=lambda b: (b["box"][1], b["box"][0]))
    joined = "".join(norm(b["source"]) for b in done)
    worst = 100.0
    for line in lines:
        n = norm(line)
        if not n:
            continue
        score = fuzz.partial_ratio(n, joined) if len(joined) >= 0.9 * len(n) else 0.0
        worst = min(worst, score)
    return worst >= threshold, worst


def exact_coverage(lines: list[str], blocks: list[dict]) -> bool:
    """Every line verbatim (after normalisation) in the translated sources: the complete text was
    translated, not a typewriter prefix that the fuzzy check also accepts."""
    done = sorted((b for b in blocks if b.get("translation")), key=lambda b: (b["box"][1], b["box"][0]))
    joined = "".join(norm(b["source"]) for b in done)
    return all(norm(line) in joined for line in lines if norm(line))


def analyse(run_dir: Path, demo: list[dict], states: list[dict], run_info: dict, exit_after: float,
            threshold: float, llm_ready_ts: float | None = None) -> dict:
    occ = occurrences(demo)
    ready = next((s for s in states if s["status"] == "running" and s.get("message") != LOADING_MESSAGE), None)
    ready_ts = ready["recv_ts"] if ready else float("inf")
    exit_request_ts = run_info.get("start_ts", 0) + exit_after

    for o in occ:
        if o.final_ts is None:
            o.skip_reason = "scene never completed"
        elif o.start_ts < ready_ts:
            o.skip_reason = "shown before the app was ready"
        elif o.end_ts > exit_request_ts:
            o.skip_reason = "cut off by --exit-after"
        else:
            o.eligible = True
        o.llm_eligible = o.eligible and llm_ready_ts is not None and o.start_ts >= llm_ready_ts
        if o.final_ts is None:
            continue
        shown: dict[int, tuple[str, int]] | None = None  # block id -> (translation, quality), from coverage on
        scene_text = "".join(norm(x) for x in o.lines)
        for s in states:
            ts = s.get("recv_ts", s["ts"])
            if ts < o.final_ts - 0.02 or ts >= o.end_ts:
                continue
            in_scene = [b for b in s["blocks"] if fuzz.partial_ratio(norm(b["source"]), scene_text) >= 80]
            for b in in_scene:
                if b.get("translation"):
                    t = o.tiers.setdefault(b["source"], {})
                    q = b.get("quality", 1)
                    if q >= 2:
                        t["llm"] = b["translation"]
                        if b.get("speaker") or "speaker" not in t:  # a tier-2 cache hit has none
                            t["speaker"] = b.get("speaker")
                    else:
                        t.setdefault("mt", b["translation"])
                    seq = o.tier_seq.setdefault(b["id"], {"source": b["source"], "q": []})["q"]
                    if not seq or seq[-1] != q:
                        seq.append(q)
            if o.tier1_latency_s is None and coverage(
                    o.lines, [b for b in s["blocks"] if b.get("quality", 1) == 1], threshold)[0]:
                o.tier1_latency_s = round(ts - o.final_ts, 3)
            if o.latency_s is None:
                ok, worst = coverage(o.lines, s["blocks"], threshold)
                o.best_score = max(o.best_score, worst)
                if ok:
                    o.covered_seq, o.latency_s = s["seq"], round(ts - o.final_ts, 3)
                    o.translations = [{"source": b["source"], "translation": b["translation"],
                                       "quality": b.get("quality")} for b in s["blocks"] if b.get("translation")]
                    o.extra_blocks = [b["source"] for b in s["blocks"]  # translated text that is not in the scene
                                      if b.get("translation") and fuzz.partial_ratio(norm(b["source"]), scene_text) < 80]
            if o.latency_s is None:
                continue
            if o.exact_latency_s is None and exact_coverage(o.lines, s["blocks"]):
                o.exact_latency_s = round(ts - o.final_ts, 3)
            # Tier 2 on the page's text; a speaker label is left out (checked by "glossary names kept": the
            # 1.8B leaves an unknown name to Google on purpose).
            body = o.lines[1:] if len(o.lines) > 1 and o.lines[0] in TALK_SPEAKERS else o.lines
            if o.tier2_latency_s is None and coverage(
                    body, [b for b in s["blocks"] if b.get("quality", 1) >= 2], threshold)[0]:
                o.tier2_latency_s = round(ts - o.final_ts, 3)
            # A scene block whose translation is replaced after coverage (new wording, or a new block id for
            # the same text) is a visible blink, unless a better tier replaced it in place (a refinement).
            # A block that is still there but shows no translation any more is an empty frame (a flash).
            current = {b["id"]: (b["translation"], b.get("quality", 1)) for b in in_scene if b.get("translation")}
            if shown is None:
                shown = dict(current)
            else:
                for bid, (tr, q) in current.items():
                    prev = shown.get(bid)
                    if prev is not None and prev[0] != tr and q > prev[1]:
                        o.refinements += 1
                    elif prev is None or prev[0] != tr:
                        o.retranslations += 1
                o.blanks += sum(1 for b in in_scene if b["id"] in shown and b["id"] not in current)
                shown.update(current)

    scenes: dict[str, dict] = {}
    for o in occ:
        d = scenes.setdefault(o.scene, {"occurrences": 0, "eligible": 0, "covered": 0, "latencies_s": [],
                                        "exact_latencies_s": [], "retranslations": 0, "refinements": 0,
                                        "blanks": 0, "llm_eligible": 0, "refined": 0, "tier2_latencies_s": [],
                                        "tier1_latencies_s": [], "skipped": {}, "example": None,
                                        "worst_scores": [], "extra_blocks": []})
        d["extra_blocks"].extend(x for x in o.extra_blocks if x not in d["extra_blocks"])
        d["occurrences"] += 1
        if not o.eligible:
            d["skipped"][o.skip_reason] = d["skipped"].get(o.skip_reason, 0) + 1
            continue
        d["eligible"] += 1
        d["worst_scores"].append(round(o.best_score, 1))
        d["blanks"] += o.blanks
        if o.tier1_latency_s is not None:
            d["tier1_latencies_s"].append(o.tier1_latency_s)
        if o.llm_eligible:
            d["llm_eligible"] += 1
            if o.tier2_latency_s is not None:
                d["refined"] += 1
                d["tier2_latencies_s"].append(o.tier2_latency_s)
        if o.latency_s is not None:
            d["covered"] += 1
            d["latencies_s"].append(o.latency_s)
            d["retranslations"] += o.retranslations
            d["refinements"] += o.refinements
            if o.exact_latency_s is not None:
                d["exact_latencies_s"].append(o.exact_latency_s)
            if d["example"] is None:
                d["example"] = {"cycle": o.cycle, "lines": o.lines, "blocks": o.translations}
    for d in scenes.values():
        lat, ex, t2 = d["latencies_s"], d["exact_latencies_s"], d["tier2_latencies_s"]
        d.update(p50_s=pct(lat, 0.5), p95_s=pct(lat, 0.95), max_s=max(lat) if lat else None,
                 exact_p50_s=pct(ex, 0.5), exact_max_s=max(ex) if ex else None,
                 tier2_p50_s=pct(t2, 0.5), tier2_max_s=max(t2) if t2 else None)
    all_lat = [x for d in scenes.values() for x in d["latencies_s"]]
    all_exact = [x for d in scenes.values() for x in d["exact_latencies_s"]]
    all_t2 = [x for d in scenes.values() for x in d["tier2_latencies_s"]]
    all_t1 = [x for d in scenes.values() for x in d["tier1_latencies_s"]]

    # Self-capture: our translations / status texts must never come back through OCR.
    translations = {norm(b["translation"]) for s in states for b in s["blocks"] if b.get("translation")}
    vi_sources, echoed = set(), set()
    for s in states:
        for b in s["blocks"]:
            src = b["source"]
            if has_vietnamese(src):
                vi_sources.add(src)
            n = norm(src)
            if len(n) >= 6 and any(len(t) >= 6 and fuzz.partial_ratio(n, t) >= 90 for t in translations):
                echoed.add(src)
    return {
        "ready_after_start_s": round(ready_ts - run_info.get("start_ts", ready_ts), 3) if ready else None,
        "scenes": scenes,
        "latency_all_s": {"n": len(all_lat), "p50": pct(all_lat, 0.5), "p95": pct(all_lat, 0.95),
                          "max": max(all_lat) if all_lat else None},
        "latency_exact_s": {"n": len(all_exact), "p50": pct(all_exact, 0.5), "p95": pct(all_exact, 0.95),
                            "max": max(all_exact) if all_exact else None},
        "latency_tier2_s": {"n": len(all_t2), "p50": pct(all_t2, 0.5), "p95": pct(all_t2, 0.95),
                            "max": max(all_t2) if all_t2 else None},
        "latency_tier1_s": {"n": len(all_t1), "p50": pct(all_t1, 0.5), "p95": pct(all_t1, 0.95),
                            "max": max(all_t1) if all_t1 else None},
        "llm_ready_after_start_s": (round(llm_ready_ts - run_info["start_ts"], 3)
                                    if llm_ready_ts is not None and "start_ts" in run_info else None),
        "llm_eligible": sum(d["llm_eligible"] for d in scenes.values()),
        "refined": sum(d["refined"] for d in scenes.values()),
        "refinements": sum(d["refinements"] for d in scenes.values()),
        "blanks": sum(d["blanks"] for d in scenes.values()),
        "retranslations": sum(d["retranslations"] for d in scenes.values()),
        "occurrences": [{k: (None if v == float("inf") else v) for k, v in o.__dict__.items() if k != "translations"}
                        for o in occ],
        "self_capture": {"vietnamese_sources": sorted(vi_sources), "echoed_translations": sorted(echoed)},
    }


def composite(run_dir: Path, analysis: dict, states: list[dict], dump: Path, shots: Path, limit: int = 1) -> list[str]:
    """Overlay snapshot alpha-blended onto the scene image (original above, overlay below)."""
    by_seq = {s["seq"]: s for s in states}
    snaps = sorted((int(p.stem.split("_")[1]), p) for p in dump.glob("overlay_*.png"))
    made: list[str] = []
    per_scene: dict[str, int] = {}
    for o in analysis["occurrences"]:
        if o["covered_seq"] is None or per_scene.get(o["scene"], 0) >= limit:
            continue
        scene_img = cv2.imread(str(shots / f"{o['scene']}.png"))
        if scene_img is None:
            continue
        end_ts = o["end_ts"] if o["end_ts"] is not None else float("inf")
        cands = [(seq, p) for seq, p in snaps if seq >= o["covered_seq"] and seq in by_seq
                 and by_seq[seq].get("recv_ts", 0) < end_ts]
        if not cands:
            continue
        seq, path = cands[-1]
        ov = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if ov is None or ov.shape[:2] != scene_img.shape[:2] or ov.shape[2] != 4:
            continue
        a = ov[..., 3:4].astype(np.float32) / 255
        blended = (ov[..., :3].astype(np.float32) * a + scene_img.astype(np.float32) * (1 - a)).astype(np.uint8)
        out = run_dir / f"composite_{o['scene']}_{seq}.png"
        cv2.imwrite(str(out), np.vstack([scene_img, np.full((8, scene_img.shape[1], 3), 255, np.uint8), blended]))
        made.append(out.name)
        per_scene[o["scene"]] = per_scene.get(o["scene"], 0) + 1
    return made


# --- contextual LLM tier ------------------------------------------------------------------------

def write_e2e_profile(home: Path) -> Path:
    """The demo conversation's glossary profile in the app's data dir (returns its file)."""
    from app.profiles import Profile, ProfileStore
    from app.translate.contextual import Character, Term

    store = ProfileStore(home / "profiles")
    store.save(Profile(E2E_PROFILE, style=E2E_STYLE, terms=[Term(*t) for t in E2E_TERMS],
                       characters=[Character(*c) for c in E2E_CHARACTERS]))
    path = store.path_of(E2E_PROFILE)
    assert path is not None
    return path


def llm_ready_from_log(log_text: str) -> float | None:
    """time.time() of the first "llama-server ... ready at" line in app.log (local-time stamps)."""
    for line in log_text.splitlines():
        if "llama-server" in line and "ready at" in line:
            m = _LOG_TS.match(line)
            if m:
                return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S.%f").timestamp()
    return None


def _has_word(text: str, word: str, *, case: bool = False) -> bool:
    """`word` as a whole word in `text` (case-insensitive unless `case`)."""
    text = unicodedata.normalize("NFC", text)
    if not case:
        text, word = text.casefold(), word.casefold()
    return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text) is not None


def _match_line(tiers: dict[str, dict], line: str) -> dict:
    best = max(tiers.items(), key=lambda kv: fuzz.ratio(norm(kv[0]), norm(line)), default=None)
    return best[1] if best is not None and fuzz.ratio(norm(best[0]), norm(line)) >= 80 else {}


def address_roles(speaker: str, source: str, translation: str) -> list[str]:
    """Pronouns of a sibling's line in the wrong role (the speaker called by the listener's word or
    the reverse), as messages; empty when none is found or the speaker is not a sibling."""
    if speaker not in ADDRESS_ROLES or not source:
        return []
    own, other = ADDRESS_ROLES[speaker]
    return [i.message for i in check_line(source, translation, own, other, "ja")]


def address_of(speaker: str, translation: str, source: str = "") -> str:
    """"ok" (an expected sibling pronoun, no wrong one, none in the wrong role), "wrong" (bạn / cậu /
    tôi / tớ, or - given the source line - a pronoun in the wrong role) or "none"."""
    if speaker not in ADDRESS_WORDS:
        return "n/a"
    if any(_has_word(translation, w) for w in WRONG_ADDRESS) or address_roles(speaker, source, translation):
        return "wrong"
    return "ok" if any(_has_word(translation, w) for w in ADDRESS_WORDS[speaker]) else "none"


def llm_report(occurrences_: list[dict]) -> dict:
    """Per demo-conversation page (first occurrence with an LLM translation): Google's and the LLM's
    translation; the speaker label exactly as in the glossary (an unknown speaker: rendered in Latin
    letters); names (exact case) and glossary terms inside the line kept; the speaker the LLM
    attributed; the form of address used (informational: model quality)."""
    names = {c[0]: c[1] for c in E2E_CHARACTERS}
    speakers = {s for s, _ in JA_TALK}
    rows: list[dict] = []
    seen: set[str] = set()
    for o in occurrences_:
        if o["scene"] in seen or len(o["lines"]) != 2 or o["lines"][0] not in speakers:
            continue
        speaker, line = o["lines"]
        tiers = o.get("tiers") or {}
        label = next((t for src, t in tiers.items() if norm(src) == norm(speaker)), {})
        body = _match_line(tiers, line)
        if not body.get("llm"):
            continue
        seen.add(o["scene"])
        llm = body["llm"]
        label_llm = (label.get("llm") or "").strip()
        expected = names.get(speaker)
        mentioned = [names[n] for n in names if n in line]
        terms = [tgt for src, tgt, _ in E2E_TERMS if src in line]
        rows.append({
            "scene": o["scene"], "speaker": speaker, "source": line, "google": body.get("mt"), "llm": llm,
            "speaker_attributed": body.get("speaker"), "label_llm": label_llm or None,
            "label_ok": bool(label_llm) and (label_llm == expected if expected else not _JA.search(label_llm)),
            "names_in_line_ok": all(_has_word(llm, n, case=True) for n in mentioned),
            "terms_ok": all(t.casefold() in unicodedata.normalize("NFC", llm).casefold() for t in terms),
            "terms": terms, "address": address_of(speaker, llm, line), "roles": address_roles(speaker, line, llm),
        })
    known = [r for r in rows if r["address"] != "n/a"]
    return {
        "pages": rows,
        "labels_ok": sum(r["label_ok"] for r in rows), "labels": sum(1 for r in rows if r["label_llm"]),
        "names_in_lines_ok": sum(r["names_in_line_ok"] for r in rows),
        "terms_ok": sum(r["terms_ok"] for r in rows if r["terms"]), "terms_n": sum(1 for r in rows if r["terms"]),
        "address_ok": sum(r["address"] == "ok" for r in known),
        "address_wrong": sum(r["address"] == "wrong" for r in known), "address_n": len(known),
        "address_role_wrong": sum(bool(r["roles"]) for r in known),
        "n": len(rows),
    }


def tier_order(occurrences_: list[dict], mode: str) -> dict:
    """The qualities each dialogue block of a page shown after the LLM was ready went through.
    refine: Google (1) first where it arrived first, then the LLM (2) in place; never back to 1.
    only: no Google text at all while the LLM is up. Speaker labels are left out (the glossary)."""
    blocks = refined = llm_first = downgraded = not_reached = google_shown = 0
    examples: list[dict] = []
    for o in occurrences_:
        if not o.get("llm_eligible") or not o.get("lines"):
            continue
        body = [norm(x) for x in o["lines"][1:]]
        for bid, info in (o.get("tier_seq") or {}).items():
            src = norm(info["source"])
            if not src or not any(fuzz.partial_ratio(src, line) >= 80 for line in body):
                continue
            q = info["q"]
            blocks += 1
            if any(b < a for a, b in zip(q, q[1:])):
                downgraded += 1
            if not q or q[-1] < 2:
                not_reached += 1
            if q[:2] == [1, 2]:
                refined += 1
            elif q[:1] == [2]:
                llm_first += 1
            google_shown += 1 in q
            if len(examples) < 12:
                examples.append({"scene": o["scene"], "cycle": o["cycle"], "block": bid, "q": q})
    ok = blocks > 0 and downgraded == 0 and not_reached == 0 and (mode != "only" or google_shown == 0)
    return {"ok": ok, "blocks": blocks, "google_then_llm": refined, "llm_first": llm_first,
            "downgraded": downgraded, "not_reached_tier2": not_reached, "google_shown": google_shown,
            "examples": examples}


def llm_records_report(records: list[dict]) -> dict:
    """The app's llm.jsonl (one line per LLM screen result): speaker of every dialogue line the LLM
    translated against JA_TALK, the dialogue memory growing, new terms, time per screen."""
    expected = {norm(line): speaker for speaker, line in JA_TALK}
    attributed = wrong = 0
    wrong_examples: list[dict] = []
    distinct: set[str] = set()  # dialogue lines the LLM translated: each one goes into the memory
    memory = [r["memory_lines"] for r in records if r.get("memory_lines") is not None]
    for r in records:
        for it in r.get("items", []):
            text = r.get("translations", {}).get(str(it["id"]))
            if not text or it.get("role") == "name":
                continue
            distinct.add(norm(it["text"]))
            want = next((sp for line, sp in expected.items() if fuzz.ratio(norm(it["text"]), line) >= 85), None)
            if want is None:
                continue
            got = r.get("speakers", {}).get(str(it["id"]))
            attributed += 1
            if got != want:
                wrong += 1
                if len(wrong_examples) < 5:
                    wrong_examples.append({"text": it["text"], "expected": want, "got": got})
    lat = [r["latency_s"] for r in records if r.get("latency_s")]  # 0: dialogue served from the tier-2 cache
    monotonic = all(b >= a for a, b in zip(memory, memory[1:]))
    return {
        "records": len(records), "dialogue_lines": attributed, "speaker_wrong": wrong,
        "speaker_wrong_examples": wrong_examples, "distinct_dialogue_lines": len(distinct),
        "memory_lines": memory, "memory_monotonic": monotonic,
        "memory_grows": bool(memory) and monotonic and memory[-1] >= min(len(distinct), 50) > 0,
        "new_terms": [t for r in records for t in r.get("new_terms", [])],
        "auto_terms_added": sum(r.get("auto_terms_added") or 0 for r in records),
        "latency_s": {"n": len(lat), "p50": pct(lat, 0.5), "p95": pct(lat, 0.95), "max": max(lat) if lat else None},
    }


def tier2_by_source(occurrences_: list[dict], records: list[dict]) -> dict:
    """Latency to tier 2 per page, split into pages the LLM translated then (an llm.jsonl record
    while the page was shown) and pages served from the tier-2 cache (seen before)."""
    fresh: list[float] = []
    cached: list[float] = []
    for o in occurrences_:
        if not o.get("llm_eligible") or o.get("tier2_latency_s") is None:
            continue
        start, end = o.get("final_ts") or o["start_ts"], o.get("end_ts") or float("inf")
        body = [norm(x) for x in o["lines"][1:]]
        called = any(start - 0.05 <= r["ts"] < end and r.get("latency_s") and any(  # latency 0: from the cache
            fuzz.ratio(norm(it["text"]), line) >= 85 for it in r.get("items", []) if str(it["id"]) in
            r.get("translations", {}) for line in body) for r in records)
        (fresh if called else cached).append(o["tier2_latency_s"])

    def stats(v: list[float]) -> dict:
        return {"n": len(v), "p50": pct(v, 0.5), "p95": pct(v, 0.95), "max": max(v) if v else None}

    return {"llm_call": stats(fresh), "cache": stats(cached)}


def profile_auto_terms(home: Path) -> list[dict]:
    """Auto terms (proposed by the LLM) in the run's E2E profile file, read back after the app exited."""
    from app.profiles import ProfileStore

    profile = ProfileStore(home / "profiles").get(E2E_PROFILE)
    return [{"source": t.source, "target": t.target, "kind": t.kind} for t in profile.terms if t.auto]


def gpu_mem_used_mb() -> int | None:
    """Used VRAM of GPU 0 (nvidia-smi), None if unavailable."""
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True,
                             text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return int(out.stdout.split()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def listen_addresses(p: psutil.Process) -> set[str]:
    """IP addresses `p` listens on (TCP)."""
    get = getattr(p, "net_connections", None) or p.connections
    try:
        return {c.laddr.ip for c in get(kind="inet") if c.status == psutil.CONN_LISTEN}
    except psutil.Error:
        return set()


def wait_for_rect(log: Path, proc: subprocess.Popen, timeout: float) -> list[int]:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise RuntimeError(f"demo window exited early (code {proc.returncode})")
        for rec in read_jsonl(log):
            if "rect" in rec:
                return rec["rect"]
        time.sleep(0.1)
    raise RuntimeError("demo window did not report its rect")


def kill(proc: subprocess.Popen | None) -> None:
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


def print_table(result: dict) -> None:
    a = result["analysis"]
    print(f"\nE2E {result['run']}  scenes={result['scene_set']}  app lang={result['app_lang']}")
    print(f"{'scene':<15}{'shown':>6}{'eligible':>9}{'covered':>8}{'p50 s':>8}{'p95 s':>8}{'max s':>8}"
          f"{'exact p50':>10}{'exact max':>10}{'re-tr':>6}  extra blocks")

    def f(v):
        return f"{v:.2f}" if v is not None else "-"

    for name, d in a["scenes"].items():
        print(f"{name:<15}{d['occurrences']:>6}{d['eligible']:>9}{d['covered']:>8}{f(d['p50_s']):>8}"
              f"{f(d['p95_s']):>8}{f(d['max_s']):>8}{f(d.get('exact_p50_s')):>10}{f(d.get('exact_max_s')):>10}"
              f"{d.get('retranslations', 0):>6}  {d['extra_blocks'] or '-'}")
    la, le = a["latency_all_s"], a.get("latency_exact_s", {})
    print(f"all: n={la['n']} p50={la['p50']} p95={la['p95']} max={la['max']} s; exact text: n={le.get('n')} "
          f"p50={le.get('p50')} max={le.get('max')} s; re-translations after coverage {a.get('retranslations')}; "
          f"app ready after {a['ready_after_start_s']} s")
    llm = result.get("llm")
    if llm:
        t1, t2 = a.get("latency_tier1_s", {}), a.get("latency_tier2_s", {})
        print(f"LLM ({llm['mode']}{', NO MODEL installed' if llm.get('no_model') else ''}, model "
              f"{llm['model'] or '-'}): server ready {a.get('llm_ready_after_start_s')} s after start; tier 2 on "
              f"{a.get('refined')}/{a.get('llm_eligible')} pages shown after that; latency to Google-only coverage "
              f"n={t1.get('n')} p50={t1.get('p50')} p95={t1.get('p95')} s, to tier 2 n={t2.get('n')} "
              f"p50={t2.get('p50')} p95={t2.get('p95')} max={t2.get('max')} s; {a.get('refinements')} refinements "
              f"in place, {a.get('blanks')} blank frames")
        split = llm.get("tier2_latency_s") or {}
        if split:
            fresh, cached = split["llm_call"], split["cache"]
            print(f"  latency to tier 2: pages the LLM translated then n={fresh['n']} p50={fresh['p50']} "
                  f"p95={fresh['p95']} max={fresh['max']} s; pages from the tier-2 cache n={cached['n']} "
                  f"p50={cached['p50']} p95={cached['p95']} s")
        to = llm.get("tier_order") or {}
        if to:
            print(f"  tier order: {to['blocks']} dialogue blocks: {to['google_then_llm']} Google then LLM in place, "
                  f"{to['llm_first']} LLM first (faster than Google, or the tier-2 cache), {to['downgraded']} "
                  f"back to Google, {to['not_reached_tier2']} never reached tier 2, {to['google_shown']} showed Google")
        rr = llm.get("records") or {}
        if rr:
            lat, job = rr.get("latency_s", {}), llm.get("job_ms", {})
            print(f"  LLM screens: {rr['records']} results, time per screen (submit -> applied) p50={lat.get('p50')} "
                  f"p95={lat.get('p95')} max={lat.get('max')} s; translate_screen p50={job.get('p50')} "
                  f"p95={job.get('p95')} max={job.get('max')} ms; speakers wrong "
                  f"{rr['speaker_wrong']}/{rr['dialogue_lines']}; memory {rr['memory_lines']}; new terms "
                  f"{rr['new_terms'] or '-'}")
        print(f"  auto terms in the profile file: {llm.get('auto_terms') or 'none'}")
        print(f"  VRAM MB {llm['vram_mb']}; llama-server CPU mean {llm.get('llama_cpu_mean_pct')}% peak "
              f"{llm.get('llama_cpu_peak_pct')}%; listens on {llm['listen'] or '-'}; left after exit: "
              f"{llm['orphans'] or 'none'}")
        rep = llm.get("report") or {}
        if rep.get("n"):
            print(f"  names: labels {rep['labels_ok']}/{rep['labels']}, in lines {rep['names_in_lines_ok']}/{rep['n']}, "
                  f"terms {rep['terms_ok']}/{rep['terms_n']}; forms of address between the siblings: "
                  f"{rep['address_ok']} ok, {rep['address_wrong']} wrong ({rep.get('address_role_wrong', 0)} with a "
                  f"pronoun in the wrong role) of {rep['address_n']} (informational)")
            for r in rep["pages"]:
                print(f"  {r['speaker']}: {r['source']}\n      Google: {r['google']}\n      LLM:    {r['llm']}"
                      f"  [label {r['label_llm']!r}, speaker {r['speaker_attributed']!r}, address {r['address']}]"
                      + "".join(f"\n      role: {m}" for m in r.get("roles", [])))
    p = result["process"]
    print(f"app: exit code {p['exit_code']}, exit {p['exit_overrun_s']} s after --exit-after, CPU (app + children, "
          f"% of one core) peak {p['cpu_peak_pct']}% incl. start-up, peak after start-up "
          f"{p['cpu_peak_after_startup_pct']}%, mean {p['cpu_mean_pct']}%; peak RSS {p['rss_peak_mb']} MB, "
          f"states {result['states']}, snapshots {result['snapshots']}")
    sc = a["self_capture"]
    lg = result["log"]
    print(f"self-capture: {len(sc['vietnamese_sources'])} Vietnamese sources, {len(sc['echoed_translations'])} "
          f"echoed translations, {lg['exclusion_problems']} exclusion problems in the log")
    backend = result.get("capture_backend", "auto")
    used = ("GDI fallback (dxcam delivered nothing)" if lg["gdi_fallback_used"] else "dxcam") \
        if backend == "auto" else backend
    print(f"capture: display {lg['display_refreshes_per_s_at_start']} refreshes/s at start, {used}; "
          f"OCR p50 {result['ocr_ms']['p50']} ms over {result['ocr_ms']['n']} passes; translate batch p50 "
          f"{result['translate_batch_ms']['p50']} ms, max {result['translate_batch_ms']['max']} ms "
          f"({result['translate_batch_ms']['n']} batches)")
    for c in result["checks"]:
        print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}")
    print("RESULT:", "PASS" if result["passed"] else "FAIL")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="End-to-end check of the screen translator.")
    ap.add_argument("--scenes", choices=("ja", "en", "mixed", "llm"),
                    help="demo scene set (default: llm with --llm, else mixed)")
    ap.add_argument("--lang", choices=("ja", "en"), help="app source language (default: en for --scenes en, else ja)")
    ap.add_argument("--exit-after", type=float, help="seconds (default 48; 60 with the LLM tier, 80 with --llm)")
    ap.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    ap.add_argument("--interval", type=float, help="seconds per demo scene (default 4; 6 with the LLM tier)")
    ap.add_argument("--threshold", type=float, default=85.0, help="fuzzy score for a covered line")
    ap.add_argument("--min-coverage", type=float, default=0.8, help="share of eligible occurrences covered")
    ap.add_argument("--max-exit-overrun", type=float, default=6.0)
    ap.add_argument("--guard", action="store_true", help="keep the anti-cheat guard on (default --no-guard)")
    ap.add_argument("--capture", choices=("auto", "dxcam", "mss", "wgc"), help="app capture backend (default: app's)")
    ap.add_argument("--llm", action="store_true",
                    help="the contextual LLM checks: scene set llm, LLM mode refine, 80 s (each can be overridden)")
    ap.add_argument("--llm-mode", choices=("off", "refine", "only"),
                    help="contextual LLM tier (default: refine with --llm or --scenes llm, else off)")
    ap.add_argument("--llm-model", metavar="NAME", help="GGUF file name in the models dir (default: the app's)")
    ap.add_argument("--no-model", action="store_true",
                    help="run the app with an empty LLM home (no model installed): Google must take over")
    ap.add_argument("--home", type=Path, help="app data dir (default: a fresh one in the run dir)")
    ap.add_argument("--run", help="run name (default: timestamp)")
    args = ap.parse_args(argv)
    scene_set = args.scenes or ("llm" if args.llm else "mixed")
    app_lang = args.lang or ("en" if scene_set == "en" else "ja")
    llm_mode = args.llm_mode or ("refine" if args.llm or scene_set == "llm" or args.no_model else "off")
    use_llm = llm_mode != "off"
    if args.no_model and not use_llm:
        ap.error("--no-model needs an LLM mode (refine or only)")
    default_exit = 80.0 if args.llm else 60.0 if use_llm else 48.0
    exit_after = args.exit_after if args.exit_after is not None else default_exit
    interval = args.interval if args.interval is not None else (6.0 if use_llm else 4.0)

    variant = f"{scene_set}-{llm_mode}{'-nomodel' if args.no_model else ''}" if use_llm else scene_set
    run = args.run or f"{datetime.now():%Y%m%d-%H%M%S}-{variant}"
    run_dir = RESULTS / "e2e" / run
    run_dir.mkdir(parents=True, exist_ok=True)
    demo_log, shots, dump = run_dir / "demo.jsonl", run_dir / "scenes", run_dir / "dump"
    home = (args.home or run_dir / "home").resolve()
    env = dict(os.environ, SCREEN_TRANSLATOR_HOME=str(home), PYTHONIOENCODING="utf-8")
    if args.no_model:
        empty = run_dir / "empty-llm-home"
        empty.mkdir(exist_ok=True)
        env["SCREEN_TRANSLATOR_LLM_HOME"] = str(empty.resolve())
    py = sys.executable
    if use_llm:
        print(f"glossary profile {E2E_PROFILE!r}: {write_e2e_profile(home)}", flush=True)

    demo = app = None
    samples: list[tuple[float, float, float]] = []
    llama_cpu: list[float] = []  # llama-server CPU% per sample (included in `samples` too)
    llama: dict[int, psutil.Process] = {}  # llama-server processes seen in the app's tree
    listen: set[str] = set()
    vram = {"before": gpu_mem_used_mb() if use_llm else None, "peak": None, "after": None}
    try:
        demo = subprocess.Popen(
            [py, "-m", "tools.demo_text_window", "--log", str(demo_log), "--scenes", scene_set,
             "--duration", str(exit_after + 20), "--interval", str(interval), "--shots", str(shots)],
            cwd=PROJECT_ROOT, env=env, stdout=subprocess.DEVNULL, stderr=open(run_dir / "demo_stderr.txt", "w"))
        rect = wait_for_rect(demo_log, demo, 20)
        refresh_hz = display_refreshes_per_s()
        capture = args.capture
        if capture is None and refresh_hz is not None and refresh_hz < ASLEEP_REFRESHES_PER_S:
            # Desktop Duplication hands out no new frames while the monitor sleeps; once the app has a
            # first frame, dxcam serves that cached frame forever and the demo's scene changes are never
            # seen (observed: a run stuck on one page from 18 s on). GDI still reads the desktop.
            capture = "mss"
            print(f"display asleep ({refresh_hz} refreshes/s): capturing with GDI (--capture mss) for this run",
                  flush=True)
        print(f"demo window rect {rect}; starting the app for {exit_after:.0f} s (LLM {llm_mode}) ...", flush=True)
        cmd = [py, "-m", "app", f"--region={','.join(map(str, rect))}", "--lang", app_lang, "--device", args.device,
               "--no-hotkeys", "--exit-after", str(exit_after), "--debug-dump", str(dump), "--log-level", "INFO",
               "--llm-mode", llm_mode]
        if use_llm:
            cmd += ["--profile", E2E_PROFILE]
            if args.llm_model:
                cmd += ["--llm-model", args.llm_model]
        if not args.guard:
            cmd.append("--no-guard")
        if capture:
            cmd += ["--capture", capture]
        t_start = time.time()
        app = subprocess.Popen(cmd, cwd=PROJECT_ROOT, env=env, stdout=open(run_dir / "app_stdout.txt", "w"),
                               stderr=open(run_dir / "app_stderr.txt", "w", encoding="utf-8", errors="replace"))
        root_ps = psutil.Process(app.pid)
        watched: dict[int, psutil.Process] = {}
        deadline = time.monotonic() + exit_after + 40
        tick = 0
        while app.poll() is None and time.monotonic() < deadline:
            for p in process_tree(root_ps):
                if p.pid not in watched:
                    watched[p.pid] = p
                    try:
                        p.cpu_percent(None)  # first call primes the counter
                        if "llama-server" in p.name().lower():
                            llama[p.pid] = p
                    except psutil.Error:
                        pass
            time.sleep(0.5)
            cpu = rss = cpu_llama = 0.0
            for p in list(watched.values()):
                try:
                    c = p.cpu_percent(None)
                    cpu += c
                    if p.pid in llama:
                        cpu_llama += c
                    rss += p.memory_info().rss / 2**20
                except psutil.Error:
                    watched.pop(p.pid, None)
            samples.append((time.time(), cpu, rss))
            llama_cpu.append(cpu_llama)
            tick += 1
            for p in llama.values():
                if p.is_running():
                    listen |= listen_addresses(p)
            if use_llm and tick % 4 == 0:
                used = gpu_mem_used_mb()
                if used is not None:
                    vram["peak"] = max(vram["peak"] or 0, used)
        if app.poll() is None:
            print("app did not exit in time; killing it", flush=True)
            kill(app)
        t_end = time.time()
        exit_code = app.wait()
    finally:
        kill(app)
        kill(demo)
    time.sleep(0.5)
    orphans = []
    for p in llama.values():  # psutil.Process.is_running() also checks the create time (no pid reuse)
        if p.is_running():
            orphans.append(p.pid)
            try:
                p.kill()  # we started it (through the app): never leave it holding VRAM
            except psutil.Error:
                pass
    if use_llm:
        vram["after"] = gpu_mem_used_mb()

    demo_recs = read_jsonl(demo_log)
    states = read_jsonl(dump / "states.jsonl")
    run_info = json.loads((dump / "run.json").read_text(encoding="utf-8")) if (dump / "run.json").exists() else {}
    log_path = home / "logs" / "app.log"
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    llm_ready_ts = llm_ready_from_log(log_text) if use_llm else None
    analysis = analyse(run_dir, demo_recs, states, run_info, exit_after, args.threshold, llm_ready_ts)
    composites = composite(run_dir, analysis, states, dump, shots)
    log_info = {
        "path": str(log_path),
        "exclusion_problems": len(_EXCLUSION_PROBLEMS.findall(log_text)),
        "warnings": len(re.findall(r" WARNING ", log_text)),
        "errors": len(re.findall(r" (ERROR|CRITICAL) ", log_text)),
        "ocr_passes": len(re.findall(r"ocr pass \d+", log_text)),
        "translate_batches": len(re.findall(r"translated \d+ texts", log_text)),
        "shutdown": (re.findall(r"shutdown complete in \d+ ms", log_text) or [None])[-1],
        "gdi_fallback_used": "capturing with GDI" in log_text,
        "display_refreshes_per_s_at_start": refresh_hz,
    }
    ocr_ms = [float(x) for x in re.findall(r"ocr (\d+\.\d) ms", log_text)]
    tr_ms = [float(x) for x in re.findall(r"translated \d+ texts in (\d+) ms", log_text)]
    llm_ms = [float(ms) for needed, ms, cached in  # jobs that called the LLM (not answered by the cache alone)
              re.findall(r"refined \d+ of (\d+) texts in (\d+) ms \(\d+ items on screen, (\d+) from", log_text)
              if int(cached) < int(needed)]
    run_start = run_info.get("start_ts", t_start)
    live = [s for s in samples if s[0] >= run_start + 3]  # skip start-up (imports, CUDA init)
    proc = {
        "exit_code": exit_code,
        "wall_s": round(t_end - t_start, 2),
        "startup_s": round(run_start - t_start, 2) if run_info else None,
        "exit_overrun_s": round(t_end - (run_start + exit_after), 2) if run_info else None,
        "cpu_peak_pct": round(max((s[1] for s in samples), default=0), 1),
        "cpu_mean_pct": round(statistics.fmean([s[1] for s in live]), 1) if live else None,
        "cpu_peak_after_startup_pct": round(max((s[1] for s in live), default=0), 1),
        "rss_peak_mb": round(max((s[2] for s in samples), default=0), 1),
        "logical_cpus": psutil.cpu_count(),
    }

    checks = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    scenes = analysis["scenes"]
    eligible = sum(d["eligible"] for d in scenes.values())
    covered = sum(d["covered"] for d in scenes.values())
    missing = [n for n, d in scenes.items() if d["covered"] == 0]
    check("every scene translated", bool(scenes) and not missing and all(d["eligible"] for d in scenes.values()),
          f"not covered: {missing or 'none'}")
    check("coverage", eligible > 0 and covered / eligible >= args.min_coverage,
          f"{covered}/{eligible} eligible occurrences (min {args.min_coverage:.0%})")
    sc = analysis["self_capture"]
    check("no self-capture", not sc["vietnamese_sources"] and not sc["echoed_translations"]
          and log_info["exclusion_problems"] == 0,
          f"{len(sc['vietnamese_sources'])} VI sources, {len(sc['echoed_translations'])} echoes, "
          f"{log_info['exclusion_problems']} exclusion problems")
    check("clean exit", exit_code == 0 and proc["exit_overrun_s"] is not None
          and proc["exit_overrun_s"] <= args.max_exit_overrun,
          f"code {exit_code}, {proc['exit_overrun_s']} s after --exit-after (max {args.max_exit_overrun})")
    check("states published", len(states) > 0, f"{len(states)} states")

    llm = None
    if use_llm:
        conversation = scene_set == "llm"
        report = llm_report(analysis["occurrences"]) if conversation and not args.no_model else {}
        raw_records = read_jsonl(dump / "llm.jsonl")
        records = llm_records_report(raw_records)
        order = tier_order(analysis["occurrences"], llm_mode)
        auto_terms = profile_auto_terms(home)
        model = (re.findall(r"llama-server ready at \S+ \(([^)]*)\)", log_text) or [None])[0]
        llm = {"mode": llm_mode, "no_model": args.no_model, "model": model, "ready_ts": llm_ready_ts,
               "vram_mb": vram, "listen": sorted(listen), "llama_pids": sorted(llama), "orphans": orphans,
               "llama_cpu_mean_pct": round(statistics.fmean(llama_cpu), 1) if llama and llama_cpu else None,
               "llama_cpu_peak_pct": round(max(llama_cpu), 1) if llama and llama_cpu else None,
               "job_ms": {"n": len(llm_ms), "p50": pct(llm_ms, 0.5), "p95": pct(llm_ms, 0.95),
                          "max": max(llm_ms) if llm_ms else None},
               "report": report, "records": records, "tier_order": order, "auto_terms": auto_terms,
               "tier2_latency_s": tier2_by_source(analysis["occurrences"], raw_records)}
        check("no llama-server after exit", not orphans, f"left running (killed now): {orphans or 'none'}")
        if vram["before"] is not None and vram["after"] is not None:
            check("VRAM released", vram["after"] - vram["before"] <= VRAM_TOLERANCE_MB,
                  f"{vram['before']} MB before, peak {vram['peak']} MB, {vram['after']} MB after "
                  f"(max +{VRAM_TOLERANCE_MB})")
        if args.no_model:
            # Speaker labels that are glossary names show the glossary's rendering (tier 2) in every mode.
            glossary = {norm(c[0]) for c in E2E_CHARACTERS} | {norm(t[0]) for t in E2E_TERMS}
            q2 = {b["source"] for s in states for b in s["blocks"] if b.get("quality", 1) >= 2}
            check("missing model reported", MSG_NO_MODEL in log_text and "no LLM model found" in log_text,
                  f"status {MSG_NO_MODEL!r} {'logged' if MSG_NO_MODEL in log_text else 'NOT logged'}")
            check("no llama-server started", not llama, f"{len(llama)} llama-server processes seen")
            other = sorted(x for x in q2 if norm(x) not in glossary)
            check("Google fallback only", not other and covered > 0,
                  f"tier 2 only on glossary names {sorted(q2 - set(other)) or '-'}; other tier-2 texts "
                  f"{other or 'none'}; {covered} pages covered")
        else:
            n_llm, refined = analysis["llm_eligible"], analysis["refined"]
            check("LLM server ready", llm_ready_ts is not None,
                  f"ready {analysis['llm_ready_after_start_s']} s after start")
            check("LLM translations shown", n_llm > 0 and refined / n_llm >= args.min_coverage,
                  f"{refined}/{n_llm} pages shown after the server was ready reached tier 2 "
                  f"(min {args.min_coverage:.0%})")
            check("tier order" + (" (only: no Google while the LLM is up)" if llm_mode == "only" else
                                  " (Google first, LLM in place, never back)"), order["ok"],
                  f"{order['blocks']} dialogue blocks: {order['google_then_llm']} Google then LLM, "
                  f"{order['llm_first']} LLM first, {order['downgraded']} back to Google, "
                  f"{order['not_reached_tier2']} without tier 2, {order['google_shown']} showed Google")
            check("no blank frame on refinement", analysis["blanks"] == 0,
                  f"{analysis['blanks']} blank frames, {analysis['refinements']} refinements in place")
            check("llama-server on 127.0.0.1 only", bool(listen) and listen <= {"127.0.0.1"},
                  f"listening on {sorted(listen) or 'nothing seen'} ({len(llama)} llama-server processes)")
            if report.get("n"):
                check("glossary names kept", report["labels_ok"] == report["labels"] > 0
                      and report["names_in_lines_ok"] == report["n"] and report["terms_ok"] == report["terms_n"],
                      f"speaker labels exact {report['labels_ok']}/{report['labels']}, names in lines "
                      f"{report['names_in_lines_ok']}/{report['n']}, terms {report['terms_ok']}/{report['terms_n']}")
            if conversation:
                check("speaker attribution", records["dialogue_lines"] > 0 and records["speaker_wrong"] == 0,
                      f"{records['dialogue_lines'] - records['speaker_wrong']}/{records['dialogue_lines']} dialogue "
                      f"lines the LLM translated have the right speaker"
                      + (f"; wrong: {records['speaker_wrong_examples']}" if records["speaker_wrong"] else ""))
                check("dialogue memory grows", records["memory_grows"],
                      f"memory sizes {records['memory_lines'] or 'none'} for "
                      f"{records['distinct_dialogue_lines']} distinct dialogue lines")
                if UNKNOWN_SPEAKERS and PromptOptions.for_model(model or "").label_terms:
                    stored = {norm(t["source"]) for t in auto_terms}
                    missing_terms = [s for s in UNKNOWN_SPEAKERS if norm(s) not in stored]
                    check("auto terms stored in the profile", not missing_terms,
                          f"auto terms {[(t['source'], t['target']) for t in auto_terms] or 'none'}; "
                          f"missing {missing_terms or 'none'}")
                elif UNKNOWN_SPEAKERS:  # the 1.8B proposes no names (it gave "Kendz" for ケンジ)
                    check("no auto terms from a model that may not propose them", not auto_terms,
                          f"auto terms {[(t['source'], t['target']) for t in auto_terms] or 'none'}")

    result = {
        "run": run, "date": datetime.now().isoformat(timespec="seconds"), "scene_set": scene_set,
        "llm_mode": llm_mode, "capture_backend": capture or "auto",
        "app_lang": app_lang, "device": args.device, "exit_after_s": exit_after, "interval_s": interval,
        "region": rect, "states": len(states), "snapshots": len(list(dump.glob("overlay_*.png"))),
        "composites": composites, "process": proc, "log": log_info, "llm": llm,
        "ocr_ms": {"n": len(ocr_ms), "p50": pct(ocr_ms, 0.5), "p95": pct(ocr_ms, 0.95)},
        "translate_batch_ms": {"n": len(tr_ms), "p50": pct(tr_ms, 0.5), "p95": pct(tr_ms, 0.95),
                               "max": max(tr_ms) if tr_ms else None},
        "analysis": analysis, "checks": checks, "passed": all(c["ok"] for c in checks),
        "run_dir": str(run_dir.relative_to(PROJECT_ROOT)),
    }
    (run_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path = RESULTS / "e2e_mvp.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        summary = {}
    key = variant + (f"@{Path(args.llm_model).stem}" if use_llm and args.llm_model else "")
    summary.setdefault("runs", {})[key] = result
    summary["updated"] = result["date"]
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print_table(result)
    print(f"details: {run_dir}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
