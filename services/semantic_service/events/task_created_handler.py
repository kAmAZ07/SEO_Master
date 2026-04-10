import asyncio
import json
from datetime import datetime, timezone

import aio_pika
from sqlalchemy import select

from services.semantic_service.config import settings
from services.semantic_service.db.models import ContentDraftRow
from services.semantic_service.db.session import get_session


def _extract_payload(message: dict) -> dict:
    payload = message.get("payload")
    if isinstance(payload, dict):
        return payload
    return message


def _build_interlink_draft(metadata: dict) -> dict:
    interlinks = metadata.get("interlinks") or []
    normalized = []
    for item in interlinks[:25]:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "target_url": item.get("target_url"),
                "anchor_text": item.get("anchor_text"),
                "context": item.get("context"),
                "position": item.get("position"),
                "impact_score": item.get("impact_score"),
            }
        )
    return {
        "draft_type": "interlink_plan",
        "summary": {
            "total_links": metadata.get("total_links", len(normalized)),
            "average_impact_score": metadata.get("average_impact_score"),
            "generated_from_event": True,
        },
        "recommendations": normalized,
    }


async def _handle_message(body: bytes) -> None:
    try:
        event = json.loads(body.decode("utf-8"))
    except Exception:
        return

    if event.get("event_name") not in (None, "TaskCreated"):
        return

    payload = _extract_payload(event)
    task_id = payload.get("task_id")
    project_id = payload.get("project_id")
    root_url = payload.get("url")
    task_type = str(payload.get("task_type") or "")
    metadata = payload.get("metadata") or {}

    if not task_id or not root_url or task_type != "ADD_INTERNAL_LINKS":
        return

    draft_id = f"task-{task_id}"
    draft_payload = {
        "task_id": task_id,
        "task_type": task_type,
        "source_event": "management.task.created",
        "metadata": metadata,
        **_build_interlink_draft(metadata),
    }

    async with get_session() as session:
        existing = await session.execute(select(ContentDraftRow).where(ContentDraftRow.draft_id == draft_id))
        row = existing.scalar_one_or_none()
        if row is None:
            session.add(
                ContentDraftRow(
                    draft_id=draft_id,
                    project_id=project_id,
                    root_url=root_url,
                    created_at=datetime.now(timezone.utc),
                    drafts=draft_payload,
                )
            )
        else:
            row.project_id = project_id
            row.root_url = root_url
            row.drafts = draft_payload
            row.updated_at = datetime.now(timezone.utc)
            session.add(row)
        await session.commit()


async def maybe_start_task_created_consumer() -> None:
    if not settings.rabbitmq_url:
        return

    try:
        conn = await aio_pika.connect_robust(settings.rabbitmq_url)
    except Exception:
        return

    async with conn:
        ch = await conn.channel()
        ex = await ch.declare_exchange("seo_master.events", aio_pika.ExchangeType.TOPIC, durable=True)
        q = await ch.declare_queue("semantic.task_created", durable=True)
        await q.bind(ex, routing_key="management.task.created")

        async with q.iterator() as it:
            async for msg in it:
                async with msg.process(requeue=False):
                    await _handle_message(msg.body)
                await asyncio.sleep(0)
