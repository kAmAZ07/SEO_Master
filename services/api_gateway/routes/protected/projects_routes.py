import ipaddress
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config.database_config import get_db
from config.logging_config import get_logger
from database.models import Project, User
from services.api_gateway.auth import (
    get_current_user,
)
from services.api_gateway.config import settings
from services.api_gateway.projects import access
from services.api_gateway.projects.claims import CLAIMED, claim_public_audit
from services.api_gateway.projects.errors import ScopeInvalid
from services.api_gateway.projects.http import handle_project_errors
from services.api_gateway.projects.network import make_fetch
from services.api_gateway.projects.verification import DomainVerificationService, VerificationConfig
from services.api_gateway.projects.workspaces import ensure_personal_workspace
from services.api_gateway.routes.protected.common import (
    _project_verification,
    _serialize_project_private,
    _serialize_verification,
)
from shared.netpolicy import is_public_ip
from shared.projects.roles import Permission
from shared.projects.scope import ProjectScope, ScopeError, parse_scope

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["Protected"])

MAX_NAME_LENGTH = 255
MAX_DESCRIPTION_LENGTH = 2000
MAX_URL_LENGTH = 2048


class CreateProjectRequest(BaseModel):
    """Тело создания проекта. Владелец и workspace берутся из сессии, а не из тела."""

    name: str = Field(..., max_length=MAX_NAME_LENGTH)
    url: str = Field(..., max_length=MAX_URL_LENGTH)
    description: Optional[str] = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)
    sourceAuditId: Optional[str] = Field(default=None, max_length=64)
    claimToken: Optional[str] = Field(default=None, max_length=128)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


def get_verification_service(db: Session = Depends(get_db)) -> DomainVerificationService:
    return DomainVerificationService(db, make_fetch(), VerificationConfig.from_settings(settings))


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        return True
    except ValueError:
        return False


def _scope_from_request(url: str) -> ProjectScope:
    try:
        scope = parse_scope(url)
    except ScopeError as exc:
        raise ScopeInvalid(str(exc), code="scope_invalid") from exc
    # Адрес-литерал отклоняется сразу; имена разрешаются при проверке владения и обходе.
    if _is_ip_literal(scope.host) and not is_public_ip(scope.host):
        raise ScopeInvalid("address is not public", code="scope_invalid")
    return scope


def _find_by_scope(db: Session, workspace_id: str, scope: ProjectScope) -> Optional[Project]:
    return db.execute(
        select(Project).where(
            Project.workspace_id == workspace_id,
            Project.origin == scope.origin,
            Project.path_prefix == scope.path_prefix,
        )
    ).scalar_one_or_none()


@router.get("/projects")
async def list_projects(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    projects = access.list_projects(db, str(current_user.id))
    return [_serialize_project_private(project, db) for project in projects]


@router.post("/projects")
@handle_project_errors
async def create_project_endpoint(
    request: CreateProjectRequest,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user_id = str(current_user.id)
    scope = _scope_from_request(request.url)
    workspace = ensure_personal_workspace(db, user_id)

    project = _find_by_scope(db, workspace.id, scope)
    created = project is None
    if project is None:
        project = Project(
            id=str(uuid.uuid4()),
            name=request.name,
            url=request.url.strip(),
            description=(request.description or "").strip() or None,
            owner_id=user_id,
            workspace_id=workspace.id,
            origin=scope.origin,
            path_prefix=scope.path_prefix,
            status="active",
        )
        try:
            with db.begin_nested():
                db.add(project)
                db.flush()
        except IntegrityError:
            # Параллельный запрос создал тот же проект: операция идемпотентна и возвращает его.
            project = _find_by_scope(db, workspace.id, scope)
            if project is None:
                db.rollback()
                raise
            created = False

    claim_info: Optional[Dict[str, Any]] = None
    if request.sourceAuditId or request.claimToken:
        claim = claim_public_audit(db, project, request.sourceAuditId, request.claimToken)
        claim_info = {"status": claim.status, "reason": claim.reason}
        if claim.status != CLAIMED:
            logger.info("Public audit claim rejected", extra={"reason": claim.reason})

    db.commit()
    response.status_code = 201 if created else 200
    payload = _serialize_project_private(project, db)
    if claim_info is not None:
        payload["claim"] = claim_info
    return payload


@router.get("/projects/{project_id}")
@handle_project_errors
async def get_project(project_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = access.resolve_project(db, str(current_user.id), project_id, Permission.READ)
    return _serialize_project_private(project, db)


@router.delete("/projects/{project_id}")
@handle_project_errors
async def delete_project(project_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = access.resolve_project(db, str(current_user.id), project_id, Permission.MANAGE)
    db.delete(project)
    db.commit()
    return {"success": True}


# --- подтверждение прав на сайт (ТЗ §5.2) -----------------------------------

@router.get("/projects/{project_id}/verification")
@handle_project_errors
async def get_verification(project_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = access.resolve_project(db, str(current_user.id), project_id, Permission.READ)
    return _project_verification(project, db)


@router.post("/projects/{project_id}/verification/challenge", status_code=201)
@handle_project_errors
async def issue_verification_challenge(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: DomainVerificationService = Depends(get_verification_service),
):
    """Выдаёт новый challenge. Токен показывается один раз; прежний невыполненный challenge отменяется."""
    project = access.resolve_project(db, str(current_user.id), project_id, Permission.MANAGE)
    issued = service.issue_challenge(project, str(current_user.id))
    return {
        "method": "http_challenge",
        "fileUrl": issued.file_url,
        "fileBody": issued.file_body,
        "expiresAt": issued.authorization.expires_at.isoformat(),
        "verification": _serialize_verification(service.state(project)),
    }


@router.post("/projects/{project_id}/verification/check")
@handle_project_errors
async def check_verification(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: DomainVerificationService = Depends(get_verification_service),
):
    project = access.resolve_project(db, str(current_user.id), project_id, Permission.MANAGE)
    outcome = await service.check(project)
    return {
        "verified": outcome.verified,
        "errorCode": outcome.error_code,
        "verification": _serialize_verification(service.state(project)),
    }


@router.delete("/projects/{project_id}/verification")
@handle_project_errors
async def revoke_verification(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: DomainVerificationService = Depends(get_verification_service),
):
    project = access.resolve_project(db, str(current_user.id), project_id, Permission.MANAGE)
    service.revoke(project)
    return {"success": True, "verification": _serialize_verification(service.state(project))}
