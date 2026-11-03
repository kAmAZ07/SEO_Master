"""Наблюдения (readings) для FF-Score и E-E-A-T: значение, единица, источник и доступность.

Модуль чистый: без БД, сети и часов. Недостающие данные здесь не заменяются числами:
отсутствие значения всегда выражено доступностью `unavailable` и причиной.

Доступность:
  * `measured`, `provided`, `derived` — значение участвует в полном балле;
  * `estimated`, `proxy`, `mock`, `sample` — значение хранится и показывается, но в полный балл
    не входит: такие данные нельзя смешивать с измеренными (ТЗ §8.4, §9.4);
  * `unavailable` — значения нет.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

COUNTED_AVAILABILITY = frozenset({"measured", "provided", "derived"})
EXCLUDED_AVAILABILITY = frozenset({"estimated", "proxy", "mock", "sample"})
UNAVAILABLE = "unavailable"
KNOWN_AVAILABILITY = COUNTED_AVAILABILITY | EXCLUDED_AVAILABILITY | {UNAVAILABLE}

CWV_GRADES = ("good", "needs_improvement", "poor")

# имя -> (единица, минимум, максимум, целое)
NUMERIC_SPECS: Mapping[str, tuple[str, float, float, bool]] = {
    "freshness_days_since_update": ("days", 0, 3650, True),
    "serp_shift": ("positions", -50.0, 50.0, False),
    "link_velocity": ("links_per_period", 0.0, 100.0, False),
    "semantic_distance": ("percent", 0.0, 100.0, False),
    "keyword_coverage": ("percent", 0.0, 100.0, False),
    "eeat_score": ("score_0_100", 0.0, 100.0, False),
    "broken_links_count": ("count", 0, 1_000_000, True),
    "schema_errors_count": ("count", 0, 1_000_000, True),
}
ENUM_SPECS: Mapping[str, tuple[str, tuple[str, ...]]] = {
    "cwv_grade": ("grade", CWV_GRADES),
}
ALL_INPUTS = tuple(NUMERIC_SPECS) + tuple(ENUM_SPECS)


class ReadingError(ValueError):
    """Недопустимое наблюдение. `code` стабилен и годится для ответа API."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class Reading:
    name: str
    unit: str
    value: float | int | str | None = None
    availability: str = UNAVAILABLE
    source: str | None = None
    observed_at: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.availability not in KNOWN_AVAILABILITY:
            raise ReadingError("availability_invalid", self.name)
        if self.availability == UNAVAILABLE:
            if self.value is not None:
                raise ReadingError("unavailable_with_value", self.name)
        elif self.value is None:
            raise ReadingError("value_missing", self.name)

    @property
    def counted(self) -> bool:
        return self.availability in COUNTED_AVAILABILITY

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "unit": self.unit,
            "availability": self.availability,
            "source": self.source,
            "observed_at": self.observed_at,
            "reason": self.reason,
        }


def _validate_observed_at(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise ReadingError("observed_at_invalid", name) from None
    return value


def unavailable(name: str, reason: str) -> Reading:
    """Наблюдение, которого нет. Единица берётся из спецификации входа."""
    return Reading(name=name, unit=_unit_of(name), reason=reason)


def _unit_of(name: str) -> str:
    if name in NUMERIC_SPECS:
        return NUMERIC_SPECS[name][0]
    if name in ENUM_SPECS:
        return ENUM_SPECS[name][0]
    raise ReadingError("input_unknown", name)


def make_reading(
    name: str,
    value: Any,
    *,
    availability: str = "provided",
    source: str | None = None,
    observed_at: str | None = None,
    reason: str | None = None,
) -> Reading:
    """Проверяет значение по спецификации входа. NaN, Infinity и выход за диапазон отвергаются."""
    if value is None:
        return unavailable(name, reason or "not_provided")
    unit = _unit_of(name)
    observed_at = _validate_observed_at(observed_at, name)
    if name in ENUM_SPECS:
        allowed = ENUM_SPECS[name][1]
        if not isinstance(value, str) or value.lower() not in allowed:
            raise ReadingError("value_invalid", name)
        return Reading(name, unit, value.lower(), availability, source, observed_at, reason)

    _, low, high, integer = NUMERIC_SPECS[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReadingError("value_invalid", name)
    if isinstance(value, float) and not math.isfinite(value):
        raise ReadingError("value_not_finite", name)
    if not low <= value <= high:
        raise ReadingError("value_out_of_range", name)
    if integer:
        if isinstance(value, float) and not value.is_integer():
            raise ReadingError("value_not_integer", name)
        value = int(value)
    else:
        value = float(value)
    return Reading(name, unit, value, availability, source, observed_at, reason)


def complete_readings(readings: Mapping[str, Reading]) -> dict[str, Reading]:
    """Все входы формулы; отсутствующие становятся `unavailable`, а не значением по умолчанию."""
    unknown = set(readings) - set(ALL_INPUTS)
    if unknown:
        raise ReadingError("input_unknown", sorted(unknown)[0])
    return {name: readings.get(name) or unavailable(name, "not_provided") for name in ALL_INPUTS}
