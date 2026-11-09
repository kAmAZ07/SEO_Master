"""Строгий стык с AL-5: одна версия предложения на одну задачу Management."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from services.semantic_service.proposals.facts import PageFacts, ProposalRequest, facts_text
from services.semantic_service.proposals.model import RecommendationVersion
from services.semantic_service.proposals.validators import canonical_url, validate_version
from shared.page_evidence import verify_evidence


class ProposalHandoffError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ProposalResult:
    status: str
    version: RecommendationVersion | None
    needs_input: dict[str, Any] | None = None


def audit_proposal_request(crawl: dict[str, Any], *, project_id: str, task_id: str,
                          url: str, requested: object = None, operation: str | None = None) -> ProposalRequest:
    """Use only the exact page of the persisted, completed, project-scoped audit."""
    if requested is not None and operation is not None:
        raise ProposalHandoffError("proposal_request_invalid")
    if crawl.get("project_id") != project_id or crawl.get("mode") != "full":
        raise ProposalHandoffError("proposal_scope_mismatch")
    if crawl.get("status") != "completed" or not crawl.get("audit_id"):
        raise ProposalHandoffError("proposal_evidence_required")
    target = canonical_url(url)
    pages = [p for p in crawl.get("pages", []) if isinstance(p, dict)
             and target is not None and canonical_url(str(p.get("url", ""))) == target]
    if len(pages) != 1:
        raise ProposalHandoffError("proposal_evidence_required")
    envelope = pages[0].get("page_evidence")
    if not isinstance(envelope, dict) or not verify_evidence(envelope):
        raise ProposalHandoffError("proposal_evidence_required")
    snapshot = envelope["snapshot"]
    status = snapshot.get("status_code")
    if not isinstance(status, int) or isinstance(status, bool) or not 200 <= status < 300:
        raise ProposalHandoffError("proposal_evidence_required")
    if (snapshot.get("schema_version") != "page_evidence_v1"
            or snapshot.get("project_id") != project_id
            or snapshot.get("audit_id") != crawl["audit_id"]
            or canonical_url(str(snapshot.get("url", ""))) != target
            or canonical_url(str(snapshot.get("final_url", ""))) != target):
        raise ProposalHandoffError("proposal_scope_mismatch")
    selection = bound_request(requested, project_id=project_id, task_id=task_id, url=url) if requested is not None else None
    codes = [f["code"] for f in crawl.get("findings", [])
             if isinstance(f, dict) and isinstance(f.get("code"), str)
             and isinstance(f.get("details"), dict)
             and canonical_url(str(f["details"].get("url", ""))) == target]
    try:
        facts = PageFacts.model_validate(snapshot.get("facts"))
    except ValidationError:
        raise ProposalHandoffError("proposal_evidence_required") from None
    payload: dict[str, Any] = {
        "project_id": project_id, "task_id": task_id, "target_url": target,
        "facts": facts.model_dump(),
        "evidence": [{"evidence_id": envelope["evidence_id"], "sha256": envelope["content_hash"]}],
        "finding_codes": codes,
    }
    if selection is not None:
        payload.update(operations=selection.requested_operations(), entity=selection.entity, schema_type=selection.schema_type)
        if (selection.facts != facts
                or [e.model_dump() for e in selection.evidence] != payload["evidence"]):
            raise ProposalHandoffError("proposal_evidence_mismatch")
    elif operation is not None:
        payload["operations"] = [operation]
    request = bound_request(payload, project_id=project_id, task_id=task_id, url=url)
    if snapshot.get("jsonld_blocks", 0) > 1 and request.requested_operations()[0].value == "schema_jsonld":
        raise ProposalHandoffError("proposal_evidence_ambiguous")
    return request


def bound_request(payload: object, *, project_id: str, task_id: str | None, url: str) -> ProposalRequest:
    """Не создаёт вымышленные evidence: использует явный запрос опубликованной схемы."""
    if not task_id:
        raise ProposalHandoffError("proposal_task_required")
    if payload is None:
        raise ProposalHandoffError("proposal_evidence_required")
    if not isinstance(payload, dict):
        raise ProposalHandoffError("proposal_request_invalid")
    try:
        request = ProposalRequest.model_validate(payload)
    except ValidationError:
        raise ProposalHandoffError("proposal_request_invalid") from None
    target = canonical_url(url)
    if (
        request.project_id != project_id
        or request.task_id != task_id
        or target is None
        or canonical_url(request.target_url) != target
    ):
        raise ProposalHandoffError("proposal_scope_mismatch")
    # До появления согласованной модели approval bundles задача связывается
    # ровно с одной RecommendationVersion. Нельзя молча взять первую из списка.
    if len(request.requested_operations()) != 1:
        raise ProposalHandoffError("single_proposal_required")
    return request


def proposal_result(payload: object, request: ProposalRequest) -> ProposalResult:
    """Повторно проверяет версию исходными валидаторами Semantic, включая hash."""
    if not isinstance(payload, dict):
        raise ProposalHandoffError("proposal_response_invalid")
    target = canonical_url(request.target_url)
    if payload.get("project_id") != request.project_id or payload.get("target_url") != request.target_url:
        raise ProposalHandoffError("proposal_scope_mismatch")
    items = payload.get("items")
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise ProposalHandoffError("proposal_response_invalid")
    item = items[0]
    operation = request.requested_operations()[0]
    if item.get("operation_type") != operation.value:
        raise ProposalHandoffError("proposal_operation_mismatch")
    state = item.get("status")
    if state in ("needs_input", "no_change"):
        if item.get("version") is not None:
            raise ProposalHandoffError("proposal_response_invalid")
        needs_input = item.get("needs_input")
        if state == "needs_input" and not isinstance(needs_input, dict):
            raise ProposalHandoffError("proposal_response_invalid")
        return ProposalResult(state, None, deepcopy(needs_input))
    if state not in ("created", "existing"):
        raise ProposalHandoffError("proposal_status_unknown")
    try:
        version = RecommendationVersion.model_validate(item.get("version"))
    except ValidationError:
        raise ProposalHandoffError("proposal_version_invalid") from None
    if (
        version.project_id != request.project_id
        or version.task_id != request.task_id
        or version.target.url != target
        or version.target.entity != request.entity
        or version.operation_type != operation
        or version.evidence != tuple(request.evidence)
    ):
        raise ProposalHandoffError("proposal_scope_mismatch")
    if validate_version(version, facts_text=facts_text(request.facts)):
        raise ProposalHandoffError("proposal_version_invalid")
    # before должен происходить из тех же фактов, которые отправили генератору.
    before = {
        "title": request.facts.title,
        "description": request.facts.description,
        "h1": request.facts.h1[0] if len(request.facts.h1) == 1 else None,
        "schema_jsonld": request.facts.jsonld,
    }[operation.value]
    if version.changes[0].before != before:
        raise ProposalHandoffError("proposal_before_mismatch")
    return ProposalResult(state, version)


def version_diff(version: RecommendationVersion) -> dict[str, Any]:
    """Preview хранит точную исходную версию и различает missing/value/remove."""
    before = {}
    after = {}
    for change in version.changes:
        before[change.path] = {"state": change.before_state, "value": change.before}
        after[change.path] = {"mode": change.mode.value, "value": change.after}
    return {
        "before": before,
        "after": after,
        "recommendation_version": version.model_dump(mode="json"),
    }
