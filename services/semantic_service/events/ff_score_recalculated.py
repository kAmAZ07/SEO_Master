import json
import uuid
from datetime import datetime, timezone

import aio_pika
from pydantic import BaseModel

from services.semantic_service.config import settings


class FFScoreRecalculatedPayload(BaseModel):
    project_id: str | None
    root_url: str
    ff_score_id: str
    ff_score: float
    components: dict
    inputs: dict
    eeat: dict


class FFScoreRecalculatedEvent(BaseModel):
    event_id: str
    event_name: str = "FFScoreRecalculated"
    produced_at: str
    correlation_id: str | None = None
    payload: FFScoreRecalculatedPayload

    @classmethod
    def build(
        cls,
        project_id: str | None,
        root_url: str,
        ff_score_id: str,
        ff_score: float,
        components: dict,
        inputs: dict | None = None,
        eeat: dict | None = None,
        correlation_id: str | None = None,
    ) -> "FFScoreRecalculatedEvent":
        return cls(
            event_id=str(uuid.uuid4()),
            produced_at=datetime.now(timezone.utc).isoformat(),
            correlation_id=correlation_id,
            payload=FFScoreRecalculatedPayload(
                project_id=project_id,
                root_url=root_url,
                ff_score_id=ff_score_id,
                ff_score=ff_score,
                components=components,
                inputs=inputs or {},
                eeat=eeat or {},
            ),
        )

    def to_bytes(self) -> bytes:
        return json.dumps(self.model_dump(), ensure_ascii=False).encode("utf-8")


async def publish_ffscore_recalculated(
    project_id: str | None,
    root_url: str,
    ff_score_id: str,
    ff_score: float,
    components: dict,
    *,
    inputs: dict | None = None,
    eeat: dict | None = None,
    correlation_id: str | None = None,
) -> None:
    if not settings.rabbitmq_url:
        return
    ev = FFScoreRecalculatedEvent.build(
        project_id,
        root_url,
        ff_score_id,
        ff_score,
        components,
        inputs=inputs,
        eeat=eeat,
        correlation_id=correlation_id,
    )
    conn = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with conn:
        ch = await conn.channel()
        ex = await ch.declare_exchange("seo_master.events", aio_pika.ExchangeType.TOPIC, durable=True)
        msg = aio_pika.Message(
            body=ev.to_bytes(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers={"event_id": ev.event_id, "event_type": ev.event_name},
        )
        await ex.publish(msg, routing_key="semantic.ffscore.recalculated")
