import json
from datetime import datetime, timezone

import aio_pika
from pydantic import BaseModel

from services.audit_service.config import settings


class PublicAuditCompletedEvent(BaseModel):
    event_name: str = "PublicAuditCompleted"
    audit_id: str
    root_url: str
    produced_at: str
    summary: dict

    @classmethod
    def build(cls, audit_id: str, root_url: str, summary: dict) -> "PublicAuditCompletedEvent":
        return cls(audit_id=audit_id, root_url=root_url, produced_at=datetime.now(timezone.utc).isoformat(), summary=summary)

    def to_bytes(self) -> bytes:
        return json.dumps(self.model_dump(), ensure_ascii=False).encode("utf-8")


async def publish_public_audit_completed(audit_id: str, root_url: str, summary: dict) -> None:
    if not settings.rabbitmq_url:
        return
    ev = PublicAuditCompletedEvent.build(audit_id=audit_id, root_url=root_url, summary=summary)
    conn = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with conn:
        ch = await conn.channel()
        ex = await ch.declare_exchange("seo_master.events", aio_pika.ExchangeType.TOPIC, durable=True)
        msg = aio_pika.Message(body=ev.to_bytes(), content_type="application/json", delivery_mode=aio_pika.DeliveryMode.PERSISTENT)
        await ex.publish(msg, routing_key="audit.public.completed")