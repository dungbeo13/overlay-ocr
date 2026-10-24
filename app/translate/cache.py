"""Two-level translation cache (in-memory LRU + SQLite) and a caching TranslatorEngine wrapper.

Keys are (engine, model, src, tgt, glossary_version, normalized source text). Each entry has
a quality tier (e.g. 1 = MT, 2 = LLM, 3 = human edit); a write never replaces a better tier.
Assumes a single asyncio loop / thread: the SQLite connection is used from the creating thread.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, NamedTuple

from rapidfuzz import fuzz, process

from .base import TranslationError, TranslatorEngine
from .textnorm import digit_signature, is_passthrough, needs_translation, normalize_text

__all__ = [
    "TIER_MT", "TIER_LLM", "TIER_HUMAN", "CacheNamespace", "CacheHit", "TranslationCache",
    "CachedTranslator", "default_cache_path", "glossary_version", "normalize_text",
]

TIER_MT = 1
TIER_LLM = 2
TIER_HUMAN = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS translations (
    engine TEXT NOT NULL,
    model TEXT NOT NULL,
    src TEXT NOT NULL,
    tgt TEXT NOT NULL,
    glossary_version TEXT NOT NULL,
    source_norm TEXT NOT NULL,
    translation TEXT NOT NULL,
    tier INTEGER NOT NULL,
    updated REAL NOT NULL,
    PRIMARY KEY (engine, model, src, tgt, glossary_version, source_norm)
) WITHOUT ROWID
"""

_UPSERT = """
INSERT INTO translations (engine, model, src, tgt, glossary_version, source_norm, translation, tier, updated)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (engine, model, src, tgt, glossary_version, source_norm) DO UPDATE SET
    translation = excluded.translation, tier = excluded.tier, updated = excluded.updated
WHERE excluded.tier >= translations.tier
"""

_SELECT = """
SELECT translation, tier FROM translations
WHERE engine = ? AND model = ? AND src = ? AND tgt = ? AND glossary_version = ? AND source_norm = ?
"""


class CacheNamespace(NamedTuple):
    engine: str
    model: str
    src: str
    tgt: str
    glossary_version: str = ""


@dataclass(frozen=True)
class CacheHit:
    translation: str
    tier: int
    level: str  # "l1", "l2" or "fuzzy"
    matched: str  # normalized source that matched (differs from the query for fuzzy hits)
    score: float = 100.0


def default_cache_path() -> Path:
    """%LOCALAPPDATA%/ScreenTranslator/translation_cache.sqlite3 (home dir if LOCALAPPDATA is unset)."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "ScreenTranslator" / "translation_cache.sqlite3"


def glossary_version(glossary: dict[str, str] | None) -> str:
    """Stable short hash of a glossary ("" when empty) so glossary edits invalidate entries."""
    if not glossary:
        return ""
    blob = json.dumps(sorted(glossary.items()), ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


class TranslationCache:
    """L1 LRU dict in front of an optional SQLite (WAL) store, plus fuzzy reuse.

    Fuzzy reuse only applies to normalized queries of >= `fuzzy_min_len` chars, compares
    against a bounded window of recently used entries in the same namespace with
    rapidfuzz `ratio`, and requires identical digit runs so "12 potions" never reuses "13 potions".
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        l1_size: int = 1000,
        fuzzy_window: int = 256,
        fuzzy_threshold: float = 95.0,
        fuzzy_min_len: int = 12,
    ):
        self.l1_size = l1_size
        self.fuzzy_window = fuzzy_window
        self.fuzzy_threshold = fuzzy_threshold
        self.fuzzy_min_len = fuzzy_min_len
        self._l1: OrderedDict[tuple, tuple[str, int]] = OrderedDict()
        self._recent: dict[CacheNamespace, OrderedDict[str, tuple[str, int]]] = {}
        self.stats = {"l1_hits": 0, "l2_hits": 0, "fuzzy_hits": 0, "misses": 0, "writes": 0, "skipped_writes": 0}
        self._db: sqlite3.Connection | None = None
        if db_path is not None:
            if str(db_path) != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(db_path))
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute(_SCHEMA)
            self._db.commit()

    # -- lookups -------------------------------------------------------------------------
    def get(self, ns: CacheNamespace, text: str, *, min_tier: int = 0, fuzzy: bool = True) -> CacheHit | None:
        norm = normalize_text(text, ns.src)
        key = (*ns, norm)
        entry = self._l1.get(key)
        level = "l1"
        if entry is not None:
            self._l1.move_to_end(key)
        elif self._db is not None:
            row = self._db.execute(_SELECT, key).fetchone()
            if row is not None:
                entry, level = (row[0], int(row[1])), "l2"
                self._l1_put(key, entry)
        if entry is not None and entry[1] >= min_tier:
            self.stats[f"{level}_hits"] += 1
            self._touch_recent(ns, norm, entry)
            return CacheHit(entry[0], entry[1], level, norm)

        if fuzzy and len(norm) >= self.fuzzy_min_len:
            hit = self._fuzzy(ns, norm, min_tier)
            if hit is not None:
                self.stats["fuzzy_hits"] += 1
                return hit
        self.stats["misses"] += 1
        return None

    def _fuzzy(self, ns: CacheNamespace, norm: str, min_tier: int) -> CacheHit | None:
        window = self._recent.get(ns)
        if not window:
            return None
        digits = digit_signature(norm)
        choices = [s for s, (_, tier) in window.items() if tier >= min_tier and digit_signature(s) == digits]
        if not choices:
            return None
        best = process.extractOne(norm, choices, scorer=fuzz.ratio, score_cutoff=self.fuzzy_threshold)
        if best is None:
            return None
        matched, score, _ = best
        translation, tier = window[matched]
        window.move_to_end(matched)
        return CacheHit(translation, tier, "fuzzy", matched, float(score))

    # -- writes --------------------------------------------------------------------------
    def put(self, ns: CacheNamespace, text: str, translation: str, tier: int = TIER_MT, *, persist: bool = True) -> bool:
        """Store one entry; returns False when an existing entry has a better tier."""
        return self.put_many(ns, [(text, translation)], tier, persist=persist) == 1

    def put_many(
        self, ns: CacheNamespace, pairs: Iterable[tuple[str, str]], tier: int = TIER_MT, *, persist: bool = True
    ) -> int:
        """Store (source, translation) pairs in one SQLite transaction; returns how many were written.

        `persist=False` keeps entries in memory only (used for suspicious results).
        """
        written = 0
        now = time.time()
        for text, translation in pairs:
            norm = normalize_text(text, ns.src)
            key = (*ns, norm)
            current = self._l1.get(key)
            if current is not None and current[1] > tier:
                self.stats["skipped_writes"] += 1
                continue
            if persist and self._db is not None:
                cur = self._db.execute(_UPSERT, (*key, translation, tier, now))
                if cur.rowcount == 0:  # the stored row has a better tier; keep it and load it into L1
                    self.stats["skipped_writes"] += 1
                    row = self._db.execute(_SELECT, key).fetchone()
                    if row is not None:
                        self._l1_put(key, (row[0], int(row[1])))
                    continue
            self._l1_put(key, (translation, tier))
            self._touch_recent(ns, norm, (translation, tier))
            written += 1
        if self._db is not None:
            self._db.commit()
        self.stats["writes"] += written
        return written

    def _l1_put(self, key: tuple, entry: tuple[str, int]) -> None:
        self._l1[key] = entry
        self._l1.move_to_end(key)
        while len(self._l1) > self.l1_size:
            self._l1.popitem(last=False)

    def _touch_recent(self, ns: CacheNamespace, norm: str, entry: tuple[str, int]) -> None:
        if len(norm) < self.fuzzy_min_len:
            return
        window = self._recent.setdefault(ns, OrderedDict())
        window[norm] = entry
        window.move_to_end(norm)
        while len(window) > self.fuzzy_window:
            window.popitem(last=False)

    # -- housekeeping --------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self._l1)

    def db_count(self) -> int:
        if self._db is None:
            return 0
        return int(self._db.execute("SELECT COUNT(*) FROM translations").fetchone()[0])

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    def __enter__(self) -> "TranslationCache":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _consume_exception(fut: asyncio.Future) -> None:
    if not fut.cancelled():
        fut.exception()  # mark retrieved so asyncio does not log "never retrieved"


class CachedTranslator(TranslatorEngine):
    """Wraps an engine: cache lookups first, one inner batch for the misses, in-flight dedupe.

    `namespace` overrides the engine name in cache keys so several engines can share entries
    (then tiers decide which result wins); `min_tier` treats worse cached entries as misses.
    Text with no letters is returned unchanged without calling the engine. Results the engine
    echoed back untranslated are cached in memory only, so a later run can retry them.

    Fuzzy reuse is off by default: ratio >= 95 still matches opposite meanings such as
    "You can open the door" vs "You can't open the door" (98.1). OCR jitter is handled by the
    pipeline's same-line tracking instead; pass fuzzy=True only where a wrong reuse is harmless.
    """

    def __init__(
        self,
        engine: TranslatorEngine,
        cache: TranslationCache,
        *,
        namespace: str | None = None,
        model: str | None = None,
        tier: int | None = None,
        min_tier: int = 0,
        fuzzy: bool = False,
    ):
        self.engine = engine
        self.cache = cache
        self.name = engine.name
        self._ns_engine = namespace or engine.name
        self._model = model if model is not None else str(getattr(engine, "model", ""))
        self.tier = tier if tier is not None else int(getattr(engine, "quality_tier", TIER_MT))
        self.min_tier = min_tier
        self.fuzzy = fuzzy
        self._inflight: dict[tuple[CacheNamespace, str], asyncio.Future[str]] = {}
        self.stats = {"texts": 0, "hits": 0, "skipped": 0, "sent": 0, "joined_inflight": 0, "inner_calls": 0,
                      "cache_errors": 0}

    def namespace(self, src: str, tgt: str, glossary: dict[str, str] | None = None) -> CacheNamespace:
        return CacheNamespace(self._ns_engine, self._model, src, tgt, glossary_version(glossary))

    async def translate_batch(
        self,
        texts: list[str],
        src: str,
        tgt: str,
        *,
        context: list[str] | None = None,
        glossary: dict[str, str] | None = None,
    ) -> list[str]:
        ns = self.namespace(src, tgt, glossary)
        loop = asyncio.get_running_loop()
        out: list[str | None] = [None] * len(texts)
        waits: list[tuple[int, asyncio.Future[str]]] = []
        mine: dict[str, asyncio.Future[str]] = {}  # norm -> future this call must resolve
        self.stats["texts"] += len(texts)

        # Every exit path must resolve and unregister the futures in `mine`; a future left in
        # _inflight would make every later request for that text wait forever.
        try:
            for i, text in enumerate(texts):
                norm = normalize_text(text, src)
                if not needs_translation(norm):
                    out[i] = text
                    self.stats["skipped"] += 1
                    continue
                hit = self.cache.get(ns, norm, min_tier=self.min_tier, fuzzy=self.fuzzy)
                if hit is not None:
                    out[i] = hit.translation
                    self.stats["hits"] += 1
                    continue
                fut = self._inflight.get((ns, norm))  # also finds duplicates within this call
                if fut is None:
                    fut = loop.create_future()
                    fut.add_done_callback(_consume_exception)
                    self._inflight[(ns, norm)] = fut
                    mine[norm] = fut
                elif norm not in mine:
                    self.stats["joined_inflight"] += 1
                waits.append((i, fut))

            if mine:
                norms = list(mine)
                self.stats["sent"] += len(norms)
                self.stats["inner_calls"] += 1
                results = await self.engine.translate_batch(norms, src, tgt, context=context, glossary=glossary)
                if len(results) != len(norms):
                    raise TranslationError(
                        f"{self.engine.name} returned {len(results)} results for {len(norms)} texts", retryable=True
                    )
                good = [(n, r) for n, r in zip(norms, results) if not is_passthrough(n, r)]
                suspect = [(n, r) for n, r in zip(norms, results) if is_passthrough(n, r)]
                try:
                    self.cache.put_many(ns, good, self.tier)
                    if suspect:
                        self.cache.put_many(ns, suspect, self.tier, persist=False)
                except sqlite3.Error:  # e.g. "database is locked": keep the translations, skip persisting
                    self.stats["cache_errors"] += 1
                for norm, result in zip(norms, results):
                    self._inflight.pop((ns, norm), None)
                    if not mine[norm].done():
                        mine[norm].set_result(result)
        except BaseException as exc:
            err = exc if isinstance(exc, TranslationError) else TranslationError(
                f"{self.engine.name} failed: {exc!r}", retryable=True)
            for norm, fut in mine.items():
                if self._inflight.get((ns, norm)) is fut:
                    del self._inflight[(ns, norm)]
                if not fut.done():
                    fut.set_exception(err)
            raise

        for i, fut in waits:
            # shield: cancelling this caller must not cancel a future other callers share
            out[i] = await asyncio.shield(fut)
        return [s if s is not None else "" for s in out]

    async def aclose(self) -> None:
        await self.engine.aclose()
