import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from services.client_api_gateway.db.models import ClientKey

ROTATION_DAYS = int(os.getenv("HMAC_ROTATION_DAYS", "90"))
GRACE_DAYS = int(os.getenv("HMAC_GRACE_DAYS", "7"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def generate_key_id() -> str:
    return f"ck_{uuid.uuid4().hex}"


def generate_secret() -> str:
    return secrets.token_urlsafe(32)


def _expires_at(now: datetime) -> datetime:
    return now + timedelta(days=ROTATION_DAYS)


def _grace_until(now: datetime) -> datetime:
    return now + timedelta(days=GRACE_DAYS)


def ensure_active_key(db, project_id: str) -> ClientKey:
    now = _now()
    current = (
        db.query(ClientKey)
        .filter(ClientKey.project_id == project_id, ClientKey.is_active.is_(True))
        .order_by(ClientKey.created_at.desc())
        .first()
    )

    if not current:
        return rotate_project_key(db, project_id)

    if current.expires_at and current.expires_at <= now:
        return rotate_project_key(db, project_id)

    return current


def rotate_project_key(db, project_id: str) -> ClientKey:
    now = _now()
    current = (
        db.query(ClientKey)
        .filter(ClientKey.project_id == project_id, ClientKey.is_active.is_(True))
        .order_by(ClientKey.created_at.desc())
        .first()
    )

    if current:
        current.is_active = False
        current.rotated_at = now
        current.grace_until = _grace_until(now)
        db.add(current)

    new_key = ClientKey(
        project_id=project_id,
        key_id=generate_key_id(),
        secret=generate_secret(),
        is_active=True,
        created_at=now,
        expires_at=_expires_at(now),
    )

    db.add(new_key)
    db.commit()
    db.refresh(new_key)

    return new_key


def is_key_valid(key: ClientKey, now: Optional[datetime] = None) -> bool:
    now = now or _now()
    if key.is_active and (key.expires_at is None or key.expires_at > now):
        return True
    if key.grace_until and key.grace_until >= now:
        return True
    return False


def get_valid_keys(
    db,
    project_id: str,
    key_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> List[ClientKey]:
    query = db.query(ClientKey).filter(ClientKey.project_id == project_id)
    if key_id:
        query = query.filter(ClientKey.key_id == key_id)

    keys = query.all()
    return [key for key in keys if is_key_valid(key, now=now)]
