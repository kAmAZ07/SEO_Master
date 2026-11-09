"""Real publisher confirms and mandatory-routing failures on disposable PostgreSQL/RabbitMQ."""

import asyncio
import json
import os
from urllib.parse import urlsplit
import uuid

import aio_pika
import pytest
from sqlalchemy.orm import Session

from tests.integration.test_management_approvals_postgres import ADMIN_URL, NOW, decide, stand as approval_stand
from services.management_service.models import ManagementOutbox
from services.management_service.outbox import drain_outbox
from services.management_service.outbox_transport import publish_stored_event, settings

AMQP_URL = os.environ.get("TEST_AMQP_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL or not AMQP_URL, reason="disposable PG and AMQP URLs required")
stand = approval_stand


@pytest.fixture()
def broker(monkeypatch):
    url = urlsplit(AMQP_URL)
    assert url.hostname == "127.0.0.1" and url.username == "seo_ar4_test"
    monkeypatch.setattr(settings, "RABBITMQ_HOST", url.hostname)
    monkeypatch.setattr(settings, "RABBITMQ_PORT", url.port)
    monkeypatch.setattr(settings, "RABBITMQ_USER", url.username)
    monkeypatch.setattr(settings, "RABBITMQ_PASSWORD", url.password)
    monkeypatch.setattr(settings, "RABBITMQ_VHOST", "/")
    return AMQP_URL


def test_real_broker_ack_marks_exact_stored_event_published(stand, broker):
    engine, _, command = stand
    result = decide(engine, command)

    async def scenario():
        connection = await aio_pika.connect(broker, timeout=5)
        async with connection:
            channel = await connection.channel()
            exchange = await channel.declare_exchange("seo_master.events", aio_pika.ExchangeType.TOPIC, durable=True)
            queue = await channel.declare_queue("ar4-test-" + uuid.uuid4().hex, exclusive=True, auto_delete=True)
            await queue.bind(exchange, routing_key="hitl.approved")
            counts = await drain_outbox(lambda: Session(engine), publish_stored_event, clock=lambda: NOW, max_events=1)
            assert counts["published"] == 1 and counts["failed"] == 0
            message = await queue.get(timeout=5)
            assert message is not None
            async with message.process():
                body = json.loads(message.body)
                assert message.message_id == body["event_id"] == message.headers["event_id"]
                assert body["payload"]["approval_hash"] == result["approval_hash"]
                assert body["payload"]["publication_status"] == "not_requested"
                assert body["payload"]["auto_deployed"] is False
    asyncio.run(scenario())
    with Session(engine) as db:
        row = db.query(ManagementOutbox).one()
        assert row.status == "published" and row.published_at is not None


def test_unroutable_real_broker_message_remains_pending(stand, broker):
    engine, _, command = stand
    decide(engine, command)
    # No queue is bound: the mandatory return must never become a published row.
    returned = []

    async def unroutable(event):
        try:
            await publish_stored_event(event)
        except aio_pika.exceptions.DeliveryError:
            returned.append(True)
            raise

    counts = asyncio.run(drain_outbox(lambda: Session(engine), unroutable, clock=lambda: NOW, max_events=1))
    assert returned == [True]  # A connection outage is not evidence of mandatory routing.
    assert counts["failed"] == 1 and counts["published"] == 0
    with Session(engine) as db:
        row = db.query(ManagementOutbox).one()
        assert row.status == "pending" and row.published_at is None
        assert row.error_code == "publisher_unavailable"
