import asyncio
import json
from datetime import datetime, timezone
import aio_pika
from sqlalchemy import select

from services.reporting_service.config import settings
from services.reporting_service.db.session import get_session
from services.reporting_service.db.models import MetricsHistoryRow


async def _handle_message(body: bytes) -> None:
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return

    if payload.get("event_name") != "FFScoreRecalculated":
        return

    project_id = payload.get("project_id")
    root_url = payload.get("root_url") or ""
    ff_score = payload.get("ff_score")
    ff_score_id = payload.get("ff_score_id")
    components = payload.get("components") or {}

    metric_id = f"ff-{ff_score_id or int(datetime.now(timezone.utc).timestamp())}"

    async with get_session() as session:
        existing = await session.execute(select(MetricsHistoryRow).where(MetricsHistoryRow.metric_id == metric_id))
        if existing.scalar_one_or_none() is not None:
            return
        session.add(
            MetricsHistoryRow(
                metric_id=metric_id,
                project_id=project_id,
                root_url=root_url,
                created_at=datetime.now(timezone.utc),
                metrics={"ff_score": ff_score, "components": components},
            )
        )
        await session.commit()


async def maybe_start_ffscore_consumer() -> None:
    if not settings.rabbitmq_url:
        return

    try:
        conn = await aio_pika.connect_robust(settings.rabbitmq_url)
    except Exception:
        return

    async with conn:
        ch = await conn.channel()
        ex = await ch.declare_exchange("seo_master.events", aio_pika.ExchangeType.TOPIC, durable=True)
        q = await ch.declare_queue("reporting.ffscore_recalculated", durable=True)
        await q.bind(ex, routing_key="semantic.ffscore.recalculated")

        async with q.iterator() as it:
            async for msg in it:
                async with msg.process(requeue=False):
                    await _handle_message(msg.body)
                await asyncio.sleep(0)