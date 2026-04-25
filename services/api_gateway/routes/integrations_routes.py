from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config.database_config import get_db
from config.logging_config import get_logger
from database.models import Project, User
from services.api_gateway.auth import get_current_user
from services.project_integrations import (
    IntegrationNotFoundError,
    IntegrationValidationError,
    IntegrationsService,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["Integrations"])


class TildaIntegrationRequest(BaseModel):
    public_key: str = Field(..., alias="publicKey")
    secret_key: str = Field(..., alias="secretKey")
    project_id: str = Field(..., alias="projectId")
    page_mappings: Dict[str, str] = Field(default_factory=dict, alias="pageMappings")

    class Config:
        allow_population_by_field_name = True


class WordpressIntegrationRequest(BaseModel):
    base_url: str = Field(..., alias="baseUrl")
    hmac_secret: str = Field(..., alias="hmacSecret")

    class Config:
        allow_population_by_field_name = True


class GSCIntegrationRequest(BaseModel):
    property_url: str = Field(..., alias="propertyUrl")
    credentials_json: str = Field(..., alias="credentialsJson")
    token_json: Optional[str] = Field(default=None, alias="tokenJson")

    class Config:
        allow_population_by_field_name = True


class GA4IntegrationRequest(BaseModel):
    property_id: str = Field(..., alias="propertyId")
    credentials_json: str = Field(..., alias="credentialsJson")
    token_json: Optional[str] = Field(default=None, alias="tokenJson")

    class Config:
        allow_population_by_field_name = True


class YandexIntegrationRequest(BaseModel):
    token: str
    user_id: str = Field(..., alias="userId")
    host_id: str = Field(..., alias="hostId")

    class Config:
        allow_population_by_field_name = True


class IntegrationResponse(BaseModel):
    platform: str
    connected: bool
    status: str
    hint: Optional[str] = None
    connected_at: Optional[str] = None
    updated_at: Optional[str] = None
    project_identifier: Optional[str] = None
    site_url: Optional[str] = None
    page_mappings_count: Optional[int] = None
    plugin_health: Optional[Dict[str, Any]] = None
    account_identifier: Optional[str] = None
    auth_mode: Optional[str] = None


class IntegrationsListResponse(BaseModel):
    project_id: str
    items: list[IntegrationResponse]


service = IntegrationsService()


def _get_owned_project(db: Session, project_id: str, user_id: str) -> Project:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.owner_id == user_id)
        .first()
    )
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def _map_integration_error(exc: Exception) -> HTTPException:
    if isinstance(exc, IntegrationNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, IntegrationValidationError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Integration request failed")


@router.get("/projects/{project_id}/integrations", response_model=IntegrationsListResponse)
async def list_project_integrations(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> IntegrationsListResponse:
    _get_owned_project(db, project_id, str(current_user.id))
    items = [IntegrationResponse(**item) for item in service.list_integrations(db, project_id)]
    return IntegrationsListResponse(project_id=project_id, items=items)


@router.post("/projects/{project_id}/integrations/tilda", response_model=IntegrationResponse)
async def save_tilda_integration(
    project_id: str,
    payload: TildaIntegrationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> IntegrationResponse:
    _get_owned_project(db, project_id, str(current_user.id))

    try:
        integration = await service.save_tilda_credentials(
            db,
            project_id,
            public_key=payload.public_key,
            secret_key=payload.secret_key,
            external_project_id=payload.project_id,
            page_mappings=payload.page_mappings,
        )
    except Exception as exc:
        raise _map_integration_error(exc) from exc

    logger.info("Saved Tilda integration", extra={"project_id": project_id, "platform": "tilda"})
    return IntegrationResponse(**integration)


@router.post("/projects/{project_id}/integrations/wordpress", response_model=IntegrationResponse)
async def save_wordpress_integration(
    project_id: str,
    payload: WordpressIntegrationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> IntegrationResponse:
    _get_owned_project(db, project_id, str(current_user.id))

    try:
        integration = await service.save_wordpress_credentials(
            db,
            project_id,
            base_url=payload.base_url,
            hmac_secret=payload.hmac_secret,
        )
    except Exception as exc:
        raise _map_integration_error(exc) from exc

    logger.info("Saved WordPress integration", extra={"project_id": project_id, "platform": "wordpress"})
    return IntegrationResponse(**integration)


@router.post("/projects/{project_id}/integrations/gsc", response_model=IntegrationResponse)
async def save_gsc_integration(
    project_id: str,
    payload: GSCIntegrationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> IntegrationResponse:
    _get_owned_project(db, project_id, str(current_user.id))

    try:
        integration = await service.save_gsc_credentials(
            db,
            project_id,
            property_url=payload.property_url,
            credentials_json=payload.credentials_json,
            token_json=payload.token_json,
        )
    except Exception as exc:
        raise _map_integration_error(exc) from exc

    logger.info("Saved GSC integration", extra={"project_id": project_id, "platform": "gsc"})
    return IntegrationResponse(**integration)


@router.post("/projects/{project_id}/integrations/ga4", response_model=IntegrationResponse)
async def save_ga4_integration(
    project_id: str,
    payload: GA4IntegrationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> IntegrationResponse:
    _get_owned_project(db, project_id, str(current_user.id))

    try:
        integration = await service.save_ga4_credentials(
            db,
            project_id,
            property_id=payload.property_id,
            credentials_json=payload.credentials_json,
            token_json=payload.token_json,
        )
    except Exception as exc:
        raise _map_integration_error(exc) from exc

    logger.info("Saved GA4 integration", extra={"project_id": project_id, "platform": "ga4"})
    return IntegrationResponse(**integration)


@router.post("/projects/{project_id}/integrations/yandex", response_model=IntegrationResponse)
async def save_yandex_integration(
    project_id: str,
    payload: YandexIntegrationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> IntegrationResponse:
    _get_owned_project(db, project_id, str(current_user.id))

    try:
        integration = await service.save_yandex_credentials(
            db,
            project_id,
            token=payload.token,
            user_id=payload.user_id,
            host_id=payload.host_id,
        )
    except Exception as exc:
        raise _map_integration_error(exc) from exc

    logger.info("Saved Yandex integration", extra={"project_id": project_id, "platform": "yandex"})
    return IntegrationResponse(**integration)


@router.delete("/projects/{project_id}/integrations/{platform}")
async def revoke_project_integration(
    project_id: str,
    platform: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    _get_owned_project(db, project_id, str(current_user.id))

    try:
        service.revoke_integration(db, project_id, platform)
    except Exception as exc:
        raise _map_integration_error(exc) from exc

    logger.info("Revoked integration", extra={"project_id": project_id, "platform": platform})
    return {"success": True, "project_id": project_id, "platform": platform}
