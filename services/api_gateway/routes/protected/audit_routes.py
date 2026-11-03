from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config.database_config import get_db
from config.logging_config import get_logger
from database.models import (
    User,
)
from services.api_gateway.auth import (
    get_current_user,
)
from services.api_gateway.config import settings
from services.api_gateway.projects.errors import UrlOutOfScope
from services.api_gateway.projects.http import handle_project_errors
from services.api_gateway.projects.network import make_fetch
from services.api_gateway.projects.verification import DomainVerificationService, VerificationConfig, scope_of
from shared.projects.roles import Permission

from services.api_gateway.routes.protected.common import (
    _collect_recent_audits,
    _get_owned_project,
    _get_owned_projects,
    _project_ids,
    _proxy_service,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["Protected"])


class StartProjectAuditRequest(BaseModel):
    projectId: str
    url: Optional[str] = None
    maxPages: Optional[int] = Field(default=None, ge=1, le=1000)
    maxDepth: Optional[int] = Field(default=None, ge=0, le=4)
    jsRender: Optional[bool] = None


def _audit_progress(status_value: str) -> int:
    if status_value == "completed":
        return 100
    if status_value == "running":
        return 60
    if status_value == "queued":
        return 10
    if status_value == "failed":
        return 100
    return 0


def _audit_status_response(result: Dict[str, Any]) -> Dict[str, Any]:
    status_value = str(result.get("status") or "unknown")
    all_findings = result.get("findings", [])
    top_findings = result.get("top_findings")
    if not isinstance(top_findings, list):
        top_findings = all_findings[:5] if isinstance(all_findings, list) else []

    return {
        "uid": result.get("audit_id"),
        "audit_id": result.get("audit_id"),
        "project_id": result.get("project_id"),
        "root_url": result.get("root_url"),
        "url": result.get("root_url"),
        "mode": result.get("mode"),
        "status": status_value,
        "progress": _audit_progress(status_value),
        "results": {
            "summary": result.get("summary", {}),
            "findings": top_findings,
            "top_findings": top_findings,
            "findings_count": len(all_findings) if isinstance(all_findings, list) else 0,
            "pages": result.get("pages", []),
            "root_url": result.get("root_url"),
        },
        "created_at": result.get("created_at"),
        "completed_at": result.get("updated_at") if status_value in {"completed", "failed"} else None,
    }


@router.post("/audit/full")
@handle_project_errors
async def start_project_audit(
    request: StartProjectAuditRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_owned_project(request.projectId, current_user, db, permission=Permission.MANAGE)
    root_url = request.url or project.url
    # Полный обход допустим только внутри области проекта и после подтверждения прав на неё (ТЗ §5.2, AC-03).
    if not scope_of(project).contains(root_url):
        raise UrlOutOfScope("url is outside the project scope")
    if settings.DOMAIN_VERIFICATION_ENFORCED:
        DomainVerificationService(db, make_fetch(), VerificationConfig.from_settings(settings)).require_fresh(project)
    payload = {
        "project_id": str(project.id),
        "root_url": root_url,
        "site_type_hint": "unknown",
        "platform": "generic",
        "options": {
            "max_pages": request.maxPages or 1000,
            "max_depth": request.maxDepth if request.maxDepth is not None else 4,
            "js_render": True if request.jsRender is None else request.jsRender,
            "timeout": 30.0,
        },
    }

    response = await _proxy_service(
        settings.AUDIT_SERVICE_URL,
        "POST",
        "/audit/full",
        payload=payload,
        timeout=30.0,
    )
    result = response.json()
    audit_id = result.get("audit_id")
    if not audit_id:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="audit_service_invalid_response")

    return {
        "success": True,
        "uid": audit_id,
        "audit_id": audit_id,
        "project_id": str(project.id),
        "url": root_url,
        "status": result.get("status", "queued"),
        "mode": result.get("mode", "full"),
        "message": "Project audit started",
        "estimated_time_seconds": 120,
    }


@router.get("/audit/status/{uid}")
async def get_project_audit_status(
    uid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response = await _proxy_service(
        settings.AUDIT_SERVICE_URL,
        "GET",
        f"/audit/{uid}",
        timeout=10.0,
    )
    result = response.json()
    project_id = str(result.get("project_id") or "")
    if not project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="audit_not_found")

    _get_owned_project(project_id, current_user, db, not_found_detail="audit_not_found")
    return _audit_status_response(result)


@router.get("/audit/history")
async def get_audit_history(
    projectId: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if projectId:
        project_ids = [str(_get_owned_project(projectId, current_user, db).id)]
    else:
        project_ids = _project_ids(_get_owned_projects(current_user, db))

    return _collect_recent_audits(db, project_ids, limit=50)
