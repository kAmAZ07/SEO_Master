"""Отображение результатов FF-Score и E-E-A-T на строки БД и обратно. Модуль чистый.

В `ff_scores.inputs` для формулы `ff_score_v1` лежат не плоские значения, а полный отчёт по входам
(`readings`) и всё, что нужно показать без пересчёта: покрытие, частичный балл, режим, веса.
Строки, записанные до AL-5, имеют `formula_version = 'legacy'` и статус `legacy_estimated`: их баллы
считались с подстановкой оценок вместо недостающих данных, поэтому они возвращаются как есть,
но с явной пометкой, что это не полный балл.
"""
from __future__ import annotations

from typing import Any, Mapping

LEGACY_NOTE = (
    "Строка записана до введения честной обработки данных: недостающие входы заменялись оценками "
    "(50, 30 дней, 0). Это не полный балл."
)


def ff_row_fields(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ff_score": result["ff_score"],
        "status": result["status"],
        "formula_version": result["formula_version"],
        "components": result["components"],
        "inputs": {
            "readings": result["inputs"],
            "coverage": result["coverage"],
            "partial": result["partial"],
            "mode": result["mode"],
            "weights": result["weights"],
            "heuristic": result["heuristic"],
            "note": result["note"],
        },
        "thresholds": result["thresholds"],
    }


def ff_view(row: Any) -> dict[str, Any]:
    """Представление строки `ff_scores` для API."""
    view: dict[str, Any] = {
        "id": row.score_id,
        "status": row.status,
        "formula_version": row.formula_version,
        "score": row.ff_score,
        "components": row.components,
        "thresholds": row.thresholds,
        "created_at": row.created_at,
    }
    if row.status == "legacy_estimated":
        view.update(inputs=row.inputs, legacy_estimated=True, heuristic=True, note=LEGACY_NOTE)
        return view
    stored = row.inputs if isinstance(row.inputs, Mapping) else {}
    view.update(
        inputs=stored.get("readings", {}),
        coverage=stored.get("coverage"),
        partial=stored.get("partial"),
        mode=stored.get("mode"),
        weights=stored.get("weights"),
        heuristic=stored.get("heuristic", True),
        note=stored.get("note"),
    )
    return view


def eeat_row_fields(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "score": result["score"],
        "status": result["status"],
        "formula_version": result["formula_version"],
        "breakdown": result["breakdown"],
        "signals": {
            **result["signals"],
            "partial": result["partial"],
            "missing_inputs": result["missing_inputs"],
            "heuristic": result["heuristic"],
        },
    }


def eeat_view(row: Any) -> dict[str, Any]:
    signals = row.signals if isinstance(row.signals, Mapping) else {}
    view: dict[str, Any] = {
        "id": row.score_id,
        "status": row.status,
        "formula_version": row.formula_version,
        "score": row.score,
        "breakdown": row.breakdown,
        "partial": signals.get("partial"),
        "missing_inputs": signals.get("missing_inputs", {}),
        "heuristic": signals.get("heuristic", True),
        "signals": {k: v for k, v in signals.items() if k not in ("partial", "missing_inputs", "heuristic")},
        "created_at": row.created_at,
    }
    if row.status == "legacy_estimated":
        view.update(legacy_estimated=True, note=LEGACY_NOTE)
    return view
