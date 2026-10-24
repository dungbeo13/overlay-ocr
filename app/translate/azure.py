"""Azure AI Translator v3 engine.

POST {endpoint}/translate?api-version=3.0&from=<src>&to=<tgt>
Headers: Ocp-Apim-Subscription-Key (env AZURE_TRANSLATOR_KEY), Ocp-Apim-Subscription-Region
(env AZURE_TRANSLATOR_REGION; needed for regional/multi-service resources),
Content-Type: application/json; charset=UTF-8, optional X-ClientTraceId.
Body: [{"Text": "..."}]; response: [{"translations": [{"text": "...", "to": "vi"}]}] in input order.
Limits per request: 1000 array elements, 50,000 characters in total.
Glossary terms are sent as dynamic-dictionary markup (requires an explicit `from`).
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any

import httpx

from .base import TranslationError, TranslatorEngine
from .google_gtx import MissingApiKeyError, backoff_delay, parse_retry_after, plan_chunks
from .textnorm import apply_glossary

DEFAULT_ENDPOINT = "https://api.cognitive.microsofttranslator.com"
_RETRYABLE = {408, 429, 500, 502, 503, 504}


def dictionary_markup(term: str, translation: str) -> str:
    safe = translation.replace('"', "'")
    return f'<mstrans:dictionary translation="{safe}">{term}</mstrans:dictionary>'


def build_body(texts: list[str], src: str, glossary: dict[str, str] | None) -> list[dict[str, str]]:
    return [{"Text": apply_glossary(t, glossary, src, dictionary_markup) if glossary else t} for t in texts]


def parse_response(data: Any, expected: int) -> list[str]:
    if not isinstance(data, list) or len(data) != expected:
        got = len(data) if isinstance(data, list) else type(data).__name__
        raise TranslationError(f"azure returned {got} results for {expected} texts", retryable=True)
    try:
        return [str(item["translations"][0]["text"]) for item in data]
    except (KeyError, IndexError, TypeError) as exc:
        raise TranslationError(f"azure response missing translations ({exc!r})", retryable=True) from None


def _error_message(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error", {})
        return f"HTTP {resp.status_code} code {err.get('code')}: {err.get('message')}"
    except (ValueError, AttributeError):
        return f"HTTP {resp.status_code}"


class AzureTranslator(TranslatorEngine):
    name = "azure"
    model = "v3"
    quality_tier = 1

    def __init__(
        self,
        key: str | None = None,
        region: str | None = None,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        max_items: int = 1000,
        max_chars: int = 50_000,
        timeout: float = 15.0,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        backoff_max: float = 16.0,
        use_glossary: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        key = (key or os.environ.get("AZURE_TRANSLATOR_KEY", "")).strip()
        if not key:
            raise MissingApiKeyError("AZURE_TRANSLATOR_KEY")
        region = (region if region is not None else os.environ.get("AZURE_TRANSLATOR_REGION", "")).strip()
        headers = {"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/json; charset=UTF-8"}
        if region:
            headers["Ocp-Apim-Subscription-Region"] = region
        self.url = endpoint.rstrip("/") + "/translate"
        self.max_items = max_items
        self.max_chars = max_chars
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.use_glossary = use_glossary
        self.stats = {"requests": 0, "retries": 0, "errors": 0, "items": 0, "chars": 0, "billed_chars": 0}
        self.last_latency_s = 0.0
        self._client = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(timeout), transport=transport)

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
        if not todo:
            return out
        body = build_body([texts[i] for i in todo], src, glossary if self.use_glossary else None)
        for chunk in plan_chunks([len(b["Text"]) for b in body], self.max_items, self.max_chars):
            results = await self._post([body[j] for j in chunk], src, tgt)
            for j, r in zip(chunk, results):
                out[todo[j]] = r
        return out

    async def _post(self, items: list[dict[str, str]], src: str, tgt: str) -> list[str]:
        params = {"api-version": "3.0", "from": src, "to": tgt}
        last_error = ""
        for attempt in range(self.max_retries + 1):
            self.stats["requests"] += 1
            retry_after = None
            t0 = time.perf_counter()
            try:
                resp = await self._client.post(
                    self.url, params=params, json=items, headers={"X-ClientTraceId": str(uuid.uuid4())}
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"{type(exc).__name__}"
            else:
                self.last_latency_s = time.perf_counter() - t0
                if resp.status_code == 200:
                    self.stats["items"] += len(items)
                    self.stats["chars"] += sum(len(i["Text"]) for i in items)
                    billed = resp.headers.get("x-metered-usage")
                    if billed and billed.isdigit():
                        self.stats["billed_chars"] += int(billed)
                    try:
                        data = resp.json()
                    except ValueError:
                        raise TranslationError("azure: response is not JSON", retryable=True) from None
                    return parse_response(data, len(items))
                last_error = _error_message(resp)
                if resp.status_code not in _RETRYABLE:
                    self.stats["errors"] += 1
                    raise TranslationError(f"azure: {last_error}", retryable=False)
                retry_after = parse_retry_after(resp.headers.get("retry-after"))
            self.stats["errors"] += 1
            if attempt < self.max_retries:
                self.stats["retries"] += 1
                await asyncio.sleep(backoff_delay(attempt, self.backoff_base, self.backoff_max, retry_after))
        raise TranslationError(f"azure failed after {self.max_retries + 1} attempts: {last_error}", retryable=True)

    async def aclose(self) -> None:
        await self._client.aclose()
