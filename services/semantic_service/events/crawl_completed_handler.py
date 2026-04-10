import asyncio
import json

import aio_pika

from services.semantic_service.config import settings
from services.semantic_service.analysis.pipeline import create_semantic_analysis


def _extract_event_payload(message: dict) -> dict:
    payload = message.get("payload")
    if isinstance(payload, dict):
        return payload
    return message


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
    mode = payload.get("mode", "unknown")
    audit_id = payload.get("audit_id")

    if not root_url or not isinstance(root_url, str):
        return

    await create_semantic_analysis(
        project_id=project_id,
        root_url=root_url,
        audit_id=audit_id,
        analysis_id=payload.get("analysis_id"),
        mode=mode,
        content_text=payload.get("content_text"),
        pages=payload.get("pages", []),
        serp_top10_texts=payload.get("serp_top10_texts", []),
        keywords=payload.get("keywords", []),
    )


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
