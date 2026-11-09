"""Content-addressed audit snapshots. Hashing is separate from recommendation hashing."""

import hashlib
import json
import uuid
from typing import Any

_DOMAIN = b"SEO-MASTER-PAGE-EVIDENCE-V1\n"


def evidence_hash(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(_DOMAIN + encoded).hexdigest()


def seal_evidence(snapshot: dict[str, Any]) -> dict[str, Any]:
    digest = evidence_hash(snapshot)
    return {"evidence_id": str(uuid.uuid5(uuid.NAMESPACE_URL, _DOMAIN.decode() + digest)),
            "content_hash": digest, "snapshot": snapshot}


def verify_evidence(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"evidence_id", "content_hash", "snapshot"}:
        return False
    if not isinstance(value["snapshot"], dict):
        return False
    try:
        return value == seal_evidence(value["snapshot"])
    except (TypeError, ValueError, OverflowError):
        return False
