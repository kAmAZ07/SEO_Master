import hashlib
import json
from redis.asyncio import Redis
from services.semantic_service.config import settings


def _cache_key(provider: str, model: str, prompt: str, content: str) -> str:
    h = hashlib.sha256((prompt + "\n" + content).encode("utf-8")).hexdigest()
    return f"semantic:llm:{provider}:{model}:{h}"


async def get_cached(redis: Redis | None, provider: str, model: str, prompt: str, content: str) -> dict | None:
    if redis is None:
        return None
    key = _cache_key(provider, model, prompt, content)
    val = await redis.get(key)
    if not val:
        return None
    try:
        return json.loads(val)
    except Exception:
        return None


async def set_cached(redis: Redis | None, provider: str, model: str, prompt: str, content: str, data: dict) -> None:
    if redis is None:
        return
    key = _cache_key(provider, model, prompt, content)
    await redis.set(key, json.dumps(data, ensure_ascii=False), ex=settings.llm_cache_ttl_seconds)