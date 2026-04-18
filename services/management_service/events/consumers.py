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
_CONSUMER_STARTUP_TIMEOUT_SECONDS = 15


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
    started: asyncio.Event,
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
        raise RuntimeError(f"Failed to connect Management Service consumer {config.consumer_name}") from exc

    try:
        async with conn:
            ch = await conn.channel()
            q = await declare_resilient_queue(ch, config)
            logger.info("Management Service event consumer started", extra={"consumer_name": config.consumer_name})
            started.set()

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
        raise


async def _wait_for_consumer_startup(started_events: list[asyncio.Event]) -> None:
    while not all(event.is_set() for event in started_events):
        failed_tasks = [task for task in _consumer_tasks if task.done()]
        if failed_tasks:
            first_failure = failed_tasks[0]
            exc = first_failure.exception()
            if exc:
                raise RuntimeError("Management Service consumer failed during startup") from exc
            raise RuntimeError("Management Service consumer stopped during startup")
        await asyncio.sleep(0.05)


async def start_consumers() -> None:
    if _consumer_tasks:
        return
    if not settings.rabbitmq_url:
        raise RuntimeError("RabbitMQ URL is required for mandatory Management Service consumers")

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

    started_events: list[asyncio.Event] = []
    for config, handler, expected_event_names in consumer_specs:
        started = asyncio.Event()
        started_events.append(started)
        _consumer_tasks.append(
            asyncio.create_task(
                _consume(config, handler, expected_event_names, started),
                name=f"management-event-consumer:{config.consumer_name}",
            )
        )

    try:
        await asyncio.wait_for(
            _wait_for_consumer_startup(started_events),
            timeout=_CONSUMER_STARTUP_TIMEOUT_SECONDS,
        )
    except Exception:
        await stop_consumers()
        raise


def consumers_are_running() -> bool:
    return bool(_consumer_tasks) and all(not task.done() for task in _consumer_tasks)


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
