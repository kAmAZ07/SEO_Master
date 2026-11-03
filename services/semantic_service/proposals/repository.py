"""PostgreSQL-реализация ProposalStore: только добавление версий, уникальность (recommendation_id, version).

Версия хранится целиком в `payload` (канонический JSON модели) и частично в колонках для поиска.
При чтении payload заново проходит валидацию модели, а `content_hash` пересчитывается: испорченная
строка не превратится молча в «валидное» предложение.
"""
from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from services.semantic_service.db.models import RecommendationVersionRow
from services.semantic_service.proposals.model import (
    OperationType,
    RecommendationVersion,
    compute_content_hash,
    parse_utc,
)
from services.semantic_service.proposals.store import VersionConflict

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


# Нарушения, означающие «такую версию уже записал другой запрос»: повтор с новым номером безопасен.
CONFLICT_CONSTRAINTS = frozenset({
    "uq_recommendation_versions_version", "uq_recommendation_versions_first", "recommendation_versions_pkey",
})


class CorruptedVersion(Exception):
    """Строка в БД не соответствует модели или своему content_hash."""


def _violated_constraint(error: IntegrityError) -> str | None:
    """Имя нарушенного ограничения: asyncpg отдаёт его атрибутом, psycopg2 — через `diag`."""
    original = error.orig
    diag = getattr(original, "diag", None)
    name = getattr(diag, "constraint_name", None) or getattr(original, "constraint_name", None)
    if name is None and original is not None and getattr(original, "__cause__", None) is not None:
        name = getattr(original.__cause__, "constraint_name", None)
    return name


def row_from_version(version: RecommendationVersion) -> RecommendationVersionRow:
    return RecommendationVersionRow(
        version_id=version.version_id,
        recommendation_id=version.recommendation_id,
        version=version.version,
        previous_version_id=version.previous_version_id,
        project_id=version.project_id,
        task_id=version.task_id,
        target_url=version.target.url,
        target_origin=version.target.origin,
        operation_type=version.operation_type.value,
        execution_mode=version.execution_mode.value,
        content_hash=version.content_hash,
        schema_version=version.schema_version,
        payload=version.model_dump(mode="json"),
        created_at=parse_utc(version.created_at),
    )


def version_from_row(row: RecommendationVersionRow) -> RecommendationVersion:
    try:
        version = RecommendationVersion.model_validate(row.payload)
    except ValueError as exc:
        raise CorruptedVersion(row.version_id) from exc
    if version.content_hash != compute_content_hash(version) or version.content_hash != row.content_hash:
        raise CorruptedVersion(row.version_id)
    return version


class SqlProposalStore:
    def __init__(self, session_factory: SessionFactory):
        self._session_factory = session_factory

    async def latest(self, project_id: str, target_url: str, operation: OperationType) -> RecommendationVersion | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(RecommendationVersionRow)
                .where(
                    RecommendationVersionRow.project_id == project_id,
                    RecommendationVersionRow.target_url == target_url,
                    RecommendationVersionRow.operation_type == operation.value,
                )
                .order_by(RecommendationVersionRow.version.desc())
                .limit(1)
            )
            row = result.scalar_one_or_none()
            return version_from_row(row) if row else None

    async def append(self, version: RecommendationVersion) -> None:
        async with self._session_factory() as session:
            session.add(row_from_version(version))
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                if _violated_constraint(exc) not in CONFLICT_CONSTRAINTS:
                    raise  # нарушение других ограничений — ошибка данных, а не гонка версий
                raise VersionConflict(version.version_id) from exc

    async def get(self, version_id: str) -> RecommendationVersion | None:
        async with self._session_factory() as session:
            row = await session.get(RecommendationVersionRow, version_id)
            return version_from_row(row) if row else None

    async def history(self, recommendation_id: str) -> list[RecommendationVersion]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(RecommendationVersionRow)
                .where(RecommendationVersionRow.recommendation_id == recommendation_id)
                .order_by(RecommendationVersionRow.version.asc())
            )
            return [version_from_row(row) for row in result.scalars()]

    async def list_for_project(self, project_id: str, limit: int = 100) -> list[RecommendationVersion]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(RecommendationVersionRow)
                .where(RecommendationVersionRow.project_id == project_id)
                .order_by(RecommendationVersionRow.created_at.desc(), RecommendationVersionRow.version_id)
                .limit(max(1, min(limit, 500)))
            )
            return [version_from_row(row) for row in result.scalars()]
