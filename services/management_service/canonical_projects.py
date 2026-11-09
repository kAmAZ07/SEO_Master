"""Read Core identity without importing its runtime models or creating projections."""

from dataclasses import dataclass
from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class ProjectTarget:
    id: str
    url: str


def active_project_targets(db: Session) -> list[ProjectTarget]:
    return [
        ProjectTarget(str(row.id), str(row.url))
        for row in db.execute(
            text(
                "SELECT id,url FROM audit_schema.projects WHERE status='active' "
                "AND origin IS NOT NULL AND path_prefix IS NOT NULL ORDER BY id"
            )
        )
    ]


def project_exists(db: Session, project_id: str) -> bool:
    return (
        db.scalar(
            text("SELECT EXISTS(SELECT 1 FROM audit_schema.projects WHERE id=:id)"),
            {"id": project_id},
        )
        is True
    )
