from typing import Any, Dict, Optional

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

    log_entry = log_deployment(
        db=db,
        project_id=mapping["project_id"],
        task_id=None,
        change_type="tilda_webhook",
        entity_id=payload.page_url or payload.page_id,
        entity_type="tilda_page",
        changes=payload.payload,
        metadata={
            "event": payload.event,
            "page_id": payload.page_id,
            "page_url": payload.page_url,
            "external_project_id": payload.external_project_id,
            "mapped_project_id": mapping["project_id"],
        },
        request=request,
        correlation_id=request.headers.get("X-Correlation-ID"),
        status="received",
    )

    return {
        "status": "queued",
        "project_id": mapping["project_id"],
        "page_id": payload.page_id,
        "page_url": payload.page_url,
        "event": payload.event,
        "log_id": str(log_entry.id),
    }
