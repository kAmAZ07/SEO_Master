"""E-E-A-T `eeat_v1`: четыре эвристических компонента и честная обработка неизвестных сигналов.

Это внутренняя эвристика приложения, а не оценка Google. Формулы компонентов и веса
(0.25 / 0.30 / 0.25 / 0.20) не менялись: при полном наборе сигналов результат прежний.

Изменилось обращение с неизвестным. Раньше «политика не найдена», «ссылок 0» и «упоминаний 0»
были значениями по умолчанию. Теперь `None` означает «не проверялось»: отсутствие найденной
политики или контактов не доказывает их отсутствие на сайте, а нулевые backlinks и упоминания
бренда не появляются из заглушек (ТЗ §9.4). Компонент без своих сигналов недоступен, полный
балл равен `null` со статусом `insufficient_data`, доступные компоненты показываются отдельно.
"""
from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urlparse

FORMULA_VERSION = "eeat_v1"
PARTIAL_FORMULA_VERSION = "eeat_partial_v1"
WEIGHTS: Mapping[str, float] = {
    "experience": 0.25,
    "expertise": 0.30,
    "authoritativeness": 0.25,
    "trustworthiness": 0.20,
}
STATUS_COMPLETE = "complete"
STATUS_INSUFFICIENT = "insufficient_data"


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _word_count(text: str) -> int:
    return len([w for w in re.split(r"\s+", text.strip()) if w])


def _readability_proxy(text: str) -> float:
    words = _word_count(text)
    if words == 0:
        return 0.0
    long_words = len([w for w in re.findall(r"\b\w+\b", text) if len(w) >= 7])
    return _clamp(100.0 - (long_words / max(words, 1)) * 100.0)


def _experience_score(text: str, has_author_schema: bool) -> float:
    cues = re.findall(r"\b(я|мой|моя|мы|лично|опыт|использовал|использовала)\b", text.lower())
    base = 20.0 + min(60.0, len(cues) * 5.0)
    if has_author_schema:
        base += 20.0
    return _clamp(base)


def _expertise_score(text: str, authoritative_outbound_links: int) -> float:
    wc = _word_count(text)
    read = _readability_proxy(text)
    depth = _clamp((wc / 1200.0) * 100.0)
    sources = _clamp(min(100.0, authoritative_outbound_links * 10.0))
    return _clamp(0.6 * depth + 0.4 * ((read + sources) / 2.0))


def _authoritativeness_score(backlinks_count: int, brand_mentions: int) -> float:
    bl = _clamp(min(100.0, backlinks_count / 10.0))
    bm = _clamp(min(100.0, brand_mentions * 8.0))
    return _clamp(0.7 * bl + 0.3 * bm)


def _trustworthiness_score(has_https: bool, has_privacy_policy: bool, has_contacts: bool, text: str) -> float:
    base = 0.0
    base += 40.0 if has_https else 0.0
    base += 30.0 if has_privacy_policy else 0.0
    base += 20.0 if has_contacts else 0.0
    spam = len(re.findall(r"\b(купить|дешево|скидка|топ)\b", text.lower()))
    penalty = min(30.0, spam * 6.0)
    return _clamp(base + 10.0 - penalty)


def _signal(value: Any, source: str | None = None) -> dict[str, Any]:
    if value is None:
        return {"value": None, "availability": "unavailable", "reason": "not_checked"}
    return {"value": value, "availability": "provided", "reason": None} | ({"source": source} if source else {})


def analyze_eeat(
    text: str,
    root_url: str,
    backlinks_count: int | None = None,
    has_https: bool | None = None,
    has_privacy_policy: bool | None = None,
    has_contacts: bool | None = None,
    has_author_schema: bool | None = None,
    authoritative_outbound_links: int | None = None,
    brand_mentions: int | None = None,
) -> dict[str, Any]:
    """Считает `eeat_v1`. `None` у сигнала — «не проверялось», а не «нет»."""
    text = text or ""
    words = _word_count(text)
    scheme = urlparse(root_url).scheme
    # HTTPS выводится из адреса, который известен всегда; явное False из запроса тоже учитывается.
    https_known = scheme in ("http", "https")
    https = (scheme == "https") and (has_https is not False) if https_known else None

    needs_text = {"text": words > 0}
    available_inputs: dict[str, dict[str, bool]] = {
        "experience": {**needs_text, "has_author_schema": has_author_schema is not None},
        "expertise": {**needs_text, "authoritative_outbound_links": authoritative_outbound_links is not None},
        "authoritativeness": {
            "backlinks_count": backlinks_count is not None,
            "brand_mentions": brand_mentions is not None,
        },
        "trustworthiness": {
            **needs_text,
            "has_https": https is not None,
            "has_privacy_policy": has_privacy_policy is not None,
            "has_contacts": has_contacts is not None,
        },
    }
    missing = {
        component: sorted(name for name, ok in inputs.items() if not ok)
        for component, inputs in available_inputs.items()
    }

    breakdown: dict[str, float | None] = dict.fromkeys(WEIGHTS)
    if not missing["experience"]:
        breakdown["experience"] = round(_experience_score(text, bool(has_author_schema)), 2)
    if not missing["expertise"]:
        breakdown["expertise"] = round(_expertise_score(text, int(authoritative_outbound_links or 0)), 2)
    if not missing["authoritativeness"]:
        breakdown["authoritativeness"] = round(
            _authoritativeness_score(int(backlinks_count or 0), int(brand_mentions or 0)), 2
        )
    if not missing["trustworthiness"]:
        breakdown["trustworthiness"] = round(
            _trustworthiness_score(bool(https), bool(has_privacy_policy), bool(has_contacts), text), 2
        )

    used = [name for name, value in breakdown.items() if value is not None]
    full = len(used) == len(WEIGHTS)
    score: float | None = None
    if full:
        score = round(_clamp(sum(breakdown[name] * WEIGHTS[name] for name in WEIGHTS)), 2)  # type: ignore[operator]

    partial: dict[str, Any] | None = None
    if used and not full:
        weight_sum = sum(WEIGHTS[name] for name in used)
        partial = {
            "formula_version": PARTIAL_FORMULA_VERSION,
            "score": round(_clamp(sum(breakdown[name] * WEIGHTS[name] / weight_sum for name in used)), 2),  # type: ignore[operator]
            "components_used": used,
            "weights_applied": {name: round(WEIGHTS[name] / weight_sum, 4) for name in used},
            "coverage": round(weight_sum, 4),
            "comparable_to_full_score": False,
        }

    return {
        "formula_version": FORMULA_VERSION,
        "status": STATUS_COMPLETE if full else STATUS_INSUFFICIENT,
        "score": score,
        "breakdown": breakdown,
        "partial": partial,
        "missing_inputs": {component: names for component, names in missing.items() if names},
        "heuristic": True,
        "signals": {
            "weights": dict(WEIGHTS),
            "word_count": words,
            "readability_proxy": round(_readability_proxy(text), 2) if words else None,
            "backlinks_count": _signal(backlinks_count),
            "brand_mentions": _signal(brand_mentions),
            "authoritative_outbound_links": _signal(authoritative_outbound_links),
            "has_https": _signal(https, "root_url"),
            "has_privacy_policy": _signal(has_privacy_policy),
            "has_contacts": _signal(has_contacts),
            "has_author_schema": _signal(has_author_schema),
        },
    }
