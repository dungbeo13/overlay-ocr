"""Gemini API engine: one generateContent call per batch, JSON output constrained by a schema.

POST https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent
Header x-goog-api-key (env GEMINI_API_KEY); model from env GEMINI_MODEL.
Items go in as [{"id", "text"}] and must come back as [{"id", "translation"}]; ids missing from
the answer are re-requested once. Docs checked 2026-10: Gemini 3.x takes
generationConfig.thinkingConfig.thinkingLevel ("minimal" is the Flash-Lite default; 3.8 Flash
rejects it), 2.5 takes thinkingBudget (0 = off), and Google strongly recommends leaving
temperature at its default 1.0 on Gemini 3 (lower values can cause looping), so temperature
is only sent for older models unless set explicitly.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any

import httpx

from .base import TranslationError, TranslatorEngine
from .google_gtx import AsyncTokenBucket, MissingApiKeyError, backoff_delay, parse_retry_after

DEFAULT_MODEL = "gemini-3.1-flash-lite"
BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_RETRYABLE = {408, 429, 500, 502, 503, 504}
_BLOCK_FINISH = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}
_AUTO = "auto"

LANG_NAMES = {"en": "English", "ja": "Japanese", "vi": "Vietnamese", "zh": "Chinese", "ko": "Korean"}

SYSTEM_INSTRUCTION = """\
You translate on-screen text from games, manga, video subtitles and apps into Vietnamese.
- Write natural, idiomatic Vietnamese as a native speaker would say it, not word-for-word.
- Use the glossary translations exactly. Keep other personal names as they are (romanize Japanese names).
- Choose Vietnamese pronouns (anh/em, cậu/tớ, mày/tao, chị/em, ông/cháu, tôi/bạn, ...) from who is speaking \
to whom and the tone (friendly, polite, rude), using the context lines, and keep them consistent across items.
- Keep each item's line breaks, numbers, placeholders such as {0} or %s, and symbols.
- Render sound effects and exclamations as natural Vietnamese equivalents; keep UI labels short.
- Context lines are earlier text for reference only; never translate or return them.
- Return exactly one result for every id, with no notes or explanations."""

RESPONSE_SCHEMA = {  # OpenAPI-subset schema for generationConfig.responseSchema
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {"id": {"type": "INTEGER"}, "translation": {"type": "STRING"}},
        "required": ["id", "translation"],
        "propertyOrdering": ["id", "translation"],
    },
}

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")
_ITEM = re.compile(r'\{\s*"id"\s*:\s*"?(\d+)"?\s*,\s*"translation"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}')


def default_thinking(model: str) -> dict | None:
    m = model.lower()
    if m.startswith("gemini-2"):
        return None if "pro" in m else {"thinkingBudget": 0}
    if "flash-lite" in m:
        return {"thinkingLevel": "minimal"}
    return {"thinkingLevel": "low"}  # lowest level every 3.x/4 Flash and Pro accepts


def default_temperature(model: str) -> float | None:
    return 0.2 if model.lower().startswith(("gemini-1", "gemini-2")) else None


def build_prompt(
    items: dict[int, str], src: str, tgt: str, context: list[str] | None, glossary: dict[str, str] | None
) -> str:
    lines = [f"Source language: {LANG_NAMES.get(src, src)}", f"Target language: {LANG_NAMES.get(tgt, tgt)}"]
    if glossary:
        lines.append("Glossary (source => required translation):")
        lines += [f"{k} => {v}" for k, v in glossary.items()]
    if context:
        lines.append("Context (earlier lines, oldest first; do not translate):")
        lines += [f"> {c}" for c in context]
    payload = [{"id": i, "text": t} for i, t in items.items()]
    lines.append("Items to translate (JSON):")
    lines.append(json.dumps(payload, ensure_ascii=False))
    return "\n".join(lines)


def extract_text(data: dict) -> str:
    """Concatenate the non-thought text parts of the first candidate; raise on blocks."""
    block = (data.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise TranslationError(f"gemini blocked the prompt: {block}", retryable=False)
    candidates = data.get("candidates") or []
    if not candidates:
        raise TranslationError("gemini returned no candidates", retryable=True)
    cand = candidates[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
    if not text and cand.get("finishReason") in _BLOCK_FINISH:
        raise TranslationError(f"gemini stopped: {cand.get('finishReason')}", retryable=False)
    return text


def parse_items(text: str) -> dict[int, str]:
    """Map id -> translation from the model's JSON; salvages complete objects from truncated output."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        data = json.loads(cleaned, strict=False)
    except ValueError:
        salvaged: dict[int, str] = {}
        for m in _ITEM.finditer(cleaned):
            try:
                salvaged[int(m.group(1))] = json.loads(f'"{m.group(2)}"', strict=False)
            except ValueError:
                continue
        return salvaged
    if isinstance(data, dict):  # tolerate {"translations": [...]} wrappers
        data = next((v for v in data.values() if isinstance(v, list)), [])
    out: dict[int, str] = {}
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and "id" in item and isinstance(item.get("translation"), str):
            try:
                out[int(item["id"])] = item["translation"]
            except (TypeError, ValueError):
                continue
    return out


class GeminiTranslator(TranslatorEngine):
    name = "gemini"
    quality_tier = 2

    def __init__(
        self,
        key: str | None = None,
        model: str | None = None,
        *,
        temperature: float | None | str = _AUTO,
        thinking_config: dict | None | str = _AUTO,
        max_items: int = 100,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        backoff_max: float = 16.0,
        rpm: float | None = None,
        base_url: str = BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        key = (key or os.environ.get("GEMINI_API_KEY", "")).strip()
        if not key:
            raise MissingApiKeyError("GEMINI_API_KEY")
        self.model = model or os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_MODEL
        self.temperature = default_temperature(self.model) if temperature == _AUTO else temperature
        self.thinking_config = default_thinking(self.model) if thinking_config == _AUTO else thinking_config
        self.url = f"{base_url.rstrip('/')}/models/{self.model}:generateContent"
        self.max_items = max_items
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.stats = {"requests": 0, "retries": 0, "errors": 0, "rerequests": 0,
                      "prompt_tokens": 0, "output_tokens": 0, "thought_tokens": 0}
        self.last_latency_s = 0.0
        self._bucket = AsyncTokenBucket(rpm / 60.0, 1.0) if rpm else None
        self._client = httpx.AsyncClient(
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            timeout=httpx.Timeout(timeout), transport=transport,
        )

    def build_body(self, items: dict[int, str], src: str, tgt: str,
                   context: list[str] | None, glossary: dict[str, str] | None) -> dict:
        gen: dict[str, Any] = {"responseMimeType": "application/json", "responseSchema": RESPONSE_SCHEMA}
        if self.temperature is not None:
            gen["temperature"] = self.temperature
        if self.thinking_config:
            gen["thinkingConfig"] = self.thinking_config
        return {
            "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
            "contents": [{"role": "user", "parts": [{"text": build_prompt(items, src, tgt, context, glossary)}]}],
            "generationConfig": gen,
        }

    async def translate_batch(
        self,
        texts: list[str],
        src: str,
        tgt: str,
        *,
        context: list[str] | None = None,
        glossary: dict[str, str] | None = None,
    ) -> list[str]:
        out = list(texts)
        todo = [i for i, t in enumerate(texts) if t.strip()]
        for start in range(0, len(todo), self.max_items):
            chunk = {i: texts[i] for i in todo[start:start + self.max_items]}
            got = await self._request(chunk, src, tgt, context, glossary)
            missing = {i: t for i, t in chunk.items() if i not in got}
            if missing:
                self.stats["rerequests"] += 1
                got.update(await self._request(missing, src, tgt, context, glossary))
                still = [i for i in chunk if i not in got]
                if still:
                    raise TranslationError(f"gemini omitted {len(still)} of {len(chunk)} items", retryable=True)
            for i in chunk:
                out[i] = got[i]
        return out

    async def _request(self, items: dict[int, str], src: str, tgt: str,
                       context: list[str] | None, glossary: dict[str, str] | None) -> dict[int, str]:
        body = self.build_body(items, src, tgt, context, glossary)
        last_error = ""
        for attempt in range(self.max_retries + 1):
            if self._bucket is not None:
                await self._bucket.acquire()
            self.stats["requests"] += 1
            retry_after = None
            t0 = time.perf_counter()
            try:
                resp = await self._client.post(self.url, json=body)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = type(exc).__name__
            else:
                self.last_latency_s = time.perf_counter() - t0
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                    except ValueError:
                        raise TranslationError("gemini: response is not JSON", retryable=True) from None
                    usage = data.get("usageMetadata") or {}
                    self.stats["prompt_tokens"] += int(usage.get("promptTokenCount", 0))
                    self.stats["output_tokens"] += int(usage.get("candidatesTokenCount", 0))
                    self.stats["thought_tokens"] += int(usage.get("thoughtsTokenCount", 0))
                    got = parse_items(extract_text(data))
                    return {i: t for i, t in got.items() if i in items}
                last_error = _error_message(resp)
                if resp.status_code not in _RETRYABLE:
                    self.stats["errors"] += 1
                    raise TranslationError(f"gemini: {last_error}", retryable=False)
                retry_after = parse_retry_after(resp.headers.get("retry-after"))
            self.stats["errors"] += 1
            if attempt < self.max_retries:
                self.stats["retries"] += 1
                await asyncio.sleep(backoff_delay(attempt, self.backoff_base, self.backoff_max, retry_after))
        raise TranslationError(f"gemini failed after {self.max_retries + 1} attempts: {last_error}", retryable=True)

    async def aclose(self) -> None:
        await self._client.aclose()


def _error_message(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error", {})
        return f"HTTP {resp.status_code} {err.get('status')}: {err.get('message')}"
    except (ValueError, AttributeError):
        return f"HTTP {resp.status_code}"
