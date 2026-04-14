import asyncio

import aio_pika

from services.semantic_service.config import settings
from services.semantic_service.analysis.pipeline import create_semantic_analysis
from services.semantic_service.db.session import get_session
from services.event_resilience import (
    ResilientConsumerConfig,
    declare_resilient_queue,
    process_resilient_message,
)


def _extract_event_payload(message: dict) -> dict:
    payload = message.get("payload")
    if isinstance(payload, dict):
        return payload
    return message


async def _handle_event(event: dict) -> None:
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
        config = ResilientConsumerConfig(
            consumer_name="semantic.crawl_completed",
            queue_name="semantic.crawl_completed",
            routing_key="audit.crawl.completed",
            redis_url=settings.redis_url,
        )
        q = await declare_resilient_queue(ch, config)

        async with q.iterator() as it:
            async for msg in it:
                await process_resilient_message(
                    msg,
                    config=config,
                    session_factory=get_session,
                    handler=_handle_event,
                    expected_event_names=("CrawlCompleted", None),
                )
                await asyncio.sleep(0)
