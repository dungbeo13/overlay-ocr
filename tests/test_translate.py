"""Tests for app.translate (no network; HTTP is faked with httpx.MockTransport).

Run: python -m unittest tests.test_translate -v
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs

import httpx

from app.translate import (
    AzureTranslator,
    CachedTranslator,
    CacheNamespace,
    GeminiTranslator,
    GoogleGtxTranslator,
    MissingApiKeyError,
    TranslationCache,
    TranslationError,
    TranslatorEngine,
    glossary_version,
)
from app.translate.cache import normalize_text
from app.translate.gemini import default_temperature, default_thinking, parse_items
from app.translate.google_gtx import (
    AsyncTokenBucket,
    parse_batch_response,
    parse_single_response,
    plan_chunks,
    split_long_text,
)
from app.translate.textnorm import apply_glossary, is_passthrough

# Response bodies captured from translate.googleapis.com on 2026-10-22.
GTX_EXPLICIT_MANY = r'["Chào buổi sáng","Bạn có muốn lưu cài đặt của mình không?","Bình minh!"]'  # sl=ja, 3 q
GTX_EXPLICIT_ONE = r'["Bắt đầu trò chơi"]'  # sl=en, 1 q
GTX_AUTO_ONE = r'[["Xin chào thế giới","en"]]'  # sl=auto, 1 q
GTX_AUTO_MANY = r'[["Xin chào thế giới","en"],["Xin chào","ja"],["Bắt đầu trò chơi","en"]]'  # sl=auto, 3 q
GTX_NEWLINE_EMPTY = r'["Dòng một.\nDòng hai.","","Lưu và thoát"]'  # q = "Line one.\nLine two.", "", "Save & quit"
GTX_ESCAPES = r'["Đừng dừng lại và \"chạy\" <bây giờ>","Đó là 5 > 3","","HP 120\/150"]'
GTX_SINGLE_EN = (
    r'[[["Xin chào thế giới. ","Hello world. ",null,null,3,null,null,[[]],[[["824257d7a249c58caeea06a2e64a25bd",'
    r'"en_vi_2023q1.md"]]]],["Đây là một bài kiểm tra.\n","This is a test.\n",null,null,3,null,null,[[]],'
    r'[[["824257d7a249c58caeea06a2e64a25bd","en_vi_2023q1.md"],true]]],["Dòng thứ hai ở đây.","Second line here.",'
    r'null,null,3,null,null,[[]],[[["824257d7a249c58caeea06a2e64a25bd","en_vi_2023q1.md"]]]]],null,"en",null,'
    r'null,null,null,[]]'
)
GTX_SINGLE_JA = (
    r'[[["Xin chào. khỏe?\nHẹn gặp lại vào ngày mai.","こんにちは。元気？\n明日また会おう。",null,null,11,null,null,'
    r'[[],[]],[[["8610ded6ae1f9c19659bfa9bcb7344e7","ja_en_2023q1.md"],true]]]],null,"ja",null,null,null,null,[]]'
)
SORRY_REDIRECT = "https://www.google.com/sorry/index?continue=https://translate.googleapis.com/translate_a/t"


def form_of(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode("utf-8"), keep_blank_values=True)


class FakeGtx:
    """MockTransport handler for translate_a/t and translate_a/single. Override batch/single per test."""

    def __init__(self, batch=None, single=None):
        self.calls: list[tuple[str, str, dict, dict]] = []
        self.batch = batch or (lambda qs, n: httpx.Response(200, json=[f"VI[{q}]" for q in qs]))
        self.single = single or (lambda q, n: httpx.Response(200, json=[[[f"S[{q}]", q, None, None, 3]], None, "en"]))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        form = form_of(request)
        params = dict(request.url.params)
        self.calls.append((request.url.path, request.method, params, form))
        n = len(self.calls)
        if request.url.path.endswith("/translate_a/t"):
            return self.batch(form.get("q", []), n)
        return self.single(form["q"][0], n)

    def paths(self) -> list[str]:
        return [c[0].rsplit("/", 1)[-1] for c in self.calls]


def make_gtx(fake: FakeGtx, **kw) -> GoogleGtxTranslator:
    kw.setdefault("rate", 1000.0)
    kw.setdefault("burst", 1000.0)
    kw.setdefault("backoff_base", 0.001)
    kw.setdefault("backoff_max", 0.01)
    return GoogleGtxTranslator(transport=httpx.MockTransport(fake), **kw)


# -- gtx ----------------------------------------------------------------------------------
class TestGtxParsing(unittest.TestCase):
    def test_explicit_shapes(self):
        self.assertEqual(parse_batch_response(json.loads(GTX_EXPLICIT_MANY), 3),
                         ["Chào buổi sáng", "Bạn có muốn lưu cài đặt của mình không?", "Bình minh!"])
        self.assertEqual(parse_batch_response(json.loads(GTX_EXPLICIT_ONE), 1), ["Bắt đầu trò chơi"])

    def test_auto_shapes(self):
        self.assertEqual(parse_batch_response(json.loads(GTX_AUTO_ONE), 1), ["Xin chào thế giới"])
        self.assertEqual(parse_batch_response(json.loads(GTX_AUTO_MANY), 3),
                         ["Xin chào thế giới", "Xin chào", "Bắt đầu trò chơi"])

    def test_newlines_empty_and_escapes(self):
        self.assertEqual(parse_batch_response(json.loads(GTX_NEWLINE_EMPTY), 3),
                         ["Dòng một.\nDòng hai.", "", "Lưu và thoát"])
        out = parse_batch_response(json.loads(GTX_ESCAPES), 4)
        self.assertEqual(out[0], 'Đừng dừng lại và "chạy" <bây giờ>')
        self.assertEqual(out[3], "HP 120/150")

    def test_legacy_shapes(self):
        self.assertEqual(parse_batch_response("Xin chào", 1), ["Xin chào"])
        self.assertEqual(parse_batch_response(["Xin chào", "en"], 1), ["Xin chào"])  # old sl=auto single
        self.assertEqual(parse_batch_response(["Xin chào", "en"], 2), ["Xin chào", "en"])  # two real items

    def test_bad_shapes(self):
        for bad in ({"x": 1}, [None], [1, 2], 42):
            with self.assertRaises(ValueError):
                parse_batch_response(bad, 2)

    def test_single(self):
        self.assertEqual(parse_single_response(json.loads(GTX_SINGLE_EN)),
                         "Xin chào thế giới. Đây là một bài kiểm tra.\nDòng thứ hai ở đây.")
        self.assertEqual(parse_single_response(json.loads(GTX_SINGLE_JA)),
                         "Xin chào. khỏe?\nHẹn gặp lại vào ngày mai.")
        self.assertEqual(parse_single_response([None, None, "en"]), "")
        with self.assertRaises(ValueError):
            parse_single_response({"a": 1})


class TestChunking(unittest.TestCase):
    def test_item_limit(self):
        self.assertEqual([len(c) for c in plan_chunks([10] * 120, 50, 5000)], [50, 50, 20])

    def test_char_limit(self):
        self.assertEqual(plan_chunks([2000, 2000, 2000, 10], 50, 5000), [[0, 1], [2, 3]])
        self.assertEqual(plan_chunks([6000, 10], 50, 5000), [[0], [1]])

    def test_split_long_text(self):
        text = "a" * 30 + "\n" + "b" * 30
        self.assertEqual(split_long_text(text, 40), [("a" * 30, "\n"), ("b" * 30, "")])
        self.assertEqual(split_long_text("x" * 100, 40), [("x" * 40, ""), ("x" * 40, ""), ("x" * 20, "")])
        self.assertEqual(split_long_text("short", 40), [("short", "")])
        self.assertEqual(split_long_text("one two three", 8), [("one two", " "), ("three", "")])


class TestGtxEngine(unittest.IsolatedAsyncioTestCase):
    async def test_request_format(self):
        fake = FakeGtx()
        eng = make_gtx(fake)
        self.addAsyncCleanup(eng.aclose)
        out = await eng.translate_batch(["Hello", "World"], "en", "vi")
        self.assertEqual(out, ["VI[Hello]", "VI[World]"])
        path, method, params, form = fake.calls[0]
        self.assertEqual((path, method), ("/translate_a/t", "POST"))
        self.assertEqual(params, {"client": "gtx", "sl": "en", "tl": "vi", "dt": "t"})
        self.assertEqual(form["q"], ["Hello", "World"])

    async def test_splits_by_item_count(self):
        fake = FakeGtx()
        eng = make_gtx(fake)
        self.addAsyncCleanup(eng.aclose)
        texts = [f"item {i}" for i in range(120)]
        out = await eng.translate_batch(texts, "en", "vi")
        self.assertEqual(out, [f"VI[item {i}]" for i in range(120)])
        self.assertEqual([len(c[3]["q"]) for c in fake.calls], [50, 50, 20])

    async def test_splits_by_chars(self):
        fake = FakeGtx()
        eng = make_gtx(fake)
        self.addAsyncCleanup(eng.aclose)
        texts = ["a" * 2000, "b" * 2000, "c" * 2000, "d" * 2000]
        await eng.translate_batch(texts, "en", "vi")
        self.assertEqual([len(c[3]["q"]) for c in fake.calls], [2, 2])
        self.assertTrue(all(sum(map(len, c[3]["q"])) <= 5000 for c in fake.calls))

    async def test_long_item_split_and_rejoined(self):
        fake = FakeGtx()
        eng = make_gtx(fake, max_chars=40)
        self.addAsyncCleanup(eng.aclose)
        text = "first line of a long text block\nsecond line of the same block"
        out = await eng.translate_batch([text, "short"], "en", "vi")
        self.assertEqual(out[0], "VI[first line of a long text block]\nVI[second line of the same block]")
        self.assertEqual(out[1], "VI[short]")

    async def test_blank_items_not_sent(self):
        fake = FakeGtx()
        eng = make_gtx(fake)
        self.addAsyncCleanup(eng.aclose)
        out = await eng.translate_batch(["", "  ", "Hi"], "en", "vi")
        self.assertEqual(out, ["", "  ", "VI[Hi]"])
        self.assertEqual(fake.calls[0][3]["q"], ["Hi"])
        self.assertEqual(await eng.translate_batch(["", " "], "en", "vi"), ["", " "])
        self.assertEqual(len(fake.calls), 1)

    async def test_count_mismatch_falls_back_to_single(self):
        fake = FakeGtx(batch=lambda qs, n: httpx.Response(200, json=[f"VI[{q}]" for q in qs[:-1]]))
        logs = []
        eng = make_gtx(fake, on_request=logs.append)
        self.addAsyncCleanup(eng.aclose)
        out = await eng.translate_batch(["one", "two", "three"], "en", "vi")
        self.assertEqual(out, ["S[one]", "S[two]", "S[three]"])
        self.assertEqual(fake.paths(), ["t", "single", "single", "single"])
        _, method, params, form = fake.calls[1]
        self.assertEqual(method, "POST")
        self.assertEqual(params, {})  # everything in the form body
        self.assertEqual({k: v[0] for k, v in form.items()}, {"client": "gtx", "sl": "en", "tl": "vi", "dt": "t", "q": "one"})
        self.assertEqual(eng.stats.mismatches, 1)
        self.assertEqual(eng.stats.fallback_items, 3)
        self.assertTrue(logs[0].mismatch)
        self.assertEqual([lg.kind for lg in logs], ["batch", "single", "single", "single"])

    async def test_unparseable_batch_falls_back(self):
        fake = FakeGtx(batch=lambda qs, n: httpx.Response(200, text="not json"))
        eng = make_gtx(fake)
        self.addAsyncCleanup(eng.aclose)
        self.assertEqual(await eng.translate_batch(["one"], "en", "vi"), ["S[one]"])

    async def test_retry_on_429_then_success(self):
        fake = FakeGtx(batch=lambda qs, n: httpx.Response(429, text="<html>Sorry</html>") if n == 1
                       else httpx.Response(200, json=[f"VI[{q}]" for q in qs]))
        logs = []
        eng = make_gtx(fake, on_request=logs.append)
        self.addAsyncCleanup(eng.aclose)
        self.assertEqual(await eng.translate_batch(["Hi"], "en", "vi"), ["VI[Hi]"])
        self.assertEqual([lg.status for lg in logs], [429, 200])
        self.assertEqual((eng.stats.retries, eng.stats.rate_limited, eng.stats.requests), (1, 1, 2))

    async def test_sorry_redirect_is_rate_limit(self):
        fake = FakeGtx(batch=lambda qs, n: httpx.Response(302, headers={"Location": SORRY_REDIRECT}) if n == 1
                       else httpx.Response(200, json=[f"VI[{q}]" for q in qs]))
        eng = make_gtx(fake)
        self.addAsyncCleanup(eng.aclose)
        self.assertEqual(await eng.translate_batch(["Hi"], "en", "vi"), ["VI[Hi]"])
        self.assertEqual(eng.stats.rate_limited, 1)

    async def test_gives_up_after_retries(self):
        fake = FakeGtx(batch=lambda qs, n: httpx.Response(503))
        eng = make_gtx(fake, max_retries=3)
        self.addAsyncCleanup(eng.aclose)
        with self.assertRaises(TranslationError) as cm:
            await eng.translate_batch(["Hi"], "en", "vi")
        self.assertTrue(cm.exception.retryable)
        self.assertEqual(len(fake.calls), 4)

    async def test_client_error_not_retried(self):
        fake = FakeGtx(batch=lambda qs, n: httpx.Response(400))
        eng = make_gtx(fake)
        self.addAsyncCleanup(eng.aclose)
        with self.assertRaises(TranslationError) as cm:
            await eng.translate_batch(["Hi"], "en", "vi")
        self.assertFalse(cm.exception.retryable)
        self.assertEqual(len(fake.calls), 1)

    async def test_timeout_retried(self):
        def batch(qs, n):
            if n == 1:
                raise httpx.ConnectTimeout("slow")
            return httpx.Response(200, json=[f"VI[{q}]" for q in qs])
        fake = FakeGtx(batch=batch)
        eng = make_gtx(fake)
        self.addAsyncCleanup(eng.aclose)
        self.assertEqual(await eng.translate_batch(["Hi"], "en", "vi"), ["VI[Hi]"])
        self.assertEqual(eng.stats.retries, 1)

    async def test_glossary_presubstitution(self):
        fake = FakeGtx()
        eng = make_gtx(fake)
        self.addAsyncCleanup(eng.aclose)
        await eng.translate_batch(["田中さん、おはよう"], "ja", "vi", glossary={"田中": "Tanaka"})
        self.assertEqual(fake.calls[0][3]["q"], ["Tanakaさん、おはよう"])

    async def test_token_bucket_paces_requests(self):
        bucket = AsyncTokenBucket(rate=50.0, burst=1.0)
        t0 = time.perf_counter()
        for _ in range(6):
            await bucket.acquire()
        self.assertGreaterEqual(time.perf_counter() - t0, 0.09)  # 5 waits of 20 ms


# -- normalization and cache -------------------------------------------------------------
class TestNormalization(unittest.TestCase):
    def test_nfkc_and_whitespace(self):
        self.assertEqual(normalize_text("ＨＰ　１００／１２０", "en"), "HP 100/120")
        self.assertEqual(normalize_text("  Hello \t  world  ", "en"), "Hello world")
        self.assertEqual(normalize_text("Line one  \n\n  Line two\r\n", "en"), "Line one\nLine two")
        self.assertEqual(normalize_text("ﾃｽﾄ", "ja"), "テスト")

    def test_japanese_spaces(self):
        self.assertEqual(normalize_text("こ ん に ち は 世 界", "ja"), "こんにちは世界")
        self.assertEqual(normalize_text("すごい ！", "ja"), "すごい!")
        self.assertEqual(normalize_text("HP を 回復", "ja"), "HP を回復")
        self.assertEqual(normalize_text("Hello world", "ja"), "Hello world")
        self.assertEqual(normalize_text("こ ん に ち は", "en"), "こ ん に ち は")  # only for ja/zh

    def test_idempotent(self):
        for s, lang in [("  ＡＢＣ  ｄｅｆ \n\n x", "en"), ("お は よ う ！ 　元気？", "ja"), ("a\r\n\r\n b", "en")]:
            once = normalize_text(s, lang)
            self.assertEqual(normalize_text(once, lang), once)

    def test_passthrough(self):
        self.assertTrue(is_passthrough("Start game", "Start  game"))
        self.assertFalse(is_passthrough("OK", "OK"))  # too short to judge
        self.assertFalse(is_passthrough("Start game", "Bắt đầu trò chơi"))

    def test_glossary_boundaries(self):
        self.assertEqual(apply_glossary("Ken met Kenneth", {"Ken": "Kenji"}, "en"), "Kenji met Kenneth")
        self.assertEqual(apply_glossary("田中と田中太郎", {"田中": "Tanaka", "田中太郎": "Tanaka Taro"}, "ja"),
                         "TanakaとTanaka Taro")


class TestTranslationCache(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "cache.sqlite3"
        self.ns = CacheNamespace("gtx", "gtx", "en", "vi", "")

    def tearDown(self):
        self._tmp.cleanup()

    def test_sqlite_roundtrip_and_l1(self):
        with TranslationCache(self.db) as c1:
            self.assertTrue(c1.put(self.ns, "Hello   world", "Xin chào thế giới"))
            self.assertEqual(c1.get(self.ns, "Hello world").level, "l1")
        with TranslationCache(self.db) as c2:
            hit = c2.get(self.ns, " Hello world ")
            self.assertEqual((hit.translation, hit.level), ("Xin chào thế giới", "l2"))
            self.assertEqual(c2.get(self.ns, "Hello world").level, "l1")
            self.assertEqual(c2.db_count(), 1)
            mode = c2._db.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(mode.lower(), "wal")

    def test_key_includes_namespace(self):
        with TranslationCache(self.db) as c:
            c.put(self.ns, "Hello world", "Xin chào")
            for other in (self.ns._replace(tgt="ja"), self.ns._replace(model="x"),
                          self.ns._replace(glossary_version="abc"), self.ns._replace(engine="azure")):
                self.assertIsNone(c.get(other, "Hello world", fuzzy=False))

    def test_lru_eviction(self):
        c = TranslationCache(None, l1_size=3)
        for i in range(4):
            c.put(self.ns, f"text {i}", f"vi {i}")
        self.assertIsNone(c.get(self.ns, "text 0", fuzzy=False))
        self.assertEqual(c.get(self.ns, "text 3").translation, "vi 3")
        self.assertEqual(len(c), 3)
        with TranslationCache(self.db, l1_size=2) as c2:  # evicted from L1 but still in SQLite
            for i in range(3):
                c2.put(self.ns, f"text {i}", f"vi {i}")
            self.assertEqual(c2.get(self.ns, "text 0").level, "l2")

    def test_fuzzy_threshold(self):
        c = TranslationCache(None)
        c.put(self.ns, "The quick brown fox jumps", "Con cáo")
        hit = c.get(self.ns, "The quick brown fox jumpz")  # ratio 96
        self.assertEqual((hit.level, hit.translation), ("fuzzy", "Con cáo"))
        self.assertGreaterEqual(hit.score, 95)
        self.assertIsNone(c.get(self.ns, "The quick brawn fox jumpz"))  # ratio 92
        self.assertIsNone(c.get(self.ns, "The quick brown fox jumpz", fuzzy=False))
        c.put(self.ns, "Save game", "Lưu")
        self.assertIsNone(c.get(self.ns, "Save gamee"))  # under 12 chars: exact only

    def test_fuzzy_requires_same_digits(self):
        c = TranslationCache(None)
        c.put(self.ns, "You have 12 potions left", "Bạn còn 12 bình thuốc")
        self.assertIsNone(c.get(self.ns, "You have 13 potions left"))
        self.assertIsNotNone(c.get(self.ns, "You have 12 potions lefť"))

    def test_quality_tier_overwrite(self):
        with TranslationCache(self.db) as c:
            self.assertTrue(c.put(self.ns, "Hello", "LLM", tier=2))
            self.assertFalse(c.put(self.ns, "Hello", "MT", tier=1))
            self.assertEqual(c.get(self.ns, "Hello").translation, "LLM")
            self.assertTrue(c.put(self.ns, "Hello", "Human", tier=3))
            self.assertIsNone(c.get(self.ns, "Hello", min_tier=4, fuzzy=False))
        with TranslationCache(self.db) as c2:  # empty L1: SQLite still refuses the worse tier
            self.assertFalse(c2.put(self.ns, "Hello", "MT again", tier=1))
            hit = c2.get(self.ns, "Hello")
            self.assertEqual((hit.translation, hit.tier), ("Human", 3))
            self.assertTrue(c2.put(self.ns, "Hello", "Human v2", tier=3))  # equal tier replaces
        with TranslationCache(self.db) as c3:
            self.assertEqual(c3.get(self.ns, "Hello").translation, "Human v2")

    def test_glossary_version(self):
        self.assertEqual(glossary_version(None), "")
        self.assertEqual(glossary_version({"a": "1", "b": "2"}), glossary_version({"b": "2", "a": "1"}))
        self.assertNotEqual(glossary_version({"a": "1"}), glossary_version({"a": "2"}))


class FakeEngine(TranslatorEngine):
    name = "fake"
    model = "m1"

    def __init__(self, gate: asyncio.Event | None = None, fail: bool = False, echo: bool = False, tier: int = 1):
        self.calls: list[list[str]] = []
        self.gate = gate
        self.fail = fail
        self.echo = echo
        self.quality_tier = tier

    async def translate_batch(self, texts, src, tgt, *, context=None, glossary=None):
        self.calls.append(list(texts))
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise TranslationError("boom", retryable=True)
        return list(texts) if self.echo else [f"vi:{t}" for t in texts]


class TestCachedTranslator(unittest.IsolatedAsyncioTestCase):
    async def test_hits_skip_engine(self):
        eng = FakeEngine()
        ct = CachedTranslator(eng, TranslationCache(None))
        self.assertEqual(await ct.translate_batch(["Hello", "World"], "en", "vi"), ["vi:Hello", "vi:World"])
        out = await ct.translate_batch(["Hello", "World", "New one"], "en", "vi")
        self.assertEqual(out, ["vi:Hello", "vi:World", "vi:New one"])
        self.assertEqual(eng.calls, [["Hello", "World"], ["New one"]])

    async def test_no_fuzzy_reuse_by_default(self):
        eng = FakeEngine()
        ct = CachedTranslator(eng, TranslationCache(None))
        await ct.translate_batch(["You can open the door now."], "en", "vi")
        out = await ct.translate_batch(["You can't open the door now."], "en", "vi")
        self.assertEqual(out, ["vi:You can't open the door now."])
        self.assertEqual(len(eng.calls), 2)

    async def test_normalized_duplicates_sent_once(self):
        eng = FakeEngine()
        ct = CachedTranslator(eng, TranslationCache(None))
        out = await ct.translate_batch(["Hello  world", "Hello world", " Hello world\n"], "en", "vi")
        self.assertEqual(out, ["vi:Hello world"] * 3)
        self.assertEqual(eng.calls, [["Hello world"]])

    async def test_non_letters_passthrough(self):
        eng = FakeEngine()
        ct = CachedTranslator(eng, TranslationCache(None))
        self.assertEqual(await ct.translate_batch(["123/456", "!!", "Hi"], "en", "vi"), ["123/456", "!!", "vi:Hi"])
        self.assertEqual(eng.calls, [["Hi"]])

    async def test_inflight_dedupe(self):
        gate = asyncio.Event()
        eng = FakeEngine(gate)
        ct = CachedTranslator(eng, TranslationCache(None))
        a = asyncio.create_task(ct.translate_batch(["alpha line", "shared line"], "en", "vi"))
        await asyncio.sleep(0)
        b = asyncio.create_task(ct.translate_batch(["shared line", "beta line"], "en", "vi"))
        await asyncio.sleep(0)
        gate.set()
        ra, rb = await asyncio.gather(a, b)
        self.assertEqual(ra, ["vi:alpha line", "vi:shared line"])
        self.assertEqual(rb, ["vi:shared line", "vi:beta line"])
        self.assertEqual(eng.calls, [["alpha line", "shared line"], ["beta line"]])
        self.assertEqual(ct.stats["joined_inflight"], 1)
        self.assertEqual(ct._inflight, {})

    async def test_inflight_failure_propagates_and_is_not_cached(self):
        gate = asyncio.Event()
        eng = FakeEngine(gate, fail=True)
        ct = CachedTranslator(eng, TranslationCache(None))
        a = asyncio.create_task(ct.translate_batch(["shared line"], "en", "vi"))
        await asyncio.sleep(0)
        b = asyncio.create_task(ct.translate_batch(["shared line"], "en", "vi"))
        await asyncio.sleep(0)
        gate.set()
        ra, rb = await asyncio.gather(a, b, return_exceptions=True)
        self.assertIsInstance(ra, TranslationError)
        self.assertIsInstance(rb, TranslationError)
        self.assertEqual(ct._inflight, {})
        eng.fail = False
        self.assertEqual(await ct.translate_batch(["shared line"], "en", "vi"), ["vi:shared line"])
        self.assertEqual(len(eng.calls), 2)

    async def test_cancelled_waiter_does_not_break_owner(self):
        gate = asyncio.Event()
        eng = FakeEngine(gate)
        ct = CachedTranslator(eng, TranslationCache(None))
        a = asyncio.create_task(ct.translate_batch(["shared line"], "en", "vi"))
        await asyncio.sleep(0)
        b = asyncio.create_task(ct.translate_batch(["shared line"], "en", "vi"))
        await asyncio.sleep(0)
        b.cancel()
        gate.set()
        self.assertEqual(await a, ["vi:shared line"])
        with self.assertRaises(asyncio.CancelledError):
            await b

    async def test_cache_write_error_does_not_strand_waiters(self):
        cache = TranslationCache(None)
        eng = FakeEngine(asyncio.Event())
        ct = CachedTranslator(eng, cache)
        with mock.patch.object(cache, "put_many", side_effect=sqlite3.OperationalError("database is locked")):
            a = asyncio.create_task(ct.translate_batch(["shared line"], "en", "vi"))
            await asyncio.sleep(0)
            b = asyncio.create_task(ct.translate_batch(["shared line"], "en", "vi"))
            await asyncio.sleep(0)
            eng.gate.set()
            ra, rb = await asyncio.wait_for(asyncio.gather(a, b), 2)
        self.assertEqual((ra, rb), (["vi:shared line"], ["vi:shared line"]))
        self.assertEqual(ct.stats["cache_errors"], 1)
        self.assertEqual(ct._inflight, {})

    async def test_cache_read_error_mid_batch_unregisters_futures(self):
        cache = TranslationCache(None)
        eng = FakeEngine()
        ct = CachedTranslator(eng, cache)
        with mock.patch.object(cache, "get", side_effect=[None, sqlite3.OperationalError("disk I/O error")]):
            with self.assertRaises(sqlite3.OperationalError):
                await ct.translate_batch(["first line", "second line"], "en", "vi")
        self.assertEqual(ct._inflight, {})
        out = await asyncio.wait_for(ct.translate_batch(["first line"], "en", "vi"), 2)
        self.assertEqual(out, ["vi:first line"])

    async def test_passthrough_kept_in_memory_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "c.sqlite3"
            eng = FakeEngine(echo=True)
            with TranslationCache(db) as cache:
                ct = CachedTranslator(eng, cache)
                await ct.translate_batch(["Untranslated words"], "en", "vi")
                await ct.translate_batch(["Untranslated words"], "en", "vi")
                self.assertEqual(len(eng.calls), 1)  # L1 hit within the session
                self.assertEqual(cache.db_count(), 0)
            with TranslationCache(db) as cache2:
                await CachedTranslator(eng, cache2).translate_batch(["Untranslated words"], "en", "vi")
                self.assertEqual(len(eng.calls), 2)  # retried in a new session

    async def test_shared_namespace_better_tier_wins(self):
        cache = TranslationCache(None)
        mt = CachedTranslator(FakeEngine(tier=1), cache, namespace="shared", model="")
        llm_engine = FakeEngine(tier=2)
        llm_engine.translate_batch = (lambda texts, src, tgt, **kw: _async([f"llm:{t}" for t in texts]))
        llm = CachedTranslator(llm_engine, cache, namespace="shared", model="", min_tier=2)
        self.assertEqual(await mt.translate_batch(["Good morning"], "en", "vi"), ["vi:Good morning"])
        self.assertEqual(await llm.translate_batch(["Good morning"], "en", "vi"), ["llm:Good morning"])
        self.assertEqual(await mt.translate_batch(["Good morning"], "en", "vi"), ["llm:Good morning"])
        cache.put(mt.namespace("en", "vi"), "Good morning", "worse", tier=1)
        self.assertEqual(await mt.translate_batch(["Good morning"], "en", "vi"), ["llm:Good morning"])

    async def test_glossary_changes_key(self):
        eng = FakeEngine()
        ct = CachedTranslator(eng, TranslationCache(None))
        await ct.translate_batch(["Hello Ken"], "en", "vi")
        await ct.translate_batch(["Hello Ken"], "en", "vi", glossary={"Ken": "Kenji"})
        self.assertEqual(len(eng.calls), 2)


async def _async(value):
    return value


# -- Azure -------------------------------------------------------------------------------
class TestAzure(unittest.IsolatedAsyncioTestCase):
    def make(self, handler, **kw) -> AzureTranslator:
        eng = AzureTranslator("test-key-123", "southeastasia", transport=httpx.MockTransport(handler),
                              backoff_base=0.001, backoff_max=0.01, **kw)
        self.addAsyncCleanup(eng.aclose)
        return eng

    def test_missing_key(self):
        with mock.patch.dict(os.environ, {"AZURE_TRANSLATOR_KEY": ""}):
            with self.assertRaises(MissingApiKeyError) as cm:
                AzureTranslator()
        self.assertFalse(cm.exception.retryable)
        self.assertIn("AZURE_TRANSLATOR_KEY", str(cm.exception))

    async def test_request_and_response(self):
        seen = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen.append(req)
            body = json.loads(req.content)
            return httpx.Response(200, headers={"X-metered-usage": "8"},
                                  json=[{"translations": [{"text": f"vi:{b['Text']}", "to": "vi"}]} for b in body])

        eng = self.make(handler)
        out = await eng.translate_batch(["Hello", "", "Bye"], "en", "vi")
        self.assertEqual(out, ["vi:Hello", "", "vi:Bye"])
        req = seen[0]
        self.assertEqual(req.method, "POST")
        self.assertEqual((req.url.scheme, req.url.host, req.url.path),
                         ("https", "api.cognitive.microsofttranslator.com", "/translate"))
        self.assertEqual(dict(req.url.params), {"api-version": "3.0", "from": "en", "to": "vi"})
        self.assertEqual(req.headers["Ocp-Apim-Subscription-Key"], "test-key-123")
        self.assertEqual(req.headers["Ocp-Apim-Subscription-Region"], "southeastasia")
        self.assertTrue(req.headers["Content-Type"].startswith("application/json"))
        self.assertIn("X-ClientTraceId", req.headers)
        self.assertEqual(json.loads(req.content), [{"Text": "Hello"}, {"Text": "Bye"}])
        self.assertEqual(eng.stats["billed_chars"], 8)

    async def test_detected_language_shape(self):
        eng = self.make(lambda req: httpx.Response(200, json=[
            {"detectedLanguage": {"language": "ja", "score": 1.0}, "translations": [{"text": "Xin chào", "to": "vi"}]}]))
        self.assertEqual(await eng.translate_batch(["こんにちは"], "ja", "vi"), ["Xin chào"])

    async def test_glossary_dynamic_dictionary(self):
        seen = []

        def handler(req):
            seen.append(json.loads(req.content))
            return httpx.Response(200, json=[{"translations": [{"text": "x", "to": "vi"}]}])

        eng = self.make(handler)
        await eng.translate_batch(["田中さん"], "ja", "vi", glossary={"田中": "Tanaka"})
        self.assertEqual(seen[0][0]["Text"],
                         '<mstrans:dictionary translation="Tanaka">田中</mstrans:dictionary>さん')

    async def test_batching_limits(self):
        sizes = []

        def handler(req):
            body = json.loads(req.content)
            sizes.append((len(body), sum(len(b["Text"]) for b in body)))
            return httpx.Response(200, json=[{"translations": [{"text": "v", "to": "vi"}]} for _ in body])

        eng = self.make(handler)
        out = await eng.translate_batch([f"t{i}" for i in range(2500)], "en", "vi")
        self.assertEqual(len(out), 2500)
        self.assertEqual([s[0] for s in sizes], [1000, 1000, 500])
        sizes.clear()
        await eng.translate_batch(["x" * 20_000] * 5, "en", "vi")
        self.assertEqual([s[0] for s in sizes], [2, 2, 1])
        self.assertTrue(all(s[1] <= 50_000 for s in sizes))

    async def test_auth_error_not_retried(self):
        calls = []

        def handler(req):
            calls.append(req)
            return httpx.Response(401, json={"error": {"code": 401000, "message": "The request is not authorized"}})

        eng = self.make(handler)
        with self.assertRaises(TranslationError) as cm:
            await eng.translate_batch(["Hi"], "en", "vi")
        self.assertFalse(cm.exception.retryable)
        self.assertIn("401000", str(cm.exception))
        self.assertNotIn("test-key-123", str(cm.exception))
        self.assertEqual(len(calls), 1)

    async def test_429_retried(self):
        calls = []

        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                return httpx.Response(429, json={"error": {"code": 429001, "message": "slow down"}})
            return httpx.Response(200, json=[{"translations": [{"text": "ok", "to": "vi"}]}])

        eng = self.make(handler)
        self.assertEqual(await eng.translate_batch(["Hi"], "en", "vi"), ["ok"])
        self.assertEqual(eng.stats["retries"], 1)

    async def test_count_mismatch_raises(self):
        eng = self.make(lambda req: httpx.Response(200, json=[{"translations": [{"text": "a", "to": "vi"}]}]))
        with self.assertRaises(TranslationError):
            await eng.translate_batch(["a", "b"], "en", "vi")


# -- Gemini ------------------------------------------------------------------------------
def gemini_response(items: list[dict] | str, *, thought: str | None = None) -> httpx.Response:
    text = items if isinstance(items, str) else json.dumps(items, ensure_ascii=False)
    parts = ([{"text": thought, "thought": True}] if thought else []) + [{"text": text}]
    return httpx.Response(200, json={
        "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP", "index": 0}],
        "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 30, "totalTokenCount": 150},
        "modelVersion": "gemini-3.1-flash-lite",
    })


def prompt_items(body: dict) -> list[dict]:
    prompt = body["contents"][0]["parts"][0]["text"]
    return json.loads(prompt.split("Items to translate (JSON):\n", 1)[1])


class TestGemini(unittest.IsolatedAsyncioTestCase):
    def make(self, handler, **kw) -> GeminiTranslator:
        with mock.patch.dict(os.environ, {"GEMINI_MODEL": ""}):
            eng = GeminiTranslator("gem-key-456", transport=httpx.MockTransport(handler),
                                   backoff_base=0.001, backoff_max=0.01, **kw)
        self.addAsyncCleanup(eng.aclose)
        return eng

    def test_missing_key(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            with self.assertRaises(MissingApiKeyError):
                GeminiTranslator()

    def test_model_defaults(self):
        self.assertEqual(default_thinking("gemini-3.1-flash-lite"), {"thinkingLevel": "minimal"})
        self.assertEqual(default_thinking("gemini-3.8-flash"), {"thinkingLevel": "low"})
        self.assertEqual(default_thinking("gemini-2.5-flash-lite"), {"thinkingBudget": 0})
        self.assertIsNone(default_temperature("gemini-3.5-flash-lite"))
        self.assertEqual(default_temperature("gemini-2.5-flash"), 0.2)
        with mock.patch.dict(os.environ, {"GEMINI_MODEL": "gemini-3.5-flash-lite"}):
            self.assertEqual(GeminiTranslator("k").model, "gemini-3.5-flash-lite")

    async def test_request_shape_and_parse(self):
        seen = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen.append(req)
            items = prompt_items(json.loads(req.content))
            return gemini_response([{"id": it["id"], "translation": f"vi:{it['text']}"} for it in items])

        eng = self.make(handler)
        out = await eng.translate_batch(["田中さん、待って！", "", "行こう"], "ja", "vi",
                                        context=["前の行です"], glossary={"田中": "Tanaka"})
        self.assertEqual(out, ["vi:田中さん、待って！", "", "vi:行こう"])
        req = seen[0]
        self.assertEqual(req.url.path, "/v1beta/models/gemini-3.1-flash-lite:generateContent")
        self.assertEqual(req.headers["x-goog-api-key"], "gem-key-456")
        self.assertNotIn("key", dict(req.url.params))
        body = json.loads(req.content)
        gen = body["generationConfig"]
        self.assertEqual(gen["responseMimeType"], "application/json")
        self.assertEqual(gen["responseSchema"]["type"], "ARRAY")
        self.assertEqual(gen["responseSchema"]["items"]["required"], ["id", "translation"])
        self.assertEqual(gen["thinkingConfig"], {"thinkingLevel": "minimal"})
        self.assertNotIn("temperature", gen)
        self.assertIn("pronouns", body["systemInstruction"]["parts"][0]["text"])
        prompt = body["contents"][0]["parts"][0]["text"]
        self.assertIn("Source language: Japanese", prompt)
        self.assertIn("田中 => Tanaka", prompt)
        self.assertIn("> 前の行です", prompt)
        self.assertEqual(prompt_items(body), [{"id": 0, "text": "田中さん、待って！"}, {"id": 2, "text": "行こう"}])
        self.assertEqual(eng.stats["prompt_tokens"], 120)

    async def test_missing_ids_rerequested_once(self):
        seen = []

        def handler(req):
            items = prompt_items(json.loads(req.content))
            seen.append(items)
            keep = items if len(seen) > 1 else items[:1]  # first answer drops id 1
            return gemini_response([{"id": it["id"], "translation": f"vi:{it['text']}"} for it in keep])

        eng = self.make(handler)
        self.assertEqual(await eng.translate_batch(["a", "b"], "en", "vi"), ["vi:a", "vi:b"])
        self.assertEqual(seen[1], [{"id": 1, "text": "b"}])
        self.assertEqual(eng.stats["rerequests"], 1)

    async def test_still_missing_raises(self):
        eng = self.make(lambda req: gemini_response([{"id": 0, "translation": "x"}]))
        with self.assertRaises(TranslationError) as cm:
            await eng.translate_batch(["a", "b"], "en", "vi")
        self.assertTrue(cm.exception.retryable)

    async def test_thoughts_ignored_and_fences_stripped(self):
        fenced = '```json\n[{"id": 0, "translation": "Xin chào"}]\n```'
        eng = self.make(lambda req: gemini_response(fenced, thought="Let me think about pronouns..."))
        self.assertEqual(await eng.translate_batch(["Hello"], "en", "vi"), ["Xin chào"])

    async def test_blocked_prompt(self):
        eng = self.make(lambda req: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}))
        with self.assertRaises(TranslationError) as cm:
            await eng.translate_batch(["x"], "en", "vi")
        self.assertFalse(cm.exception.retryable)

    async def test_http_errors(self):
        calls = []

        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                return httpx.Response(503, json={"error": {"code": 503, "status": "UNAVAILABLE", "message": "busy"}})
            return gemini_response([{"id": 0, "translation": "ok"}])

        eng = self.make(handler)
        self.assertEqual(await eng.translate_batch(["x"], "en", "vi"), ["ok"])
        bad = self.make(lambda req: httpx.Response(
            400, json={"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "bad field"}}))
        with self.assertRaises(TranslationError) as cm:
            await bad.translate_batch(["x"], "en", "vi")
        self.assertFalse(cm.exception.retryable)
        self.assertIn("INVALID_ARGUMENT", str(cm.exception))

    def test_parse_items_variants(self):
        self.assertEqual(parse_items('[{"id": "1", "translation": "a"}]'), {1: "a"})
        self.assertEqual(parse_items('{"translations": [{"id": 0, "translation": "b"}]}'), {0: "b"})
        truncated = '[{"id": 0, "translation": "một\\ndòng"}, {"id": 1, "transl'
        self.assertEqual(parse_items(truncated), {0: "một\ndòng"})
        self.assertEqual(parse_items("garbage"), {})


class TestChrf(unittest.TestCase):
    def test_bounds_and_order(self):
        from bench.eval_translate import chrf
        self.assertAlmostEqual(chrf("Lưu trò chơi", "Lưu trò chơi"), 100.0)
        self.assertAlmostEqual(chrf("Lưu trò chơi", "Lưu  trò\nchơi"), 100.0)  # whitespace ignored
        self.assertEqual(chrf("xyz", "abc"), 0.0)
        self.assertEqual(chrf("", "abc"), 0.0)
        close = chrf("Bạn có chắc chắn muốn thoát không?", "Bạn có chắc muốn thoát không?")
        far = chrf("Hàng tồn kho đã đầy.", "Túi đồ đã đầy.")
        self.assertTrue(0 < far < close < 100)
        # recall-weighted (beta=2): dropping words costs more than adding them
        self.assertGreater(chrf("a b c d e", "a b c"), chrf("a b c", "a b c d e"))


if __name__ == "__main__":
    unittest.main()
