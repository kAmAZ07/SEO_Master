"""Сборка входов FF-Score из запроса, сохранённого анализа и данных аудита.

Модуль чистый: без БД, сети и часов. Его задача — превратить разрозненные поля запроса в
`Reading` с честной доступностью и ничего не придумывать:

  * отсутствующее значение остаётся `unavailable` с причиной, а не заменяется 50, 30 дням или 0;
  * источник вида `estimate`, `mock`, `sample`, `proxy` или `*_degraded` понижает доступность
    значения: оно показывается, но в полный балл не входит (ТЗ §8.4, §9.4);
  * нулевое число найденных ссылок или упоминаний бренда без указанного источника не считается
    наблюдением: счётчик, который никто не измерял, равен «не проверялось», а не «нет»;
  * семантическая близость и покрытие ключей, посчитанные без SERP-текстов или без ключей,
    недоступны: прежний анализ возвращал для них 100 и 0 как заглушки.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from services.semantic_service.scoring.readings import (
    CWV_GRADES,
    KNOWN_AVAILABILITY,
    Reading,
    ReadingError,
    make_reading,
    unavailable,
)

# Слова источника, которые прямо называют качество данных.
_SOURCE_AVAILABILITY = {
    "estimate": "estimated",
    "estimated": "estimated",
    "mock": "mock",
    "sample": "sample",
    "proxy": "proxy",
    "measured": "measured",
    "derived": "derived",
    "provided": "provided",
}
_BROKEN_LINK_CODES = frozenset({"broken_link_404"})
_SCHEMA_PREFIXES = ("jsonld_",)
_COUNT_SIGNALS = ("backlinks_count", "brand_mentions", "authoritative_outbound_links")
_CWV_ORDER = {"poor": 0, "needs_improvement": 1, "good": 2}


@dataclass(frozen=True)
class StoredAnalysis:
    """Последний сохранённый семантический анализ проекта."""

    semantic_distance: Mapping[str, Any] | None = None
    keyword_coverage: Mapping[str, Any] | None = None
    observed_at: str | None = None


def availability_for(source: str | None, explicit: str | None = None) -> str:
    """Доступность значения по названию источника или явному указанию вызывающего.

    Явное указание главнее и обязано быть известным. Метка источника с суффиксом `_degraded`
    означает «получено по урезанным данным» и считается оценочным значением."""
    if explicit is not None:
        if explicit not in KNOWN_AVAILABILITY or explicit == "unavailable":
            raise ReadingError("availability_invalid", str(explicit))
        return explicit
    label = (source or "").strip().lower()
    if label in _SOURCE_AVAILABILITY:
        return _SOURCE_AVAILABILITY[label]
    if label.endswith("_degraded"):
        return "estimated"
    return "provided"


def _source_label(sources: Mapping[str, Any], name: str, default: str) -> str:
    value = sources.get(name)
    return value if isinstance(value, str) and value.strip() else default


def _provided(name: str, value: Any, sources: Mapping[str, Any], availability: Mapping[str, Any]) -> Reading:
    """Значение, пришедшее в запросе. `None` — «не передано»."""
    if value is None:
        return unavailable(name, "not_provided")
    label = _source_label(sources, name, "request")
    return make_reading(
        name,
        value,
        availability=availability_for(sources.get(name) if isinstance(sources.get(name), str) else None, availability.get(name)),
        source=label,
    )


def count_signal(value: int | None, name: str, sources: Mapping[str, Any]) -> int | None:
    """Счётчик для E-E-A-T. Ноль без источника — не наблюдение (см. docstring модуля)."""
    if value is None:
        return None
    if value == 0 and not _source_label(sources, name, ""):
        return None
    return value


def _semantic_distance(analysis: StoredAnalysis | None) -> Reading:
    if analysis is None or analysis.semantic_distance is None:
        return unavailable("semantic_distance", "analysis_missing")
    data = analysis.semantic_distance
    if not data.get("n"):
        return unavailable("semantic_distance", "serp_top10_texts_missing")
    value = data.get("semantic_distance")
    if value is None:
        return unavailable("semantic_distance", "analysis_missing")
    return make_reading("semantic_distance", value, availability="derived", source="semantic_analysis", observed_at=analysis.observed_at)


def _keyword_coverage(analysis: StoredAnalysis | None) -> Reading:
    if analysis is None or analysis.keyword_coverage is None:
        return unavailable("keyword_coverage", "analysis_missing")
    data = analysis.keyword_coverage
    if not data.get("total"):
        return unavailable("keyword_coverage", "keywords_missing")
    value = data.get("coverage")
    if value is None:
        return unavailable("keyword_coverage", "analysis_missing")
    return make_reading("keyword_coverage", value, availability="derived", source="semantic_analysis", observed_at=analysis.observed_at)


def cwv_grade_from_summary(summary: Mapping[str, Any] | None) -> Reading:
    """Худшая оценка по LCP, INP и CLS. INP заменил FID в Core Web Vitals (март 2024), поэтому FID
    не подменяет INP: если INP неизвестен, оценка по FID считается прокси и в полный балл не входит."""
    if not isinstance(summary, Mapping) or not isinstance(summary.get("cwv"), Mapping):
        return unavailable("cwv_grade", "audit_summary_missing")
    cwv = summary["cwv"]

    def grade(key: str) -> str | None:
        value = str(cwv.get(key) or "").lower()
        return value if value in CWV_GRADES else None

    lcp, inp, cls, fid = grade("LCP_grade"), grade("INP_grade"), grade("CLS_grade"), grade("FID_grade")
    metrics = {"LCP": lcp, "INP": inp, "CLS": cls}
    known = {k: v for k, v in metrics.items() if v is not None}
    if len(known) == 3:
        worst = min(known.values(), key=lambda g: _CWV_ORDER[g])
        return make_reading("cwv_grade", worst, availability="measured", source="audit_summary")
    if inp is None and fid is not None and lcp is not None and cls is not None:
        worst = min((lcp, cls, fid), key=lambda g: _CWV_ORDER[g])
        return make_reading("cwv_grade", worst, availability="proxy", source="audit_summary", reason="inp_missing_fid_used")
    if known:
        worst = min(known.values(), key=lambda g: _CWV_ORDER[g])
        missing = ",".join(sorted(k for k, v in metrics.items() if v is None))
        return make_reading("cwv_grade", worst, availability="proxy", source="audit_summary", reason=f"cwv_partial:{missing}")
    return unavailable("cwv_grade", "cwv_not_measured")


def count_findings(findings: list[Any], *, codes: frozenset[str] = frozenset(), prefixes: tuple[str, ...] = ()) -> int:
    total = 0
    for finding in findings:
        if not isinstance(finding, Mapping):
            continue
        code = str(finding.get("code") or "")
        if code in codes or (prefixes and code.startswith(prefixes)):
            total += 1
    return total


def build_ff_readings(
    *,
    values: Mapping[str, Any],
    sources: Mapping[str, Any],
    availability: Mapping[str, Any],
    audit_summary: Mapping[str, Any] | None,
    audit_findings: list[Any] | None,
    analysis: StoredAnalysis | None,
    eeat_result: Mapping[str, Any],
) -> dict[str, Reading]:
    """Все девять входов `ff_score_v1`. `values` — поля запроса (None = не передано)."""
    readings: dict[str, Reading] = {}
    for name in ("freshness_days_since_update", "serp_shift", "link_velocity"):
        readings[name] = _provided(name, values.get(name), sources, availability)

    for name, stored in (("semantic_distance", _semantic_distance), ("keyword_coverage", _keyword_coverage)):
        if values.get(name) is not None:
            readings[name] = _provided(name, values[name], sources, availability)
        else:
            readings[name] = stored(analysis)

    if eeat_result.get("score") is not None and eeat_result.get("status") == "complete":
        readings["eeat_score"] = make_reading("eeat_score", eeat_result["score"], availability="derived", source="eeat_v1")
    else:
        readings["eeat_score"] = unavailable("eeat_score", "eeat_insufficient_data")

    if values.get("cwv_grade"):
        readings["cwv_grade"] = _provided("cwv_grade", values["cwv_grade"], sources, availability)
    else:
        readings["cwv_grade"] = cwv_grade_from_summary(audit_summary)

    counts = (
        ("broken_links_count", dict(codes=_BROKEN_LINK_CODES)),
        ("schema_errors_count", dict(prefixes=_SCHEMA_PREFIXES)),
    )
    for name, selector in counts:
        if values.get(name) is not None:
            readings[name] = _provided(name, values[name], sources, availability)
        elif audit_findings is None:
            readings[name] = unavailable(name, "audit_findings_not_provided")
        else:
            readings[name] = make_reading(name, count_findings(audit_findings, **selector), availability="derived", source="audit_findings")
    return readings


def eeat_signals_from_request(values: Mapping[str, Any], sources: Mapping[str, Any]) -> dict[str, Any]:
    """Сигналы E-E-A-T: нули-счётчики без источника превращаются в «не проверялось»."""
    signals = {
        "backlinks_count": values.get("backlinks_count"),
        "brand_mentions": values.get("brand_mentions"),
        "authoritative_outbound_links": values.get("authoritative_outbound_links"),
        "has_https": values.get("has_https"),
        "has_privacy_policy": values.get("has_privacy_policy"),
        "has_contacts": values.get("has_contacts"),
        "has_author_schema": values.get("has_author_schema"),
    }
    for name in _COUNT_SIGNALS:
        signals[name] = count_signal(signals[name], name, sources)
    return signals
