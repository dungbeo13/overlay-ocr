"""Tests for the pronoun-role measurement tools: bench.eval_roles_heldout (labelled lines, derived
references, the judge), bench.roles_llm (the minimal context, the injected first answer) and
bench.eval_roles_reask (the app's re-ask path on an injected answer, the oracle pass). No model: the
"real" llama-server is an httpx.MockTransport.

Run: python -m unittest tests.test_bench_roles -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from app.translate.llm_local import LocalLlmTranslator
from app.translate.llm_prompt import address_pair, screen_role_issues
from app.translate.contextual import ScreenResult
from app.translate.pronoun_roles import check_line, parse_ref
from bench import eval_roles_heldout as heldout
from bench import eval_roles_reask as reask
from bench import roles_llm

REN_PROTECT = {"id": "T1", "cat": "explicit", "src_lang": "en", "speaker": "Kaito", "listener": "Sera", "own": "anh",
               "other": "em", "source": "Don't worry, I'll protect you.", "translation": "Đừng lo, em sẽ bảo vệ anh.",
               "label": "error", "wrong": [["em", 0], ["anh", 0]]}
REN_OK = {**REN_PROTECT, "id": "T2", "translation": "Đừng lo, anh sẽ bảo vệ em.", "label": "ok", "wrong": []}
# an error the source-only checker cannot see (a reported request): only the oracle knows the word
TEACHER_MSG = {"id": "T3", "cat": "other", "src_lang": "ja", "speaker": "Aoi", "listener": "Rin", "own": "tớ",
               "other": "cậu", "source": "先生が、凛に職員室に来てって言ってたよ",
               "translation": "Thầy bảo tớ lên phòng giáo viên đấy.", "label": "error", "wrong": [["tớ", 0]]}


def write_set(lines: list[dict]) -> Path:
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "set.json"
    path.write_text(json.dumps({"lines": lines}, ensure_ascii=False), encoding="utf-8")
    write_set.keep.append(tmp)
    return path


write_set.keep = []


def _misfire(real, source, translation, own, other, src_lang, *, expected=None):
    """check_line with a made-up source-only misfire: a subject issue on the first pronoun of every text."""
    from app.translate.pronoun_roles import SELF_AS_LISTENER, RoleIssue, analyze  # noqa: PLC0415
    found = real(source, translation, own, other, src_lang, expected=expected)
    u = analyze(translation, own, other)[:1]
    return found + [RoleIssue(SELF_AS_LISTENER, x.start, x.end, x.word, "?", "made up", "pronoun", "subject")
                    for x in u]


class LabelledLineTests(unittest.TestCase):
    def test_load_lines_defaults(self):
        bare = {k: v for k, v in REN_PROTECT.items() if k not in ("id", "cat", "wrong")}
        (ln,) = heldout.load_lines([write_set([bare])])
        self.assertEqual((ln["id"], ln["cat"], ln["wrong"], ln["ref"], ln["group"]), ("set#1", "", [], "", "pron"))
        path = write_set([REN_PROTECT])
        path.write_text(json.dumps([REN_PROTECT], ensure_ascii=False), encoding="utf-8")  # a bare list
        self.assertEqual(heldout.load_lines([path])[0]["id"], "T1")

    def test_src_lang_auto_is_judged_as_the_app_would(self):
        # the app always has ja or en: a kanji-only line in an "auto" (or src_lang-less) file is Japanese, so
        # its question about the listener is judged (under "auto" check_line read 大丈夫？ as English)
        ask = {"id": "T5", "speaker": "Sera", "listener": "Ren", "own": "em", "other": "anh", "source": "大丈夫？",
               "translation": "Em có sao không?", "label": "error", "wrong": [["Em", 0]]}
        no_lang = {k: v for k, v in REN_PROTECT.items() if k != "src_lang"}
        lines = heldout.load_lines([write_set([{**ask, "src_lang": "auto"}, ask, no_lang])])
        self.assertEqual([ln["src_lang"] for ln in lines], ["ja", "ja", "en"])
        totals, by, _ = heldout.evaluate([write_set([{**ask, "src_lang": "auto"}])])
        self.assertEqual((totals["tp"], totals["acted_tp"], by["ask"]["tp"]), (1, 1, 1))
        self.assertEqual(roles_llm.line_lang(lines[0]), "ja")

    def test_judge_without_the_checks_that_misfire_on_the_ref(self):
        """The judge does not count a source-only check that flags the line's own reference (it misfires
        there); ref_only judges by the reference alone, independent of the checks the app acts on."""
        ln = {"id": "T6", "src_lang": "en", "speaker": "Sera", "listener": "Ren", "own": "em", "other": "anh",
              "source": "You saved me.", "translation": "Em được anh cứu đấy.", "label": "ok", "wrong": [],
              "ref": "{S:Em} được {L:anh} cứu đấy."}
        (ln,) = heldout.load_lines([write_set([ln])])
        with mock.patch.object(heldout, "check_line", side_effect=lambda *a, **k: _misfire(check_line, *a, **k)):
            self.assertEqual(heldout.ref_misfires(ln), frozenset({"subject"}))
            self.assertEqual(heldout.judge(ln, ln["translation"]), [])  # the misfire is not counted
            self.assertEqual([i.anchor for i in heldout.judge(ln, ln["translation"], source_only=True)], ["subject"])
        # by the reference alone: its subject-verb contexts ("anh" before "cứu")
        self.assertEqual(heldout.judge(ln, ln["translation"], ref_only=True), [])
        self.assertEqual([i.found for i in heldout.judge(ln, "Anh được em cứu đấy.", ref_only=True)], ["em"])
        self.assertTrue(all(i.basis == "ref" for i in heldout.judge(ln, "Anh được em cứu đấy.", ref_only=True)))
        (t,) = heldout.load_lines([write_set([TEACHER_MSG])])  # no ref: nothing to judge by it alone
        self.assertEqual(heldout.judge(t, t["translation"], ref_only=True), [])

    def test_ref_misfires_see_a_slot_reported_under_another_anchor(self):
        """check_line reports a span once (under the first anchor that judged it): a favour slot of the reference
        whose needed role its tag contradicts misfires too, so the judge does not count favour issues there."""
        ln = {"id": "T8", "src_lang": "ja", "speaker": "Sera", "listener": "Ren", "own": "em", "other": "anh",
              "source": "手伝ってくれる？", "translation": "Anh giúp em nhé?", "label": "ok", "wrong": [],
              "ref": "{L:Anh} giúp {S:em} nhé?"}
        (ln,) = heldout.load_lines([write_set([ln])])
        self.assertEqual(heldout.ref_misfires(ln), frozenset())
        ln.pop("_ref_misfires")
        with mock.patch.object(heldout, "judged_slots", return_value=[(0, 3, "Anh", "self", "favour")]):
            self.assertEqual(heldout.ref_misfires(ln), frozenset({"favour"}))

    def test_reask_counts_right_lines_rewritten_without_asking(self):
        row = {"error": False, "verdict": "repaired", "reask": None, "requests": 0, "request_ms": 0, "parse_retry": 0,
               "judged_by": "ref", "ref_before": [], "ref_after": [], "chrf_gain": None, "group": "other", "favour": True}
        s = reask.summarize([row, {**row, "verdict": "not asked"}])
        self.assertEqual((s["ok_repaired"], s["ok_asked"], s["ok_changed"]), (1, 0, 0))

    def test_derived_refs_and_label_issues(self):
        (ln,) = heldout.load_lines([write_set([REN_PROTECT])])
        self.assertEqual([(i.found, i.expected_word) for i in heldout.label_issues(ln)], [("em", "anh"), ("anh", "em")])
        ref = heldout.derive_ref(ln)
        self.assertEqual(ref, "Đừng lo, {S:anh} sẽ bảo vệ {L:em}.")
        self.assertEqual(check_line(ln["source"], parse_ref(ref)[0], "anh", "em", "en", expected=ref), [])
        ln["ref"] = ref
        self.assertTrue(heldout.judge(ln, ln["translation"]))  # the wrong answer, judged with the ref
        self.assertEqual(heldout.judge(ln, "Đừng lo, anh sẽ bảo vệ em."), [])
        self.assertIsNone(heldout.judge(ln, None))
        (t,) = heldout.load_lines([write_set([TEACHER_MSG])])
        self.assertEqual(heldout.derive_ref(t), "Thầy bảo {L:cậu} lên phòng giáo viên đấy.")
        self.assertEqual(heldout.judge(t, t["translation"], source_only=True), [])  # the checker cannot see it

    def test_benefactive_lines_are_counted_across_the_groups(self):
        """"favour": lines with who did a favour for whom (a cue list of the source, not the checker), next to
        their own group; the held-out D line the 7B got wrong is caught and asked again."""
        kaito = {"id": "T7", "src_lang": "ja", "speaker": "Kaito", "listener": "Sera", "own": "anh", "other": "em",
                 "source": "弁当作ってくれたのか。ありがとな、セラ。", "translation": "Anh làm bữa trưa cho em à? Cảm ơn em nhé, Sera.",
                 "label": "error", "wrong": [["Anh", 0], ["em", 0]],
                 "ref": "{L:Em} làm cơm hộp cho {S:anh} à? Cảm ơn {L:em} nhé, Sera."}
        lines = heldout.load_lines([write_set([kaito, REN_PROTECT])])
        self.assertEqual([(ln["group"], ln["favour"]) for ln in lines], [("other", True), ("pron", False)])
        self.assertEqual([heldout.benefactive(s) for s in ("今度、クッキー焼いてあげるね。", "お越しいただき、ありがとう。",
                                                           "You made this for me?", "Thanks for helping me.", "ありがとう。")],
                         [True, True, True, True, False])
        totals, by, rows = heldout.evaluate([write_set([kaito, REN_PROTECT])])
        self.assertEqual((by["favour"]["tp"], by["favour"]["acted_tp"], by["other"]["tp"], totals["acted_tp"]), (1, 1, 1, 2))
        self.assertEqual([r["favour"] for r in rows], [True, False])

    def test_evaluate_reports_the_judge_with_refs(self):
        with_ref = {**REN_PROTECT, "ref": "Đừng lo, {S:anh} sẽ bảo vệ {L:em}."}
        ok_ref = {**REN_OK, "ref": "Đừng lo, {S:anh} sẽ bảo vệ {L:em}."}
        totals, by, rows = heldout.evaluate([write_set([with_ref, ok_ref, TEACHER_MSG])])
        self.assertEqual((totals["tp"], totals["fn"], totals["tn"]), (1, 1, 1))
        self.assertEqual((totals["acted_tp"], totals["acted_fp"]), (1, 0))  # the explicit "I" / "you": asked again
        self.assertEqual((totals["with_ref"], totals["judge_tp"], totals["judge_tn"], totals["ref_flagged"]), (2, 1, 1, 0))
        self.assertNotIn("judge", rows[2])  # no ref: no judge
        totals, _, _ = heldout.evaluate([write_set([REN_PROTECT, TEACHER_MSG])], derive_refs=True)
        # the reported request is no anchor of the reference either: the judge misses it too
        self.assertEqual((totals["with_ref"], totals["judge_tp"], totals["judge_fn"]), (2, 1, 1))


class MinimalContextTests(unittest.TestCase):
    def test_the_pair_is_defined_as_labelled(self):
        for ln in (REN_PROTECT, TEACHER_MSG, {**REN_PROTECT, "speaker": "Grandma Fumi", "listener": "Haru",
                                              "own": "bà", "other": "cháu"},
                   {**REN_PROTECT, "speaker": "Sir Alan", "listener": "Princess Elise", "own": "thần",
                    "other": "công chúa"}):
            with self.subTest(speaker=ln["speaker"]):
                ctx = roles_llm.line_context(ln)
                self.assertEqual(address_pair(ln["speaker"], ln["listener"], ctx), (ln["own"], ln["other"], True))
        res = ScreenResult({1: "Kaito", 2: REN_PROTECT["translation"]}, {1: None, 2: "Kaito"})
        found = screen_role_issues(res, roles_llm.line_items(REN_PROTECT), roles_llm.line_context(REN_PROTECT), "en")
        self.assertEqual([i.anchor for i in found[roles_llm.LINE_ID][0]], ["subject", "object"])
        # the model is always told a source language, as in the app
        self.assertEqual([roles_llm.line_lang(ln) for ln in (REN_PROTECT, {**TEACHER_MSG, "src_lang": "auto"},
                                                              {**REN_PROTECT, "src_lang": ""})], ["en", "ja", "en"])

    def test_scene_lines_have_refs_and_pairs(self):
        lines = roles_llm.scene_lines(["roles_siblings_ja"])
        self.assertTrue(lines)
        self.assertTrue(all(ln["ref"] and ln["own"] and ln["other"] for ln in lines))
        self.assertEqual((lines[0]["speaker"], lines[0]["listener"]), ("Yuto", "Aoi"))

    def test_e2e_scenes_add_to_the_labelled_lines(self):
        """--scenes adds the scene lines to the folder's (it used to drop the held-out folder); --scenes-only
        measures them alone."""
        from bench import eval_roles_e2e as e2e  # noqa: PLC0415
        scenes = roles_llm.scene_lines(["roles_siblings_ja"])
        path = write_set([REN_PROTECT, TEACHER_MSG])
        with mock.patch.object(e2e, "HELDOUT_DIR", path.parent):
            _, lines = e2e.collect_lines([], False, "roles_siblings_ja", False)
            self.assertEqual(len(lines), 2 + len(scenes))
            _, lines = e2e.collect_lines([], False, "roles_siblings_ja", True)
            self.assertEqual(len(lines), len(scenes))
            _, lines = e2e.collect_lines([], False, "", False)
            self.assertEqual([ln["id"] for ln in lines], ["T1", "T3"])


class ReaskPathTests(unittest.IsolatedAsyncioTestCase):
    def translator(self, answers: list[str], cls: type[LocalLlmTranslator] = LocalLlmTranslator
                   ) -> tuple[LocalLlmTranslator, roles_llm.InjectingTransport, list[dict]]:
        bodies: list[dict] = []

        def server(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            bodies.append(body)
            schema = body["response_format"]["json_schema"]["schema"]
            ids = [p["properties"]["id"]["const"] for p in schema["properties"]["items"]["prefixItems"]]
            text = answers.pop(0)
            return httpx.Response(200, json={"choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps({"items": [{"id": i, "speaker": None, "translation": text}
                                                                      for i in ids], "new_terms": []},
                                                           ensure_ascii=False)}}]})

        transport = roles_llm.InjectingTransport(httpx.MockTransport(server))
        client = roles_llm.client_for(transport)
        self.addAsyncCleanup(client.aclose)
        return cls("http://127.0.0.1:5555", "test-model", client=client), transport, bodies

    async def test_injected_answer_goes_through_the_apps_reask(self):
        tr, transport, bodies = self.translator(["Đừng lo, anh sẽ bảo vệ em."])
        (ln,) = heldout.load_lines([write_set([REN_PROTECT])])
        ln["ref"] = heldout.derive_ref(ln)
        row = await reask.run_line(tr, transport, ln, oracle=False)
        self.assertEqual((row["verdict"], row["reask"], row["final"], row["requests"]),
                         ("fixed", "taken", "Đừng lo, anh sẽ bảo vệ em.", 1))
        self.assertEqual(len(bodies), 1)  # the first answer was injected, only the re-ask was sent
        note = json.loads(bodies[0]["messages"][-1]["content"].splitlines()[-1])["note"]
        self.assertIn('"em" there means Kaito, write "anh"', note)
        # a right line: nothing is sent
        tr, transport, bodies = self.translator([])
        (ok,) = heldout.load_lines([write_set([REN_OK])])
        row = await reask.run_line(tr, transport, ok, oracle=False)
        self.assertEqual((row["verdict"], row["requests"], row["changed"]), ("not asked", 0, False))
        s = reask.summarize([row])
        self.assertEqual((s["ok"], s["ok_asked"], s["ok_changed"]), (1, 0, 0))
        # tôi -> the pair's word (fix_pronouns) is no role repair: not counted as a right line rewritten
        (ok,) = heldout.load_lines([write_set([{**REN_OK, "translation": "Đừng lo, tôi sẽ bảo vệ em."}])])
        row = await reask.run_line(tr, transport, ok, oracle=False)
        self.assertEqual((row["verdict"], row["shown_first"]), ("not asked", "Đừng lo, anh sẽ bảo vệ em."))
        # the role repair rewriting a line is
        (bad,) = heldout.load_lines([write_set([{**REN_OK, "source": "ごめんね。", "translation": "Em xin lỗi nhé.",
                                                 "src_lang": "ja"}])])
        row = await reask.run_line(tr, transport, bad, oracle=False)
        self.assertEqual((row["verdict"], row["shown_first"]), ("repaired", "Anh xin lỗi nhé."))

    async def test_oracle_asks_for_what_the_checker_cannot_see(self):
        (ln,) = heldout.load_lines([write_set([TEACHER_MSG])])
        ln["ref"] = heldout.derive_ref(ln)
        tr, transport, bodies = self.translator([])
        row = await reask.run_line(tr, transport, ln, oracle=False)
        self.assertEqual((row["verdict"], row["requests"]), ("not asked", 0))
        tr, transport, bodies = self.translator(["Thầy bảo cậu lên phòng giáo viên đấy."])
        row = await reask.run_line(tr, transport, ln, oracle=True)
        # taken; the judge (the reference) does not see the labelled error, so it cannot score the change
        self.assertEqual((row["reask"], row["verdict"], row["final"]),
                         ("taken", "unjudged", "Thầy bảo cậu lên phòng giáo viên đấy."))
        note = json.loads(bodies[0]["messages"][-1]["content"].splitlines()[-1])["note"]
        self.assertIn('"tớ" there means Rin, write "cậu"', note)
        # the oracle is gone afterwards: the app's own checker again
        import app.translate.llm_local as llm_local  # noqa: PLC0415
        self.assertIs(llm_local.screen_role_issues, screen_role_issues)

    async def test_e2e_off_and_on(self):
        """eval_roles_e2e: the same fresh first answer, post-edit off keeps it, on asks again."""
        from dataclasses import replace  # noqa: PLC0415
        from bench import eval_roles_e2e as e2e  # noqa: PLC0415
        lines = heldout.load_lines([write_set([REN_PROTECT, REN_OK])])
        for ln in lines:
            ln["ref"] = heldout.derive_ref(ln)
        wrong, right = "Đừng lo, em sẽ bảo vệ anh.", "Đừng lo, anh sẽ bảo vệ em."
        tr, _, bodies = self.translator([wrong, wrong], e2e.RecordingTranslator)
        tr.prompt_options = replace(tr.prompt_options, repair_roles=False, reask_roles=False)
        off = await e2e.run_pass(tr, lines)
        tr, _, bodies = self.translator([wrong, right, wrong, right], e2e.RecordingTranslator)
        on = await e2e.run_pass(tr, lines)
        summary, rows = e2e.summarize(lines, off, on)
        self.assertEqual([r["off"] for r in rows], [wrong, wrong])
        self.assertEqual([r["on"] for r in rows], [right, right])
        self.assertEqual([(r["first"], r["first_differs"], r["repaired"]) for r in rows], [(wrong, False, False)] * 2)
        self.assertEqual((summary["errors_off"], summary["errors_first"], summary["errors_on"], summary["asked"],
                          summary["worse"], summary["better"], summary["better_same_first"], summary["reask_rate"]),
                         (2, 2, 0, 2, 0, 2, 2, 1.0))
        self.assertEqual(summary["groups"]["pron"]["errors_off"], 2)
        # a first answer the repair rewrites (Sera's ごめんね): recorded as repaired, judged on the same answer
        sera = {**REN_PROTECT, "id": "T4", "speaker": "Sera", "listener": "Ren", "own": "em", "other": "anh",
                "src_lang": "ja", "source": "ごめんね。", "translation": "Anh xin lỗi nhé.", "wrong": [["Anh", 0]]}
        (ln,) = heldout.load_lines([write_set([sera])])
        ln["ref"] = heldout.derive_ref(ln)
        tr, _, bodies = self.translator(["Anh xin lỗi nhé."], e2e.RecordingTranslator)
        (row,) = await e2e.run_pass(tr, [ln])
        self.assertEqual((row["first"], row["first_fixed"], row["out"], row["repaired"], row["reask"], len(bodies)),
                         ("Anh xin lỗi nhé.", "Anh xin lỗi nhé.", "Em xin lỗi nhé.", True, None, 1))
        self.assertTrue(row["issues_first"])
        self.assertEqual(row["issues"], [])


if __name__ == "__main__":
    unittest.main()
