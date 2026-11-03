"""Предусловия и ожидаемые проверки выводятся из изменений, а не пишутся вручную.

Строитель использует эти функции при создании версии, валидатор сравнивает с ними готовую версию:
предусловие нельзя ослабить, а ожидаемую проверку подменить, не изменив `content_hash`.
"""
from __future__ import annotations

from typing import Iterable

from services.semantic_service.proposals.model import (
    Change,
    ChangeMode,
    EvidenceRef,
    ExpectedCheck,
    Precondition,
    Target,
    sha256_text,
)


def derive_preconditions(project_id: str, target: Target, evidence: Iterable[EvidenceRef], changes: Iterable[Change]) -> tuple[Precondition, ...]:
    items: list[Precondition] = [Precondition(kind="target_in_project", project_id=project_id, origin=target.origin)]
    for change in changes:
        if change.mode is ChangeMode.UNCHANGED:
            continue
        items.append(
            Precondition(
                kind="before_digest",
                path=change.path,
                state=change.before_state,
                sha256=sha256_text(change.before) if change.before is not None else None,
            )
        )
    items.extend(Precondition(kind="evidence_hash", evidence_id=e.evidence_id, sha256=e.sha256) for e in evidence)
    return tuple(items)


def derive_expected_checks(target: Target, changes: Iterable[Change]) -> tuple[ExpectedCheck, ...]:
    items: list[ExpectedCheck] = [ExpectedCheck(kind="target_http_ok", url=target.url)]
    for change in changes:
        if change.mode is ChangeMode.SET and change.after is not None:
            items.append(ExpectedCheck(kind="field_equals_after", path=change.path, sha256=sha256_text(change.after)))
        elif change.mode is ChangeMode.REMOVE:
            items.append(ExpectedCheck(kind="field_removed", path=change.path))
    return tuple(items)
