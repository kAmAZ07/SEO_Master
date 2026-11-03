"""Событие FFScoreRecalculated, версия схемы 2.

Что изменилось относительно версии 1:
  * `ff_score` может быть `null` (полный балл не посчитан: `status = insufficient_data`);
  * добавлены `status`, `formula_version` и `schema_version`;
  * `eeat_score` — число 0..100 или `null`. Поле `eeat` тоже только число или `null` (алиас прежнего
    имени): потребители, читающие `eeat` как число, больше не получают объект. Подробности
    E-E-A-T (компоненты, статус, недостающие сигналы) лежат в объекте `eeat_details`.

NaN, Infinity и значения вне 0..100 отвергаются при сборке события, а не уходят потребителям.
"""
import json
import math
import uuid
from datetime import datetime, timezone

import aio_pika
from pydantic import BaseModel, field_validator

from services.semantic_service.config import settings

SCHEMA_VERSION = "ffscore_recalculated/2"


def _score(value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError("score must be a finite number in 0..100 or null")
    return float(value)


class FFScoreRecalculatedPayload(BaseModel):
    schema_version: str = SCHEMA_VERSION
    project_id: str | None
    root_url: str
    ff_score_id: str
    formula_version: str
    status: str
    ff_score: float | None
    components: dict
    inputs: dict
    eeat_score: float | None
    eeat: float | None
    eeat_details: dict

    @field_validator("ff_score", "eeat_score", "eeat", mode="before")
    @classmethod
    def _finite_score(cls, value):
        return _score(value)

    @field_validator("status")
    @classmethod
    def _known_status(cls, value: str) -> str:
        if value not in ("complete", "insufficient_data"):
            raise ValueError("status must be complete or insufficient_data")
        return value


class FFScoreRecalculatedEvent(BaseModel):
    event_id: str
    event_name: str = "FFScoreRecalculated"
    produced_at: str
    correlation_id: str | None = None
    payload: FFScoreRecalculatedPayload

    @classmethod
    def build(
        cls,
        project_id: str | None,
        root_url: str,
        ff_score_id: str,
        ff_score: float | None,
        components: dict,
        *,
        status: str,
        formula_version: str,
        inputs: dict | None = None,
        eeat_details: dict | None = None,
        correlation_id: str | None = None,
    ) -> "FFScoreRecalculatedEvent":
        details = eeat_details or {}
        eeat_score = details.get("score")
        if (ff_score is None) != (status == "insufficient_data"):
            raise ValueError("ff_score is null exactly when status is insufficient_data")
        return cls(
            event_id=str(uuid.uuid4()),
            produced_at=datetime.now(timezone.utc).isoformat(),
            correlation_id=correlation_id,
            payload=FFScoreRecalculatedPayload(
                project_id=project_id,
                root_url=root_url,
                ff_score_id=ff_score_id,
                formula_version=formula_version,
                status=status,
                ff_score=ff_score,
                components=components,
                inputs=inputs or {},
                eeat_score=eeat_score,
                eeat=eeat_score,
                eeat_details=details,
            ),
        )

    def to_bytes(self) -> bytes:
        return json.dumps(self.model_dump(), ensure_ascii=False, allow_nan=False).encode("utf-8")


async def publish_ffscore_recalculated(
    project_id: str | None,
    root_url: str,
    ff_score_id: str,
    ff_score: float | None,
    components: dict,
    *,
    status: str,
    formula_version: str,
    inputs: dict | None = None,
    eeat_details: dict | None = None,
    correlation_id: str | None = None,
) -> None:
    if not settings.rabbitmq_url:
        return
    ev = FFScoreRecalculatedEvent.build(
        project_id,
        root_url,
        ff_score_id,
        ff_score,
        components,
        status=status,
        formula_version=formula_version,
        inputs=inputs,
        eeat_details=eeat_details,
        correlation_id=correlation_id,
    )
    conn = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with conn:
        ch = await conn.channel()
        ex = await ch.declare_exchange("seo_master.events", aio_pika.ExchangeType.TOPIC, durable=True)
        msg = aio_pika.Message(
            body=ev.to_bytes(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers={"event_id": ev.event_id, "event_type": ev.event_name},
        )
        await ex.publish(msg, routing_key="semantic.ffscore.recalculated")
