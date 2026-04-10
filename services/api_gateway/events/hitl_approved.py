import json
import uuid
from datetime import datetime, timezone
from typing import Optional

import aio_pika
from pydantic import BaseModel

from config.logging_config import get_logger
from services.api_gateway.config import get_rabbitmq_url

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
        *,
        task_id: str,
        project_id: str,
        approved_by: str,
        approved_at: str,
        auto_deployed: bool = True,
        notes: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> "HITLApprovedEvent":
        return cls(
            event_id=str(uuid.uuid4()),
            produced_at=datetime.now(timezone.utc).isoformat(),
            payload=HITLApprovedPayload(
                task_id=task_id,
                project_id=project_id,
                approved_by=approved_by,
                approved_at=approved_at,
                auto_deployed=auto_deployed,
                notes=notes,
                correlation_id=correlation_id,
            ),
        )

    def to_bytes(self) -> bytes:
        return json.dumps(self.dict(), ensure_ascii=False).encode("utf-8")


async def publish_hitl_approved_event(
    *,
    task_id: str,
    project_id: str,
    approved_by: str,
    approved_at: str,
    auto_deployed: bool = True,
    notes: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> None:
    event = HITLApprovedEvent.build(
        task_id=task_id,
        project_id=project_id,
        approved_by=approved_by,
        approved_at=approved_at,
        auto_deployed=auto_deployed,
        notes=notes,
        correlation_id=correlation_id,
    )
    rabbitmq_url = get_rabbitmq_url()
    conn = await aio_pika.connect_robust(rabbitmq_url)
    async with conn:
        ch = await conn.channel()
        ex = await ch.declare_exchange(EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True)
        msg = aio_pika.Message(
            body=event.to_bytes(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers={
                "event_id": event.event_id,
                "event_type": event.event_name,
                **({"correlation_id": correlation_id} if correlation_id else {}),
            },
        )
        try:
            await ex.publish(msg, routing_key=ROUTING_KEY)
        except Exception as exc:
            logger.error(
                "Failed to publish HITLApproved event from API Gateway",
                extra={"task_id": task_id, "project_id": project_id, "correlation_id": correlation_id, "error": str(exc)},
            )
            raise
