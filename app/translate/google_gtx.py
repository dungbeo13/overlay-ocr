"""Google Translate "gtx" engine (free, unofficial endpoint; for personal use only).

Batch:    POST translate_a/t?client=gtx&sl=..&tl=..&dt=t, form body with repeated q=...
Fallback: POST translate_a/single, form body client/sl/tl/dt/q, one text per request.

Live probing (2026-10, on the author's machine) found that GET requests to both endpoints got a
429 "Sorry..." page while exactly these POST forms returned 200, and that /t with all fields
in the body was redirected (302) to google.com/sorry. Redirects to /sorry count as rate limits.

Also hosts small HTTP helpers shared by the other engines (token bucket, backoff, key errors).
"""
from __future__ import annotations

import asyncio
import random
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

import httpx

from .base import TranslationError, TranslatorEngine
from .textnorm import apply_glossary

BATCH_URL = "https://translate.googleapis.com/translate_a/t"
SINGLE_URL = "https://translate.googleapis.com/translate_a/single"
_LANG_CODE = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")
_BLOCKED_MARKERS = ("/sorry/", "unusual traffic", "automated queries")


# -- shared helpers ------------------------------------------------------------------------
class MissingApiKeyError(TranslationError):
    """Raised at engine construction when its API key env var is not set."""

    def __init__(self, env_var: str):
        super().__init__(f"{env_var} is not set; set it in the environment to use this engine", retryable=False)
        self.env_var = env_var


class AsyncTokenBucket:
    """`rate` requests/s with up to `burst` back-to-back; waiters are served in FIFO order."""

    def __init__(self, rate: float, burst: float = 1.0):
        if rate <= 0:
            raise ValueError("rate must be > 0")
        self.rate = rate
        self.burst = max(1.0, burst)
        self._tokens = self.burst
        self._stamp = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        """Take one token, sleeping as needed; returns the seconds spent waiting."""
        waited = 0.0
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.burst, self._tokens + (now - self._stamp) * self.rate)
                self._stamp = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return waited
                delay = (1.0 - self._tokens) / self.rate
                await asyncio.sleep(delay)
                waited += delay


def backoff_delay(attempt: int, base: float, cap: float, retry_after: float | None = None) -> float:
    """Exponential backoff (base * 2**attempt, capped) with +-10% jitter; honours Retry-After."""
    delay = min(cap, base * (2 ** attempt)) * random.uniform(0.9, 1.1)
    if retry_after is not None:
        delay = max(delay, min(cap, retry_after))
    return delay


def parse_retry_after(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None  # HTTP-date form; not worth parsing here


def plan_chunks(lengths: list[int], max_items: int, max_chars: int) -> list[list[int]]:
    """Group consecutive item indices so each group has <= max_items and <= max_chars total
    (a single item longer than max_chars still gets its own group)."""
    chunks: list[list[int]] = []
    cur: list[int] = []
    cur_chars = 0
    for i, n in enumerate(lengths):
        if cur and (len(cur) >= max_items or cur_chars + n > max_chars):
            chunks.append(cur)
            cur, cur_chars = [], 0
        cur.append(i)
        cur_chars += n
    if cur:
        chunks.append(cur)
    return chunks


def split_long_text(text: str, limit: int) -> list[tuple[str, str]]:
    """Split text longer than `limit` at a line break, else a space, else hard.

    Returns (piece, separator) pairs; joining translated pieces with their separators
    rebuilds the layout.
    """
    out: list[tuple[str, str]] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut <= 0:
            cut = rest.rfind(" ", 0, limit)
        if cut <= 0:
            out.append((rest[:limit], ""))
            rest = rest[limit:]
        else:
            out.append((rest[:cut], rest[cut]))
            rest = rest[cut + 1:]
    if rest or not out:
        out.append((rest, ""))
    return out


# -- response parsing ----------------------------------------------------------------------
def _first_str(item: Any) -> str | None:
    while isinstance(item, list) and item:
        item = item[0]
    return item if isinstance(item, str) else None


def parse_batch_response(data: Any, expected: int | None = None) -> list[str]:
    """Parse translate_a/t JSON into one string per q.

    Observed shapes (2026-10):
      explicit sl, any count:  ["tr1", "tr2", ...]                 (one q: ["tr"])
      sl=auto:                 [["tr1", "en"], ["tr2", "ja"], ...]  (one q: [["tr", "en"]])
    Also accepted: a bare string for one q, and ["tr", "en"] for one q with sl=auto (older
    clients report both). Raises ValueError on anything else.
    """
    if isinstance(data, str):
        return [data]
    if not isinstance(data, list):
        raise ValueError(f"unexpected gtx response type {type(data).__name__}")
    if (expected == 1 and len(data) == 2 and all(isinstance(x, str) for x in data)
            and _LANG_CODE.match(data[1])):
        return [data[0]]
    out = []
    for item in data:
        s = _first_str(item)
        if s is None:
            raise ValueError(f"unexpected gtx item {item!r:.80}")
        out.append(s)
    return out


def parse_single_response(data: Any) -> str:
    """Parse translate_a/single?dt=t JSON: [[[tr, orig, ...], [tr, orig, ...], ...], None, "en", ...].
    The translation is the concatenation of the segment strings (they keep their own spacing/newlines)."""
    if not isinstance(data, list) or not data:
        raise ValueError("unexpected gtx single response")
    segments = data[0]
    if segments is None:
        return ""
    if not isinstance(segments, list):
        raise ValueError("unexpected gtx single segments")
    return "".join(seg[0] for seg in segments if isinstance(seg, list) and seg and isinstance(seg[0], str))


# -- engine --------------------------------------------------------------------------------
@dataclass
class GtxRequestLog:
    kind: str  # "batch" or "single"
    status: int | None  # HTTP status; None for network errors/timeouts
    latency_s: float  # HTTP time only (excludes throttling waits)
    items: int
    chars: int
    attempt: int  # 0 = first try
    error: str | None = None
    mismatch: bool = False  # batch result count != input count (or unparseable)


@dataclass
class GtxStats:
    requests: int = 0  # HTTP attempts
    ok: int = 0
    retries: int = 0
    errors: int = 0  # failed attempts, any reason
    rate_limited: int = 0  # 429 or redirect to /sorry
    mismatches: int = 0
    fallback_items: int = 0  # items translated one by one via /single
    items: int = 0  # items sent in batch requests
    chars: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


class GoogleGtxTranslator(TranslatorEngine):
    name = "gtx"
    model = "gtx"
    quality_tier = 1

    def __init__(
        self,
        *,
        rate: float = 1.0,
        burst: float = 2.0,
        max_items: int = 50,
        max_chars: int = 5000,
        timeout: float = 10.0,
        max_retries: int = 4,
        backoff_base: float = 1.0,
        backoff_max: float = 16.0,
        use_glossary: bool = True,
        batch_url: str = BATCH_URL,
        single_url: str = SINGLE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        on_request: Callable[[GtxRequestLog], None] | None = None,
    ):
        self.max_items = max_items
        self.max_chars = max_chars
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.use_glossary = use_glossary
        self.batch_url = batch_url
        self.single_url = single_url
        self.on_request = on_request
        self.stats = GtxStats()
        self._bucket = AsyncTokenBucket(rate, burst)
        self._client = httpx.AsyncClient(
            http2=True, timeout=httpx.Timeout(timeout), transport=transport, follow_redirects=False
        )

    async def translate_batch(
        self,
        texts: list[str],
        src: str,
        tgt: str,
        *,
        context: list[str] | None = None,
        glossary: dict[str, str] | None = None,
    ) -> list[str]:
        out = list(texts)  # blank items pass through untouched
        pieces: list[str] = []
        owners: list[tuple[int, str]] = []  # (text index, separator after the piece)
        for i, text in enumerate(texts):
            if not text.strip():
                continue
            if self.use_glossary and glossary:
                text = apply_glossary(text, glossary, src)  # pre-substitute fixed names/terms
            for piece, sep in split_long_text(text, self.max_chars):
                pieces.append(piece)
                owners.append((i, sep))
        if not pieces:
            return out

        translated = [""] * len(pieces)
        for chunk in plan_chunks([len(p) for p in pieces], self.max_items, self.max_chars):
            results = await self._translate_chunk([pieces[j] for j in chunk], src, tgt)
            for j, r in zip(chunk, results):
                translated[j] = r

        grouped: dict[int, list[tuple[str, str]]] = {}
        for (i, sep), tr in zip(owners, translated):
            grouped.setdefault(i, []).append((tr, sep))
        for i, parts in grouped.items():
            out[i] = parts[0][0] if len(parts) == 1 else "".join(tr.strip() + sep for tr, sep in parts)
        return out

    async def _translate_chunk(self, chunk: list[str], src: str, tgt: str) -> list[str]:
        params = {"client": "gtx", "sl": src, "tl": tgt, "dt": "t"}
        chars = sum(len(t) for t in chunk)
        self.stats.items += len(chunk)
        self.stats.chars += chars
        payload, log = await self._send("batch", self.batch_url, params=params, data={"q": chunk},
                                        items=len(chunk), chars=chars)
        try:
            results = parse_batch_response(payload, expected=len(chunk)) if payload is not None else None
        except ValueError:
            results = None
        if results is None or len(results) != len(chunk):
            log.mismatch = True
            log.error = f"count mismatch: got {None if results is None else len(results)} for {len(chunk)}"
            self.stats.mismatches += 1
            self._emit(log)
            return [await self._translate_single(t, src, tgt) for t in chunk]
        self._emit(log)
        return results

    async def _translate_single(self, text: str, src: str, tgt: str) -> str:
        self.stats.fallback_items += 1
        data = {"client": "gtx", "sl": src, "tl": tgt, "dt": "t", "q": text}
        payload, log = await self._send("single", self.single_url, params=None, data=data, items=1, chars=len(text))
        try:
            result = parse_single_response(payload)
        except ValueError:
            log.error = "unparseable single response"
            self._emit(log)
            raise TranslationError("gtx single: unexpected response shape", retryable=True) from None
        self._emit(log)
        return result

    async def _send(
        self, kind: str, url: str, *, params: dict | None, data: dict, items: int, chars: int
    ) -> tuple[Any, GtxRequestLog]:
        """POST with throttling and retries. Returns (parsed JSON or None if not JSON, log of the
        successful attempt, not yet emitted). Raises TranslationError when attempts run out."""
        last_error = ""
        for attempt in range(self.max_retries + 1):
            await self._bucket.acquire()
            self.stats.requests += 1
            log = GtxRequestLog(kind, None, 0.0, items, chars, attempt)
            retry_after = None
            t0 = time.perf_counter()
            try:
                resp = await self._client.post(url, params=params, data=data)
            except httpx.TimeoutException as exc:
                last_error = f"timeout ({type(exc).__name__})"
            except httpx.TransportError as exc:
                last_error = f"network error ({type(exc).__name__}: {exc})"
            else:
                log.status = resp.status_code
                log.latency_s = time.perf_counter() - t0
                status = resp.status_code
                if status == 200:
                    try:
                        payload = resp.json()
                    except ValueError:
                        payload = None
                    if payload is not None or not any(m in resp.text for m in _BLOCKED_MARKERS):
                        self.stats.ok += 1
                        return payload, log
                    last_error = "blocked (HTTP 200 sorry page)"
                    self.stats.rate_limited += 1
                elif status == 429 or 300 <= status < 400:
                    last_error = f"rate limited (HTTP {status})"
                    self.stats.rate_limited += 1
                    retry_after = parse_retry_after(resp.headers.get("retry-after"))
                elif status >= 500:
                    last_error = f"server error (HTTP {status})"
                else:
                    self.stats.errors += 1
                    log.error = f"HTTP {status}"
                    self._emit(log)
                    raise TranslationError(f"gtx {kind}: HTTP {status}", retryable=False)
            if not log.latency_s:
                log.latency_s = time.perf_counter() - t0
            log.error = last_error
            self.stats.errors += 1
            self._emit(log)
            if attempt < self.max_retries:
                self.stats.retries += 1
                await asyncio.sleep(backoff_delay(attempt, self.backoff_base, self.backoff_max, retry_after))
        raise TranslationError(f"gtx {kind} failed after {self.max_retries + 1} attempts: {last_error}",
                               retryable=True)

    def _emit(self, log: GtxRequestLog) -> None:
        if self.on_request is not None:
            self.on_request(log)

    async def aclose(self) -> None:
        await self._client.aclose()
