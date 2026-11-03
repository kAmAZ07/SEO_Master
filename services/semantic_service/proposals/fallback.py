"""Детерминированный путь: значения только из фактов страницы, без шаблонных заглушек.

Прежний fallback подставлял «<адрес> — Официальная страница», «Заголовок страницы» и
«Краткое описание страницы...». Такие тексты не имеют отношения к странице и не готовы к
публикации (ТЗ §9.3). Здесь, если фактов не хватает, возвращается `Missing` — генератор превращает
его в `needs_input`, а не в предложение.

Результат этих функций — всего лишь кандидат: он проходит тот же валидатор, что и ответы LLM.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from services.semantic_service.proposals.facts import PageFacts
from services.semantic_service.proposals.model import OperationType
from services.semantic_service.proposals.validators import TEXT_LIMITS, canonical_jsonld, clean_line

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")
_TITLE_SEPARATORS = re.compile(r"\s+(?:[—–|:]|-{1,2}|::)\s+")
_TRAILING = " ,;:-—–|/"
PROVIDER = "deterministic"
MODEL = "facts_v1"


@dataclass(frozen=True)
class Missing:
    """Не хватает фактов. `missing` — стабильные имена недостающих данных."""

    missing: tuple[str, ...]
    reason: str = "insufficient_facts"


def _one_line(value: str | None) -> str:
    return clean_line(re.sub(r"[\r\n]+", " ", value or ""))


def _sentences(text: str | None) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    return [s.strip() for s in _SENTENCE_SPLIT.split(cleaned) if s.strip()]


def _cut(value: str, limit: int) -> str:
    """Обрезка по границе слова без многоточия; хвостовые знаки убираются."""
    if len(value) <= limit:
        return value.strip(_TRAILING)
    cut = value[: limit + 1]
    space = cut.rfind(" ")
    cut = cut[:space] if space >= max(10, limit // 2) else value[:limit]
    return cut.strip(_TRAILING)


def _first_heading(facts: PageFacts) -> str:
    for heading in facts.h1:
        line = _one_line(heading)
        if line:
            return line
    return ""


def _strip_site_name(title: str, site_name: str | None) -> str:
    parts = [p.strip() for p in _TITLE_SEPARATORS.split(title) if p.strip()]
    if len(parts) > 1:
        if site_name and parts[-1].lower() == site_name.lower():
            return " — ".join(parts[:-1])
        return parts[0]
    return title


def fallback_title(facts: PageFacts) -> str | Missing:
    low, high = TEXT_LIMITS[OperationType.TITLE]
    base = _first_heading(facts)
    if not base:
        sentences = _sentences(facts.text)
        base = _one_line(sentences[0]) if sentences else ""
    if len(base) < low:
        return Missing(("h1_or_page_text",))
    site = _one_line(facts.site_name)
    if site and site.lower() not in base.lower():
        combined = f"{base} — {site}"
        if len(combined) <= high:
            return combined
    return _cut(base, high)


def fallback_description(facts: PageFacts) -> str | Missing:
    low, high = TEXT_LIMITS[OperationType.DESCRIPTION]
    source = facts.text if (facts.text and len(facts.text.strip()) >= low) else None
    existing = _one_line(facts.description)
    if source is None and existing:
        source = existing  # слишком длинное описание укорачивается, а не заменяется выдумкой
    sentences = _sentences(source)
    if not sentences:
        return Missing(("page_text",))
    chosen = ""
    for sentence in sentences:
        sentence = _one_line(sentence)
        candidate = f"{chosen} {sentence}".strip()
        if len(candidate) > high:
            if not chosen:
                chosen = _cut(sentence, high)
            break
        chosen = candidate
        if len(chosen) >= 110:
            break
    chosen = chosen.strip()
    if len(chosen) < low:
        return Missing(("page_text",))
    return chosen


def fallback_h1(facts: PageFacts) -> str | Missing:
    if len([h for h in facts.h1 if _one_line(h)]) > 1:
        return Missing(("single_h1_decision",), reason="multiple_h1_manual_review")
    low, high = TEXT_LIMITS[OperationType.H1]
    title = _one_line(facts.title)
    if title:
        candidate = _strip_site_name(title, facts.site_name)
        if low <= len(candidate) <= high:
            return candidate
    sentences = _sentences(facts.text)
    if sentences:
        first = _cut(_one_line(sentences[0]), high)
        if len(first) >= low:
            return first
    return Missing(("title_or_page_text",))


def fallback_schema(facts: PageFacts, schema_type: str, proposed_description: str | None = None) -> str | Missing:
    if schema_type == "Organization":
        name = _one_line(facts.organization_name) or _one_line(facts.site_name)
    else:
        name = _first_heading(facts) or _strip_site_name(_one_line(facts.title), facts.site_name) or _one_line(facts.site_name)
    if not name:
        return Missing(("page_name",))
    data: dict[str, str] = {"@context": "https://schema.org", "@type": schema_type, "name": name}
    description = _one_line(facts.description) or (proposed_description or "")
    if 50 <= len(description) <= 500:
        data["description"] = description
    return canonical_jsonld(data)


def parse_schema_response(text: str) -> dict | None:
    """Достаёт JSON-объект из ответа LLM (допускает обёртку ```json ... ```), иначе `None`."""
    body = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", body, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        body = fenced.group(1)
    try:
        data = json.loads(body)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
