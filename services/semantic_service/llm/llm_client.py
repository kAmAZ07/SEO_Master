import asyncio
import json
from datetime import datetime, timezone
import httpx
from redis.asyncio import Redis

from services.semantic_service.config import settings
from services.semantic_service.llm.prompt_templates import TITLE_PROMPT, DESCRIPTION_PROMPT, H1_PROMPT, SCHEMA_PROMPT
from services.semantic_service.llm.llm_cache import get_cached, set_cached


def _fallback_drafts(root_url: str) -> dict:
    base = root_url.rstrip("/")
    return {
        "title": f"{base} — Официальная страница",
        "description": "Краткое описание страницы. Уточните уникальное предложение и ключевую пользу для пользователя.",
        "h1": "Заголовок страницы",
        "schema_jsonld": {"@context": "https://schema.org", "@type": "WebPage", "url": base},
        "provider": "fallback",
        "model": "template",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def _openai_chat(prompt: str, content: str, timeout_s: float) -> str | None:
    if not settings.openai_api_key:
        return None
    url = settings.openai_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
    payload = {
        "model": settings.openai_model,
        "messages": [
            {"role": "system", "content": "You are a helpful SEO assistant."},
            {"role": "user", "content": prompt + "\n\nCONTENT:\n" + content},
        ],
        "temperature": 0.4,
    }
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        r = await client.post(url, json=payload, headers=headers)
        if r.status_code >= 400:
            return None
        j = r.json()
        c = (((j.get("choices") or [{}])[0]).get("message") or {}).get("content")
        if isinstance(c, str) and c.strip():
            return c.strip()
        return None


async def _gemini_generate(prompt: str, content: str, timeout_s: float) -> str | None:
    if not settings.gemini_api_key:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent"
    params = {"key": settings.gemini_api_key}
    payload = {
        "contents": [{"parts": [{"text": prompt + "\n\nCONTENT:\n" + content}]}],
        "generationConfig": {"temperature": 0.4},
    }
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        r = await client.post(url, params=params, json=payload)
        if r.status_code >= 400:
            return None
        j = r.json()
        cands = j.get("candidates") or []
        if not cands:
            return None
        parts = (((cands[0] or {}).get("content") or {}).get("parts")) or []
        texts = [p.get("text") for p in parts if isinstance(p.get("text"), str)]
        out = "\n".join([t.strip() for t in texts if t and t.strip()]).strip()
        return out or None


async def _call_provider(provider: str, prompt: str, content: str, timeout_s: float) -> tuple[str, str | None]:
    if provider == "openai":
        return "openai", await _openai_chat(prompt, content, timeout_s)
    if provider == "gemini":
        return "gemini", await _gemini_generate(prompt, content, timeout_s)
    return provider, None


async def _best_effort_text(redis: Redis | None, prompt: str, content: str, timeout_s: float) -> tuple[str, str | None, str | None]:
    for provider, model in (("openai", settings.openai_model), ("gemini", settings.gemini_model)):
        cached = await get_cached(redis, provider, model, prompt, content)
        if cached and isinstance(cached.get("text"), str):
            return provider, model, cached["text"]

    tasks = []
    if settings.openai_api_key:
        tasks.append(_call_provider("openai", prompt, content, timeout_s))
    if settings.gemini_api_key:
        tasks.append(_call_provider("gemini", prompt, content, timeout_s))

    if not tasks:
        return "fallback", None, None

    done, _ = await asyncio.wait([asyncio.create_task(t) for t in tasks], timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED)
    for d in done:
        provider, text = d.result()
        model = settings.openai_model if provider == "openai" else settings.gemini_model
        if isinstance(text, str) and text.strip():
            await set_cached(redis, provider, model, prompt, content, {"text": text.strip()})
            return provider, model, text.strip()

    return "fallback", None, None


async def generate_drafts(root_url: str, content: str) -> dict:
    redis = None
    if settings.redis_url:
        try:
            redis = Redis.from_url(settings.redis_url, decode_responses=False)
        except Exception:
            redis = None

    try:
        provider_t, model_t, title = await _best_effort_text(redis, TITLE_PROMPT, content, settings.llm_timeout_s_simple)
        provider_d, model_d, desc = await _best_effort_text(redis, DESCRIPTION_PROMPT, content, settings.llm_timeout_s_simple)
        provider_h, model_h, h1 = await _best_effort_text(redis, H1_PROMPT, content, settings.llm_timeout_s_simple)
        provider_s, model_s, schema = await _best_effort_text(redis, SCHEMA_PROMPT, content, settings.llm_timeout_s_complex)

        if not title or not desc or not h1:
            return _fallback_drafts(root_url)

        schema_obj = None
        if schema:
            try:
                schema_obj = json.loads(schema)
            except Exception:
                schema_obj = {"@context": "https://schema.org", "@type": "WebPage", "url": root_url.rstrip("/")}

        return {
            "title": title,
            "description": desc,
            "h1": h1,
            "schema_jsonld": schema_obj,
            "provider": provider_t if provider_t != "fallback" else (provider_d if provider_d != "fallback" else provider_h),
            "model": model_t or model_d or model_h or "unknown",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:
                pass