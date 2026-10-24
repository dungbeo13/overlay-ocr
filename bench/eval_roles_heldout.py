"""Held-out measurement of pronoun_roles.check_line (source only) and llm_prompt.repair_roles.

The files in bench/data/translate/heldout/ are written blind to the checker's code. Do NOT tune
rules on them: once a rule is changed while looking at these lines they stop being held out; move
them to roles_dev/ and write a fresh set instead. bench/data/translate/roles_dev/ holds the earlier
held-out sets (A, B, C1, C2, D1, D2: their misses were read, rules tuned on A, B and D) and the
reviewers' labelled sets (roles_labelled.json, roles_labelled_r2.json, roles_labelled_r3.json, and the
round-6 review's benefactive set roles_labelled_r4.json, tuned on in round 7) and the benefactive set
written with the round-6 rules (roles_benefactive_dev.json): development data, `--dev`.

Each line is also put in a group by its source alone (a rough cue list, independent of the checker,
for the per-group numbers): "ask" (a question about the listener: 大丈夫？, are you hurt), "greet"
(ただいま, 行ってきます, おかえり, I'm home), "pron" (an explicit pronoun or a self-referring role word:
私は, お前を, 先生が, I / you), else "other". Across the groups, "favour" counts the lines with a
benefactive (〜てくれた / 〜てもらう / 〜てあげる, "for me / for you", "thanks for …ing").

A line counts as caught when check_line reports anything; "acted" counts only what the app acts on
(a repair, or an issue it asks the model again for: llm_prompt.reask_issues), since "sẽ" alone is
only reported.

A line may carry a role-tagged reference "ref" (the right translation, every pronoun use tagged:
"{S:Em} xin lỗi {L:anh} nhé", see pronoun_roles.parse_ref), as the held-out sets from D on do; the
dev sets mostly do not. With refs the "judge" numbers say how well check_line(..., expected=ref)
tells the labelled errors from the right lines (what bench/eval_roles_reask.py and eval_roles_e2e.py
judge fresh outputs by), and each ref is itself checked: tags against pronoun_roles.analyze
(ref_disagreements) and no issue for the ref's own text.

    python -m bench.eval_roles_heldout [--dev] [--show] [file.json ...]

Results: bench/results/eval_roles_heldout.json (the held-out folder), eval_roles_dev.json (--dev),
eval_roles_files.json (files given on the command line).
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path

from app.translate.llm_prompt import reask_issues, repair_roles, role_repairs
from app.translate.pronoun_roles import (
    LISTENER,
    LISTENER_AS_SELF,
    SELF,
    SELF_AS_LISTENER,
    RoleIssue,
    analyze,
    check_line,
    judged_slots,
    parse_ref,
    ref_disagreements,
)
from bench.common import DATA_DIR, save_results

_FIRST = frozenset({"tôi", "tao", "tớ"})  # first / second person whatever the pair (pronoun_roles)
_SECOND = frozenset({"mày", "bạn", "ngươi"})

HELDOUT_DIR = DATA_DIR / "translate" / "heldout"
DEV_DIR = DATA_DIR / "translate" / "roles_dev"
GROUPS = ("ask", "greet", "pron", "other")

_GROUP_CUES = (
    ("ask", re.compile(r"大丈夫[？?]|大丈夫(?:です|でした)?か|怪我|けが|ケガ|元気[？?]|元気(?:です)?か|疲れてない|寒くない|暑くない"
                       r"|お腹(?:すい|空い)|どうしたの|どうした[？?]|何してる|痛くない|眠くない|平気[？?]"
                       r"|\b(?:are|r) (?:you|u) (?:ok|okay|alright|all right|hurt|cold|hungry|tired|sure)\b"
                       r"|\bdid you (?:eat|get hurt|sleep)\b|\bwhat are you doing\b|\bwhat's wrong\b|\bare you\b",
                       re.IGNORECASE)),
    ("greet", re.compile(r"ただいま|行ってきます|行ってくる|いってきます|いってくる|いただきます|ごちそうさま|ご馳走様"
                         r"|お先に失礼|おやすみ|おかえり|お帰り"
                         r"|\bI'?m home\b|\bI'?m back\b|\bI'?m off\b|\bwelcome (?:home|back)\b|\bgood ?night\b"
                         r"|\bI'?m heading (?:out|off)\b|\bthanks for the (?:meal|food)\b", re.IGNORECASE)),
    ("pron", re.compile(r"私|僕|俺|あたし|わし|儂|わたくし|拙者|お前|おまえ|あなた|あんた|君|てめ|貴様|お主|おぬし"
                        r"|(?:先生|お母さん|母さん|お父さん|父さん|お姉ちゃん|姉ちゃん|お兄ちゃん|兄ちゃん|おばあちゃん"
                        r"|おじいちゃん)(?:が|は|も|の|に|を)"
                        r"|\b(?:I|I'm|I'll|I'd|I've|me|my|mine|myself|you|your|yours|yourself|you're|you'll|we|us"
                        r"|our)\b",
                        re.IGNORECASE)),
)


def group_of(source: str) -> str:
    text = unicodedata.normalize("NFKC", source or "").replace("’", "'")
    return next((g for g, rx in _GROUP_CUES if rx.search(text)), "other")


# Lines with a benefactive (who did a favour for whom), across the groups above: a rough cue list,
# independent of the checker (pronoun_roles._ja_favours / _en_favours), for the "favour" numbers.
_FAVOUR_CUES = re.compile(r"[てで](?:くれ|呉れ|くださ|下さ|もら|貰|いただ|頂|あげ|上げ|やっ|やる|やろ|差し上げ)"
                          r"|[おご][^\s、。！？!?…]{1,4}?(?:いただ|くださ)|(?<![てで])くれ(?:た|る)"
                          r"|\bfor (?:me|you)\b|\bthank(?:s| you) for \w+ing\b", re.IGNORECASE)
FAVOUR = "favour"


def benefactive(source: str) -> bool:
    return bool(_FAVOUR_CUES.search(unicodedata.normalize("NFKC", source or "")))


def _span(text: str, word: str, nth: int) -> tuple[int, int] | None:
    ms = list(re.finditer(rf"(?<!\w){re.escape(word)}(?!\w)", text, re.IGNORECASE))
    return (ms[nth].start(), ms[nth].end()) if nth < len(ms) else None


def _blank() -> dict:
    return dict(tp=0, fp=0, fn=0, tn=0, span_tp=0, span_fp=0, span_fn=0, repairs_on_errors=0, repairs_on_ok=0,
                repairs_right=0, acted_tp=0, acted_fp=0, acted_fn=0, acted_tn=0,
                with_ref=0, judge_tp=0, judge_fp=0, judge_fn=0, judge_tn=0, judge_ref_tp=0, judge_ref_fp=0,
                judge_ref_fn=0, judge_ref_tn=0, ref_flagged=0, ref_tag_mismatch=0)


def _rates(t: dict) -> dict:
    def ratio(a: int, b: int) -> float:
        return round(a / max(1, b), 3)

    t["precision"] = ratio(t["tp"], t["tp"] + t["fp"])
    t["recall"] = ratio(t["tp"], t["tp"] + t["fn"])
    t["span_precision"] = ratio(t["span_tp"], t["span_tp"] + t["span_fp"])
    t["span_recall"] = ratio(t["span_tp"], t["span_tp"] + t["span_fn"])
    t["acted_precision"] = ratio(t["acted_tp"], t["acted_tp"] + t["acted_fp"])
    t["acted_recall"] = ratio(t["acted_tp"], t["acted_tp"] + t["acted_fn"])
    if t["with_ref"]:
        t["judge_precision"] = ratio(t["judge_tp"], t["judge_tp"] + t["judge_fp"])
        t["judge_recall"] = ratio(t["judge_tp"], t["judge_tp"] + t["judge_fn"])
        t["judge_ref_precision"] = ratio(t["judge_ref_tp"], t["judge_ref_tp"] + t["judge_ref_fp"])
        t["judge_ref_recall"] = ratio(t["judge_ref_tp"], t["judge_ref_tp"] + t["judge_ref_fn"])
    return t


def _first(word) -> str:
    """One pair word (a labelled own / other may be a list of accepted words)."""
    return word if isinstance(word, str) else next(iter(word), "")


_JA_SCRIPT = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")


def line_lang(source: str, src_lang: str | None) -> str:
    """The source language of a labelled line as the app always has one (Config.src_lang is ja or en): a
    file that leaves src_lang out or says "auto" gets Japanese when the line has kana / kanji, else
    English (pronoun_roles reads a kanji-only line such as 大丈夫？ as English under "auto")."""
    if src_lang and src_lang != "auto":
        return src_lang
    return "ja" if _JA_SCRIPT.search(source or "") else "en"


def load_lines(paths: list[Path]) -> list[dict]:
    """The labelled lines of `paths` ({"lines": [...]} or a bare list), NFC, with defaults for what a
    file may leave out (cat, wrong, ref, speaker / listener names; src_lang as line_lang gives it) and the
    file and source group added."""
    out = []
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        for n, ln in enumerate(data["lines"] if isinstance(data, dict) else data, 1):
            src = unicodedata.normalize("NFC", ln["source"])
            ref = ln.get("ref") or ""
            out.append({**ln, "id": ln.get("id") or f"{path.stem}#{n}", "file": path.name, "cat": ln.get("cat", ""),
                        "source": src, "translation": unicodedata.normalize("NFC", ln.get("translation") or ""),
                        "src_lang": line_lang(src, ln.get("src_lang")), "wrong": ln.get("wrong") or [],
                        "label": ln.get("label") or "", "ref": unicodedata.normalize("NFC", ref) if ref else "",
                        "speaker": ln.get("speaker") or "Speaker", "listener": ln.get("listener") or "Listener",
                        "group": group_of(src), FAVOUR: benefactive(src)})
    return out


def _words(pair) -> tuple[str, ...]:
    return tuple(w.lower() for w in ([pair] if isinstance(pair, str) else pair or ()))


def label_issues(ln: dict) -> list[RoleIssue]:
    """The labelled wrong words of a line's translation as role issues (basis "label"): each must be
    the other person ("em" from a speaker who says "em" used for the listener -> the listener's word)."""
    own_w, other_w = _words(ln["own"]), _words(ln["other"])
    out = []
    for w, n in ln["wrong"]:
        span = _span(ln["translation"], w, n)
        if span is None:
            continue
        found = ln["translation"][span[0]:span[1]]
        low = found.lower()
        if (low in own_w) == (low in other_w) and not (low in _FIRST or low in _SECOND):
            continue  # a word of both or neither: no role to swap it to
        as_listener = low in own_w or low in _FIRST  # the speaker's word, used for the listener
        want = (other_w if as_listener else own_w)[:1]
        want_w = (want[0][:1].upper() + want[0][1:] if found[:1].isupper() else want[0]) if want else None
        who = "the listener" if as_listener else "the speaker"
        out.append(RoleIssue(LISTENER_AS_SELF if as_listener else SELF_AS_LISTENER, span[0], span[1], found, want_w,
                             f"{who} is meant here: '{found}' must be {who} ('{want_w}')", "label", "label"))
    return sorted(out, key=lambda i: i.start)


def derive_ref(ln: dict) -> str:
    """A role-tagged reference built from a labelled line that has none: its translation with each
    labelled wrong word swapped for the other person's word, and every pronoun use of the pair tagged
    with its role (pronoun_roles.analyze; a swapped word with the role it now has). For dev data;
    refs written by a person (the held-out sets from D on) are better: this one keeps the labelled
    translation's wording. "" when a wrong word cannot be swapped (a word of both or neither)."""
    tr, own, other = ln["translation"], ln["own"], ln["other"]
    fixes = {(i.start, i.end): i for i in label_issues(ln)}
    if len(fixes) != len(ln["wrong"]):
        return ""
    tags: dict[tuple[int, int], tuple[str, str]] = {}  # span -> (tag, word)
    for u in analyze(tr, own, other):
        if u.role in (SELF, LISTENER) and (u.start, u.end) not in fixes:
            tags[(u.start, u.end)] = ("S" if u.role == SELF else "L", u.word)
    for span, i in fixes.items():
        tags[span] = ("S" if i.kind == SELF_AS_LISTENER else "L", i.expected_word or i.found)
    out = tr
    for (s, e), (tag, word) in sorted(tags.items(), reverse=True):
        out = out[:s] + "{" + tag + ":" + word + "}" + out[e:]
    return out


def ref_misfires(ln: dict) -> frozenset[str]:
    """The anchors (subject, apology, state, ...) the source-only checks flag in the line's own
    reference: on this line they misfire, so the judge does not count them (cached in the line). A slot
    reported under one anchor may be judged by another as well (check_line reports a span once: a "favour"
    slot of the reference that another anchor flagged first): the judged slots whose needed role the
    reference's tag contradicts count too."""
    cached = ln.get("_ref_misfires")
    if cached is None or cached[0] != ln["ref"]:
        anchors: set[str] = set()
        if ln["ref"]:
            plain, tags = parse_ref(ln["ref"])
            anchors = {i.anchor for i in check_line(ln["source"], plain, ln["own"], ln["other"], ln["src_lang"])}
            tagged = {(s, e): role for s, e, role in tags}
            anchors |= {slot for s, e, _, role, slot in judged_slots(ln["source"], plain, ln["own"], ln["other"],
                                                                      ln["src_lang"], spans=True)
                        if tagged.get((s, e)) not in (None, role)}
        cached = ln["_ref_misfires"] = (ln["ref"], frozenset(anchors))
    return cached[1]


def judge(ln: dict, text: str | None, *, source_only: bool = False, ref_only: bool = False) -> list[RoleIssue] | None:
    """The role issues of `text` for a labelled line; None for no text. With a reference ln["ref"] (and
    not `source_only`): check_line with it, without the source-only issues of an anchor the reference
    itself is flagged on (ref_misfires: "お前は俺が送っていく" -> "{L:Em} thì để {S:anh} đưa về" once had
    both slots flagged); `ref_only`: only what the reference says (basis "ref": its tagged anchor slots
    and subject-verb contexts, independent of the app's own checks). Without one, from the source alone."""
    if not text:
        return None
    if source_only or not ln["ref"]:
        return [] if ref_only and not source_only else \
            check_line(ln["source"], text, ln["own"], ln["other"], ln["src_lang"])
    found = check_line(ln["source"], text, ln["own"], ln["other"], ln["src_lang"], expected=ln["ref"])
    if ref_only:
        return [i for i in found if i.basis == "ref"]
    misfires = ref_misfires(ln)
    return [i for i in found if i.basis == "ref" or i.anchor not in misfires]


def acted_on(translation: str, source: str, own, other, src_lang: str, issues=None) -> bool:
    """What the app does something about: a repair, or an issue it asks the model again for."""
    issues = check_line(source, translation, own, other, src_lang) if issues is None else issues
    return repair_roles(translation, source, own, other, src_lang) != translation or bool(reask_issues(issues))


def evaluate(paths: list[Path], derive_refs: bool = False) -> tuple[dict, dict[str, dict], list[dict]]:
    """(totals, totals per source group, rows). A repair is "right" when it rewrites exactly the
    labelled wrong words (each to the other role's word) and nothing else. With a "ref" (with
    `derive_refs`, one built from the labels where a line has none), the judge (check_line with the
    reference) is scored on the labelled translation too."""
    t = _blank()
    by = {g: _blank() for g in (*GROUPS, FAVOUR)}  # FAVOUR: the benefactive lines, across the groups
    rows = []
    for ln in load_lines(paths):
        if derive_refs and not ln["ref"]:
            ln["ref"] = derive_ref(ln)
        tr, src, own, other, lang = ln["translation"], ln["source"], ln["own"], ln["other"], ln["src_lang"]
        g = ln["group"]
        issues = check_line(src, tr, own, other, lang)
        wrong = {s for s in (_span(tr, w, n) for w, n in ln["wrong"]) if s}
        got = {(i.start, i.end) for i in issues}
        err = ln["label"] == "error"
        verdict = "tp" if err and issues else "fn" if err else "fp" if issues else "tn"
        rep = repair_roles(tr, src, own, other, lang)
        fixed = {(i.start, i.end) for i in role_repairs(tr, src, own, other, lang)}
        acted = acted_on(tr, src, own, other, lang, issues)
        acted_verdict = "acted_" + ("tp" if err and acted else "fn" if err else "fp" if acted else "tn")
        jv = jv_ref = ref_flagged = None
        mismatch: list[str] = []
        if ln["ref"]:
            judged, judged_ref = judge(ln, tr), judge(ln, tr, ref_only=True)
            jv = "judge_" + ("tp" if err and judged else "fn" if err else "fp" if judged else "tn")
            jv_ref = "judge_ref_" + ("tp" if err and judged_ref else "fn" if err else "fp" if judged_ref else "tn")
            ref_flagged = [f"{i.found}:{i.anchor}/{i.basis}"
                           for i in check_line(src, parse_ref(ln["ref"])[0], own, other, lang, expected=ln["ref"])]
            mismatch = ref_disagreements(ln["ref"], own, other)
        for d in (t, by[g], *((by[FAVOUR],) if ln[FAVOUR] else ())):
            d[verdict] += 1
            d[acted_verdict] += 1
            d["span_tp"] += len(got & wrong)
            d["span_fp"] += len(got - wrong)
            d["span_fn"] += len(wrong - got)
            if rep != tr:
                d["repairs_on_errors" if err else "repairs_on_ok"] += 1
                d["repairs_right"] += int(err and fixed == wrong)
            if jv:
                d["with_ref"] += 1
                d[jv] += 1
                d[jv_ref] += 1
                d["ref_flagged"] += bool(ref_flagged)
                d["ref_tag_mismatch"] += bool(mismatch)
        rows.append({"id": ln["id"], "verdict": verdict, "acted": acted_verdict[6:], "cat": ln["cat"], "group": g,
                     FAVOUR: ln[FAVOUR], "source": src, "translation": tr, "repaired": rep if rep != tr else "",
                     "issues": [f"{i.found}:{i.kind}/{i.anchor}/{i.basis}" for i in issues],
                     **({"judge": jv[6:], "judge_ref": jv_ref[10:], "ref": ln["ref"], "ref_flagged": ref_flagged,
                         "ref_tags": mismatch} if jv else {})})
    return _rates(t), {g: _rates(d) for g, d in by.items()}, rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*", type=Path, help=f"labelled JSON files (default: {HELDOUT_DIR}/*.json)")
    ap.add_argument("--dev", action="store_true", help=f"the development sets in {DEV_DIR} instead")
    ap.add_argument("--show", action="store_true", help="print every flagged, missed or repaired line")
    ap.add_argument("--derive-refs", action="store_true",
                    help="lines without a ref: build one from the labels for the judge numbers (dev data)")
    args = ap.parse_args()
    folder = DEV_DIR if args.dev else HELDOUT_DIR
    paths = args.files or sorted(folder.glob("*.json"))
    if not paths:
        print(f"no labelled files in {folder}")
        return
    totals, by, rows = evaluate(paths, args.derive_refs)
    print(f"lines {sum(totals[k] for k in ('tp', 'fp', 'fn', 'tn'))}: errors {totals['tp'] + totals['fn']}, "
          f"ok {totals['fp'] + totals['tn']}")
    print(f"line: TP {totals['tp']} FP {totals['fp']} FN {totals['fn']} TN {totals['tn']}  "
          f"precision {totals['precision']:.2f}  recall {totals['recall']:.2f}")
    print(f"span: precision {totals['span_precision']:.2f}  recall {totals['span_recall']:.2f}")
    print(f"acted on (repaired or asked again): TP {totals['acted_tp']} FP {totals['acted_fp']}  "
          f"precision {totals['acted_precision']:.2f}  recall {totals['acted_recall']:.2f}")
    print(f"repairs: on error lines {totals['repairs_on_errors']} ({totals['repairs_right']} exactly right), "
          f"on ok lines {totals['repairs_on_ok']}")
    if totals["with_ref"]:
        print(f"judge (check_line with the ref) on {totals['with_ref']} lines with a ref: TP {totals['judge_tp']} "
              f"FP {totals['judge_fp']} FN {totals['judge_fn']}  precision {totals['judge_precision']:.2f}  "
              f"recall {totals['judge_recall']:.2f}; refs flagged by their own check {totals['ref_flagged']} (those "
              f"anchors not counted on that line), refs whose tags disagree with the analyser "
              f"{totals['ref_tag_mismatch']}")
        print(f"judge from the ref alone (basis 'ref', none of the app's own checks): TP {totals['judge_ref_tp']} "
              f"FP {totals['judge_ref_fp']} FN {totals['judge_ref_fn']}  precision {totals['judge_ref_precision']:.2f}  "
              f"recall {totals['judge_ref_recall']:.2f}")
    else:
        print("no line has a 'ref': judge numbers skipped")
    for g in (*GROUPS, FAVOUR):
        d = by[g]
        judge = f"  judge P {d['judge_precision']:.2f} R {d['judge_recall']:.2f}" if d["with_ref"] else ""
        if g == FAVOUR:
            print("  benefactive lines (who did a favour for whom; across the groups above):")
        print(f"  {g:<5} errors {d['tp'] + d['fn']:>3} ok {d['fp'] + d['tn']:>3}  TP {d['tp']:>3} FP {d['fp']:>2} "
              f"FN {d['fn']:>3}  P {d['precision']:.2f} R {d['recall']:.2f}  acted P {d['acted_precision']:.2f} "
              f"R {d['acted_recall']:.2f}  repairs {d['repairs_on_errors']}/{d['repairs_on_ok']} (errors/ok){judge}")
    if args.show:
        for r in rows:
            odd_ref = r.get("ref_flagged") or r.get("ref_tags")
            if r["verdict"] != "tn" or r["repaired"] or r.get("judge") not in (None, "tn") or odd_ref:
                judge = f", judge {r['judge']}" if r.get("judge") else ""
                print(f"\n{r['id']} {r['verdict'].upper()} (acted {r['acted']}{judge}) [{r['cat']}|{r['group']}] "
                      f"{r['source']}\n   MT : {r['translation']}")
                if r["repaired"]:
                    print(f"   FIX: {r['repaired']}")
                if r["issues"]:
                    print(f"   issues {r['issues']}")
                if odd_ref:
                    print(f"   REF: {r['ref']}\n   ref flagged {r['ref_flagged']}, tags {r['ref_tags']}")
    # bench/results/eval_roles_heldout.json is the held-out measurement only: other runs have their own file
    name = "eval_roles_files" if args.files else "eval_roles_dev" if args.dev else "eval_roles_heldout"
    path = save_results(name, {"files": [p.name for p in paths], "totals": totals, "groups": by, "rows": rows})
    print(f"\nsaved {path}")


if __name__ == "__main__":
    main()
