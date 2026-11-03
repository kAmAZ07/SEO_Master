"""FF-Score `ff_score_v1`: формулы компонентов, веса и честная обработка недостающих данных.

Формулы компонентов и веса 0.30 / 0.40 / 0.30 не менялись относительно прежней реализации:
при полном наборе входов балл совпадает с прежним. Изменилось одно: отсутствующий вход больше
не заменяется числом (50, 30 дней, 0). Компонент без всех своих входов недоступен, а полный балл
при недоступном компоненте равен `null` со статусом `insufficient_data` (ТЗ §9.4).

Частичный балл существует отдельно под именем `ff_score_partial_v1`: он считается только по
доступным компонентам, веса пересчитаны явно, у него есть покрытие и пометка, что его нельзя
сравнивать с полным. Молчаливой перенормировки нет.

Пороги 40 и 60 — эвристические режимы, а не прогноз роста или выручки.
"""
from __future__ import annotations

from typing import Any, Mapping

from services.semantic_service.scoring.readings import (
    Reading,
    complete_readings,
)

FORMULA_VERSION = "ff_score_v1"
PARTIAL_FORMULA_VERSION = "ff_score_partial_v1"
WEIGHTS: Mapping[str, float] = {"freshness": 0.30, "familiarity": 0.40, "quality": 0.30}
THRESHOLDS: Mapping[str, float] = {"rescue_lt": 40.0, "growth_gt": 60.0}
COMPONENT_INPUTS: Mapping[str, tuple[str, ...]] = {
    "freshness": ("freshness_days_since_update", "serp_shift", "link_velocity"),
    "familiarity": ("semantic_distance", "keyword_coverage", "eeat_score"),
    "quality": ("cwv_grade", "broken_links_count", "schema_errors_count"),
}
CWV_SCORES: Mapping[str, float] = {"good": 90.0, "needs_improvement": 60.0, "poor": 30.0}

STATUS_COMPLETE = "complete"
STATUS_INSUFFICIENT = "insufficient_data"
HEURISTIC_NOTE = (
    "Эвристическая оценка приложения, не прогноз позиций, трафика или выручки "
    "и не доказанная причинная связь."
)


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _freshness_component(days_since_update: int, serp_shift: float, link_velocity: float) -> float:
    freshness = _clamp(100.0 - (days_since_update / 180.0) * 100.0)
    serp = _clamp(50.0 + (-serp_shift) * 10.0)
    lv = _clamp(link_velocity * 20.0)
    return _clamp(0.5 * freshness + 0.3 * serp + 0.2 * lv)


def _familiarity_component(semantic_distance: float, keyword_coverage: float, eeat_score: float) -> float:
    dist_score = _clamp(100.0 - semantic_distance)
    cov = _clamp(keyword_coverage)
    eeat = _clamp(eeat_score)
    return _clamp(0.5 * dist_score + 0.3 * cov + 0.2 * eeat)


def _quality_component(cwv_grade: str, broken_links_count: int, schema_errors_count: int) -> float:
    cwv = CWV_SCORES[cwv_grade]
    links_penalty = min(60.0, broken_links_count * 6.0)
    schema_penalty = min(50.0, schema_errors_count * 5.0)
    return _clamp(cwv - links_penalty * 0.6 - schema_penalty * 0.4)


def _compute(component: str, r: Mapping[str, Reading]) -> float:
    values = [r[name].value for name in COMPONENT_INPUTS[component]]
    if component == "freshness":
        return _freshness_component(values[0], values[1], values[2])  # type: ignore[arg-type]
    if component == "familiarity":
        return _familiarity_component(values[0], values[1], values[2])  # type: ignore[arg-type]
    return _quality_component(values[0], values[1], values[2])  # type: ignore[arg-type]


def mode_for(score: float) -> dict[str, Any]:
    """Эвристический режим по порогам. Не гарантия роста."""
    if score < THRESHOLDS["rescue_lt"]:
        name = "rescue"
    elif score > THRESHOLDS["growth_gt"]:
        name = "growth"
    else:
        name = "maintain"
    return {"name": name, "heuristic": True, "thresholds": dict(THRESHOLDS)}


def _missing_reason(reading: Reading) -> str:
    if reading.availability == "unavailable":
        return reading.reason or "not_provided"
    # Значение есть, но оно оценочное/прокси/мок: в полный балл не входит.
    return f"excluded_{reading.availability}"


def _component_report(component: str, r: Mapping[str, Reading]) -> dict[str, Any]:
    inputs = COMPONENT_INPUTS[component]
    missing = [name for name in inputs if not r[name].counted]
    reasons = {name: _missing_reason(r[name]) for name in missing}
    available = not missing
    return {
        "status": "available" if available else "unavailable",
        "score": round(_compute(component, r), 2) if available else None,
        "weight": WEIGHTS[component],
        "inputs": list(inputs),
        "missing_inputs": missing,
        "missing_reasons": reasons,
    }


def calculate_ff_score(readings: Mapping[str, Reading]) -> dict[str, Any]:
    """Считает `ff_score_v1`. Не выдумывает данные: см. docstring модуля."""
    r = complete_readings(readings)
    components = {name: _component_report(name, r) for name in COMPONENT_INPUTS}
    available = [name for name, c in components.items() if c["status"] == "available"]
    missing_components = [name for name in COMPONENT_INPUTS if name not in available]

    full = len(available) == len(COMPONENT_INPUTS)
    ff_score: float | None = None
    mode: dict[str, Any] | None = None
    if full:
        total = sum(components[name]["score"] * WEIGHTS[name] for name in COMPONENT_INPUTS)
        ff_score = round(_clamp(total), 2)
        mode = mode_for(ff_score)

    partial: dict[str, Any] | None = None
    if available and not full:
        weight_sum = sum(WEIGHTS[name] for name in available)
        applied = {name: round(WEIGHTS[name] / weight_sum, 4) for name in available}
        partial_score = _clamp(sum(components[name]["score"] * WEIGHTS[name] / weight_sum for name in available))
        partial = {
            "formula_version": PARTIAL_FORMULA_VERSION,
            "score": round(partial_score, 2),
            "components_used": available,
            "weights_applied": applied,
            "coverage": round(weight_sum, 4),
            "comparable_to_full_score": False,
            "note": "Частичный балл по доступным компонентам; его нельзя сравнивать с полным ff_score_v1.",
        }

    missing_inputs = sorted({name for c in components.values() for name in c["missing_inputs"]})
    return {
        "formula_version": FORMULA_VERSION,
        "status": STATUS_COMPLETE if full else STATUS_INSUFFICIENT,
        "ff_score": ff_score,
        "components": components,
        "weights": dict(WEIGHTS),
        "thresholds": dict(THRESHOLDS),
        "mode": mode,
        "coverage": {
            "components_available": len(available),
            "components_total": len(COMPONENT_INPUTS),
            "weight_available": round(sum(WEIGHTS[name] for name in available), 4),
            "missing_components": missing_components,
            "missing_inputs": missing_inputs,
        },
        "partial": partial,
        "heuristic": True,
        "note": HEURISTIC_NOTE,
        "inputs": {name: r[name].as_dict() for name in r},
    }
