"""Представление FF-Score для API проектов и панели. Модуль чистый: строки БД читаются, ничего не пишется.

После AL-5 полный балл бывает `null` (`status = insufficient_data`), а компоненты новой формулы
хранятся отчётами `{status, score, ...}`, не числами. Строки, записанные до AL-5 (`legacy_estimated`),
содержат плоские числа и оценки вместо недостающих данных. Недоступное значение здесь остаётся
`None`: ни `0`, ни среднее его не заменяют.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Optional

COMPONENT_NAMES = ("freshness", "familiarity", "quality")
COMPLETE = "complete"
LEGACY = "legacy_estimated"


def _finite(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def component_value(raw: object) -> Optional[float]:
    """Число для плоского (legacy) значения; `score` для доступного отчёта; иначе `None`."""
    if isinstance(raw, Mapping):
        return _finite(raw.get("score")) if raw.get("status") == "available" else None
    return _finite(raw)


def ff_score_view(row: Any, timestamp: str) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    components = row.components if isinstance(row.components, Mapping) else {}
    stored = row.inputs if isinstance(row.inputs, Mapping) else {}
    status = row.status
    # Полный балл показывается только у строк, где он действительно есть; расхождение статуса и значения не маскируется.
    total = _finite(row.ff_score) if status in (COMPLETE, LEGACY) else None
    view: dict[str, Any] = {
        "total": total,
        "status": status,
        "formulaVersion": row.formula_version,
        "heuristic": True if status == LEGACY else bool(stored.get("heuristic", True)),
        "legacyEstimated": status == LEGACY,
        "timestamp": timestamp,
    }
    for name in COMPONENT_NAMES:
        view[name] = component_value(components.get(name))
    if status != LEGACY:
        view["partial"] = stored.get("partial")
        view["coverage"] = stored.get("coverage")
    return view


def average_full_score(rows: Iterable[Any]) -> tuple[Optional[float], int]:
    """Среднее только по полным баллам (`complete`). Оценочные legacy-строки и `null` не участвуют.

    Возвращает `(среднее или None, число учтённых проектов)`.
    """
    scores = [
        score for row in rows
        if row is not None and row.status == COMPLETE and (score := _finite(row.ff_score)) is not None
    ]
    return (round(sum(scores) / len(scores), 2), len(scores)) if scores else (None, 0)
