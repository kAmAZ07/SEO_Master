"""Личное workspace и членство (ТЗ REQ-CORE §3.2). Командные приглашения вне скоупа RC."""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.models import Workspace, WorkspaceMember
from shared.projects.roles import Role

PERSONAL_WORKSPACE_NAME = "Личное пространство"


def find_personal_workspace(db: Session, user_id: str) -> Optional[Workspace]:
    return db.execute(
        select(Workspace).where(Workspace.owner_id == user_id, Workspace.kind == "personal")
    ).scalar_one_or_none()


def ensure_personal_workspace(db: Session, user_id: str) -> Workspace:
    """Возвращает личное workspace пользователя, создавая его при первом обращении.

    Безопасно при гонке: уникальный частичный индекс не даёт создать второе, проигравший
    запрос перечитывает уже созданное.
    """
    existing = find_personal_workspace(db, user_id)
    if existing is not None:
        return existing

    workspace = Workspace(id=str(uuid.uuid4()), name=PERSONAL_WORKSPACE_NAME, kind="personal", owner_id=user_id)
    try:
        with db.begin_nested():
            db.add(workspace)
            db.flush()
            db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user_id, role=Role.OWNER.value))
            db.flush()
    except IntegrityError:
        winner = find_personal_workspace(db, user_id)
        if winner is None:  # нарушение не из-за гонки за личное workspace
            raise
        return winner
    return workspace
