"""Кэш ответов LLM: ключ с tenant scope и версиями, TTL 7 дней, отказ Redis не ломает работу.

Ключ включает (ТЗ §9.3): tenant scope, хеш входных данных (evidence/текст), провайдера и модель,
версии prompt / schema / validator и параметры запроса. Смена любой версии даёт другой ключ, поэтому
устаревший ответ не переживает смену validator. В кэш кладётся только текст, уже принятый validator,
а при чтении он проходит validator повторно (кэш не обходит проверку).

Без tenant scope кэш не используется: общий ключ позволил бы одному проекту получить ответ,
построенный на данных другого.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from services.semantic_service.config import settings

KEY_VERSION = "v2"
CACHE_OP_TIMEOUT_S = 0.5


@dataclass(frozen=True)
class CacheKeyParts:
    scope: str
    input_hash: str
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    validator_version: str
    params: str  # канонический JSON параметров (kind, temperature, max_output_tokens, ...)


def build_cache_key(parts: CacheKeyParts) -> str:
    if not parts.scope:
        raise ValueError("cache_scope_required")
    canonical = json.dumps(asdict(parts), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"semantic:llm:{KEY_VERSION}:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def hash_input(*values: str) -> str:
    digest = hashlib.sha256()
    for value in values:
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


class RedisResponseCache:
    """Тонкая оболочка над redis.asyncio.Redis. Любая ошибка — промах, а не исключение."""

    def __init__(self, redis: Any, ttl_seconds: int | None = None):
        self._redis = redis
        self._ttl = ttl_seconds if ttl_seconds is not None else settings.llm_cache_ttl_seconds

    async def get(self, key: str) -> dict | None:
        if self._redis is None:
            return None
        try:
            raw = await asyncio.wait_for(self._redis.get(key), CACHE_OP_TIMEOUT_S)
            if not raw:
                return None
            value = json.loads(raw)
            return value if isinstance(value, dict) else None
        except Exception:  # noqa: BLE001 - недоступный или испорченный кэш не должен ломать безопасный путь
            return None

    async def set(self, key: str, value: dict) -> None:
        if self._redis is None:
            return
        try:
            payload = json.dumps(value, ensure_ascii=False)
            await asyncio.wait_for(self._redis.set(key, payload, ex=self._ttl), CACHE_OP_TIMEOUT_S)
        except Exception:  # noqa: BLE001
            return

    async def close(self) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.aclose()
        except Exception:  # noqa: BLE001
            return


def open_cache(redis_url: str | None) -> RedisResponseCache:
    """Кэш поверх Redis либо пустой (всегда промах), если Redis не настроен или не создаётся."""
    if not redis_url:
        return RedisResponseCache(None)
    try:
        from redis.asyncio import Redis

        return RedisResponseCache(Redis.from_url(redis_url, decode_responses=False))
    except Exception:  # noqa: BLE001
        return RedisResponseCache(None)
