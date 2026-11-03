"""Хранилище версий: только добавление. Версия не изменяется и не заменяется.

`VersionConflict` означает, что такая пара (recommendation_id, version) уже есть: проигравший в гонке
перечитывает последнюю версию и повторяет попытку, а не перезаписывает чужую.
"""
from __future__ import annotations

from typing import Protocol

from services.semantic_service.proposals.model import OperationType, RecommendationVersion


class VersionConflict(Exception):
    """Версия с такими recommendation_id и version уже существует."""


class ProposalStore(Protocol):
    async def latest(self, project_id: str, target_url: str, operation: OperationType) -> RecommendationVersion | None: ...

    async def append(self, version: RecommendationVersion) -> None: ...

    async def get(self, version_id: str) -> RecommendationVersion | None: ...

    async def history(self, recommendation_id: str) -> list[RecommendationVersion]: ...

    async def list_for_project(self, project_id: str, limit: int = 100) -> list[RecommendationVersion]: ...


class InMemoryProposalStore:
    """Эталонная реализация контракта для тестов и локальных прогонов."""

    def __init__(self) -> None:
        self._by_id: dict[str, RecommendationVersion] = {}

    async def latest(self, project_id: str, target_url: str, operation: OperationType) -> RecommendationVersion | None:
        matches = [
            v for v in self._by_id.values()
            if v.project_id == project_id and v.target.url == target_url and v.operation_type == operation
        ]
        return max(matches, key=lambda v: v.version, default=None)

    async def append(self, version: RecommendationVersion) -> None:
        if version.version_id in self._by_id:
            raise VersionConflict("version_id")
        if any(v.recommendation_id == version.recommendation_id and v.version == version.version for v in self._by_id.values()):
            raise VersionConflict("recommendation_version")
        self._by_id[version.version_id] = version

    async def get(self, version_id: str) -> RecommendationVersion | None:
        return self._by_id.get(version_id)

    async def history(self, recommendation_id: str) -> list[RecommendationVersion]:
        return sorted((v for v in self._by_id.values() if v.recommendation_id == recommendation_id), key=lambda v: v.version)

    async def list_for_project(self, project_id: str, limit: int = 100) -> list[RecommendationVersion]:
        items = [v for v in self._by_id.values() if v.project_id == project_id]
        return sorted(items, key=lambda v: (v.created_at, v.version_id), reverse=True)[:limit]
