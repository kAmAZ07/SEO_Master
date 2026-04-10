import asyncio
import json
from datetime import datetime, timezone

import aio_pika

from services.semantic_service.config import settings
from services.semantic_service.db.session import get_session
from services.semantic_service.db.models import SemanticAnalysisRow
from services.semantic_service.analysis.content_gap import analyze_content_gap
from services.semantic_service.analysis.semantic_distance import serp_minus_10_distance
from services.semantic_service.analysis.keyword_coverage import keyword_coverage


def _extract_event_payload(message: dict) -> dict:
    payload = message.get("payload")
    if isinstance(payload, dict):
        return payload
    return message


def _build_target_text(payload: dict) -> str:
    content_text = payload.get("content_text")
    if isinstance(content_text, str) and content_text.strip():
        return content_text.strip()

    parts: list[str] = []
    for page in payload.get("pages", [])[:10]:
        if not isinstance(page, dict):
            continue
        parts.extend(
            [
                str(page.get("title") or ""),
                str(page.get("description") or ""),
                str(page.get("h1") or ""),
            ]
        )

    return "\n".join(part for part in parts if part).strip()


async def _handle_message(body: bytes) -> None:
    try:
        event = json.loads(body.decode("utf-8"))
    except Exception:
        return

    if event.get("event_name") not in (None, "CrawlCompleted"):
        return

    payload = _extract_event_payload(event)
    root_url = payload.get("root_url")
    project_id = payload.get("project_id")
    target_text = _build_target_text(payload)
    serp_texts = payload.get("serp_top10_texts", []) or []
    keywords = payload.get("keywords", []) or []
    mode = payload.get("mode", "unknown")
    audit_id = payload.get("audit_id")

    if not root_url or not isinstance(root_url, str):
        return

    kc = keyword_coverage(target_text, keywords)
    dist = serp_minus_10_distance(target_text, serp_texts)
    gap = analyze_content_gap(target_text, serp_texts, keywords)

    analysis_id = payload.get("analysis_id")
    if not analysis_id or not isinstance(analysis_id, str):
        analysis_id = payload.get("crawl_id") or payload.get("audit_id") or "analysis"
        analysis_id = f"{analysis_id}-{int(datetime.now(timezone.utc).timestamp())}"

    async with get_session() as session:
        session.add(
            SemanticAnalysisRow(
                analysis_id=str(analysis_id),
                project_id=project_id,
                root_url=root_url,
                created_at=datetime.now(timezone.utc),
                content_gap=gap,
                semantic_distance=dist,
                keyword_coverage=kc,
                inputs={
                    "keywords_count": len(keywords),
                    "serp_n": len(serp_texts),
                    "mode": mode,
                    "audit_id": audit_id,
                    "content_length": len(target_text),
                },
            )
        )
        await session.commit()


async def maybe_start_crawl_completed_consumer() -> None:
    if not settings.rabbitmq_url:
        return

    try:
        conn = await aio_pika.connect_robust(settings.rabbitmq_url)
    except Exception:
        return

    async with conn:
        ch = await conn.channel()
        ex = await ch.declare_exchange("seo_master.events", aio_pika.ExchangeType.TOPIC, durable=True)
        q = await ch.declare_queue("semantic.crawl_completed", durable=True)
        await q.bind(ex, routing_key="audit.crawl.completed")

        async with q.iterator() as it:
            async for msg in it:
                async with msg.process(requeue=False):
                    await _handle_message(msg.body)
                await asyncio.sleep(0)
