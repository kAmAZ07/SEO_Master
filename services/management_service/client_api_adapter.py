
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
import hashlib
import hmac
import json
import os
import time

import httpx
from sqlalchemy.orm import Session

from services.management_service.config import settings
from services.management_service.db.models import Task, TaskStatus, TaskType, Changelog
from config.logging_config import get_logger
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from prometheus_client import Counter, Histogram

logger = get_logger(__name__)

deployment_requests_total = Counter(
    "deployment_requests_total",
    "Total deployment requests to Client API Gateway",
    ["status"],
)

deployment_duration = Histogram(
    "deployment_duration_seconds",
    "Duration of deployment requests",
)

deployment_errors_total = Counter(
    "deployment_errors_total",
    "Total deployment errors",
    ["error_type"],
)

PATCH_ENDPOINTS_BY_TASK = {
    TaskType.UPDATE_META: "/api/client/meta",
    TaskType.UPDATE_SCHEMA: "/api/client/schema",
    TaskType.ADD_INTERNAL_LINKS: "/api/client/interlinks",
}

PATCH_ENDPOINTS_BY_TYPE = {
    "meta": "/api/client/meta",
    "schema": "/api/client/schema",
    "interlinks": "/api/client/interlinks",
}

PATCH_BASE_PATH = {
    "meta": "",
    "schema": "/schema",
    "interlinks": "/internal_links",
}


def _join_url(base_url: str, path: str) -> str:
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base_url.rstrip('/')}{path}"


def _escape_patch_segment(segment: str) -> str:
    return segment.replace("~", "~0").replace("/", "~1")


def _build_patch_ops_from_dict(
    before: Dict[str, Any],
    after: Dict[str, Any],
    base_path: str = "",
) -> List[Dict[str, Any]]:
    ops: List[Dict[str, Any]] = []
    base = base_path.rstrip("/")

    for key, value in after.items():
        op = "replace" if key in before else "add"
        key_path = _escape_patch_segment(str(key))
        path = f"{base}/{key_path}" if base else f"/{key_path}"
        ops.append({"op": op, "path": path, "value": value})

    for key in before.keys():
        if key not in after:
            key_path = _escape_patch_segment(str(key))
            path = f"{base}/{key_path}" if base else f"/{key_path}"
            ops.append({"op": "remove", "path": path})

    if not ops and base:
        ops.append({"op": "replace", "path": base, "value": after})

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


def _looks_like_patch(ops: Any) -> bool:
    if not isinstance(ops, list) or not ops:
        return False
    return all(isinstance(op, dict) and "op" in op and "path" in op for op in ops)


class ClientAPIAdapter:

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (
            base_url
            or getattr(settings, "CLIENT_API_GATEWAY_URL", None)
            or settings.CLIENT_GATEWAY_URL
        )
        self.internal_api_key = getattr(settings, "INTERNAL_API_KEY", None)
        self.deploy_mode = os.getenv("CLIENT_API_DEPLOY_MODE", "hmac_patch").lower()
        self.timeout = httpx.Timeout(60.0, connect=10.0, read=50.0)

        if not self.base_url:
            raise ValueError("CLIENT_GATEWAY_URL is not configured")

        if not self.internal_api_key:
            logger.warning("INTERNAL_API_KEY is not configured; internal deploy disabled")

    def _validate_internal_change_data(self, changes_data: Dict[str, Any]):
        required_fields = [
            "project_id",
            "task_id",
            "change_type",
            "entity_id",
            "entity_type",
            "changes",
        ]

        for field in required_fields:
            if field not in changes_data:
                raise ValueError(f"Missing required field: {field}")

        if not isinstance(changes_data["changes"], dict):
            raise ValueError("changes must be a dictionary")

        if "before" not in changes_data["changes"] or "after" not in changes_data["changes"]:
            raise ValueError("changes must contain 'before' and 'after' keys")

    def _build_internal_payload(
        self,
        task_id: str,
        changes_data: Dict[str, Any],
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._validate_internal_change_data(changes_data)

        payload = {
            "project_id": changes_data["project_id"],
            "task_id": task_id,
            "change_type": changes_data["change_type"],
            "entity_id": changes_data["entity_id"],
            "entity_type": changes_data["entity_type"],
            "changes": changes_data["changes"],
            "priority": changes_data.get("priority", 5),
            "metadata": {
                **(changes_data.get("metadata", {})),
                "deploy_contract": "diff-v1",
                "correlation_id": correlation_id,
                "deployed_at": datetime.utcnow().isoformat(),
                "deployed_from": "management_service",
            },
        }

        return payload

    def _resolve_change_type(
        self,
        changes_data: Dict[str, Any],
        task: Optional[Task] = None,
    ) -> Optional[str]:
        if task and task.task_type in PATCH_ENDPOINTS_BY_TASK:
            if task.task_type == TaskType.UPDATE_META:
                return "meta"
            if task.task_type == TaskType.UPDATE_SCHEMA:
                return "schema"
            if task.task_type == TaskType.ADD_INTERNAL_LINKS:
                return "interlinks"

        change_type = changes_data.get("change_type")
        if not change_type:
            return None

        change_type = str(change_type).lower()
        if "meta" in change_type:
            return "meta"
        if "schema" in change_type:
            return "schema"
        if "interlink" in change_type:
            return "interlinks"

        return None

    def _resolve_patch_endpoint(
        self,
        change_type: Optional[str],
        task: Optional[Task] = None,
    ) -> Optional[str]:
        if task and task.task_type in PATCH_ENDPOINTS_BY_TASK:
            return PATCH_ENDPOINTS_BY_TASK[task.task_type]
        if change_type:
            return PATCH_ENDPOINTS_BY_TYPE.get(change_type)
        return None

    def _find_hmac_credentials(self, data: Any) -> Tuple[Optional[str], Optional[str]]:
        if not isinstance(data, dict):
            return None, None

        nested_keys = ("hmac", "client_api", "client_api_gateway")
        for key in nested_keys:
            value = data.get(key)
            if isinstance(value, dict):
                secret, key_id = self._find_hmac_credentials(value)
                if secret:
                    return secret, key_id

        secret = (
            data.get("hmac_secret")
            or data.get("client_api_secret")
            or data.get("client_hmac_secret")
            or data.get("secret")
        )
        key_id = (
            data.get("hmac_key_id")
            or data.get("client_api_key_id")
            or data.get("client_hmac_key_id")
            or data.get("key_id")
        )

        return secret, key_id

    def _resolve_hmac_credentials(
        self,
        changes_data: Dict[str, Any],
        task: Optional[Task] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        secret, key_id = self._find_hmac_credentials(changes_data)
        if secret:
            return secret, key_id

        secret, key_id = self._find_hmac_credentials(changes_data.get("metadata", {}))
        if secret:
            return secret, key_id

        if task:
            secret, key_id = self._find_hmac_credentials(task.metadata or {})
            if secret:
                return secret, key_id

            project = task.project
            if project is not None:
                secret, key_id = self._find_hmac_credentials(project.settings or {})
                if secret:
                    return secret, key_id

                secret, key_id = self._find_hmac_credentials(project.metadata or {})
                if secret:
                    return secret, key_id

        env_secret = os.getenv("CLIENT_API_HMAC_SECRET")
        env_key_id = os.getenv("CLIENT_API_HMAC_KEY_ID")
        return env_secret, env_key_id

    def _extract_json_patch_ops(
        self,
        changes_data: Dict[str, Any],
        task: Optional[Task] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        for key in ("json_patch", "patch", "json_patch_ops"):
            ops = changes_data.get(key)
            if _looks_like_patch(ops):
                return ops

        if _looks_like_patch(changes_data.get("changes")):
            return changes_data.get("changes")

        if task:
            metadata = task.metadata or {}
            for key in ("json_patch", "patch", "json_patch_ops"):
                ops = metadata.get(key)
                if _looks_like_patch(ops):
                    return ops

        return None

    def _build_json_patch_ops(
        self,
        change_type: Optional[str],
        changes_data: Dict[str, Any],
        task: Optional[Task] = None,
    ) -> List[Dict[str, Any]]:
        ops = self._extract_json_patch_ops(changes_data, task)
        if ops:
            return ops

        changes_payload = changes_data.get("changes")
        before: Any = {}
        after: Any = {}

        if isinstance(changes_payload, dict) and ("before" in changes_payload or "after" in changes_payload):
            before = changes_payload.get("before") or {}
            after = changes_payload.get("after") or {}
        else:
            after = changes_payload

        base_path = PATCH_BASE_PATH.get(change_type or "", "")

        if change_type in ("schema", "interlinks"):
            if not base_path:
                base_path = "/value"
            op = "replace" if before not in (None, {}, []) else "add"
            reverse_op = "replace" if after not in (None, {}, []) and before not in (None, {}, []) else "remove"
            patch_op = {"op": op, "path": base_path, "value": after}
            if before not in (None, {}, []):
                patch_op["old_value"] = before
            if reverse_op == "replace":
                patch_op["reverse"] = {"op": reverse_op, "path": base_path, "value": before}
            else:
                patch_op["reverse"] = {"op": reverse_op, "path": base_path}
            return [patch_op]

        if isinstance(after, dict):
            ops = _build_patch_ops_from_dict(before or {}, after, base_path)
            for patch_op in ops:
                path = patch_op.get("path")
                if not path:
                    continue
                key = path.split("/")[-1].replace("~1", "/").replace("~0", "~")
                if isinstance(before, dict) and key in before:
                    patch_op["old_value"] = before.get(key)
            return ops

        if base_path:
            op = {"op": "replace", "path": base_path, "value": after}
            if before not in (None, {}, []):
                op["old_value"] = before
            return [op]

        return [{"op": "replace", "path": "/value", "value": after}]

    def _build_patch_payload(
        self,
        task_id: str,
        changes_data: Dict[str, Any],
        change_type: Optional[str],
        task: Optional[Task],
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        project_id = changes_data.get("project_id") or (str(task.project_id) if task else None)
        if not project_id:
            raise ValueError("project_id is required for HMAC patch")

        entity_id = changes_data.get("entity_id")
        entity_type = changes_data.get("entity_type")
        if not entity_id or not entity_type:
            raise ValueError("entity_id and entity_type are required for HMAC patch")

        changes_ops = self._build_json_patch_ops(change_type, changes_data, task)
        rollback_ops = _build_reverse_patch_ops(changes_ops)
        metadata = changes_data.get("metadata", {}) or {}
        metadata = {
            **metadata,
            "deploy_contract": "patch-v1",
            "diff": changes_data.get("changes"),
            "rollback_changes": rollback_ops,
        }

        payload = {
            "project_id": str(project_id),
            "task_id": task_id,
            "entity_id": entity_id,
            "entity_type": entity_type,
            "changes": changes_ops,
            "metadata": metadata,
        }

        if correlation_id:
            payload["correlation_id"] = correlation_id

        return payload

    def _build_hmac_signature(
        self,
        secret: str,
        timestamp: str,
        method: str,
        path: str,
        body: bytes,
    ) -> str:
        body_hash = hashlib.sha256(body or b"").hexdigest()
        message = f"{timestamp}{method.upper()}{path}{body_hash}".encode("utf-8")
        return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    )
    async def _send_internal_deploy_request(
        self,
        payload: Dict[str, Any],
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.internal_api_key:
            raise ValueError("INTERNAL_API_KEY not configured for internal deploy")

        headers = {
            "X-Internal-API-Key": self.internal_api_key,
            "Content-Type": "application/json",
        }

        if correlation_id:
            headers["X-Correlation-ID"] = correlation_id

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                with deployment_duration.time():
                    response = await client.post(
                        _join_url(self.base_url, "/internal/deploy"),
                        json=payload,
                        headers=headers,
                    )
                    response.raise_for_status()

                result = response.json()
                deployment_requests_total.labels(status="success").inc()

                return result

            except httpx.HTTPStatusError as exc:
                deployment_requests_total.labels(status="error").inc()
                deployment_errors_total.labels(error_type="http_error").inc()

                logger.error(
                    f"Client API Gateway HTTP error: {exc.response.status_code}",
                    extra={
                        "task_id": payload.get("task_id"),
                        "project_id": payload.get("project_id"),
                        "status_code": exc.response.status_code,
                        "response_body": exc.response.text[:500],
                        "correlation_id": correlation_id,
                        "mode": "internal",
                    },
                )
                raise

            except httpx.TimeoutException as exc:
                deployment_requests_total.labels(status="timeout").inc()
                deployment_errors_total.labels(error_type="timeout").inc()

                logger.error(
                    f"Client API Gateway timeout: {exc}",
                    extra={
                        "task_id": payload.get("task_id"),
                        "project_id": payload.get("project_id"),
                        "correlation_id": correlation_id,
                        "mode": "internal",
                    },
                )
                raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    )
    async def _send_hmac_patch_request(
        self,
        endpoint: str,
        payload: Dict[str, Any],
        project_id: str,
        hmac_secret: str,
        key_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        timestamp = str(int(time.time()))
        signature = self._build_hmac_signature(hmac_secret, timestamp, "PATCH", endpoint, body)

        headers = {
            "Content-Type": "application/json",
            "X-Project-ID": str(project_id),
            "X-Timestamp": timestamp,
            "X-Signature": signature,
        }

        if key_id:
            headers["X-Key-ID"] = key_id

        if correlation_id:
            headers["X-Correlation-ID"] = correlation_id

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                with deployment_duration.time():
                    response = await client.patch(
                        _join_url(self.base_url, endpoint),
                        content=body,
                        headers=headers,
                    )
                    response.raise_for_status()

                try:
                    result = response.json()
                except ValueError:
                    result = {
                        "status": "success",
                        "status_code": response.status_code,
                    }

                if "change_id" not in result and "deployment_id" in result:
                    result["change_id"] = result["deployment_id"]

                deployment_requests_total.labels(status="success").inc()
                return result

            except httpx.HTTPStatusError as exc:
                deployment_requests_total.labels(status="error").inc()
                deployment_errors_total.labels(error_type="http_error").inc()

                logger.error(
                    f"Client API Gateway HTTP error: {exc.response.status_code}",
                    extra={
                        "project_id": project_id,
                        "status_code": exc.response.status_code,
                        "response_body": exc.response.text[:500],
                        "correlation_id": correlation_id,
                        "mode": "hmac_patch",
                    },
                )
                raise

            except httpx.TimeoutException as exc:
                deployment_requests_total.labels(status="timeout").inc()
                deployment_errors_total.labels(error_type="timeout").inc()

                logger.error(
                    f"Client API Gateway timeout: {exc}",
                    extra={
                        "project_id": project_id,
                        "correlation_id": correlation_id,
                        "mode": "hmac_patch",
                    },
                )
                raise

    async def deploy_changes(
        self,
        task_id: str,
        changes_data: Dict[str, Any],
        correlation_id: Optional[str] = None,
        use_internal: Optional[bool] = None,
        task: Optional[Task] = None,
    ) -> Dict[str, Any]:
        logger.info(
            f"Deploying changes for task {task_id}",
            extra={
                "task_id": task_id,
                "project_id": changes_data.get("project_id"),
                "change_type": changes_data.get("change_type"),
                "entity_id": changes_data.get("entity_id"),
                "correlation_id": correlation_id,
            },
        )

        if use_internal is None:
            use_internal = self.deploy_mode == "internal"

        if use_internal:
            payload = self._build_internal_payload(task_id, changes_data, correlation_id)
            return await self._send_internal_deploy_request(payload, correlation_id)

        change_type = self._resolve_change_type(changes_data, task)
        endpoint = self._resolve_patch_endpoint(change_type, task)

        if not endpoint:
            if self.internal_api_key:
                logger.warning(
                    "No HMAC PATCH endpoint for change type; falling back to internal deploy",
                    extra={"task_id": task_id, "change_type": changes_data.get("change_type")},
                )
                payload = self._build_internal_payload(task_id, changes_data, correlation_id)
                return await self._send_internal_deploy_request(payload, correlation_id)
            raise ValueError("No HMAC PATCH endpoint available and internal deploy is disabled")

        hmac_secret, key_id = self._resolve_hmac_credentials(changes_data, task)
        if not hmac_secret:
            if self.internal_api_key:
                logger.warning(
                    "HMAC secret missing; falling back to internal deploy",
                    extra={"task_id": task_id, "project_id": changes_data.get("project_id")},
                )
                payload = self._build_internal_payload(task_id, changes_data, correlation_id)
                return await self._send_internal_deploy_request(payload, correlation_id)
            raise ValueError("HMAC secret not found and internal deploy is disabled")

        patch_payload = self._build_patch_payload(
            task_id=task_id,
            changes_data=changes_data,
            change_type=change_type,
            task=task,
            correlation_id=correlation_id,
        )

        return await self._send_hmac_patch_request(
            endpoint=endpoint,
            payload=patch_payload,
            project_id=patch_payload["project_id"],
            hmac_secret=hmac_secret,
            key_id=key_id,
            correlation_id=correlation_id,
        )

    async def deploy_multiple_changes(
        self,
        task_id: str,
        changes_list: List[Dict[str, Any]],
        correlation_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        results = []
        failed_deployments = []

        logger.info(
            f"Deploying {len(changes_list)} changes for task {task_id}",
            extra={
                "task_id": task_id,
                "total_changes": len(changes_list),
                "correlation_id": correlation_id,
            },
        )

        for idx, changes_data in enumerate(changes_list):
            try:
                result = await self.deploy_changes(task_id, changes_data, correlation_id)
                results.append({
                    "index": idx,
                    "success": True,
                    "result": result,
                })

            except Exception as exc:
                failed_deployments.append({
                    "index": idx,
                    "changes_data": changes_data,
                    "error": str(exc),
                })

                results.append({
                    "index": idx,
                    "success": False,
                    "error": str(exc),
                })

        if failed_deployments:
            logger.warning(
                f"Failed to deploy {len(failed_deployments)} out of {len(changes_list)} changes",
                extra={
                    "task_id": task_id,
                    "failed_count": len(failed_deployments),
                    "total_count": len(changes_list),
                    "correlation_id": correlation_id,
                },
            )

        return results

    async def get_deployment_status(
        self,
        project_id: str,
        change_id: str,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.internal_api_key:
            raise ValueError("INTERNAL_API_KEY not configured for internal status checks")

        headers = {
            "X-Internal-API-Key": self.internal_api_key,
            "Content-Type": "application/json",
        }

        if correlation_id:
            headers["X-Correlation-ID"] = correlation_id

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(
                    _join_url(self.base_url, f"/changes/pending/{project_id}"),
                    headers=headers,
                )
                response.raise_for_status()

                result = response.json()

                for change in result:
                    if change.get("change_id") == change_id:
                        return change

                return {"status": "not_found"}

            except Exception as exc:
                logger.error(
                    f"Failed to get deployment status: {exc}",
                    extra={
                        "project_id": project_id,
                        "change_id": change_id,
                        "correlation_id": correlation_id,
                    },
                )
                raise

    async def get_pending_changes(
        self,
        project_id: str,
        limit: int = 50,
        correlation_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if not self.internal_api_key:
            raise ValueError("INTERNAL_API_KEY not configured for internal status checks")

        headers = {
            "X-Internal-API-Key": self.internal_api_key,
            "Content-Type": "application/json",
        }

        if correlation_id:
            headers["X-Correlation-ID"] = correlation_id

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(
                    _join_url(self.base_url, f"/changes/pending/{project_id}"),
                    params={"limit": limit},
                    headers=headers,
                )
                response.raise_for_status()

                return response.json()

            except Exception as exc:
                logger.error(
                    f"Failed to get pending changes: {exc}",
                    extra={
                        "project_id": project_id,
                        "correlation_id": correlation_id,
                    },
                )
                raise



def _get_entity_type(task_type: TaskType) -> str:
    entity_type_map = {
        TaskType.UPDATE_META: "wordpress_post",
        TaskType.UPDATE_CONTENT: "wordpress_content",
        TaskType.ADD_INTERNAL_LINKS: "wordpress_post",
        TaskType.UPDATE_SCHEMA: "wordpress_post",
        TaskType.FIX_404: "wordpress_redirect",
        TaskType.UPDATE_TILDA_PAGE: "tilda_page",
    }

    return entity_type_map.get(task_type, "wordpress_post")


def _extract_changes_from_task(task: Task) -> Dict[str, Any]:
    metadata = task.metadata or {}

    if task.task_type == TaskType.ADD_INTERNAL_LINKS:
        interlinks = metadata.get("interlinks", [])

        return {
            "before": {"internal_links": []},
            "after": {
                "internal_links": [
                    {
                        "target_url": link["target_url"],
                        "anchor_text": link["anchor_text"],
                        "position": link.get("position", "body"),
                    }
                    for link in interlinks
                ]
            },
        }

    if task.task_type == TaskType.UPDATE_META:
        diff_data = metadata.get("diff_data", {})

        return {
            "before": {
                "title": diff_data.get("before", {}).get("title", ""),
                "description": diff_data.get("before", {}).get("description", ""),
                "h1": diff_data.get("before", {}).get("h1", ""),
            },
            "after": {
                "title": diff_data.get("after", {}).get("title", ""),
                "description": diff_data.get("after", {}).get("description", ""),
                "h1": diff_data.get("after", {}).get("h1", ""),
            },
        }

    if task.task_type == TaskType.UPDATE_SCHEMA:
        diff_data = metadata.get("diff_data", {})

        return {
            "before": {"schema": diff_data.get("before", {}).get("schema", {})},
            "after": {"schema": diff_data.get("after", {}).get("schema", {})},
        }

    if "diff_data" in metadata:
        diff_data = metadata["diff_data"]
        return {
            "before": diff_data.get("before", {}),
            "after": diff_data.get("after", {}),
        }

    return {
        "before": {},
        "after": metadata.get("changes", {}),
    }


def _calculate_priority(task: Task) -> int:
    metadata = task.metadata or {}

    impact_score = metadata.get("impact_score", 0.5)

    if "average_impact_score" in metadata:
        impact_score = metadata["average_impact_score"]

    priority = int(impact_score * 10)

    return max(1, min(priority, 10))


def _log_to_changelog(
    db: Session,
    task: Task,
    change_id: Optional[str],
    deployment_result: Dict[str, Any],
    correlation_id: Optional[str] = None,
):
    changes = _extract_changes_from_task(task)

    changelog_entry = Changelog(
        project_id=task.project_id,
        task_id=str(task.id),
        entity_id=task.url,
        entity_type=_get_entity_type(task.task_type),
        change_type=task.task_type.value,
        before_value=changes.get("before"),
        after_value=changes.get("after"),
        applied=False,
        source="HITL" if task.status == TaskStatus.APPROVED else "auto",
        metadata={
            "change_id": change_id,
            "correlation_id": correlation_id,
            "deployment_status": deployment_result.get("status"),
            "created_at": datetime.utcnow().isoformat(),
        },
    )

    db.add(changelog_entry)
    db.commit()

    logger.info(
        "Logged deployment to changelog",
        extra={
            "task_id": str(task.id),
            "change_id": change_id,
            "project_id": task.project_id,
            "correlation_id": correlation_id,
        },
    )


async def deploy_changes(
    task_id: str,
    changes_data: Dict[str, Any],
    correlation_id: Optional[str] = None,
    use_internal: Optional[bool] = None,
) -> Dict[str, Any]:
    adapter = ClientAPIAdapter()
    return await adapter.deploy_changes(task_id, changes_data, correlation_id, use_internal)


async def deploy_task_changes(
    db: Session,
    task: Task,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    if task.status != TaskStatus.APPROVED:
        raise ValueError(f"Task {task.id} is not approved for deployment")

    adapter = ClientAPIAdapter()

    changes_data = {
        "project_id": task.project_id,
        "task_id": str(task.id),
        "change_type": task.task_type.value,
        "entity_id": task.url,
        "entity_type": _get_entity_type(task.task_type),
        "changes": _extract_changes_from_task(task),
        "priority": _calculate_priority(task),
        "metadata": task.metadata,
    }

    try:
        result = await adapter.deploy_changes(
            str(task.id),
            changes_data,
            correlation_id,
            task=task,
        )

        change_id = (
            result.get("change_id")
            or result.get("deployment_id")
            or result.get("id")
        )

        task.status = TaskStatus.DEPLOYED
        task.metadata = {
            **(task.metadata or {}),
            "deployment": {
                "change_id": change_id,
                "deployed_at": datetime.utcnow().isoformat(),
                "status": result.get("status"),
            },
        }

        db.add(task)
        db.flush()

        _log_to_changelog(db, task, change_id, result, correlation_id)

        db.commit()

        logger.info(
            f"Task {task.id} deployed successfully",
            extra={
                "task_id": str(task.id),
                "project_id": task.project_id,
                "change_id": change_id,
                "correlation_id": correlation_id,
            },
        )

        return result

    except Exception as exc:
        task.status = TaskStatus.FAILED
        task.metadata = {
            **(task.metadata or {}),
            "error": {
                "message": str(exc),
                "failed_at": datetime.utcnow().isoformat(),
            },
        }

        db.add(task)
        db.commit()

        logger.error(
            f"Failed to deploy task {task.id}: {exc}",
            extra={
                "task_id": str(task.id),
                "project_id": task.project_id,
                "correlation_id": correlation_id,
            },
        )
        raise


async def deploy_interlink_task(
    db: Session,
    task: Task,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    if task.task_type != TaskType.ADD_INTERNAL_LINKS:
        raise ValueError(f"Task {task.id} is not an interlink task")

    if task.status != TaskStatus.APPROVED:
        raise ValueError(f"Task {task.id} is not approved")

    return await deploy_task_changes(db, task, correlation_id)


async def deploy_approved_tasks(
    db: Session,
    project_id: str,
    max_tasks: int = 10,
    correlation_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    approved_tasks = db.query(Task).filter(
        Task.project_id == project_id,
        Task.status == TaskStatus.APPROVED,
    ).order_by(
        Task.metadata["average_impact_score"].desc().nullslast(),
    ).limit(max_tasks).all()

    results = []

    for task in approved_tasks:
        try:
            result = await deploy_task_changes(db, task, correlation_id)
            results.append({
                "task_id": str(task.id),
                "success": True,
                "result": result,
            })
        except Exception as exc:
            results.append({
                "task_id": str(task.id),
                "success": False,
                "error": str(exc),
            })

    logger.info(
        f"Deployed {len(results)} tasks for project {project_id}",
        extra={
            "project_id": project_id,
            "total_tasks": len(results),
            "correlation_id": correlation_id,
        },
    )

    return results
