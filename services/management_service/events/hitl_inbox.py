"""Durable notification inbox. Human decisions originate only in Management outbox."""
import asyncio
import json
from typing import Any, Callable
from uuid import UUID

import aio_pika
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from services.event_resilience import ResilientConsumerConfig
from services.management_service.approval_ledger import read_approved_notification
from services.management_service.approval_policy import ApprovalError
from services.management_service.models import ManagementOutbox
from shared.hitl_execution import canonical, digest

CONSUMER = "management.hitl_approved"


def record_notification(db: Session, event: dict[str, Any]) -> dict[str, Any]:
    """Caller commits the inbox insert; duplicate bodies cannot replace an old delivery."""
    try:
        event_id = str(UUID(event.get("event_id", "")))
    except (ValueError, TypeError, AttributeError):
        raise ApprovalError("approval_notification_invalid") from None
    origin = db.execute(select(ManagementOutbox.meta).where(ManagementOutbox.id == UUID(event_id))).scalar_one_or_none()
    if origin != event:
        raise ApprovalError("approval_notification_not_from_outbox")
    result = read_approved_notification(db, event)
    payload = canonical(event)
    inserted = db.execute(text("""INSERT INTO public.processed_events
        (id,event_id,consumer_name,event_name,routing_key,payload,metadata)
        VALUES (:id,:event,:consumer,'HITLApproved','hitl.approved',CAST(:payload AS jsonb),CAST(:metadata AS jsonb))
        ON CONFLICT (consumer_name,event_id) DO NOTHING RETURNING id"""),
        {"id": CONSUMER + ":" + event_id, "event": event_id, "consumer": CONSUMER,
         "payload": payload, "metadata": canonical({"payload_hash": digest(event), "publication_status": "not_requested"})}).scalar_one_or_none()
    if inserted is None:
        previous = db.execute(text("SELECT payload FROM public.processed_events WHERE consumer_name=:consumer AND event_id=:event"),
                              {"consumer": CONSUMER, "event": event_id}).scalar_one()
        if previous != event:
            raise ApprovalError("approval_notification_replay_conflict")
    return result


def _store(factory: Callable[[], Session], event: dict[str, Any]) -> None:
    with factory() as db:
        try:
            record_notification(db, event)
            db.commit()
        except BaseException:
            db.rollback()
            raise


def _decode(body: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = value
        return result
    if len(body) > 65536:
        raise ValueError("notification_too_large")
    event = json.loads(body, object_pairs_hook=unique)
    if not isinstance(event, dict):
        raise ValueError("notification_invalid")
    canonical(event)  # Reject non-finite JSON numbers too.
    return event


async def process_notification(message: aio_pika.abc.AbstractIncomingMessage, *,
                               config: ResilientConsumerConfig, factory: Callable[[], Session],
                               retry_exchange: aio_pika.abc.AbstractExchange) -> None:
    try:
        event = _decode(bytes(message.body))
        await asyncio.to_thread(_store, factory, event)
    except (ApprovalError, ValueError, UnicodeError):
        await message.reject(requeue=False)  # Declared durable DLQ; never make a decision.
        return
    except Exception:
        attempt = (message.headers or {}).get("x-retry-attempt", 0)
        if not isinstance(attempt, int) or isinstance(attempt, bool) or not 0 <= attempt < len(config.retry_delays_seconds):
            await message.reject(requeue=False)
            return
        delay = config.retry_delays_seconds[attempt]
        retry = aio_pika.Message(body=bytes(message.body), content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers={**dict(message.headers or {}), "x-retry-attempt": attempt + 1})
        # Publisher confirms + mandatory routing are configured on the consumer channel.
        # If confirmation fails, leave the original unacked for broker redelivery.
        await retry_exchange.publish(retry, routing_key=f"{config.queue_name}.retry.{delay}s", mandatory=True)
        await message.ack()
        return
    await message.ack()  # Only after the durable DB commit (including on duplicate).
