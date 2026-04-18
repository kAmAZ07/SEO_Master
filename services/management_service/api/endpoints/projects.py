from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from services.management_service.db.models import Project
from services.management_service.db.session import get_db

router = APIRouter()


class ProjectCreateRequest(BaseModel):
    name: str = Field(..., max_length=255)
    domain: str = Field(..., max_length=255)
    platform: str = Field("wordpress", max_length=50)
    settings: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _serialize_project(project: Project) -> dict[str, Any]:
    return {
        "id": str(project.id),
        "name": project.name,
        "domain": project.domain,
        "url": project.meta.get("root_url") if isinstance(project.meta, dict) else None,
        "platform": project.platform,
        "is_active": project.is_active,
        "settings": project.settings or {},
        "metadata": project.meta or {},
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }


@router.get("")
@router.get("/")
def list_projects(
    active_only: bool = True,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    query = db.query(Project)
    if active_only:
        query = query.filter(Project.is_active.is_(True))
    rows = query.order_by(Project.created_at.desc()).offset(offset).limit(limit).all()
    return [_serialize_project(project) for project in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
@router.post("/", status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreateRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    existing = db.query(Project).filter(Project.domain == payload.domain).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="project_domain_already_exists")

    project = Project(
        id=uuid.uuid4(),
        name=payload.name,
        domain=payload.domain,
        platform=payload.platform,
        settings=payload.settings,
        meta=payload.metadata,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return _serialize_project(project)


@router.get("/{project_id}")
def get_project(project_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project_not_found")
    return _serialize_project(project)


@router.patch("/{project_id}")
def update_project(
    project_id: str,
    payload: ProjectCreateRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project_not_found")

    duplicate = db.query(Project).filter(Project.domain == payload.domain, Project.id != project.id).first()
    if duplicate:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="project_domain_already_exists")

    project.name = payload.name
    project.domain = payload.domain
    project.platform = payload.platform
    project.settings = payload.settings
    project.meta = payload.metadata
    db.add(project)
    db.commit()
    db.refresh(project)
    return _serialize_project(project)


@router.delete("/{project_id}")
def deactivate_project(project_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project_not_found")

    project.is_active = False
    db.add(project)
    db.commit()
    return {"status": "deactivated", "project_id": project_id}
