"""Short PostgreSQL leases; network publication happens outside DB transactions."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any, Awaitable, Callable
import uuid

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from services.management_service.models import ManagementOutbox

SessionFactory = Callable[[], Session]


@dataclass(frozen=True)
class LeasedEvent:
    event_id: uuid.UUID
    lease_token: uuid.UUID
    routing_key: str
    envelope: dict[str, Any]
    attempt: int


def claim_event(factory: SessionFactory, *, now: datetime, lease_seconds: int = 60) -> LeasedEvent | None:
    if now.tzinfo is None or not 30 <= lease_seconds <= 300:
        raise ValueError("outbox_lease_invalid")
    with factory() as db:
        row = db.execute(select(
            ManagementOutbox.id, ManagementOutbox.routing_key, ManagementOutbox.meta, ManagementOutbox.attempts,
        ).where(or_(
            and_(ManagementOutbox.status == "pending", ManagementOutbox.available_at <= now),
            and_(ManagementOutbox.status == "leased", ManagementOutbox.lease_expires_at <= now),
        )).order_by(ManagementOutbox.created_at, ManagementOutbox.id).with_for_update(skip_locked=True).limit(1)).first()
        if row is None:
            return None
        token = uuid.uuid4()
        db.execute(update(ManagementOutbox).where(ManagementOutbox.id == row[0]).values(
            status="leased", lease_token=token, lease_expires_at=now + timedelta(seconds=lease_seconds),
            attempts=ManagementOutbox.attempts + 1, error_code=None, updated_at=now,
        ))
        db.commit()
        return LeasedEvent(row[0], token, row[1], row[2], row[3] + 1)


def finish_event(factory: SessionFactory, event: LeasedEvent, *, now: datetime,
                 error_code: str | None = None) -> bool:
    values: dict[str, Any] = {"lease_token": None, "lease_expires_at": None, "updated_at": now}
    if error_code is None:
        values.update(status="published", published_at=now, error_code=None)
    else:
        if error_code not in {"publisher_unavailable", "publisher_timeout"}:
            raise ValueError("outbox_error_code_invalid")
        values.update(status="pending", error_code=error_code,
                      available_at=now + timedelta(seconds=min(300, 2 ** min(event.attempt, 9))))
    with factory() as db:
        changed = db.execute(update(ManagementOutbox).where(
            ManagementOutbox.id == event.event_id, ManagementOutbox.status == "leased",
            ManagementOutbox.lease_token == event.lease_token, ManagementOutbox.lease_expires_at > now,
        ).values(**values).returning(ManagementOutbox.id)).scalar_one_or_none()
        db.commit()
        return changed is not None


async def drain_outbox(factory: SessionFactory, publisher: Callable[[LeasedEvent], Awaitable[None]], *,
                       clock: Callable[[], datetime] | None = None, max_events: int = 20) -> dict[str, int]:
    if not 1 <= max_events <= 100:
        raise ValueError("outbox_batch_invalid")
    now = clock or (lambda: datetime.now(timezone.utc))
    counts = {"published": 0, "failed": 0, "stale_lease": 0}
    deadline = monotonic() + 15
    for _ in range(max_events):
        if monotonic() >= deadline:
            break
        event = claim_event(factory, now=now())
        if event is None:
            break
        error = None
        try:
            await asyncio.wait_for(publisher(event), timeout=15)
        except asyncio.TimeoutError:
            error = "publisher_timeout"
        except Exception:
            # Delivery may already have reached the broker. Keep the original ID/body.
            error = "publisher_unavailable"
        if not finish_event(factory, event, now=now(), error_code=error):
            counts["stale_lease"] += 1
        else:
            counts["failed" if error else "published"] += 1
    return counts
