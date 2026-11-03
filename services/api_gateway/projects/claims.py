"""Присоединение публичного аудита к проекту по одноразовому токену создателя."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from database.models import Project, PublicAuditResult
from services.api_gateway.projects.verification import scope_of
from shared.projects.claims import is_valid_audit_id, token_matches

CLAIMED = "claimed"
REJECTED = "rejected"


@dataclass(frozen=True)
class ClaimResult:
    status: str
    reason: Optional[str] = None


def claim_public_audit(db: Session, project: Project, audit_id: object, token: object) -> ClaimResult:
    """Не коммитит: вызывающий делает commit вместе с созданием проекта.

    Причины отказа не различают «аудита нет» и «токен неверный», чтобы по ответу нельзя было
    перебирать существующие идентификаторы.
    """
    if not is_valid_audit_id(audit_id):
        return ClaimResult(REJECTED, "invalid_claim")
    row = db.get(PublicAuditResult, audit_id)
    if row is None or row.mode != "public" or not token_matches(row.claim_token_hash, token):
        return ClaimResult(REJECTED, "invalid_claim")
    if row.status == "expired":
        return ClaimResult(REJECTED, "invalid_claim")
    if row.project_id:
        return ClaimResult(REJECTED, "already_claimed")
    if not scope_of(project).contains(row.root_url):
        return ClaimResult(REJECTED, "out_of_scope")

    claimed = db.execute(
        update(PublicAuditResult).execution_options(synchronize_session=False)
        .where(
            PublicAuditResult.audit_id == audit_id,
            PublicAuditResult.project_id.is_(None),
            PublicAuditResult.claim_token_hash == row.claim_token_hash,
        )
        .values(project_id=project.id, claim_token_hash=None)  # токен одноразовый
    )
    if claimed.rowcount != 1:
        return ClaimResult(REJECTED, "already_claimed")
    return ClaimResult(CLAIMED)
