"""One executor: read-only preflight, authoritative linearization, one exact apply.

An unknown outcome is reconciled through CMS status, never a second blind apply.
Transport credentials and endpoint bindings are server-owned, not caller payload.
"""
import asyncio
import json
import os
from typing import Any, Awaitable, Protocol

import httpx
from fastapi import HTTPException

from services.client_api_gateway.config import settings
from services.client_api_gateway.wordpress_v2 import WordPressV2
from shared.hitl_execution import ExecutionError, ExecutionRequest, WordPressBinding
from shared.cms_http import CMSHttpClient


def wordpress_target(project_id: str, binding: WordPressBinding | None = None, *, http: CMSHttpClient | None = None) -> WordPressV2:
    try:
        raw = settings.WORDPRESS_V2_TARGETS_JSON or ""
        if len(raw) > 1_048_576:
            raise ValueError()
        targets = json.loads(raw)
        target = targets[project_id]
        if not isinstance(target, dict) or set(target) != {"base_url", "site_id", "key_id", "secret_ref", "receipt_secret_ref"}:
            raise ValueError()
        configured = WordPressBinding.model_validate({key: target[key] for key in ("base_url", "site_id", "key_id")})
        if binding is not None and binding != configured:
            raise ExecutionError("cms_integration_invalidated")
        secrets = []
        for name in ("secret_ref", "receipt_secret_ref"):
            reference = target[name]
            if not isinstance(reference, str) or not reference.startswith("env:") or not reference[4:].isidentifier():
                raise ValueError()
            secrets.append(os.environ.get(reference[4:], ""))
        return WordPressV2(project_id, configured, *secrets, http=http)
    except (ValueError, KeyError, TypeError):
        raise HTTPException(503, "wordpress_v2_binding_unconfigured") from None


class Authority(Protocol):
    def __call__(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Awaitable[dict[str, Any]]: ...


class TargetFactory(Protocol):
    def __call__(self, project_id: str, binding: WordPressBinding | None = None) -> WordPressV2: ...


class HITLExecutor:
    def __init__(self, authorization: str, *, authority: Authority | None = None, target_factory: TargetFactory | None = None):
        if (not authorization.lower().startswith("bearer ") or not authorization[7:].strip() or len(authorization) > 8192):
            raise HTTPException(401, "user_session_required")
        self.authorization = authorization
        self.authority = authority or self._authority
        self.target_factory = target_factory or wordpress_target

    async def read(self, operation_id: str) -> dict[str, Any]:
        return await self.authority("GET", "/" + operation_id)

    async def _authority(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        key = settings.MANAGEMENT_EXECUTOR_API_KEY or ""
        internal_key = settings.INTERNAL_API_KEY or ""
        if len(key) < 32 or len(internal_key) < 32:
            raise HTTPException(503, "executor_identity_unconfigured")
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False) as client:
                response = await client.request(method, settings.MANAGEMENT_SERVICE_URL.rstrip("/") + "/internal/executions" + path,
                                                json=payload, headers={"Authorization": self.authorization,
                                                "X-Internal-API-Key": internal_key, "X-Executor-Key": key})
            data = response.json()
        except (httpx.RequestError, ValueError):
            raise HTTPException(503, "execution_authority_unavailable") from None
        if response.status_code >= 400:
            detail = data.get("detail", "execution_authority_rejected") if isinstance(data, dict) else "execution_authority_rejected"
            raise HTTPException(response.status_code, detail)
        if not isinstance(data, dict):
            raise HTTPException(502, "execution_authority_invalid")
        return data

    async def execute(self, references: ExecutionRequest) -> dict[str, Any]:
        operation_id = references.operation_id
        try:
            operation = await self.authority("GET", "/" + operation_id)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            version = await self.authority("POST", "/context", references.model_dump())
            target = self.target_factory(version["project_id"])
            before = await target.preflight(version)
            operation = await self.authority("POST", "/reserve", {**references.model_dump(), "binding": target.binding.model_dump(), "before": before})
        record = operation["record"]
        if any(record.get(key) != value for key, value in references.model_dump().items()):
            raise HTTPException(409, "execution_idempotency_conflict")
        if operation["state"] in {"applied_unverified", "no_op"}:
            return operation
        target = self.target_factory(record["project_id"], WordPressBinding.model_validate(record["binding"]))
        if operation["state"] in {"applying", "outcome_unknown"}:
            try:
                receipt = await target.status(record)
            except ExecutionError:
                return operation  # No receipt is not proof that a mutation never happened.
            return await self.authority("POST", "/" + operation_id + "/finish", {"receipt": receipt})
        if operation["state"] != "ready":
            raise HTTPException(409, "execution_state_conflict")
        current = await target.preflight(record["version"])
        if current != record["before"]:
            raise HTTPException(409, "cms_precondition_conflict")
        await target.dry_run(record)
        # Management locks and validates approval/role/TTL/version/evidence/domain again here.
        operation = await self.authority("POST", "/" + operation_id + "/begin")
        try:
            receipt = await target.apply(operation["record"])
        except BaseException:
            try:
                await asyncio.shield(self.authority("POST", "/" + operation_id + "/finish", {"receipt": None}))
            except Exception:
                pass  # Durable state stays applying and is reconciled via CMS status only.
            raise
        return await self.authority("POST", "/" + operation_id + "/finish", {"receipt": receipt})
