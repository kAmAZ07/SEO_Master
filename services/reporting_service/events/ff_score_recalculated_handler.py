import asyncio
from datetime import datetime, timezone
import aio_pika
from sqlalchemy import select

from services.reporting_service.config import settings
from services.reporting_service.db.session import get_session
from services.reporting_service.db.models import MetricsHistoryRow
from services.event_resilience import (
    ResilientConsumerConfig,
    declare_resilient_queue,
    process_resilient_message,
)


async def _handle_event(event: dict) -> None:
    if (event.get("event_name") or event.get("event_type")) != "FFScoreRecalculated":
        return

    payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
    project_id = payload.get("project_id")
    root_url = payload.get("root_url") or ""
    ff_score = payload.get("ff_score")
    ff_score_id = payload.get("ff_score_id")
    components = payload.get("components") or {}
    inputs = payload.get("inputs") or {}
    eeat = payload.get("eeat") or {}
    event_id = event.get("event_id")
    if not event_id:
        raise ValueError("event_id is required for FFScoreRecalculated processing")

    metric_id = str(event_id)

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
                metrics={
                    "ff_score": ff_score,
                    "components": components,
                    "inputs": inputs,
                    "eeat": eeat,
                    "event_id": event_id,
                    "ff_score_id": ff_score_id,
                },
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
        config = ResilientConsumerConfig(
            consumer_name="reporting.ffscore_recalculated",
            queue_name="reporting.ffscore_recalculated",
            routing_key="semantic.ffscore.recalculated",
            redis_url=settings.redis_url,
        )
        q = await declare_resilient_queue(ch, config)

        async with q.iterator() as it:
            async for msg in it:
                await process_resilient_message(
                    msg,
                    config=config,
                    session_factory=get_session,
                    handler=_handle_event,
                    expected_event_names=("FFScoreRecalculated",),
                )
                await asyncio.sleep(0)
