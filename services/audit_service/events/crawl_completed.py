import json
import uuid
from datetime import datetime, timezone

import aio_pika
from pydantic import BaseModel

from services.audit_service.config import settings


EXCHANGE_NAME = "seo_master.events"
ROUTING_KEY = "audit.crawl.completed"


class CrawlCompletedPayload(BaseModel):
    audit_id: str
    project_id: str | None = None
    root_url: str
    mode: str
    summary: dict
    pages: list[dict] = []
    content_text: str = ""


class CrawlCompletedEvent(BaseModel):
    event_id: str
    event_name: str = "CrawlCompleted"
    produced_at: str
    payload: CrawlCompletedPayload

    @classmethod
    def build(
        cls,
        *,
        audit_id: str,
        project_id: str | None,
        root_url: str,
        mode: str,
        summary: dict,
        pages: list[dict],
        content_text: str,
    ) -> "CrawlCompletedEvent":
        return cls(
            event_id=str(uuid.uuid4()),
            produced_at=datetime.now(timezone.utc).isoformat(),
            payload=CrawlCompletedPayload(
                audit_id=audit_id,
                project_id=project_id,
                root_url=root_url,
                mode=mode,
                summary=summary,
                pages=pages,
                content_text=content_text,
            ),
        )

    def to_bytes(self) -> bytes:
        return json.dumps(self.model_dump(), ensure_ascii=False).encode("utf-8")


async def publish_crawl_completed(
    *,
    audit_id: str,
    project_id: str | None,
    root_url: str,
    mode: str,
    summary: dict,
    pages: list[dict],
    content_text: str,
) -> None:
    if not settings.rabbitmq_url:
        return

    event = CrawlCompletedEvent.build(
        audit_id=audit_id,
        project_id=project_id,
        root_url=root_url,
        mode=mode,
        summary=summary,
        pages=pages,
        content_text=content_text,
    )
    headers = {
        "event_type": event.event_name,
        "event_id": event.event_id,
    }

    conn = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with conn:
        ch = await conn.channel()
        ex = await ch.declare_exchange(EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True)
        msg = aio_pika.Message(
            body=event.to_bytes(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers=headers,
        )
        await ex.publish(msg, routing_key=ROUTING_KEY)
