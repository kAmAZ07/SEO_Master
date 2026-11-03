"""Генерация типизированных предложений: LLM → кэш → детерминированный путь → `needs_input`.

Порядок для каждого типа (title, description, H1, JSON-LD):
  1. значение до изменения берётся из фактов страницы; без точного `before` предложения нет;
  2. если провайдеры настроены, `llm.policy.run_policy` по очереди пробует кэш и провайдеров;
     ответ принимается только если его принял тот же валидатор, что проверяет готовую версию;
  3. иначе (или если все ответы отвергнуты) значение строится из фактов страницы (`fallback.py`);
  4. не хватает фактов — результат `needs_input`, а не шаблонная заглушка;
  5. готовое значение собирается в RecommendationVersion и снова проходит валидатор.
Любой путь — LLM, кэш или fallback — заканчивается одним и тем же `build_version`.

Одинаковое содержимое не создаёт новую версию (идемпотентность по `content_hash`); изменившееся
создаёт следующую, предыдущая остаётся в истории.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Callable

from services.semantic_service.llm import prompt_templates
from services.semantic_service.llm.llm_client import LlmRuntime
from services.semantic_service.llm.llm_cache import hash_input
from services.semantic_service.llm.policy import CacheContext, run_policy
from services.semantic_service.llm.types import COMPLEX, SIMPLE, LlmOutcome, LlmRequest
from services.semantic_service.proposals import fallback
from services.semantic_service.proposals.builder import ChangeSpec, IdFactory, build_version
from services.semantic_service.proposals.facts import (
    PageFacts,
    ProposalRequest,
    build_prompt_content,
    facts_text,
)
from services.semantic_service.proposals.model import (
    PATHS,
    SCHEMA_VERSION,
    VALIDATOR_VERSION,
    ChangeMode,
    OperationType,
    RecommendationVersion,
)
from services.semantic_service.proposals.store import ProposalStore, VersionConflict
from services.semantic_service.proposals.validators import (
    Issue,
    ProposalRejected,
    TextContext,
    canonical_jsonld,
    canonical_url,
    check_jsonld,
    check_text,
    clean_line,
    host_of,
)

CREATED = "created"
EXISTING = "existing"  # то же содержимое уже есть последней версией: новая не создаётся
NO_CHANGE = "no_change"
NEEDS_INPUT = "needs_input"

_TOKENS = {
    OperationType.TITLE: 80,
    OperationType.DESCRIPTION: 160,
    OperationType.H1: 60,
    OperationType.SCHEMA_JSONLD: 300,
}
_QUOTES = "\"'`«»“”„‘’ "
_MAX_ATTEMPTS_ON_CONFLICT = 3


@dataclass
class GenerationItem:
    operation: OperationType
    status: str
    version: RecommendationVersion | None = None
    missing: tuple[str, ...] = ()
    reason: str | None = None
    issues: tuple[Issue, ...] = ()
    llm_attempts: tuple[dict, ...] = ()

    def as_dict(self) -> dict:
        data: dict = {
            "operation_type": self.operation.value,
            "status": self.status,
            "version": self.version.model_dump(mode="json") if self.version else None,
            "llm_attempts": list(self.llm_attempts),
        }
        if self.status == NEEDS_INPUT:
            data["needs_input"] = {
                "missing": list(self.missing),
                "reason": self.reason,
                "issues": [i.as_dict() for i in self.issues],
            }
        return data


@dataclass
class GenerationResult:
    items: list[GenerationItem] = field(default_factory=list)


def _before_for(operation: OperationType, facts: PageFacts) -> tuple[str | None, fallback.Missing | None]:
    """Значение «до» из фактов. Нельзя угадать: при неизвестном до возвращается Missing."""
    if operation is OperationType.TITLE:
        return (facts.title, None) if facts.title is not None else (None, None)
    if operation is OperationType.DESCRIPTION:
        return (facts.description, None) if facts.description is not None else (None, None)
    if operation is OperationType.H1:
        headings = [h for h in facts.h1 if h.strip()]
        if len(headings) > 1:
            return None, fallback.Missing(("single_h1_decision",), reason="multiple_h1_manual_review")
        return (headings[0], None) if headings else (None, None)
    if facts.jsonld_present is False:
        return None, None
    if facts.jsonld_present is True and facts.jsonld is not None:
        return facts.jsonld, None
    missing = "existing_jsonld" if facts.jsonld_present is True else "jsonld_presence"
    return None, fallback.Missing((missing,), reason="before_value_unknown")


def _acceptor(operation: OperationType, ctx: TextContext, schema_type: str) -> Callable[[str], object | None]:
    if operation is OperationType.SCHEMA_JSONLD:
        def accept_schema(text: str):
            data = fallback.parse_schema_response(text)
            if data is None or data.get("@type") != schema_type:
                return None
            value = canonical_jsonld(data)
            return None if check_jsonld(value, ctx) else value

        return accept_schema

    def accept_text(text: str):
        value = clean_line(text.strip().strip(_QUOTES))
        if prompt_templates.NEEDS_INPUT_MARKER in value:
            return None
        return None if check_text(operation, value, ctx) else value

    return accept_text


def _attempt_dicts(outcome: LlmOutcome) -> tuple[dict, ...]:
    return tuple(
        {"provider": a.provider, "outcome": a.outcome, "code": a.code, "elapsed_ms": a.elapsed_ms} for a in outcome.attempts
    )


async def _llm_candidate(
    operation: OperationType, request: ProposalRequest, ctx: TextContext, runtime: LlmRuntime, content: str,
    remaining_s: float,
) -> LlmOutcome:
    kind = "schema" if operation is OperationType.SCHEMA_JSONLD else operation.value
    llm_request = LlmRequest(
        kind=kind,
        prompt=prompt_templates.PROMPTS[kind],
        content=content,
        complexity=COMPLEX if operation is OperationType.SCHEMA_JSONLD else SIMPLE,
        max_output_tokens=_TOKENS[operation],
    )
    cache = CacheContext(
        cache=runtime.cache,
        scope=request.project_id,
        input_hash=hash_input(kind, request.schema_type, content),
        prompt_version=prompt_templates.PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        validator_version=VALIDATOR_VERSION,
    )
    budget = replace(runtime.budget, deadline_s=max(0.0, remaining_s))
    return await run_policy(
        runtime.providers, llm_request, _acceptor(operation, ctx, request.schema_type), budget, cache=cache
    )


async def generate_proposals(
    request: ProposalRequest,
    *,
    runtime: LlmRuntime,
    store: ProposalStore,
    now: Callable[[], datetime],
    new_id: IdFactory,
    clock: Callable[[], float] = time.monotonic,
) -> GenerationResult:
    url = canonical_url(request.target_url)
    if url is None:
        raise ProposalRejected([Issue("target_url_invalid", "target_url")])
    operations = request.requested_operations()
    if not operations:
        raise ProposalRejected([Issue("no_operation_requested", "operations")])

    facts = request.facts
    ctx = TextContext(host=host_of(url), facts_text=facts_text(facts))
    content = build_prompt_content(facts, request.finding_codes)
    started = clock()
    result = GenerationResult()
    produced: dict[OperationType, str] = {}

    for operation in operations:
        before, missing = _before_for(operation, facts)
        if missing is not None:
            result.items.append(GenerationItem(operation, NEEDS_INPUT, missing=missing.missing, reason=missing.reason))
            continue

        value: str | None = None
        source, provider, model = "deterministic", fallback.PROVIDER, fallback.MODEL
        attempts: tuple[dict, ...] = ()
        if runtime.providers and content:
            remaining = runtime.budget.deadline_s - (clock() - started)
            outcome = await _llm_candidate(operation, request, ctx, runtime, content, remaining)
            attempts = _attempt_dicts(outcome)
            if outcome.ok:
                value = str(outcome.value)
                source, provider, model = outcome.source or "llm", outcome.provider or "", outcome.model or ""

        if value is None:
            candidate = _deterministic(operation, facts, request.schema_type, produced.get(OperationType.DESCRIPTION))
            if isinstance(candidate, fallback.Missing):
                result.items.append(
                    GenerationItem(operation, NEEDS_INPUT, missing=candidate.missing, reason=candidate.reason, llm_attempts=attempts)
                )
                continue
            value = candidate

        if before is not None and value == before:
            result.items.append(GenerationItem(operation, NO_CHANGE, llm_attempts=attempts))
            continue

        spec = ChangeSpec(
            path=_path(operation), mode=ChangeMode.SET, before=before, after=value,
            source=source, provider=provider, model=model,
        )
        try:
            item = await _persist(request, operation, spec, store, now, new_id, ctx.facts_text, url)
        except ProposalRejected as exc:
            result.items.append(
                GenerationItem(operation, NEEDS_INPUT, reason="candidate_rejected", issues=tuple(exc.issues), llm_attempts=attempts)
            )
            continue
        item.llm_attempts = attempts
        produced[operation] = value
        result.items.append(item)
    return result


def _path(operation: OperationType) -> str:
    return PATHS[operation].pattern.strip("^$").replace("\\", "")


def _deterministic(operation: OperationType, facts: PageFacts, schema_type: str, proposed_description: str | None):
    if operation is OperationType.TITLE:
        return fallback.fallback_title(facts)
    if operation is OperationType.DESCRIPTION:
        return fallback.fallback_description(facts)
    if operation is OperationType.H1:
        return fallback.fallback_h1(facts)
    return fallback.fallback_schema(facts, schema_type, proposed_description)


async def _persist(
    request: ProposalRequest, operation: OperationType, spec: ChangeSpec, store: ProposalStore,
    now: Callable[[], datetime], new_id: IdFactory, facts_text_value: str | None, url: str,
) -> GenerationItem:
    for _ in range(_MAX_ATTEMPTS_ON_CONFLICT):
        previous = await store.latest(request.project_id, url, operation)
        version = build_version(
            project_id=request.project_id,
            task_id=request.task_id,
            target_url=url,
            entity=request.entity,
            evidence=list(request.evidence),
            operation=operation,
            specs=[spec],
            now=now(),
            new_id=new_id,
            prompt_version=prompt_templates.PROMPT_VERSION if spec.source != "deterministic" else None,
            previous=previous,
            facts_text=facts_text_value,
        )
        if previous is not None and previous.content_hash == version.content_hash:
            return GenerationItem(operation, EXISTING, version=previous)
        try:
            await store.append(version)
        except VersionConflict:
            continue  # параллельный запрос успел раньше: перечитываем последнюю версию
        return GenerationItem(operation, CREATED, version=version)
    raise VersionConflict("too_many_concurrent_regenerations")
