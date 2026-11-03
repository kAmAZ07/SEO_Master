import uuid
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config.database_config import get_db
from config.logging_config import get_logger
from database.models import (
    Project,
    PublicAuditResult,
    User,
)
from services.api_gateway.auth import (
    get_current_user,
)

from services.api_gateway.routes.protected.common import (
    _get_owned_project,
    _serialize_project_private,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["Protected"])


class CreateProjectRequest(BaseModel):
    name: str
    url: str
    description: Optional[str] = None
    sourceAuditId: Optional[str] = None


def _normalize_url_for_match(value: str) -> str:
    parsed = urlparse(value.strip().lower())
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc.replace("www.", "", 1)
    path = parsed.path.rstrip("/")
    return f"{scheme}://{netloc}{path}"


@router.get("/projects")
async def list_projects(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    projects = db.query(Project).filter(Project.owner_id == current_user.id).order_by(Project.created_at.desc()).all()
    return [_serialize_project_private(project, db) for project in projects]


@router.post("/projects")
async def create_project_endpoint(
    request: CreateProjectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = db.query(Project).filter(Project.url == request.url, Project.owner_id == current_user.id).first()
    if existing:
        if request.sourceAuditId:
            public_audit = db.query(PublicAuditResult).filter(
                PublicAuditResult.audit_id == request.sourceAuditId,
                PublicAuditResult.mode == "public",
            ).first()
            if (
                public_audit
                and not public_audit.project_id
                and _normalize_url_for_match(public_audit.root_url) == _normalize_url_for_match(request.url)
            ):
                public_audit.project_id = str(existing.id)
                db.commit()
                db.refresh(existing)
        return _serialize_project_private(existing, db)

    url_taken = db.query(Project).filter(Project.url == request.url).first()
    if url_taken:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Project with this URL already exists")

    project = Project(
        id=str(uuid.uuid4()),
        name=request.name,
        url=request.url,
        owner_id=str(current_user.id),
        status="active",
    )
    db.add(project)
    if request.sourceAuditId:
        public_audit = db.query(PublicAuditResult).filter(
            PublicAuditResult.audit_id == request.sourceAuditId,
            PublicAuditResult.mode == "public",
        ).first()
        if (
            public_audit
            and not public_audit.project_id
            and _normalize_url_for_match(public_audit.root_url) == _normalize_url_for_match(request.url)
        ):
            public_audit.project_id = project.id
    db.commit()
    db.refresh(project)
    return _serialize_project_private(project, db)


@router.get("/projects/{project_id}")
async def get_project(project_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _get_owned_project(project_id, current_user, db)
    return _serialize_project_private(project, db)


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id, Project.owner_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    db.delete(project)
    db.commit()
    return {"success": True}
