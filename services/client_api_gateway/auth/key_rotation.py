import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, List, Optional

from services.client_api_gateway.db.models import ClientKey

ROTATION_DAYS = int(os.getenv("HMAC_ROTATION_DAYS", "90"))
GRACE_DAYS = int(os.getenv("HMAC_GRACE_DAYS", "7"))


class HMACKeyConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class EnvHMACKey:
    project_id: str
    key_id: str
    secret: str
    is_active: bool = True
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    rotated_at: Optional[datetime] = None
    grace_until: Optional[datetime] = None

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.secret.encode("utf-8")).hexdigest()

    @property
    def secret_ref(self) -> str:
        return f"env:{self.key_id}:{self.fingerprint[:16]}"


@dataclass(frozen=True)
class HMACKeyCandidate:
    metadata: ClientKey
    secret: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expires_at(now: datetime) -> datetime:
    return now + timedelta(days=ROTATION_DAYS)


def _grace_until(now: datetime) -> datetime:
    return now + timedelta(days=GRACE_DAYS)


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)

    raw = str(value).strip()
    if raw.isdigit():
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HMACKeyConfigError(f"Invalid HMAC key datetime: {raw}") from exc

    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _ensure_aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "active"}


def _stable_env_key_id(project_id: str, secret: str) -> str:
    digest = hashlib.sha256(f"{project_id}:{secret}".encode("utf-8")).hexdigest()
    return f"ck_env_{digest[:16]}"


def _iter_json_keys(raw: str) -> Iterable[EnvHMACKey]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HMACKeyConfigError("CLIENT_API_HMAC_KEYS_JSON must be valid JSON") from exc

    projects = parsed.get("projects") if isinstance(parsed, dict) and "projects" in parsed else parsed
    if isinstance(projects, list):
        entries = projects
    elif isinstance(projects, dict):
        entries = []
        for project_id, project_entries in projects.items():
            if isinstance(project_entries, dict):
                project_entries = [project_entries]
            for item in project_entries or []:
                if isinstance(item, dict):
                    entries.append({"project_id": project_id, **item})
    else:
        raise HMACKeyConfigError("CLIENT_API_HMAC_KEYS_JSON must be an object or list")

    for item in entries:
        if not isinstance(item, dict):
            continue
        project_id = str(item.get("project_id") or item.get("projectId") or "").strip()
        secret = str(item.get("secret") or item.get("hmac_secret") or item.get("hmacSecret") or "").strip()
        if not project_id or not secret:
            continue

        key_id = str(item.get("key_id") or item.get("keyId") or "").strip()
        if not key_id:
            key_id = _stable_env_key_id(project_id, secret)

        yield EnvHMACKey(
            project_id=project_id,
            key_id=key_id,
            secret=secret,
            is_active=_as_bool(item.get("is_active", item.get("active")), default=True),
            created_at=_parse_datetime(item.get("created_at") or item.get("createdAt")),
            expires_at=_parse_datetime(item.get("expires_at") or item.get("expiresAt")),
            rotated_at=_parse_datetime(item.get("rotated_at") or item.get("rotatedAt")),
            grace_until=_parse_datetime(item.get("grace_until") or item.get("graceUntil")),
        )


def get_configured_env_keys(project_id: str, key_id: Optional[str] = None) -> List[EnvHMACKey]:
    keys: List[EnvHMACKey] = []
    raw_json = os.getenv("CLIENT_API_HMAC_KEYS_JSON")
    if raw_json:
        keys.extend(_iter_json_keys(raw_json))

    filtered = [item for item in keys if item.project_id == project_id]
    if key_id:
        filtered = [item for item in filtered if item.key_id == key_id]
    return filtered


def _sync_key_metadata(db, env_key: EnvHMACKey, now: datetime) -> ClientKey:
    created_at = env_key.created_at or now
    expires_at = env_key.expires_at or _expires_at(created_at)
    metadata = (
        db.query(ClientKey)
        .filter(ClientKey.project_id == env_key.project_id, ClientKey.key_id == env_key.key_id)
        .first()
    )

    if metadata is None:
        metadata = ClientKey(
            project_id=env_key.project_id,
            key_id=env_key.key_id,
            secret_ref=env_key.secret_ref,
            is_active=env_key.is_active,
            created_at=created_at,
            expires_at=expires_at,
        )
    else:
        metadata.secret_ref = env_key.secret_ref
        metadata.is_active = env_key.is_active
        metadata.expires_at = expires_at

    metadata.rotated_at = env_key.rotated_at
    metadata.grace_until = env_key.grace_until
    metadata.meta = {
        **(metadata.meta or {}),
        "secret_source": "env",
        "secret_fingerprint": env_key.fingerprint[:16],
        "rotation_managed_by": "environment",
    }
    db.add(metadata)
    return metadata


def sync_project_keys_from_env(db, project_id: str) -> List[ClientKey]:
    now = _now()
    env_keys = get_configured_env_keys(project_id)
    if not env_keys:
        return []

    env_key_ids = {env_key.key_id for env_key in env_keys}
    stale_keys = (
        db.query(ClientKey)
        .filter(ClientKey.project_id == project_id, ~ClientKey.key_id.in_(env_key_ids))
        .all()
    )
    for stale_key in stale_keys:
        stale_key.is_active = False
        stale_key.rotated_at = stale_key.rotated_at or now
        stale_key.grace_until = stale_key.grace_until or now
        stale_key.secret_ref = f"disabled:{stale_key.key_id}"
        stale_key.meta = {
            **(stale_key.meta or {}),
            "secret_source": "disabled",
            "disabled_reason": "not_present_in_env_hmac_keys",
        }
        db.add(stale_key)

    metadata = [_sync_key_metadata(db, env_key, now) for env_key in env_keys]
    db.commit()
    for item in metadata:
        db.refresh(item)
    return metadata


def ensure_active_key(db, project_id: str) -> ClientKey:
    keys = get_valid_keys(db, project_id)
    if not keys:
        raise HMACKeyConfigError(f"No env-managed HMAC keys configured for project {project_id}")
    active = [key for key in keys if key.is_active]
    return (active or keys)[0]


def rotate_project_key(db, project_id: str) -> ClientKey:
    keys = sync_project_keys_from_env(db, project_id)
    if not keys:
        raise HMACKeyConfigError(
            "HMAC rotation is env-managed; add the next key to CLIENT_API_HMAC_KEYS_JSON first"
        )

    valid = [key for key in keys if is_key_valid(key)]
    if not valid:
        raise HMACKeyConfigError(f"No valid env-managed HMAC keys configured for project {project_id}")
    active = [key for key in valid if key.is_active]
    return (active or valid)[0]


def is_key_valid(key: ClientKey, now: Optional[datetime] = None) -> bool:
    now = now or _now()
    expires_at = _ensure_aware(key.expires_at)
    grace_until = _ensure_aware(key.grace_until)
    if key.is_active and (expires_at is None or expires_at > now):
        return True
    if grace_until and grace_until >= now:
        return True
    return False


def get_valid_keys(
    db,
    project_id: str,
    key_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> List[ClientKey]:
    env_keys = get_configured_env_keys(project_id, key_id=key_id)
    if not env_keys:
        return []

    sync_project_keys_from_env(db, project_id)
    env_key_ids = {env_key.key_id for env_key in env_keys}

    query = db.query(ClientKey).filter(
        ClientKey.project_id == project_id,
        ClientKey.key_id.in_(env_key_ids),
    )

    keys = query.all()
    return [key for key in keys if is_key_valid(key, now=now)]


def get_valid_key_candidates(
    db,
    project_id: str,
    key_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> List[HMACKeyCandidate]:
    env_keys = {key.key_id: key for key in get_configured_env_keys(project_id, key_id=key_id)}
    valid_metadata = get_valid_keys(db, project_id=project_id, key_id=key_id, now=now)

    candidates: List[HMACKeyCandidate] = []
    for metadata in valid_metadata:
        env_key = env_keys.get(metadata.key_id)
        if env_key:
            candidates.append(HMACKeyCandidate(metadata=metadata, secret=env_key.secret))
    return candidates
