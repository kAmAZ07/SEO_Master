"""Scoped priority changes and their atomic audit trail in Management's journal."""
from datetime import datetime, timezone
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from services.management_service.approval_policy import ApprovalError, UserActor
from services.management_service.models import Changelog
from services.management_service.priority_policy import PriorityChange, PriorityOverride, PriorityRecord, PriorityTask, load_priority
from services.management_service.task_access import accessible_task
from shared.projects.roles import Permission


class PriorityService:
    def __init__(self, db: Session):
        self.db = db

    def change(self, task_id: str, actor: UserActor, command: PriorityChange) -> dict[str, Any]:
        try:
            self.db.execute(text("SET LOCAL lock_timeout='5s'"))
            task = cast(PriorityTask, accessible_task(self.db, task_id, actor, Permission.MANAGE, lock=True))
            current = load_priority(task.meta)
            if current.revision != command.expected_revision:
                raise ApprovalError('priority_revision_conflict')
            now = datetime.now(timezone.utc)
            override = current.override
            if command.action == 'override':
                assert command.score is not None
                override = PriorityOverride(score=command.score, actor_id=actor.user_id,
                                            reason=command.reason, at=now.isoformat())
            elif command.action == 'clear_override':
                override = None
            updated = PriorityRecord(revision=current.revision + 1, inputs=command.inputs or current.inputs, override=override)
            task.meta = {**(task.meta or {}), 'priority': updated.model_dump(mode='json')}
            task.calculate_priority()
            task.updated_at = now
            self.db.add(Changelog(id=uuid4(), project_id=task.project_id, task_id=task.id,
                entity_id=str(task.id), entity_type='task_priority', change_type=command.action,
                source='priority_v1', applied=False, created_at=now,
                before_value=current.snapshot(), after_value=updated.snapshot(),
                meta={'actor_id': actor.user_id, 'reason': command.reason, 'revision': updated.revision}))
            self.db.commit()
            return updated.snapshot()
        except Exception:
            self.db.rollback()
            raise

    def history(self, task_id: str, actor: UserActor, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        task = accessible_task(self.db, task_id, actor, Permission.READ)
        rows = self.db.query(Changelog).filter(Changelog.task_id == task.id,
            Changelog.project_id == task.project_id, Changelog.source == 'priority_v1').order_by(
                Changelog.created_at.desc(), Changelog.id.desc()).offset(offset).limit(limit).all()
        return [{'id': str(row.id), 'action': row.change_type, 'actor_id': row.meta['actor_id'],
                 'reason': row.meta['reason'], 'revision': row.meta['revision'], 'at': row.created_at.isoformat(),
                 'before': row.before_value, 'after': row.after_value} for row in rows]
