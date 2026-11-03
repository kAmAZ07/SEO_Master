"""Входные данные генерации: факты страницы и ссылки на evidence. Всё ограничено по размеру.

Тексты и HTML страницы — недоверенные данные. Они не подставляются в промпт как инструкции
(промпт и данные разделены маркерами), а перед отправкой провайдеру вычищаются управляющие
символы и маркеры разделителя. Провайдеру уходит не больше `MAX_PROMPT_CONTENT_CHARS` знаков.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from services.semantic_service.proposals.model import (
    MAX_EVIDENCE,
    UUID_RE,
    EvidenceRef,
    ExternalEntity,
    OperationType,
)

MAX_PROMPT_CONTENT_CHARS = 6000
MAX_FACT_TEXT = 50_000
MAX_FINDING_CODES = 30

# Находки аудита, из которых выводятся типы предложений (коды совпадают с analyzers/*).
FINDING_TO_OPERATION: dict[str, OperationType] = {
    "title_missing": OperationType.TITLE,
    "title_too_short": OperationType.TITLE,
    "title_too_long": OperationType.TITLE,
    "description_missing": OperationType.DESCRIPTION,
    "description_too_short": OperationType.DESCRIPTION,
    "description_too_long": OperationType.DESCRIPTION,
    "h1_missing": OperationType.H1,
    "h1_too_short": OperationType.H1,
    "h1_multiple": OperationType.H1,
    "jsonld_missing": OperationType.SCHEMA_JSONLD,
    "jsonld_empty": OperationType.SCHEMA_JSONLD,
    "jsonld_invalid_json": OperationType.SCHEMA_JSONLD,
    "jsonld_invalid_structure": OperationType.SCHEMA_JSONLD,
    "jsonld_missing_context": OperationType.SCHEMA_JSONLD,
    "jsonld_missing_type": OperationType.SCHEMA_JSONLD,
}
GENERATED_OPERATIONS = (
    OperationType.TITLE, OperationType.DESCRIPTION, OperationType.H1, OperationType.SCHEMA_JSONLD,
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f  ﻿​‮]")
_MARKERS = re.compile(r"(?i)</?\s*page_content\s*>")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PageFacts(_Strict):
    """Что известно о странице из аудита. Отсутствующее поле — `None`, а не пустая строка."""

    title: str | None = Field(default=None, max_length=2000)
    description: str | None = Field(default=None, max_length=5000)
    h1: list[str] = Field(default_factory=list, max_length=20)
    text: str | None = Field(default=None, max_length=MAX_FACT_TEXT)
    lang: str | None = Field(default=None, max_length=16)
    site_name: str | None = Field(default=None, max_length=200)
    organization_name: str | None = Field(default=None, max_length=200)
    jsonld_present: bool | None = None
    jsonld: str | None = Field(default=None, max_length=8192)  # точная текущая строка JSON-LD, если она есть

    @field_validator("h1")
    @classmethod
    def _h1_items(cls, value: list[str]) -> list[str]:
        if any(len(item) > 2000 for item in value):
            raise ValueError("h1 item is too long")
        return value


class ProposalRequest(_Strict):
    project_id: str
    task_id: str | None = Field(default=None, max_length=128)
    target_url: str = Field(min_length=8, max_length=2048)
    entity: ExternalEntity | None = None
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=MAX_EVIDENCE)
    finding_codes: list[str] = Field(default_factory=list, max_length=MAX_FINDING_CODES)
    operations: list[OperationType] | None = None
    schema_type: Literal["WebPage", "Article", "Organization"] = "WebPage"
    facts: PageFacts = Field(default_factory=PageFacts)

    @field_validator("project_id")
    @classmethod
    def _project_uuid(cls, value: str) -> str:
        if not UUID_RE.fullmatch(value):
            raise ValueError("project_id must be a canonical lowercase UUID")
        return value

    @model_validator(mode="after")
    def _resolve_operations(self) -> "ProposalRequest":
        if self.operations is not None:
            unsupported = [o for o in self.operations if o not in GENERATED_OPERATIONS]
            if unsupported:
                raise ValueError(f"operation is not generated automatically: {unsupported[0].value}")
            if not self.operations or len(set(self.operations)) != len(self.operations):
                raise ValueError("operations must be a non-empty list without duplicates")
        return self

    def requested_operations(self) -> list[OperationType]:
        """Явный список либо типы, выведенные из находок аудита (в порядке GENERATED_OPERATIONS)."""
        if self.operations is not None:
            return list(self.operations)
        wanted = {FINDING_TO_OPERATION[c] for c in self.finding_codes if c in FINDING_TO_OPERATION}
        return [op for op in GENERATED_OPERATIONS if op in wanted]


def sanitize_for_prompt(value: str) -> str:
    """Убирает управляющие символы и маркеры разделителя: страница не должна закрывать блок данных."""
    value = unicodedata.normalize("NFC", value)
    value = _MARKERS.sub(" ", _CONTROL.sub(" ", value))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", value)).strip()


def build_prompt_content(facts: PageFacts, finding_codes: list[str]) -> str:
    """Ограниченное описание страницы для LLM: поля с подписями и фрагмент основного текста."""
    lines = []
    if facts.lang:
        lines.append(f"language: {sanitize_for_prompt(facts.lang)}")
    if facts.site_name:
        lines.append(f"site_name: {sanitize_for_prompt(facts.site_name)}")
    if facts.title is not None:
        lines.append(f"current_title: {sanitize_for_prompt(facts.title)}")
    if facts.description is not None:
        lines.append(f"current_description: {sanitize_for_prompt(facts.description)}")
    for index, heading in enumerate(facts.h1[:5]):
        lines.append(f"current_h1_{index + 1}: {sanitize_for_prompt(heading)}")
    if finding_codes:
        lines.append("audit_findings: " + ", ".join(sorted(set(sanitize_for_prompt(c) for c in finding_codes))[:20]))
    header = "\n".join(lines)
    budget = max(0, MAX_PROMPT_CONTENT_CHARS - len(header) - 20)
    body = sanitize_for_prompt(facts.text)[:budget] if facts.text else ""
    return (header + ("\n\nmain_text:\n" + body if body else "")).strip()[:MAX_PROMPT_CONTENT_CHARS]


def facts_text(facts: PageFacts) -> str:
    """Все слова фактов одной строкой — основа для проверки «утверждение есть на странице»."""
    parts = [facts.title or "", facts.description or "", *facts.h1, facts.text or "", facts.site_name or "", facts.organization_name or ""]
    return "\n".join(p for p in parts if p)
