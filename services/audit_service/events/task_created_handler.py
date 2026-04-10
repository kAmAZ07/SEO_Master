import asyncio
import json

import aio_pika

from config.logging_config import get_logger
from services.audit_service.config import settings

logger = get_logger(__name__)

SUPPORTED_TASK_TYPES = {
    "UPDATE_META",
    "UPDATE_CONTENT",
    "ADD_INTERNAL_LINKS",
    "UPDATE_SCHEMA",
    "FIX_404",
    "UPDATE_TILDA_PAGE",
    "OPTIMIZE_IMAGES",
    "FIX_BROKEN_LINKS",
}


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

    if event.get("event_name") not in (None, "TaskCreated"):
        return

    payload = _extract_event_payload(event)
    task_id = payload.get("task_id")
    project_id = payload.get("project_id")
    root_url = payload.get("url")
    task_type = str(payload.get("task_type") or "")
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    correlation_id = payload.get("correlation_id")

    if not task_id or not project_id or not root_url or task_type not in SUPPORTED_TASK_TYPES:
        return

    try:
        from services.audit_service.main import queue_full_audit_for_task_created

        result = await queue_full_audit_for_task_created(
            task_id=str(task_id),
            project_id=str(project_id),
            root_url=str(root_url),
            task_type=task_type,
            metadata=metadata,
            correlation_id=correlation_id,
        )
        logger.info(
            "TaskCreated event queued full audit",
            extra={
                "task_id": str(task_id),
                "project_id": str(project_id),
                "audit_id": result.audit_id,
                "status": result.status,
                "correlation_id": correlation_id,
            },
        )
    except Exception as exc:
        logger.error(
            "Failed to handle TaskCreated event in Audit Service",
            extra={"task_id": str(task_id), "project_id": str(project_id), "correlation_id": correlation_id, "error": str(exc)},
            exc_info=True,
        )
        raise


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
        q = await ch.declare_queue("audit.task_created", durable=True)
        await q.bind(ex, routing_key="management.task.created")

        async with q.iterator() as it:
            async for msg in it:
                async with msg.process(requeue=False):
                    await _handle_message(msg.body)
                await asyncio.sleep(0)
