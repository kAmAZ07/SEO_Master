"""Точка входа в LLM-слой: провайдеры из настроек, кэш Redis и политика с общим бюджетом.

Прежний `generate_drafts` и шаблонный `_fallback_drafts` («Официальная страница», «Заголовок
страницы») удалены: шаблонная заглушка не является готовым к публикации предложением (ТЗ §9.3).
Преждевременный fallback при первом неуспешном ответе и утечку задач (`asyncio.wait` с
`FIRST_COMPLETED`) заменяет `llm.policy.run_policy`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from services.semantic_service.config import settings
from services.semantic_service.llm.llm_cache import RedisResponseCache, open_cache
from services.semantic_service.llm.policy import Budget
from services.semantic_service.llm.providers import configured_providers
from services.semantic_service.llm.types import LlmProvider, ResponseCache


@dataclass
class LlmRuntime:
    """Всё, что нужно генератору предложений от LLM-слоя; подменяется заглушками в тестах."""

    providers: Sequence[LlmProvider]
    cache: ResponseCache
    budget: Budget

    async def aclose(self) -> None:
        if isinstance(self.cache, RedisResponseCache):
            await self.cache.close()


def open_runtime(deadline_s: float = 30.0) -> LlmRuntime:
    """Рабочая конфигурация из настроек сервиса. Без ключей провайдеров список пуст — тогда
    генератор использует только детерминированный путь по фактам страницы."""
    return LlmRuntime(
        providers=configured_providers(),
        cache=open_cache(settings.redis_url),
        budget=Budget(
            simple_timeout_s=settings.llm_timeout_s_simple,
            complex_timeout_s=settings.llm_timeout_s_complex,
            deadline_s=deadline_s,
        ),
    )
