import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import aio_pika
from pydantic import BaseModel, Field

from services.management_service.config import settings
from config.logging_config import get_logger

logger = get_logger(__name__)

EXCHANGE_NAME = "seo_master.events"
ROUTING_KEY = "management.task.created"


class TaskCreatedPayload(BaseModel):
    task_id: str
    project_id: str
    task_type: str
    url: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    correlation_id: Optional[str] = None


class TaskCreatedEvent(BaseModel):
    event_id: str
    event_name: str = "TaskCreated"
    produced_at: str
    payload: TaskCreatedPayload

    @classmethod
    def build(
        cls,
        task_id: str,
        project_id: str,
        task_type: str,
        url: str,
        metadata: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> "TaskCreatedEvent":
        payload = TaskCreatedPayload(
            task_id=task_id,
            project_id=project_id,
            task_type=task_type,
            url=url,
            metadata=metadata or {},
            correlation_id=correlation_id,
        )
        return cls(
            event_id=str(uuid.uuid4()),
            produced_at=datetime.now(timezone.utc).isoformat(),
            payload=payload,
        )

    def to_bytes(self) -> bytes:
        return json.dumps(self.model_dump(), ensure_ascii=False).encode("utf-8")


async def publish_task_created_event(
    db,
    task_id: str,
    project_id: str,
    task_type,
    url: str,
    metadata: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
) -> None:
    del db

    if not settings.rabbitmq_url:
        return

    task_type_value = task_type.value if hasattr(task_type, "value") else str(task_type)
    event = TaskCreatedEvent.build(
        task_id=task_id,
        project_id=project_id,
        task_type=task_type_value,
        url=url,
        metadata=metadata,
        correlation_id=correlation_id,
    )

    headers = {
        "event_type": event.event_name,
        "event_id": event.event_id,
    }
    if correlation_id:
        headers["correlation_id"] = correlation_id

    try:
        conn = await aio_pika.connect_robust(settings.rabbitmq_url)
        async with conn:
            ch = await conn.channel()
            ex = await ch.declare_exchange(
                EXCHANGE_NAME,
                aio_pika.ExchangeType.TOPIC,
                durable=True,
            )
            msg = aio_pika.Message(
                body=event.to_bytes(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                headers=headers,
            )
            await ex.publish(msg, routing_key=ROUTING_KEY)
    except Exception as exc:
        logger.error(
            f"Failed to publish TaskCreated event: {exc}",
            extra={"task_id": task_id, "correlation_id": correlation_id},
        )
        raise
