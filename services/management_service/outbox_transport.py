"""Publish the stored envelope with mandatory routing and an explicit broker ACK."""

import json

import aio_pika
from pamqp.commands import Basic

from services.management_service.config import settings
from services.management_service.outbox import LeasedEvent


async def publish_stored_event(event: LeasedEvent) -> None:
    connection = await aio_pika.connect(settings.rabbitmq_url, timeout=5)
    async with connection:
        channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
        exchange = await channel.declare_exchange("seo_master.events", aio_pika.ExchangeType.TOPIC, durable=True)
        message = aio_pika.Message(
            body=json.dumps(event.envelope, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False, allow_nan=False).encode(),
            message_id=str(event.event_id), content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers={"event_id": str(event.event_id), "event_type": event.envelope["event_type"]},
        )
        confirmation = await exchange.publish(message, routing_key=event.routing_key, mandatory=True, timeout=10)
        if not isinstance(confirmation, Basic.Ack):
            raise RuntimeError("outbox_publish_not_confirmed")
