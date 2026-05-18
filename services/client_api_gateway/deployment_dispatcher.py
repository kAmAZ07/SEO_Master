import hashlib
import hmac
import json
import time
from typing import Any, Dict, List, Optional

import httpx

from config.logging_config import get_logger
from services.client_api_gateway.config import settings
from services.project_integrations import (
    IntegrationNotFoundError,
    IntegrationsService,
)

logger = get_logger(__name__)
integrations_service = IntegrationsService()

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


def _warnings_from_result(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    warnings = result.get('warnings')
    if isinstance(warnings, list):
        return warnings
    nested_result = result.get('result')
    if isinstance(nested_result, dict):
        nested_warnings = nested_result.get('warnings')
        if isinstance(nested_warnings, list):
            return nested_warnings
    return []


async def _dispatch_to_wordpress(
    *,
    change_type: str,
    project_id: str,
    entity_id: str,
    entity_type: str,
    changes: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]],
    correlation_id: Optional[str],
    base_url: str,
    hmac_secret: str,
) -> Dict[str, Any]:
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
        hmac_secret,
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
            _join_url(base_url, path),
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
        "warnings": _warnings_from_result(result),
    }


async def _dispatch_to_tilda(
    *,
    change_type: str,
    project_id: str,
    page_id: str,
    changes: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]],
    correlation_id: Optional[str],
    credentials: Dict[str, Any],
) -> Dict[str, Any]:
    if not settings.TILDA_ADAPTER_URL:
        raise ValueError("TILDA_ADAPTER_URL is not configured")
    if not settings.TILDA_INTERNAL_API_KEY:
        raise ValueError("TILDA_INTERNAL_API_KEY is not configured")

    payload = {
        "project_id": project_id,
        "page_id": page_id,
        "changes": changes,
        "metadata": metadata or {},
        "credentials": credentials,
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
        "page_id": page_id,
        "response": result,
        "warnings": _warnings_from_result(result),
    }


def _load_wordpress_credentials(db, project_id: str) -> Dict[str, Any]:
    try:
        return integrations_service.get_wordpress_credentials(db, project_id)
    except IntegrationNotFoundError:
        if settings.WORDPRESS_BASE_URL and settings.WORDPRESS_HMAC_SECRET:
            logger.warning(
                "Using legacy global WordPress credentials fallback",
                extra={"project_id": project_id},
            )
            return {
                "base_url": settings.WORDPRESS_BASE_URL,
                "hmac_secret": settings.WORDPRESS_HMAC_SECRET,
            }
        raise


def _load_tilda_credentials(db, project_id: str) -> Dict[str, Any]:
    try:
        return integrations_service.get_tilda_credentials(db, project_id)
    except IntegrationNotFoundError:
        if settings.TILDA_PUBLIC_KEY and settings.TILDA_SECRET_KEY:
            logger.warning(
                "Using legacy global Tilda credentials fallback",
                extra={"project_id": project_id},
            )
            return {
                "public_key": settings.TILDA_PUBLIC_KEY,
                "secret_key": settings.TILDA_SECRET_KEY,
                "project_id": settings.TILDA_PROJECT_ID,
                "page_mappings": {},
            }
        raise


def _normalize_tilda_mapping_key(value: Any) -> Optional[str]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "://" not in text:
        return text

    try:
        from urllib.parse import urlparse

        parsed = urlparse(text)
    except Exception:
        return text

    normalized_path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme}://{parsed.netloc}{normalized_path}"


def _resolve_tilda_page_id_from_credentials(
    *,
    credentials: Dict[str, Any],
    entity_id: str,
    metadata: Optional[Dict[str, Any]],
) -> str:
    metadata = metadata or {}
    page_mappings = credentials.get("page_mappings") or {}

    for key in ("page_id", "tilda_page_id", "external_page_id", "pageId"):
        candidate = metadata.get(key)
        if candidate:
            return str(candidate)

    if entity_id in page_mappings:
        return str(page_mappings[entity_id])

    canonical_url = _normalize_tilda_mapping_key(metadata.get("url") or entity_id)
    if canonical_url and canonical_url in page_mappings:
        return str(page_mappings[canonical_url])

    if entity_id and "://" not in str(entity_id):
        return str(entity_id)

    raise ValueError(
        "Tilda page mapping is missing for this entity. Provide page_id metadata or register a page mapping first."
    )


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
        tilda_credentials = _load_tilda_credentials(db, project_id)
        page_id = _resolve_tilda_page_id_from_credentials(
            credentials=tilda_credentials,
            entity_id=entity_id,
            metadata=enriched_metadata,
        )
        result = await _dispatch_to_tilda(
            change_type=normalized_change_type,
            project_id=project_id,
            page_id=page_id,
            changes=patch_ops,
            metadata={
                **enriched_metadata,
                "external_project_id": tilda_credentials.get("project_id"),
            },
            correlation_id=correlation_id,
            credentials={
                "public_key": tilda_credentials["public_key"],
                "secret_key": tilda_credentials["secret_key"],
            },
        )
    else:
        wordpress_credentials = _load_wordpress_credentials(db, project_id)
        result = await _dispatch_to_wordpress(
            change_type=normalized_change_type,
            project_id=project_id,
            entity_id=entity_id,
            entity_type=entity_type,
            changes=patch_ops,
            metadata=enriched_metadata,
            correlation_id=correlation_id,
            base_url=wordpress_credentials["base_url"],
            hmac_secret=wordpress_credentials["hmac_secret"],
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
        db=db,
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
