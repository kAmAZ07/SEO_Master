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
ROUTING_KEY = "hitl.approved"


class HITLApprovedPayload(BaseModel):
    task_id: str
    project_id: str
    approved_by: str
    approved_at: str
    auto_deployed: bool = True
    notes: Optional[str] = None
    correlation_id: Optional[str] = None


class HITLApprovedEvent(BaseModel):
    event_id: str
    event_name: str = "HITLApproved"
    produced_at: str
    payload: HITLApprovedPayload

    @classmethod
    def build(
        cls,
        task_id: str,
        project_id: str,
        approved_by: str,
        auto_deployed: bool = True,
        notes: Optional[str] = None,
        correlation_id: Optional[str] = None,
        approved_at: Optional[str] = None,
    ) -> "HITLApprovedEvent":
        approved_at_value = approved_at or datetime.now(timezone.utc).isoformat()
        payload = HITLApprovedPayload(
            task_id=task_id,
            project_id=project_id,
            approved_by=approved_by,
            approved_at=approved_at_value,
            auto_deployed=auto_deployed,
            notes=notes,
            correlation_id=correlation_id,
        )
        return cls(
            event_id=str(uuid.uuid4()),
            produced_at=datetime.now(timezone.utc).isoformat(),
            payload=payload,
        )

    def to_bytes(self) -> bytes:
        return json.dumps(self.model_dump(), ensure_ascii=False).encode("utf-8")


async def publish_hitl_approved_event(
    db,
    task_id: str,
    project_id: str,
    approved_by: str,
    auto_deployed: bool = True,
    correlation_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> None:
    del db

    if not settings.rabbitmq_url:
        return

    event = HITLApprovedEvent.build(
        task_id=task_id,
        project_id=project_id,
        approved_by=approved_by,
        auto_deployed=auto_deployed,
        notes=notes,
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
            f"Failed to publish HITLApproved event: {exc}",
            extra={"task_id": task_id, "correlation_id": correlation_id},
        )
        raise
