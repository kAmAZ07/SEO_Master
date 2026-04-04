import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import aio_pika

from config.logging_config import get_logger
from services.management_service.config import settings

logger = get_logger(__name__)

EXCHANGE_NAME = "seo_master.events"


def _build_message_body(
    event_type: str,
    payload: Dict[str, Any],
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "produced_at": datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id,
        "payload": payload,
    }


async def publish_event(
    *,
    routing_key: str,
    payload: Dict[str, Any],
    event_type: str,
    correlation_id: Optional[str] = None,
    exchange_name: str = EXCHANGE_NAME,
) -> Dict[str, Any]:
    if not settings.rabbitmq_url:
        logger.warning(
            "RabbitMQ URL is not configured; event was not published",
            extra={"routing_key": routing_key, "event_type": event_type},
        )
        return {"status": "skipped", "reason": "rabbitmq_not_configured"}

    body = _build_message_body(
        event_type=event_type,
        payload=payload,
        correlation_id=correlation_id,
    )
    headers: Dict[str, Any] = {
        "event_type": event_type,
        "routing_key": routing_key,
        "event_id": body["event_id"],
    }
    if correlation_id:
        headers["correlation_id"] = correlation_id

    try:
        connection = await aio_pika.connect_robust(settings.rabbitmq_url)
        async with connection:
            channel = await connection.channel()
            exchange = await channel.declare_exchange(
                exchange_name,
                aio_pika.ExchangeType.TOPIC,
                durable=True,
            )
            message = aio_pika.Message(
                body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                headers=headers,
            )
            await exchange.publish(message, routing_key=routing_key)
    except Exception as exc:
        logger.error(
            "Failed to publish event",
            extra={
                "routing_key": routing_key,
                "event_type": event_type,
                "correlation_id": correlation_id,
                "error": str(exc),
            },
        )
        raise

    return {
        "status": "published",
        "routing_key": routing_key,
        "event_type": event_type,
        "correlation_id": correlation_id,
        "exchange": exchange_name,
    }
