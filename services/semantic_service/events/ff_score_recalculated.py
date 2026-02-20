import json
from datetime import datetime, timezone
import aio_pika
from pydantic import BaseModel
from services.semantic_service.config import settings


class FFScoreRecalculatedEvent(BaseModel):
    event_name: str = "FFScoreRecalculated"
    project_id: str | None
    root_url: str
    ff_score_id: str
    ff_score: float
    components: dict
    produced_at: str

    @classmethod
    def build(cls, project_id: str | None, root_url: str, ff_score_id: str, ff_score: float, components: dict) -> "FFScoreRecalculatedEvent":
        return cls(
            project_id=project_id,
            root_url=root_url,
            ff_score_id=ff_score_id,
            ff_score=ff_score,
            components=components,
            produced_at=datetime.now(timezone.utc).isoformat(),
        )

    def to_bytes(self) -> bytes:
        return json.dumps(self.model_dump(), ensure_ascii=False).encode("utf-8")


async def publish_ffscore_recalculated(project_id: str | None, root_url: str, ff_score_id: str, ff_score: float, components: dict) -> None:
    if not settings.rabbitmq_url:
        return
    ev = FFScoreRecalculatedEvent.build(project_id, root_url, ff_score_id, ff_score, components)
    conn = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with conn:
        ch = await conn.channel()
        ex = await ch.declare_exchange("seo_master.events", aio_pika.ExchangeType.TOPIC, durable=True)
        msg = aio_pika.Message(body=ev.to_bytes(), content_type="application/json", delivery_mode=aio_pika.DeliveryMode.PERSISTENT)
        await ex.publish(msg, routing_key="semantic.ffscore.recalculated")