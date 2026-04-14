from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncContextManager, Awaitable, Callable, Iterable, Mapping, Sequence

import aio_pika
from sqlalchemy import text

try:
    from redis.asyncio import Redis
except Exception:  # pragma: no cover - redis is optional for local/test runs
    Redis = None  # type: ignore[assignment]


EVENT_EXCHANGE_NAME = "seo_master.events"
EVENT_DLX_NAME = "seo_master.events.dlx"
RETRY_DELAYS_SECONDS = (10, 60, 300)
REDIS_BUFFER_TTL_SECONDS = 7 * 24 * 60 * 60

EVENT_TRACKING_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS public.processed_events (
        id VARCHAR(255) PRIMARY KEY,
        event_id VARCHAR(128) NOT NULL,
        consumer_name VARCHAR(128) NOT NULL,
        event_name VARCHAR(128),
        routing_key VARCHAR(255),
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        processed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
        CONSTRAINT uq_processed_events_consumer_event UNIQUE (consumer_name, event_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_processed_events_event_id ON public.processed_events(event_id)",
    "CREATE INDEX IF NOT EXISTS idx_processed_events_consumer ON public.processed_events(consumer_name)",
    "CREATE INDEX IF NOT EXISTS idx_processed_events_processed_at ON public.processed_events(processed_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS public.failed_events (
        id VARCHAR(255) PRIMARY KEY,
        event_id VARCHAR(128) NOT NULL,
        consumer_name VARCHAR(128) NOT NULL,
        event_name VARCHAR(128),
        routing_key VARCHAR(255),
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        error TEXT NOT NULL,
        attempt INTEGER NOT NULL DEFAULT 1,
        retry_policy JSONB NOT NULL DEFAULT '[10, 60, 300]'::jsonb,
        next_retry_at TIMESTAMP WITH TIME ZONE,
        resolved BOOLEAN NOT NULL DEFAULT FALSE,
        resolved_at TIMESTAMP WITH TIME ZONE,
        failed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
        CONSTRAINT uq_failed_events_consumer_event UNIQUE (consumer_name, event_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_failed_events_event_id ON public.failed_events(event_id)",
    "CREATE INDEX IF NOT EXISTS idx_failed_events_consumer ON public.failed_events(consumer_name)",
    "CREATE INDEX IF NOT EXISTS idx_failed_events_resolved ON public.failed_events(resolved)",
    "CREATE INDEX IF NOT EXISTS idx_failed_events_next_retry_at ON public.failed_events(next_retry_at)",
    "CREATE INDEX IF NOT EXISTS idx_failed_events_failed_at ON public.failed_events(failed_at DESC)",
)


@dataclass(frozen=True)
class ResilientConsumerConfig:
    consumer_name: str
    queue_name: str
    routing_key: str
    exchange_name: str = EVENT_EXCHANGE_NAME
    dlx_name: str = EVENT_DLX_NAME
    retry_delays_seconds: Sequence[int] = RETRY_DELAYS_SECONDS
    redis_url: str | None = None
    circuit_breaker_threshold: int = 5
    circuit_breaker_ttl_seconds: int = 60


AsyncSessionFactory = Callable[[], AsyncContextManager[Any]]
AsyncEventHandler = Callable[[dict[str, Any]], Awaitable[None]]
SyncSessionFactory = Callable[[], Any]
SyncEventHandler = Callable[[Any, dict[str, Any]], None]

_memory_circuit_until: dict[str, float] = {}
_memory_circuit_failures: dict[str, int] = {}


def decode_event_body(body: bytes) -> dict[str, Any]:
    try:
        event = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise ValueError("Event body must be valid UTF-8 JSON") from exc
    if not isinstance(event, dict):
        raise ValueError("Event body must be a JSON object")
    return event


def extract_event_name(event: Mapping[str, Any], headers: Mapping[str, Any] | None = None) -> str | None:
    headers = headers or {}
    event_name = event.get("event_name") or event.get("event_type") or headers.get("event_type")
    if isinstance(event_name, bytes):
        event_name = event_name.decode("utf-8", errors="replace")
    return str(event_name) if event_name else None


def extract_required_event_id(event: Mapping[str, Any], headers: Mapping[str, Any] | None, body: bytes) -> str:
    headers = headers or {}
    event_id = event.get("event_id") or headers.get("event_id")
    if isinstance(event_id, bytes):
        event_id = event_id.decode("utf-8", errors="replace")
    if event_id:
        return str(event_id)
    body_hash = hashlib.sha256(body).hexdigest()[:32]
    raise ValueError(f"event_id is required by event-bus contract; fallback_id=missing:{body_hash}")


async def declare_resilient_queue(
    channel: aio_pika.abc.AbstractChannel,
    config: ResilientConsumerConfig,
) -> aio_pika.abc.AbstractQueue:
    exchange = await channel.declare_exchange(config.exchange_name, aio_pika.ExchangeType.TOPIC, durable=True)
    dlx = await channel.declare_exchange(config.dlx_name, aio_pika.ExchangeType.TOPIC, durable=True)
    retry_exchange = await channel.declare_exchange(
        f"{config.exchange_name}.retry",
        aio_pika.ExchangeType.TOPIC,
        durable=True,
    )
    dlq = await channel.declare_queue(f"{config.queue_name}.dlq", durable=True)
    await dlq.bind(dlx, routing_key=f"{config.queue_name}.dead")
    for delay_seconds in config.retry_delays_seconds:
        retry_queue = await channel.declare_queue(
            f"{config.queue_name}.retry.{delay_seconds}s",
            durable=True,
            arguments={
                "x-message-ttl": int(delay_seconds) * 1000,
                "x-dead-letter-exchange": config.exchange_name,
                "x-dead-letter-routing-key": config.routing_key,
            },
        )
        await retry_queue.bind(retry_exchange, routing_key=f"{config.queue_name}.retry.{delay_seconds}s")
    queue = await channel.declare_queue(
        config.queue_name,
        durable=True,
        arguments={
            "x-dead-letter-exchange": config.dlx_name,
            "x-dead-letter-routing-key": f"{config.queue_name}.dead",
        },
    )
    await queue.bind(exchange, routing_key=config.routing_key)
    return queue


async def ensure_event_tracking_tables(session: Any) -> None:
    for statement in EVENT_TRACKING_STATEMENTS:
        await session.execute(text(statement))


def ensure_event_tracking_tables_sync(session: Any) -> None:
    for statement in EVENT_TRACKING_STATEMENTS:
        session.execute(text(statement))


async def process_resilient_message(
    msg: aio_pika.abc.AbstractIncomingMessage,
    *,
    config: ResilientConsumerConfig,
    session_factory: AsyncSessionFactory,
    handler: AsyncEventHandler,
    expected_event_names: Iterable[str | None] | None = None,
) -> None:
    event, event_id, event_name, metadata = _message_context(msg, config)
    if event is None:
        await _record_invalid_message(msg, config, session_factory, event_id, event_name, metadata)
        return

    if expected_event_names is not None and event_name not in set(expected_event_names):
        await msg.ack()
        return

    if await _is_circuit_open(config):
        await _buffer_event(config, event_id, event_name, event, "circuit_open", metadata)
        await msg.reject(requeue=False)
        return

    try:
        async with session_factory() as session:
            await ensure_event_tracking_tables(session)
            if await _event_processed(session, config.consumer_name, event_id):
                await msg.ack()
                return
    except Exception as exc:
        await _record_circuit_failure(config)
        await _buffer_event(config, event_id, event_name, event, f"inbox_unavailable: {exc}", metadata)
        await msg.reject(requeue=False)
        return

    try:
        await handler(event)
    except Exception as exc:
        attempt = await _record_failure_safely(config, session_factory, event_id, event_name, event, str(exc), metadata)
        await _record_circuit_failure(config)
        await _buffer_event(config, event_id, event_name, event, str(exc), metadata)
        if attempt and await _publish_retry(msg, config, attempt, event_id, event_name, event, str(exc), metadata):
            return
        await msg.reject(requeue=False)
        return

    try:
        async with session_factory() as session:
            await ensure_event_tracking_tables(session)
            await _mark_processed(session, config.consumer_name, event_id, event_name, config.routing_key, event, metadata)
            await session.commit()
    except Exception as exc:
        await _record_circuit_failure(config)
        await _buffer_event(config, event_id, event_name, event, f"processed_store_failed: {exc}", metadata)
        await msg.reject(requeue=False)
        return

    await _reset_circuit(config)
    await msg.ack()


async def process_resilient_message_sync_store(
    msg: aio_pika.abc.AbstractIncomingMessage,
    *,
    config: ResilientConsumerConfig,
    session_factory: SyncSessionFactory,
    handler: SyncEventHandler,
    expected_event_names: Iterable[str | None] | None = None,
) -> None:
    event, event_id, event_name, metadata = _message_context(msg, config)
    if event is None:
        await _record_invalid_message_sync(msg, config, session_factory, event_id, event_name, metadata)
        return

    if expected_event_names is not None and event_name not in set(expected_event_names):
        await msg.ack()
        return

    if await _is_circuit_open(config):
        await _buffer_event(config, event_id, event_name, event, "circuit_open", metadata)
        await msg.reject(requeue=False)
        return

    db = session_factory()
    try:
        ensure_event_tracking_tables_sync(db)
        if _event_processed_sync(db, config.consumer_name, event_id):
            db.commit()
            await msg.ack()
            return
        handler(db, event)
        _mark_processed_sync(db, config.consumer_name, event_id, event_name, config.routing_key, event, metadata)
        db.commit()
    except Exception as exc:
        db.rollback()
        attempt = await _record_failure_sync_safely(config, session_factory, event_id, event_name, event or {}, str(exc), metadata)
        await _record_circuit_failure(config)
        await _buffer_event(config, event_id, event_name, event or {}, str(exc), metadata)
        if attempt and await _publish_retry(msg, config, attempt, event_id, event_name, event or {}, str(exc), metadata):
            return
        await msg.reject(requeue=False)
        return
    finally:
        db.close()

    await _reset_circuit(config)
    await msg.ack()


def _message_context(
    msg: aio_pika.abc.AbstractIncomingMessage,
    config: ResilientConsumerConfig,
) -> tuple[dict[str, Any] | None, str, str | None, dict[str, Any]]:
    body = bytes(msg.body)
    headers = dict(msg.headers or {})
    metadata = {
        "headers": _json_safe(headers),
        "queue_name": config.queue_name,
        "routing_key": getattr(msg, "routing_key", None) or config.routing_key,
        "delivery_tag": getattr(msg, "delivery_tag", None),
        "retry_policy_seconds": list(config.retry_delays_seconds),
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        event = decode_event_body(body)
        event_name = extract_event_name(event, headers)
        event_id = extract_required_event_id(event, headers, body)
        produced_at = event.get("produced_at")
        latency_ms = _latency_ms(produced_at) if isinstance(produced_at, str) else None
        if latency_ms is not None:
            metadata["queue_latency_ms"] = latency_ms
        return event, event_id, event_name, metadata
    except Exception as exc:
        fallback_id = f"invalid:{hashlib.sha256(body).hexdigest()[:32]}"
        metadata["raw_body_sha256"] = hashlib.sha256(body).hexdigest()
        metadata["decode_error"] = str(exc)
        metadata["raw_body"] = body.decode("utf-8", errors="replace")[:4096]
        return None, fallback_id, "InvalidEvent", metadata


async def _record_invalid_message(
    msg: aio_pika.abc.AbstractIncomingMessage,
    config: ResilientConsumerConfig,
    session_factory: AsyncSessionFactory,
    event_id: str,
    event_name: str | None,
    metadata: dict[str, Any],
) -> None:
    event = {"invalid": True, "raw_body_sha256": metadata.get("raw_body_sha256")}
    await _record_failure_safely(config, session_factory, event_id, event_name, event, metadata.get("decode_error", "invalid_event"), metadata)
    await _record_circuit_failure(config)
    await _buffer_event(config, event_id, event_name, event, metadata.get("decode_error", "invalid_event"), metadata)
    await msg.reject(requeue=False)


async def _record_invalid_message_sync(
    msg: aio_pika.abc.AbstractIncomingMessage,
    config: ResilientConsumerConfig,
    session_factory: SyncSessionFactory,
    event_id: str,
    event_name: str | None,
    metadata: dict[str, Any],
) -> None:
    event = {"invalid": True, "raw_body_sha256": metadata.get("raw_body_sha256")}
    await _record_failure_sync_safely(config, session_factory, event_id, event_name, event, metadata.get("decode_error", "invalid_event"), metadata)
    await _record_circuit_failure(config)
    await _buffer_event(config, event_id, event_name, event, metadata.get("decode_error", "invalid_event"), metadata)
    await msg.reject(requeue=False)


async def _event_processed(session: Any, consumer_name: str, event_id: str) -> bool:
    result = await session.execute(
        text(
            """
            SELECT 1
            FROM public.processed_events
            WHERE consumer_name = :consumer_name AND event_id = :event_id
            LIMIT 1
            """
        ),
        {"consumer_name": consumer_name, "event_id": event_id},
    )
    return result.scalar_one_or_none() is not None


def _event_processed_sync(session: Any, consumer_name: str, event_id: str) -> bool:
    result = session.execute(
        text(
            """
            SELECT 1
            FROM public.processed_events
            WHERE consumer_name = :consumer_name AND event_id = :event_id
            LIMIT 1
            """
        ),
        {"consumer_name": consumer_name, "event_id": event_id},
    )
    return result.scalar_one_or_none() is not None


async def _mark_processed(
    session: Any,
    consumer_name: str,
    event_id: str,
    event_name: str | None,
    routing_key: str,
    payload: dict[str, Any],
    metadata: dict[str, Any],
) -> None:
    params = _event_params(consumer_name, event_id, event_name, routing_key, payload, metadata)
    await session.execute(text(_MARK_PROCESSED_SQL), params)
    await session.execute(text(_RESOLVE_FAILURE_SQL), {"consumer_name": consumer_name, "event_id": event_id})


def _mark_processed_sync(
    session: Any,
    consumer_name: str,
    event_id: str,
    event_name: str | None,
    routing_key: str,
    payload: dict[str, Any],
    metadata: dict[str, Any],
) -> None:
    params = _event_params(consumer_name, event_id, event_name, routing_key, payload, metadata)
    session.execute(text(_MARK_PROCESSED_SQL), params)
    session.execute(text(_RESOLVE_FAILURE_SQL), {"consumer_name": consumer_name, "event_id": event_id})


async def _record_failure_safely(
    config: ResilientConsumerConfig,
    session_factory: AsyncSessionFactory,
    event_id: str,
    event_name: str | None,
    payload: dict[str, Any],
    error: str,
    metadata: dict[str, Any],
) -> int | None:
    try:
        async with session_factory() as session:
            await ensure_event_tracking_tables(session)
            attempt = await _next_attempt(session, config.consumer_name, event_id)
            await session.execute(text(_RECORD_FAILURE_SQL), _failure_params(config, event_id, event_name, payload, error, metadata, attempt))
            await session.commit()
            return attempt
    except Exception as exc:
        await _buffer_event(config, event_id, event_name, payload, f"failed_event_store_unavailable: {exc}; original_error: {error}", metadata)
        return None


async def _record_failure_sync_safely(
    config: ResilientConsumerConfig,
    session_factory: SyncSessionFactory,
    event_id: str,
    event_name: str | None,
    payload: dict[str, Any],
    error: str,
    metadata: dict[str, Any],
) -> int | None:
    db = session_factory()
    try:
        ensure_event_tracking_tables_sync(db)
        attempt = _next_attempt_sync(db, config.consumer_name, event_id)
        db.execute(text(_RECORD_FAILURE_SQL), _failure_params(config, event_id, event_name, payload, error, metadata, attempt))
        db.commit()
        return attempt
    except Exception as exc:
        db.rollback()
        await _buffer_event(config, event_id, event_name, payload, f"failed_event_store_unavailable: {exc}; original_error: {error}", metadata)
        return None
    finally:
        db.close()


async def _next_attempt(session: Any, consumer_name: str, event_id: str) -> int:
    result = await session.execute(
        text("SELECT attempt FROM public.failed_events WHERE consumer_name = :consumer_name AND event_id = :event_id"),
        {"consumer_name": consumer_name, "event_id": event_id},
    )
    attempt = result.scalar_one_or_none()
    return int(attempt or 0) + 1


def _next_attempt_sync(session: Any, consumer_name: str, event_id: str) -> int:
    result = session.execute(
        text("SELECT attempt FROM public.failed_events WHERE consumer_name = :consumer_name AND event_id = :event_id"),
        {"consumer_name": consumer_name, "event_id": event_id},
    )
    attempt = result.scalar_one_or_none()
    return int(attempt or 0) + 1


def _event_params(
    consumer_name: str,
    event_id: str,
    event_name: str | None,
    routing_key: str,
    payload: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": _tracking_id(consumer_name, event_id),
        "event_id": event_id,
        "consumer_name": consumer_name,
        "event_name": event_name,
        "routing_key": routing_key,
        "payload": _json_dumps(payload),
        "metadata": _json_dumps(metadata),
    }


def _failure_params(
    config: ResilientConsumerConfig,
    event_id: str,
    event_name: str | None,
    payload: dict[str, Any],
    error: str,
    metadata: dict[str, Any],
    attempt: int,
) -> dict[str, Any]:
    retry_index = attempt - 1
    delay_seconds = config.retry_delays_seconds[retry_index] if retry_index < len(config.retry_delays_seconds) else None
    return {
        **_event_params(config.consumer_name, event_id, event_name, config.routing_key, payload, metadata),
        "error": error[:8192],
        "attempt": attempt,
        "retry_policy": _json_dumps(list(config.retry_delays_seconds)),
        "retry_delay_seconds": int(delay_seconds) if delay_seconds is not None else None,
    }


async def _publish_retry(
    msg: aio_pika.abc.AbstractIncomingMessage,
    config: ResilientConsumerConfig,
    attempt: int,
    event_id: str,
    event_name: str | None,
    payload: dict[str, Any],
    error: str,
    metadata: dict[str, Any],
) -> bool:
    retry_index = attempt - 1
    if retry_index >= len(config.retry_delays_seconds):
        return False

    delay_seconds = int(config.retry_delays_seconds[retry_index])
    try:
        retry_exchange = await msg.channel.declare_exchange(
            f"{config.exchange_name}.retry",
            aio_pika.ExchangeType.TOPIC,
            durable=True,
        )
        headers = dict(msg.headers or {})
        headers.update(
            {
                "event_id": event_id,
                "event_type": event_name,
                "x-retry-attempt": attempt,
                "x-retry-delay-seconds": delay_seconds,
                "x-original-queue": config.queue_name,
                "x-original-routing-key": config.routing_key,
                "x-last-error": error[:512],
            }
        )
        retry_message = aio_pika.Message(
            body=bytes(msg.body),
            content_type=getattr(msg, "content_type", None) or "application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers=headers,
        )
        await retry_exchange.publish(retry_message, routing_key=f"{config.queue_name}.retry.{delay_seconds}s")
        await msg.ack()
        return True
    except Exception as exc:
        await _buffer_event(
            config,
            event_id,
            event_name,
            payload,
            f"retry_publish_failed: {exc}; original_error: {error}",
            metadata,
        )
        return False


async def _is_circuit_open(config: ResilientConsumerConfig) -> bool:
    now = time.monotonic()
    if _memory_circuit_until.get(config.consumer_name, 0) > now:
        return True

    client = await _redis_client(config.redis_url)
    if client is None:
        return False
    try:
        return bool(await client.get(_redis_key(config, "circuit_open")))
    except Exception:
        return False
    finally:
        await client.aclose()


async def _record_circuit_failure(config: ResilientConsumerConfig) -> None:
    failure_count = _memory_circuit_failures.get(config.consumer_name, 0) + 1
    _memory_circuit_failures[config.consumer_name] = failure_count
    if failure_count >= config.circuit_breaker_threshold:
        _memory_circuit_until[config.consumer_name] = time.monotonic() + config.circuit_breaker_ttl_seconds

    client = await _redis_client(config.redis_url)
    if client is None:
        return
    try:
        failures_key = _redis_key(config, "failures")
        failures = await client.incr(failures_key)
        await client.expire(failures_key, config.circuit_breaker_ttl_seconds)
        if failures >= config.circuit_breaker_threshold:
            await client.setex(_redis_key(config, "circuit_open"), config.circuit_breaker_ttl_seconds, "1")
    except Exception:
        return
    finally:
        await client.aclose()


async def _reset_circuit(config: ResilientConsumerConfig) -> None:
    _memory_circuit_failures.pop(config.consumer_name, None)
    _memory_circuit_until.pop(config.consumer_name, None)
    client = await _redis_client(config.redis_url)
    if client is None:
        return
    try:
        await client.delete(_redis_key(config, "failures"), _redis_key(config, "circuit_open"))
    except Exception:
        return
    finally:
        await client.aclose()


async def _buffer_event(
    config: ResilientConsumerConfig,
    event_id: str,
    event_name: str | None,
    payload: dict[str, Any],
    error: str,
    metadata: dict[str, Any],
) -> None:
    client = await _redis_client(config.redis_url)
    if client is None:
        return
    try:
        key = _redis_key(config, "buffer")
        item = {
            "event_id": event_id,
            "event_name": event_name,
            "consumer_name": config.consumer_name,
            "routing_key": config.routing_key,
            "error": error[:8192],
            "payload": payload,
            "metadata": metadata,
            "buffered_at": datetime.now(timezone.utc).isoformat(),
        }
        await client.rpush(key, _json_dumps(item))
        await client.ltrim(key, -1000, -1)
        await client.expire(key, REDIS_BUFFER_TTL_SECONDS)
    except Exception:
        return
    finally:
        await client.aclose()


async def _redis_client(redis_url: str | None) -> Any:
    if not redis_url or Redis is None:
        return None
    try:
        return Redis.from_url(str(redis_url), decode_responses=True)
    except Exception:
        return None


def _tracking_id(consumer_name: str, event_id: str) -> str:
    raw = f"{consumer_name}:{event_id}"
    if len(raw) <= 255:
        return raw
    return f"{consumer_name}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _redis_key(config: ResilientConsumerConfig, suffix: str) -> str:
    return f"event_resilience:{config.consumer_name}:{suffix}"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, default=str)
        return value
    except Exception:
        return str(value)


def _latency_ms(produced_at: str) -> int | None:
    try:
        normalized = produced_at.replace("Z", "+00:00")
        produced = datetime.fromisoformat(normalized)
        if produced.tzinfo is None:
            produced = produced.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - produced).total_seconds() * 1000))
    except Exception:
        return None


_MARK_PROCESSED_SQL = """
INSERT INTO public.processed_events (
    id, event_id, consumer_name, event_name, routing_key, payload, metadata, processed_at, created_at, updated_at
)
VALUES (
    :id, :event_id, :consumer_name, :event_name, :routing_key,
    CAST(:payload AS JSONB), CAST(:metadata AS JSONB), NOW(), NOW(), NOW()
)
ON CONFLICT (consumer_name, event_id)
DO UPDATE SET
    event_name = EXCLUDED.event_name,
    routing_key = EXCLUDED.routing_key,
    payload = EXCLUDED.payload,
    metadata = EXCLUDED.metadata,
    processed_at = NOW(),
    updated_at = NOW()
"""

_RESOLVE_FAILURE_SQL = """
UPDATE public.failed_events
SET resolved = TRUE, resolved_at = NOW(), updated_at = NOW()
WHERE consumer_name = :consumer_name AND event_id = :event_id
"""

_RECORD_FAILURE_SQL = """
INSERT INTO public.failed_events (
    id, event_id, consumer_name, event_name, routing_key, payload, error, attempt,
    retry_policy, next_retry_at, resolved, resolved_at, failed_at, metadata, created_at, updated_at
)
VALUES (
    :id, :event_id, :consumer_name, :event_name, :routing_key, CAST(:payload AS JSONB),
    :error, :attempt, CAST(:retry_policy AS JSONB),
    NOW() + (:retry_delay_seconds * INTERVAL '1 second'), FALSE, NULL, NOW(),
    CAST(:metadata AS JSONB), NOW(), NOW()
)
ON CONFLICT (consumer_name, event_id)
DO UPDATE SET
    event_name = EXCLUDED.event_name,
    routing_key = EXCLUDED.routing_key,
    payload = EXCLUDED.payload,
    error = EXCLUDED.error,
    attempt = EXCLUDED.attempt,
    retry_policy = EXCLUDED.retry_policy,
    next_retry_at = EXCLUDED.next_retry_at,
    resolved = FALSE,
    resolved_at = NULL,
    failed_at = NOW(),
    metadata = EXCLUDED.metadata,
    updated_at = NOW()
"""
