"""Translator engine interface. Language codes are ISO 639-1: "en", "ja", "vi"."""
from __future__ import annotations

from abc import ABC, abstractmethod


class TranslationError(Exception):
    """Engine failed (network, quota, malformed response). `retryable` hints backoff vs give up."""

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class TranslatorEngine(ABC):
    name: str = "base"
    model: str = ""  # part of cache keys; change it when the model changes
    quality_tier: int = 1  # 1 = MT, 2 = LLM, 3 = human (see translate.cache); better tiers win

    @abstractmethod
    async def translate_batch(
        self,
        texts: list[str],
        src: str,
        tgt: str,
        *,
        context: list[str] | None = None,
        glossary: dict[str, str] | None = None,
    ) -> list[str]:
        """Translate `texts` in order. The result always has len(texts) items or raises.

        `context` holds recent source lines (oldest first) for engines that can use it;
        `glossary` maps source terms to fixed translations. Engines may ignore both.
        """

    async def aclose(self) -> None:
        """Close network clients."""
