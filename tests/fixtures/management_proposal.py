"""Данные стыка Management, собранные настоящим чистым builder AL-5."""

from datetime import datetime, timezone
import uuid

from services.semantic_service.proposals.builder import ChangeSpec, build_version
from services.semantic_service.proposals.facts import PageFacts, ProposalRequest, facts_text
from services.semantic_service.proposals.model import ChangeMode, EvidenceRef, OperationType, sha256_text


PROJECT_ID = "11111111-1111-4111-8111-111111111111"
TASK_ID = "22222222-2222-4222-8222-222222222222"
TARGET_URL = "https://example.com/catalog"


def management_proposal_fixture(operation="title", *, evidence_override=None, entity=None, target_url=TARGET_URL):
    operation = OperationType(operation)
    text = "Мастерская обуви. Изготавливаем зимнюю обувь и кожаные ботинки ручной работы."
    facts = PageFacts(title="Мастерская обуви", h1=["Мастерская обуви"], text=text)
    before = facts.title
    path = "head.title" if operation is OperationType.TITLE else "body.h1"
    evidence = ([EvidenceRef.model_validate(item) for item in evidence_override] if evidence_override is not None
                else [EvidenceRef(evidence_id="fixture-page-1", sha256=sha256_text(text))])
    request = ProposalRequest(
        project_id=PROJECT_ID, task_id=TASK_ID, target_url=target_url,
        evidence=evidence, operations=[operation], facts=facts, entity=entity,
    )
    version = build_version(
        project_id=PROJECT_ID, task_id=TASK_ID, target_url=target_url,
        entity=entity, evidence=evidence, operation=operation,
        specs=[ChangeSpec(path=path, mode=ChangeMode.SET, before=before,
                          after="Мастерская зимней обуви")],
        now=datetime(2026, 11, 9, tzinfo=timezone.utc), new_id=lambda: str(uuid.uuid4()),
        prompt_version=None, facts_text=facts_text(facts),
    )
    response = {
        "project_id": PROJECT_ID, "target_url": target_url,
        "items": [{"operation_type": operation.value, "status": "created",
                   "version": version.model_dump(mode="json"), "llm_attempts": []}],
    }
    return request.model_dump(mode="json"), response, version
