from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Callable

import aio_pika
from sqlalchemy.orm import Session

from config.logging_config import get_logger
from services.event_resilience import (
    ResilientConsumerConfig,
    declare_resilient_queue,
    process_resilient_message_sync_store,
)
from services.management_service.config import settings
from services.management_service.db.session import SessionLocal
from services.management_service.events.crawl_completed_handler import handle_crawl_completed_event
from services.management_service.events.ff_score_recalculated_handler import handle_ff_score_recalculated_event

logger = get_logger(__name__)

_consumer_tasks: list[asyncio.Task] = []
_connections: list[aio_pika.RobustConnection] = []


def _event_correlation_id(event: dict) -> str | None:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return event.get("correlation_id") or payload.get("correlation_id")


def _handle_crawl_completed(db: Session, event: dict) -> None:
    handle_crawl_completed_event(db, event, correlation_id=_event_correlation_id(event))


def _handle_ffscore_recalculated(db: Session, event: dict) -> None:
    handle_ff_score_recalculated_event(db, event, correlation_id=_event_correlation_id(event))


async def _consume(
    config: ResilientConsumerConfig,
    handler: Callable[[Session, dict], None],
    expected_event_names: tuple[str | None, ...],
) -> None:
    try:
        conn = await aio_pika.connect_robust(settings.rabbitmq_url)
        _connections.append(conn)
    except Exception as exc:
        logger.error(
            "Failed to start Management Service event consumer",
            extra={"consumer_name": config.consumer_name, "error": str(exc)},
            exc_info=True,
        )
        return

    try:
        async with conn:
            ch = await conn.channel()
            q = await declare_resilient_queue(ch, config)
            logger.info("Management Service event consumer started", extra={"consumer_name": config.consumer_name})

            async with q.iterator() as it:
                async for msg in it:
                    await process_resilient_message_sync_store(
                        msg,
                        config=config,
                        session_factory=SessionLocal,
                        handler=handler,
                        expected_event_names=expected_event_names,
                    )
                    await asyncio.sleep(0)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error(
            "Management Service event consumer stopped unexpectedly",
            extra={"consumer_name": config.consumer_name, "error": str(exc)},
            exc_info=True,
        )


async def start_consumers() -> None:
    if _consumer_tasks:
        return
    if not settings.rabbitmq_url:
        logger.info("RabbitMQ URL is not configured; Management Service consumers are disabled")
        return

    redis_url = str(settings.REDIS_URL) if settings.REDIS_URL else None
    consumer_specs = (
        (
            ResilientConsumerConfig(
                consumer_name="management.crawl_completed",
                queue_name="management.crawl_completed",
                routing_key="audit.crawl.completed",
                redis_url=redis_url,
            ),
            _handle_crawl_completed,
            ("CrawlCompleted", None),
        ),
        (
            ResilientConsumerConfig(
                consumer_name="management.ffscore_recalculated",
                queue_name="management.ffscore_recalculated",
                routing_key="semantic.ffscore.recalculated",
                redis_url=redis_url,
            ),
            _handle_ffscore_recalculated,
            ("FFScoreRecalculated",),
        ),
    )

    for config, handler, expected_event_names in consumer_specs:
        _consumer_tasks.append(
            asyncio.create_task(
                _consume(config, handler, expected_event_names),
                name=f"management-event-consumer:{config.consumer_name}",
            )
        )


async def stop_consumers() -> None:
    for task in _consumer_tasks:
        if not task.done():
            task.cancel()

    if _consumer_tasks:
        await asyncio.gather(*_consumer_tasks, return_exceptions=True)
        _consumer_tasks.clear()

    for conn in _connections:
        with suppress(Exception):
            await conn.close()
    _connections.clear()
