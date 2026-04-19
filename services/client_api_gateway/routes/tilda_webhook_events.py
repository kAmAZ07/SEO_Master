from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from services.client_api_gateway.config import settings
from services.client_api_gateway.db import get_db
from services.client_api_gateway.logging.changelog_logger import log_deployment
from services.project_integrations import IntegrationNotFoundError, IntegrationsService

router = APIRouter(prefix="/internal/tilda/webhook", tags=["tilda-webhook"])
integrations_service = IntegrationsService()


class TildaWebhookForwardPayload(BaseModel):
    external_project_id: str = Field(..., alias="project_id", min_length=1)
    page_id: str = Field(..., min_length=1)
    page_url: Optional[str] = None
    event: str = Field(default="publish")
    payload: Dict[str, Any] = Field(default_factory=dict)
    page_snapshot: Dict[str, Any] = Field(default_factory=dict)
    change_set: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        allow_population_by_field_name = True


def _require_internal_api_key(
    x_internal_api_key: str = Header(..., alias="X-Internal-API-Key"),
) -> None:
    if not settings.INTERNAL_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="INTERNAL_API_KEY not configured",
        )
    if x_internal_api_key != settings.INTERNAL_API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal API key")


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _change_set_changes(change_set: Dict[str, Any]) -> list[Dict[str, Any]]:
    changes = change_set.get("changes") if isinstance(change_set, dict) else None
    if isinstance(changes, list):
        return [item for item in changes if isinstance(item, dict)]
    return []


def _task_url(payload: TildaWebhookForwardPayload) -> str:
    if payload.page_url:
        return payload.page_url
    return f"tilda://{payload.external_project_id}/{payload.page_id}"


def _task_title(payload: TildaWebhookForwardPayload) -> str:
    title = payload.page_snapshot.get("title") if isinstance(payload.page_snapshot, dict) else None
    if title:
        return f"Tilda page published: {title}"
    return f"Tilda page published: {payload.page_id}"


def _management_error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = response.text
    return f"Management task creation failed with {response.status_code}: {body}"


async def _create_management_task(
    *,
    payload: TildaWebhookForwardPayload,
    project_id: str,
    deployment_log_id: str,
    correlation_id: Optional[str],
) -> Dict[str, Any]:
    if not settings.MANAGEMENT_SERVICE_URL:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MANAGEMENT_SERVICE_URL is not configured",
        )

    changes = _change_set_changes(payload.change_set)
    metadata = {
        "changes": payload.change_set,
        "diff_data": {
            "before": payload.change_set.get("before") if isinstance(payload.change_set, dict) else {},
            "after": payload.change_set.get("after") if isinstance(payload.change_set, dict) else payload.page_snapshot,
            "changes": changes,
        },
        "deployment": {
            "source": "tilda_webhook",
            "platform": "tilda",
            "external_project_id": payload.external_project_id,
            "page_id": payload.page_id,
            "page_url": payload.page_url,
            "event": payload.event,
            "change_set_id": payload.change_set.get("change_set_id") if isinstance(payload.change_set, dict) else None,
            "deployment_log_id": deployment_log_id,
            "page_snapshot": payload.page_snapshot,
            "webhook_payload": payload.payload,
        },
    }
    if correlation_id:
        metadata["correlation_id"] = correlation_id

    task_payload = {
        "project_id": project_id,
        "task_type": "UPDATE_TILDA_PAGE",
        "url": _task_url(payload),
        "title": _task_title(payload),
        "description": (
            "Tilda publish webhook was synchronized through the adapter. "
            "Review the generated change-set and prepare SEO updates for the page."
        ),
        "impact_score": 0.65,
        "effort_score": 0.35,
        "metadata": metadata,
    }

    headers = {"Content-Type": "application/json"}
    if correlation_id:
        headers["X-Correlation-ID"] = correlation_id

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            response = await client.post(
                _join_url(settings.MANAGEMENT_SERVICE_URL, "/api/v1/tasks"),
                json=task_payload,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Management task creation failed: {exc}",
            ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_management_error_detail(response),
        )
    return response.json()


@router.post("/published")
async def register_tilda_webhook_event(
    request: Request,
    payload: TildaWebhookForwardPayload,
    db: Session = Depends(get_db),
    _auth: None = Depends(_require_internal_api_key),
) -> Dict[str, Any]:
    try:
        mapping = integrations_service.register_tilda_page_mapping(
            db,
            external_project_id=payload.external_project_id,
            page_id=payload.page_id,
            page_url=payload.page_url,
            event=payload.event,
            payload=payload.payload,
        )
    except IntegrationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    correlation_id = getattr(request.state, "correlation_id", None) or request.headers.get("X-Correlation-ID")
    changes = payload.change_set or {
        "source": "tilda_webhook",
        "event": payload.event,
        "page_id": payload.page_id,
        "payload": payload.payload,
    }

    log_entry = log_deployment(
        db=db,
        project_id=mapping["project_id"],
        task_id=None,
        change_type="tilda_webhook",
        entity_id=payload.page_url or payload.page_id,
        entity_type="tilda_page",
        changes=changes,
        metadata={
            "event": payload.event,
            "page_id": payload.page_id,
            "page_url": payload.page_url,
            "external_project_id": payload.external_project_id,
            "mapped_project_id": mapping["project_id"],
            "page_snapshot": payload.page_snapshot,
            "change_set_id": payload.change_set.get("change_set_id") if isinstance(payload.change_set, dict) else None,
        },
        request=request,
        correlation_id=correlation_id,
        status="received",
    )

    try:
        task = await _create_management_task(
            payload=payload,
            project_id=mapping["project_id"],
            deployment_log_id=str(log_entry.id),
            correlation_id=correlation_id,
        )
    except HTTPException as exc:
        log_entry.status = "failed"
        log_entry.error_message = str(exc.detail)
        log_entry.meta = {
            **(log_entry.meta or {}),
            "management_task_error": exc.detail,
        }
        db.add(log_entry)
        db.commit()
        raise

    log_entry.task_id = str(task.get("id") or "")
    log_entry.status = "queued"
    log_entry.meta = {
        **(log_entry.meta or {}),
        "management_task": {
            "id": task.get("id"),
            "status": task.get("status"),
            "task_type": task.get("task_type"),
        },
    }
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)

    return {
        "status": "queued",
        "project_id": mapping["project_id"],
        "page_id": payload.page_id,
        "page_url": payload.page_url,
        "event": payload.event,
        "change_set_id": payload.change_set.get("change_set_id") if isinstance(payload.change_set, dict) else None,
        "task_id": task.get("id"),
        "log_id": str(log_entry.id),
    }
