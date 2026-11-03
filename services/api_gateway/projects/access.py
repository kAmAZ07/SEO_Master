"""Доступ к проектам через членство в workspace (ТЗ §3.3: проверка scope для каждого объекта).

Чужой и несуществующий проект неразличимы (404). Участник с недостаточной ролью получает 403:
о существовании проекта он и так знает.
"""
from __future__ import annotations

from typing import List, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Project, WorkspaceMember
from services.api_gateway.projects.errors import InsufficientRole, ProjectNotFound
from shared.projects.roles import Permission, has_permission


def resolve_project(db: Session, user_id: str, project_id: str, permission: Permission = Permission.READ) -> Project:
    if not isinstance(project_id, str) or not project_id or len(project_id) > 64:
        raise ProjectNotFound()
    row = db.execute(
        select(Project, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Project.workspace_id)
        .where(Project.id == project_id, WorkspaceMember.user_id == user_id)
    ).first()
    if row is None:
        raise ProjectNotFound()
    project, role = row
    if not has_permission(role, permission):
        raise InsufficientRole(f"role {role!r} cannot {permission.value}")
    return project


def list_projects(db: Session, user_id: str) -> List[Project]:
    rows: List[Tuple[Project, str]] = db.execute(
        select(Project, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Project.workspace_id)
        .where(WorkspaceMember.user_id == user_id)
        .order_by(Project.created_at.desc(), Project.id)
    ).all()
    return [project for project, role in rows if has_permission(role, Permission.READ)]
