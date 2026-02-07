import hmac
import hashlib
import os
from datetime import datetime, timezone
from typing import Optional

from services.client_api_gateway.auth.key_rotation import get_valid_keys


class SignatureValidationError(ValueError):
    pass


MAX_DRIFT_SECONDS = int(os.getenv("HMAC_MAX_DRIFT_SECONDS", "300"))


def _normalize_signature(signature: str) -> str:
    sig = signature.strip()
    if sig.lower().startswith("sha256="):
        sig = sig.split("=", 1)[1]
    return sig


def _parse_timestamp(ts: str) -> datetime:
    ts_str = ts.strip()
    if ts_str.isdigit():
        return datetime.fromtimestamp(int(ts_str), tz=timezone.utc)
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SignatureValidationError("Invalid timestamp format") from exc


def _hash_body(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _build_message(timestamp: str, method: str, path: str, body_hash: str) -> bytes:
    payload = f"{timestamp}{method.upper()}{path}{body_hash}"
    return payload.encode("utf-8")


def _compute_signature(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def validate_request_signature(
    db,
    project_id: str,
    signature: str,
    timestamp: str,
    method: str,
    path: str,
    body: bytes,
    key_id: Optional[str] = None,
    now: Optional[datetime] = None,
):
    if not project_id:
        raise SignatureValidationError("Missing X-Project-ID")
    if not signature:
        raise SignatureValidationError("Missing X-Signature")
    if not timestamp:
        raise SignatureValidationError("Missing X-Timestamp")

    now = now or datetime.now(timezone.utc)
    ts_dt = _parse_timestamp(timestamp)
    drift = abs((now - ts_dt).total_seconds())
    if drift > MAX_DRIFT_SECONDS:
        raise SignatureValidationError("Timestamp drift too large")

    normalized_sig = _normalize_signature(signature)
    body_hash = _hash_body(body or b"")
    message = _build_message(timestamp, method, path, body_hash)

    keys = get_valid_keys(db, project_id=project_id, key_id=key_id, now=now)
    if not keys:
        raise SignatureValidationError("No valid HMAC keys for project")

    for key in keys:
        expected = _compute_signature(key.secret, message)
        if hmac.compare_digest(expected, normalized_sig):
            return key

    raise SignatureValidationError("Invalid HMAC signature")
