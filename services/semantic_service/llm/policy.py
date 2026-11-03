"""Политика обращения к LLM: кэш, затем провайдеры по очереди в общем бюджете.

Что исправлено относительно прежнего `llm_client.py`:
  * прежний код запускал всех провайдеров параллельно и брал первый *завершившийся* — даже если он
    вернул `None`, после чего сразу включался шаблонный fallback, а остальные задачи не отменялись и
    не дожидались (утечка pending tasks). Теперь провайдеры вызываются последовательно, ответ
    принимается только если он прошёл validator, иначе берётся следующий провайдер;
  * сбой одного провайдера (исключение, таймаут, мусор) изолирован и записан в журнал попыток;
  * таймаут вызова отменяет и дожидается задачу (`asyncio.wait_for`), ничего не остаётся «висеть»;
  * таймаут провайдера — 5 с для простых запросов и 15 с для сложных, но не больше остатка общего
    дедлайна задания;
  * попыток не больше одной на провайдера; общий дедлайн не продлевается.

Отмена вызывающей задачи (`CancelledError`) пробрасывается: политика её не проглатывает.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Callable, Sequence

from services.semantic_service.llm.llm_cache import CacheKeyParts, build_cache_key
from services.semantic_service.llm.types import (
    COMPLEX,
    Attempt,
    LlmOutcome,
    LlmProvider,
    LlmRequest,
    ProviderError,
    ResponseCache,
)

Accept = Callable[[str], object | None]
Clock = Callable[[], float]


@dataclass(frozen=True)
class Budget:
    """Бюджет одного запроса. `deadline_s` — остаток общего дедлайна задания."""

    simple_timeout_s: float = 5.0
    complex_timeout_s: float = 15.0
    deadline_s: float = 30.0

    def provider_timeout(self, complexity: str) -> float:
        return self.complex_timeout_s if complexity == COMPLEX else self.simple_timeout_s


@dataclass(frozen=True)
class CacheContext:
    """Всё, из чего строится ключ кэша, кроме провайдера и модели."""

    cache: ResponseCache
    scope: str | None
    input_hash: str
    prompt_version: str
    schema_version: str
    validator_version: str

    def key(self, provider: LlmProvider, request: LlmRequest) -> str:
        params = f"kind={request.kind};complexity={request.complexity};max_tokens={request.max_output_tokens};temperature=0.2"
        return build_cache_key(
            CacheKeyParts(
                scope=self.scope or "",
                input_hash=self.input_hash,
                provider=provider.name,
                model=provider.model,
                prompt_version=self.prompt_version,
                schema_version=self.schema_version,
                validator_version=self.validator_version,
                params=params,
            )
        )


def _elapsed_ms(started: float, clock: Clock) -> int:
    return max(0, int((clock() - started) * 1000))


async def _from_cache(
    providers: Sequence[LlmProvider], request: LlmRequest, accept: Accept, ctx: CacheContext, outcome: LlmOutcome,
    clock: Clock,
) -> bool:
    for provider in providers:
        started = clock()
        cached = await _cache_get(ctx, provider, request)
        text = cached.get("text") if isinstance(cached, dict) else None
        if not isinstance(text, str):
            continue
        value = _safe_accept(accept, text)
        if value is None:
            # В кэше лежит то, что текущий validator не принимает: это промах, а не результат.
            outcome.attempts.append(Attempt(provider.name, provider.model, "invalid", "cached_value_rejected", _elapsed_ms(started, clock)))
            continue
        outcome.value, outcome.text = value, text
        outcome.provider, outcome.model, outcome.source = provider.name, provider.model, "cache"
        outcome.attempts.append(Attempt(provider.name, provider.model, "cache_hit", None, _elapsed_ms(started, clock)))
        return True
    return False


async def _cache_get(ctx: CacheContext, provider: LlmProvider, request: LlmRequest) -> dict | None:
    try:
        return await ctx.cache.get(ctx.key(provider, request))
    except Exception:  # noqa: BLE001 - отказ кэша = промах, безопасный путь продолжается
        return None


async def _cache_set(ctx: CacheContext, provider: LlmProvider, request: LlmRequest, text: str) -> None:
    try:
        await ctx.cache.set(ctx.key(provider, request), {"text": text, "provider": provider.name, "model": provider.model})
    except Exception:  # noqa: BLE001
        return


def _safe_accept(accept: Accept, text: str) -> object | None:
    try:
        return accept(text)
    except Exception:  # noqa: BLE001 - validator не должен ронять запрос; непонятный ответ = отказ
        return None


async def run_policy(
    providers: Sequence[LlmProvider],
    request: LlmRequest,
    accept: Accept,
    budget: Budget,
    *,
    cache: CacheContext | None = None,
    clock: Clock = time.monotonic,
) -> LlmOutcome:
    """Возвращает первый ответ, принятый `accept`; иначе `LlmOutcome` без значения и с журналом попыток.

    `accept(text)` возвращает разобранное значение либо `None` (ответ не годится).
    Использовать fallback решает вызывающий код, видя `outcome.ok`.
    """
    outcome = LlmOutcome()
    started_at = clock()

    use_cache = cache is not None and bool(cache.scope)
    if use_cache and await _from_cache(providers, request, accept, cache, outcome, clock):  # type: ignore[arg-type]
        return outcome

    for provider in providers:
        remaining = budget.deadline_s - (clock() - started_at)
        if remaining <= 0:
            outcome.attempts.append(Attempt(provider.name, provider.model, "skipped_deadline"))
            continue
        timeout_s = min(budget.provider_timeout(request.complexity), remaining)
        call_started = clock()
        try:
            text = await asyncio.wait_for(
                provider.complete(
                    request.prompt, request.content, timeout_s=timeout_s, max_output_tokens=request.max_output_tokens
                ),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            outcome.attempts.append(Attempt(provider.name, provider.model, "timeout", "timeout", _elapsed_ms(call_started, clock)))
            continue
        except ProviderError as exc:
            outcome.attempts.append(Attempt(provider.name, provider.model, "error", exc.code, _elapsed_ms(call_started, clock)))
            continue
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - любой сбой провайдера изолирован; текст исключения не сохраняется
            outcome.attempts.append(Attempt(provider.name, provider.model, "error", "provider_exception", _elapsed_ms(call_started, clock)))
            continue

        if not isinstance(text, str) or not text.strip():
            outcome.attempts.append(Attempt(provider.name, provider.model, "invalid", "empty_response", _elapsed_ms(call_started, clock)))
            continue
        value = _safe_accept(accept, text)
        if value is None:
            outcome.attempts.append(Attempt(provider.name, provider.model, "invalid", "rejected_by_validator", _elapsed_ms(call_started, clock)))
            continue

        outcome.value, outcome.text = value, text
        outcome.provider, outcome.model, outcome.source = provider.name, provider.model, "llm"
        outcome.attempts.append(Attempt(provider.name, provider.model, "ok", None, _elapsed_ms(call_started, clock)))
        if use_cache:
            await _cache_set(cache, provider, request, text)  # type: ignore[arg-type]
        return outcome

    return outcome
