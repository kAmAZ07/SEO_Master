"""Общие типы LLM-слоя: запрос, ошибка провайдера, попытка и итог.

Слой не знает про HTTP-клиенты и Redis: провайдеры и кэш подключаются через протоколы,
поэтому CI использует контрактные заглушки (ТЗ §9.3), а staging — настоящие адаптеры.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

SIMPLE = "simple"
COMPLEX = "complex"


class ProviderError(Exception):
    """Сбой провайдера. `code` стабилен; текст исключений HTTP-клиента наружу не попадает,
    потому что он может содержать URL с ключом."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class LlmRequest:
    kind: str  # title | description | h1 | schema
    prompt: str
    content: str
    complexity: str = SIMPLE
    max_output_tokens: int = 300


@dataclass(frozen=True)
class Attempt:
    provider: str
    model: str
    outcome: str  # cache_hit | ok | timeout | error | invalid | skipped_deadline
    code: str | None = None
    elapsed_ms: int = 0


@dataclass
class LlmOutcome:
    """Итог одного запроса: принятое значение либо `None` с журналом попыток."""

    value: object | None = None
    text: str | None = None
    provider: str | None = None
    model: str | None = None
    source: str | None = None  # "llm" | "cache" | None
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.value is not None


class LlmProvider(Protocol):
    name: str
    model: str

    async def complete(self, prompt: str, content: str, *, timeout_s: float, max_output_tokens: int) -> str:
        """Возвращает непустой текст ответа либо поднимает ProviderError."""
        ...


class ResponseCache(Protocol):
    async def get(self, key: str) -> dict | None: ...

    async def set(self, key: str, value: dict) -> None: ...
