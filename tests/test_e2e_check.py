"""Tests for the offline analysis in tools.e2e_check (LLM checks) on synthetic dumps; no GUI, no app."""
from __future__ import annotations

import unittest

from tools import e2e_check as e
from tools.demo_text_window import JA_TALK


def occ(scene: str, lines: list[str], tier_seq: dict, *, eligible: bool = True, tiers: dict | None = None,
        cycle: int = 0, final_ts: float = 10.0, end_ts: float = 16.0, t2: float | None = 0.5) -> dict:
    return {"scene": scene, "cycle": cycle, "lines": lines, "llm_eligible": eligible, "tier_seq": tier_seq,
            "tiers": tiers or {}, "start_ts": final_ts, "final_ts": final_ts, "end_ts": end_ts,
            "tier2_latency_s": t2}


class TierOrderTests(unittest.TestCase):
    LINES = list(JA_TALK[0])

    def test_refine_google_then_llm_and_llm_first(self):
        o = [occ("ja_talk_1", self.LINES, {1: {"source": "セラ", "q": [1, 2]},  # label: not a dialogue block
                                           2: {"source": JA_TALK[0][1], "q": [1, 2]}}),
             occ("ja_talk_1", self.LINES, {4: {"source": JA_TALK[0][1], "q": [2]}}, cycle=1),
             occ("ja_talk_1", self.LINES, {6: {"source": JA_TALK[0][1], "q": [1]}}, eligible=False)]
        r = e.tier_order(o, "refine")
        self.assertTrue(r["ok"])
        self.assertEqual((r["blocks"], r["google_then_llm"], r["llm_first"], r["google_shown"]), (2, 1, 1, 1))

    def test_downgrade_or_no_tier2_fails(self):
        line = JA_TALK[0][1]
        self.assertFalse(e.tier_order([occ("a", self.LINES, {2: {"source": line, "q": [2, 1]}})], "refine")["ok"])
        self.assertFalse(e.tier_order([occ("a", self.LINES, {2: {"source": line, "q": [1]}})], "refine")["ok"])
        self.assertFalse(e.tier_order([], "refine")["ok"])  # nothing judged is not a pass

    def test_only_mode_must_not_show_google(self):
        line = JA_TALK[0][1]
        self.assertTrue(e.tier_order([occ("a", self.LINES, {2: {"source": line, "q": [2]}})], "only")["ok"])
        self.assertFalse(e.tier_order([occ("a", self.LINES, {2: {"source": line, "q": [1, 2]}})], "only")["ok"])


class LlmRecordsTests(unittest.TestCase):
    def record(self, ts: float, label: str, line: str, speaker: str | None, memory: int, terms=()) -> dict:
        return {"ts": ts, "latency_s": 0.2, "items": [{"id": 1, "text": label, "role": "name"},
                                                       {"id": 2, "text": line, "role": "dialog"}],
                "translations": {"1": "X", "2": "Y"}, "speakers": {"1": None, "2": speaker},
                "new_terms": [list(t) for t in terms], "auto_terms_added": len(terms), "memory_lines": memory}

    def test_speakers_memory_and_terms(self):
        recs = [self.record(1, *JA_TALK[0], "セラ", 1), self.record(2, *JA_TALK[1], "レン", 2),
                self.record(3, *JA_TALK[4], "ケンジ", 3, [("ケンジ", "Kenji", "person")])]
        r = e.llm_records_report(recs)
        self.assertEqual((r["dialogue_lines"], r["speaker_wrong"], r["distinct_dialogue_lines"]), (3, 0, 3))
        self.assertTrue(r["memory_grows"])
        self.assertEqual(r["new_terms"], [["ケンジ", "Kenji", "person"]])
        self.assertEqual(r["latency_s"]["n"], 3)

    def test_wrong_speaker_and_memory_not_growing(self):
        r = e.llm_records_report([self.record(1, *JA_TALK[0], "レン", 1), self.record(2, *JA_TALK[1], "レン", 1)])
        self.assertEqual(r["speaker_wrong"], 1)
        self.assertFalse(r["memory_grows"])  # 2 distinct lines but the memory stayed at 1
        self.assertFalse(e.llm_records_report([])["memory_grows"])

    def test_tier2_latency_split_by_llm_call_or_cache(self):
        recs = [self.record(10.4, *JA_TALK[0], "セラ", 1)]
        o = [occ("ja_talk_1", list(JA_TALK[0]), {}, final_ts=10.0, end_ts=16.0, t2=0.4),
             occ("ja_talk_1", list(JA_TALK[0]), {}, final_ts=40.0, end_ts=46.0, t2=0.1, cycle=1)]
        r = e.tier2_by_source(o, recs)
        self.assertEqual((r["llm_call"]["n"], r["llm_call"]["p50"], r["cache"]["n"], r["cache"]["p50"]),
                         (1, 0.4, 1, 0.1))


class LlmReportTests(unittest.TestCase):
    def test_labels_names_terms_and_address(self):
        o = [occ("ja_talk_1", list(JA_TALK[0]), {}, tiers={
                 "セラ": {"llm": "Sera"},
                 JA_TALK[0][1]: {"mt": "Anh ơi, chìa khóa hải đăng đâu?",
                                 "llm": "Anh ơi, chìa khóa ngọn hải đăng ở đâu vậy?", "speaker": "セラ"}}),
             occ("ja_talk_3", list(JA_TALK[2]), {}, tiers={
                 "セラ": {"llm": "Sera."}, JA_TALK[2][1]: {"llm": "Xin lỗi nhé. Cậu tìm cùng em không?"}}),
             occ("ja_talk_5", list(JA_TALK[4]), {}, tiers={
                 "ケンジ": {"llm": "Kenji"}, JA_TALK[4][1]: {"llm": "Này, sera! Chìa khóa ngọn hải đăng đây."}})]
        r = e.llm_report(o)
        self.assertEqual(r["n"], 3)
        self.assertEqual((r["labels_ok"], r["labels"]), (2, 3))  # "Sera." is not exactly "Sera"
        self.assertEqual(r["names_in_lines_ok"], 2)  # "sera" in lowercase is not the glossary name
        self.assertEqual((r["terms_ok"], r["terms_n"]), (2, 2))
        self.assertEqual((r["address_ok"], r["address_wrong"], r["address_n"]), (1, 1, 2))
        self.assertEqual(r["pages"][2]["address"], "n/a")  # Kenji is not one of the siblings

    def test_address_of(self):
        self.assertEqual(e.address_of("レン", "Tao đi trước, mày theo sau."), "ok")
        self.assertEqual(e.address_of("レン", "Đi thôi, Sera."), "none")
        self.assertEqual(e.address_of("セラ", "Bạn giúp em nhé?"), "wrong")


if __name__ == "__main__":
    unittest.main()
