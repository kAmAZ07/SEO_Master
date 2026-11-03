"""Сборка RecommendationVersion из подготовленных изменений. Чистая функция: время и идентификаторы
передаются снаружи, поэтому результат воспроизводим в тестах."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from pydantic import ValidationError

from services.semantic_service.proposals.derive import derive_expected_checks, derive_preconditions
from services.semantic_service.proposals.model import (
    CMS_FIELDS,
    GENERATOR_VERSION,
    MANAGED_OPERATIONS,
    SCHEMA_VERSION,
    VALIDATOR_VERSION,
    Change,
    ChangeMode,
    EvidenceRef,
    ExecutionMode,
    ExternalEntity,
    FieldProvenance,
    OperationType,
    RecommendationVersion,
    Target,
    compute_content_hash,
    utc_iso,
)
from services.semantic_service.proposals.validators import (
    Issue,
    ProposalRejected,
    canonical_origin,
    canonical_url,
    validate_version,
)

MANUAL_REASONS = {
    OperationType.H1: "cms_plugin_has_no_h1_write",
    OperationType.CONTENT_EDIT: "cms_plugin_has_no_content_write",
    OperationType.INTERNAL_LINK: "cms_plugin_has_no_internal_link_write",
}
BASE_LIMITATIONS = ("heuristic_text_not_reviewed_by_human", "before_observed_on_page_not_cms_snapshot")


@dataclass(frozen=True)
class ChangeSpec:
    """Желаемое изменение до сборки версии."""

    path: str
    mode: ChangeMode
    before: str | None  # None = значения нет (missing)
    after: str | None = None
    source: str = "deterministic"  # llm | cache | deterministic
    provider: str = "deterministic"
    model: str = "facts_v1"


IdFactory = Callable[[], str]


def build_version(
    *,
    project_id: str,
    task_id: str | None,
    target_url: str,
    entity: ExternalEntity | None,
    evidence: list[EvidenceRef],
    operation: OperationType,
    specs: list[ChangeSpec],
    now: datetime,
    new_id: IdFactory,
    prompt_version: str | None,
    previous: RecommendationVersion | None = None,
    extra_limitations: tuple[str, ...] = (),
    facts_text: str | None = None,
) -> RecommendationVersion:
    """Создаёт и проверяет версию. Невалидное предложение не создаётся: поднимается ProposalRejected."""
    url = canonical_url(target_url)
    origin = canonical_origin(target_url)
    if url is None or origin is None:
        raise ProposalRejected([Issue("target_url_invalid", "target.url")])
    target = Target(origin=origin, url=url, entity=entity)

    changes = tuple(
        Change(
            path=spec.path,
            mode=spec.mode,
            before_state="missing" if spec.before is None else "value",
            before=spec.before,
            after=spec.after if spec.mode is ChangeMode.SET else None,
            cms_field=CMS_FIELDS[operation],  # type: ignore[arg-type]
        )
        for spec in specs
    )
    stamp = utc_iso(now)
    provenance = tuple(
        FieldProvenance(path=spec.path, source=spec.source, provider=spec.provider, model=spec.model, generated_at=stamp)  # type: ignore[arg-type]
        for spec in specs
    )
    managed = operation in MANAGED_OPERATIONS
    limitations = list(BASE_LIMITATIONS) + list(extra_limitations)
    if managed:
        limitations.append("managed_write_depends_on_site_capabilities")

    draft = {
        "schema_version": SCHEMA_VERSION,
        "version_id": new_id(),
        "recommendation_id": previous.recommendation_id if previous else new_id(),
        "version": previous.version + 1 if previous else 1,
        "previous_version_id": previous.version_id if previous else None,
        "project_id": project_id,
        "task_id": task_id,
        "target": target,
        "evidence": tuple(evidence),
        "operation_type": operation,
        "execution_mode": ExecutionMode.MANAGED if managed else ExecutionMode.MANUAL,
        "manual_reason": None if managed else MANUAL_REASONS.get(operation, "manual_only"),
        "changes": changes,
        "preconditions": derive_preconditions(project_id, target, evidence, changes),
        "expected_checks": derive_expected_checks(target, changes),
        "limitations": tuple(dict.fromkeys(limitations)),
        "generator_version": GENERATOR_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "prompt_version": prompt_version,
        "provider": specs[0].provider,
        "model": specs[0].model,
        "provenance": provenance,
        "created_at": stamp,
    }
    draft["content_hash"] = "0" * 64
    try:
        unsigned = RecommendationVersion.model_validate(draft)
        signed = RecommendationVersion.model_validate({**draft, "content_hash": compute_content_hash(unsigned)})
    except ValidationError as exc:
        # Структурная ошибка модели (например, пустой evidence) — тоже отказ, а не исключение наружу.
        raise ProposalRejected(
            [Issue("schema_invalid", ".".join(str(part) for part in err["loc"]), err["type"]) for err in exc.errors()]
        ) from None
    issues = validate_version(signed, facts_text=facts_text)
    if issues:
        raise ProposalRejected(issues)
    return signed
