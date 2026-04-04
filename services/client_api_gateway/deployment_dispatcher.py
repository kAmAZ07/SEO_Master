import hashlib
import hmac
import json
import time
from typing import Any, Dict, List, Optional

import httpx

from config.logging_config import get_logger
from services.client_api_gateway.config import settings

logger = get_logger(__name__)

WORDPRESS_NAMESPACE = "/wp-json/seo-master/v1"


def _join_url(base_url: str, path: str) -> str:
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base_url.rstrip('/')}{path}"


def _escape_patch_segment(segment: str) -> str:
    return str(segment).replace("~", "~0").replace("/", "~1")


def _looks_like_patch(ops: Any) -> bool:
    return isinstance(ops, list) and all(
        isinstance(op, dict) and "op" in op and "path" in op
        for op in ops
    )


def _build_patch_ops_from_dict(
    before: Dict[str, Any],
    after: Dict[str, Any],
    base_path: str = "",
) -> List[Dict[str, Any]]:
    ops: List[Dict[str, Any]] = []
    base = base_path.rstrip("/")

    for key, value in after.items():
        op = "replace" if key in before else "add"
        key_path = _escape_patch_segment(key)
        path = f"{base}/{key_path}" if base else f"/{key_path}"
        patch_op: Dict[str, Any] = {"op": op, "path": path, "value": value}
        if key in before:
            patch_op["old_value"] = before.get(key)
        ops.append(patch_op)

    for key, old_value in before.items():
        if key in after:
            continue
        key_path = _escape_patch_segment(key)
        path = f"{base}/{key_path}" if base else f"/{key_path}"
        ops.append({"op": "remove", "path": path, "old_value": old_value})

    return ops


def _build_reverse_patch_ops(ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    reversed_ops: List[Dict[str, Any]] = []
    for op in reversed(ops):
        op_name = str(op.get("op") or "").lower()
        path = op.get("path")
        if not path:
            continue

        if op_name == "add":
            reversed_ops.append({"op": "remove", "path": path})
        elif op_name == "remove":
            if "old_value" in op:
                reversed_ops.append({"op": "add", "path": path, "value": op.get("old_value")})
        elif op_name == "replace":
            if "old_value" in op:
                reversed_ops.append({"op": "replace", "path": path, "value": op.get("old_value")})

    return reversed_ops


def _base_path_for_change_type(change_type: str) -> str:
    normalized = (change_type or "").lower()
    if normalized == "schema":
        return "/schema"
    if normalized == "interlinks":
        return "/internal_links"
    return ""


def _normalize_changes(change_type: str, changes: Any) -> List[Dict[str, Any]]:
    if _looks_like_patch(changes):
        return changes

    if not isinstance(changes, dict):
        return [{"op": "replace", "path": "/value", "value": changes}]

    before = changes.get("before") or {}
    after = changes.get("after") or {}
    base_path = _base_path_for_change_type(change_type)

    if not isinstance(before, dict) or not isinstance(after, dict):
        path = base_path or "/value"
        op: Dict[str, Any] = {"op": "replace", "path": path, "value": after}
        if before not in ({}, None, []):
            op["old_value"] = before
        return [op]

    ops = _build_patch_ops_from_dict(before, after, base_path)
    if ops:
        return ops

    return [{"op": "replace", "path": base_path or "/value", "value": after}]


def _target_platform(entity_type: str, metadata: Optional[Dict[str, Any]] = None) -> str:
    metadata = metadata or {}
    explicit = str(metadata.get("platform") or metadata.get("target_platform") or "").lower()
    if explicit in {"wordpress", "tilda"}:
        return explicit

    normalized_entity = (entity_type or "").lower()
    if "tilda" in normalized_entity:
        return "tilda"
    return "wordpress"


def _wordpress_path(change_type: str) -> str:
    return f"{WORDPRESS_NAMESPACE}/{change_type}"


def _build_wordpress_signature(secret: str, timestamp: str, method: str, path: str, body: bytes) -> str:
    body_hash = hashlib.sha256(body or b"").hexdigest()
    message = f"{timestamp}{method.upper()}{path}{body_hash}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


async def _dispatch_to_wordpress(
    *,
    change_type: str,
    project_id: str,
    entity_id: str,
    entity_type: str,
    changes: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]],
    correlation_id: Optional[str],
) -> Dict[str, Any]:
    if not settings.WORDPRESS_BASE_URL:
        raise ValueError("WORDPRESS_BASE_URL is not configured")
    if not settings.WORDPRESS_HMAC_SECRET:
        raise ValueError("WORDPRESS_HMAC_SECRET is not configured")

    path = _wordpress_path(change_type)
    payload = {
        "project_id": project_id,
        "entity_id": entity_id,
        "entity_type": entity_type,
        "changes": changes,
        "metadata": metadata or {},
    }
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    timestamp = str(int(time.time()))
    signature = _build_wordpress_signature(
        settings.WORDPRESS_HMAC_SECRET,
        timestamp,
        "PATCH",
        path,
        body,
    )
    headers = {
        "Content-Type": "application/json",
        "X-Project-ID": project_id,
        "X-Timestamp": timestamp,
        "X-Signature": signature,
    }
    if correlation_id:
        headers["X-Correlation-ID"] = correlation_id

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.patch(
            _join_url(settings.WORDPRESS_BASE_URL, path),
            content=body,
            headers=headers,
        )
        response.raise_for_status()
        result = response.json()

    return {
        "status": result.get("status", "applied"),
        "platform": "wordpress",
        "target_path": path,
        "response": result,
    }


async def _dispatch_to_tilda(
    *,
    change_type: str,
    project_id: str,
    entity_id: str,
    changes: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]],
    correlation_id: Optional[str],
) -> Dict[str, Any]:
    if not settings.TILDA_ADAPTER_URL:
        raise ValueError("TILDA_ADAPTER_URL is not configured")
    if not settings.TILDA_INTERNAL_API_KEY:
        raise ValueError("TILDA_INTERNAL_API_KEY is not configured")

    payload = {
        "project_id": project_id,
        "page_id": entity_id,
        "changes": changes,
        "metadata": metadata or {},
    }
    headers = {
        "Content-Type": "application/json",
        "X-Internal-API-Key": settings.TILDA_INTERNAL_API_KEY,
    }
    if correlation_id:
        headers["X-Correlation-ID"] = correlation_id

    path = f"/internal/tilda/{change_type}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            _join_url(settings.TILDA_ADAPTER_URL, path),
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        result = response.json()

    normalized_status = "applied" if result.get("status") in {"ok", "mock_applied", "applied"} else str(result.get("status") or "received")
    return {
        "status": normalized_status,
        "platform": "tilda",
        "target_path": path,
        "response": result,
    }


async def dispatch_change(
    *,
    db,
    change_type: str,
    project_id: str,
    entity_id: str,
    entity_type: str,
    changes: Any,
    metadata: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    del db

    normalized_change_type = (change_type or "").lower()
    if normalized_change_type not in {"meta", "schema", "interlinks"}:
        raise ValueError(f"Unsupported change_type: {change_type}")

    patch_ops = _normalize_changes(normalized_change_type, changes)
    rollback_ops = (metadata or {}).get("rollback_changes") or _build_reverse_patch_ops(patch_ops)
    enriched_metadata = {
        **(metadata or {}),
        "normalized_change_type": normalized_change_type,
        "rollback_changes": rollback_ops,
    }

    platform = _target_platform(entity_type, enriched_metadata)
    logger.info(
        "Dispatching client change",
        extra={
            "platform": platform,
            "change_type": normalized_change_type,
            "project_id": project_id,
            "entity_id": entity_id,
            "correlation_id": correlation_id,
        },
    )

    if platform == "tilda":
        result = await _dispatch_to_tilda(
            change_type=normalized_change_type,
            project_id=project_id,
            entity_id=entity_id,
            changes=patch_ops,
            metadata=enriched_metadata,
            correlation_id=correlation_id,
        )
    else:
        result = await _dispatch_to_wordpress(
            change_type=normalized_change_type,
            project_id=project_id,
            entity_id=entity_id,
            entity_type=entity_type,
            changes=patch_ops,
            metadata=enriched_metadata,
            correlation_id=correlation_id,
        )

    result["changes"] = patch_ops
    result["rollback_changes"] = rollback_ops
    return result


async def rollback_change(
    *,
    db,
    deployment_log,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    del db

    metadata = dict(deployment_log.meta or {})
    rollback_changes = metadata.get("rollback_changes")
    if not rollback_changes:
        rollback_changes = _build_reverse_patch_ops(list(deployment_log.changes or []))

    if not rollback_changes:
        return {
            "status": "skipped",
            "reason": "rollback_not_available",
            "deployment_id": str(deployment_log.id),
        }

    return await dispatch_change(
        db=None,
        change_type=deployment_log.change_type,
        project_id=deployment_log.project_id,
        entity_id=deployment_log.entity_id,
        entity_type=deployment_log.entity_type,
        changes=rollback_changes,
        metadata={
            **metadata,
            "rollback_of": str(deployment_log.id),
            "rollback_changes": _build_reverse_patch_ops(list(rollback_changes)),
        },
        correlation_id=correlation_id,
    )

