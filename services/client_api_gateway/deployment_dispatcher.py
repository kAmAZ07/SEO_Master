import hashlib
import hmac
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

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
    raise HTTPException(409, "version_bound_execution_required")


async def rollback_change(
    *,
    db,
    deployment_log,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    raise HTTPException(409, "version_bound_rollback_required")
