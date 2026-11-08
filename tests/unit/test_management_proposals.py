"""Версии AL-5, scoped preview и запрет старой автоматической публикации."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from services.management_service import orchestrator
from services.management_service.proposal_handoff import (
    ProposalHandoffError, audit_proposal_request, bound_request, proposal_result, version_diff,
)
from services.management_service.schemas.hitl import HITLDecision, HITLBatchApproveRequest
from services.management_service.hitl_handler import HITLHandler
from tests.fixtures.management_proposal import (
    PROJECT_ID, TASK_ID, TARGET_URL, management_proposal_fixture,
)
from shared.page_evidence import seal_evidence


def request_of(payload):
    return bound_request(payload, project_id=PROJECT_ID, task_id=TASK_ID, url=TARGET_URL)


def audit_fixture():
    request, _, _ = management_proposal_fixture()
    envelope = seal_evidence({
        "schema_version": "page_evidence_v1", "audit_id": "audit-1",
        "project_id": PROJECT_ID, "url": TARGET_URL, "final_url": TARGET_URL,
        "status_code": 200, "facts": request["facts"], "jsonld_blocks": 0,
    })
    return {"audit_id": "audit-1", "project_id": PROJECT_ID, "mode": "full", "status": "completed",
            "pages": [{"url": TARGET_URL, "page_evidence": envelope}],
            "findings": [{"code": "title_too_short", "details": {"url": TARGET_URL}}]}


def test_automatic_request_uses_persisted_exact_page_evidence():
    crawl = audit_fixture()
    request = audit_proposal_request(crawl, project_id=PROJECT_ID, task_id=TASK_ID, url=TARGET_URL)
    envelope = crawl["pages"][0]["page_evidence"]
    assert request.evidence[0].evidence_id == envelope["evidence_id"]
    assert request.evidence[0].sha256 == envelope["content_hash"]
    assert request.facts.model_dump() == envelope["snapshot"]["facts"]
    assert [op.value for op in request.requested_operations()] == ["title"]


@pytest.mark.parametrize("mutation", ["project", "target", "hash", "redirect", "audit", "public", "duplicate", "status"])
def test_audit_bridge_rejects_wrong_scope_tampering_and_ambiguous_page(mutation):
    crawl = audit_fixture()
    envelope = crawl["pages"][0]["page_evidence"]
    if mutation == "project":
        crawl["project_id"] = "other"
    elif mutation == "target":
        crawl["pages"][0]["url"] += "/other"
    elif mutation == "hash":
        envelope["snapshot"]["facts"]["title"] = "tampered"
    elif mutation in {"redirect", "audit"}:
        envelope["snapshot"]["final_url" if mutation == "redirect" else "audit_id"] = "https://other.com/"
        crawl["pages"][0]["page_evidence"] = seal_evidence(envelope["snapshot"])
    elif mutation == "public":
        crawl["mode"] = "public"
    elif mutation == "status":
        envelope["snapshot"]["status_code"] = 500
        crawl["pages"][0]["page_evidence"] = seal_evidence(envelope["snapshot"])
    else:
        crawl["pages"] *= 2
    with pytest.raises(ProposalHandoffError):
        audit_proposal_request(crawl, project_id=PROJECT_ID, task_id=TASK_ID, url=TARGET_URL)


def test_caller_cannot_replace_persisted_facts_with_arbitrary_evidence():
    request, _, _ = management_proposal_fixture()
    with pytest.raises(ProposalHandoffError, match="proposal_evidence_mismatch"):
        audit_proposal_request(audit_fixture(), project_id=PROJECT_ID, task_id=TASK_ID,
                              url=TARGET_URL, requested=request)


def test_missing_page_findings_does_not_silently_choose_an_operation():
    crawl = audit_fixture()
    crawl["findings"] = [{"code": "title_missing", "details": {"url": "https://other.com/"}}]
    with pytest.raises(ProposalHandoffError, match="single_proposal_required"):
        audit_proposal_request(crawl, project_id=PROJECT_ID, task_id=TASK_ID, url=TARGET_URL)


def test_explicit_operation_uses_audit_facts_without_caller_supplied_facts():
    crawl = audit_fixture()
    crawl["findings"].append({"code": "description_missing", "details": {"url": TARGET_URL}})
    with pytest.raises(ProposalHandoffError, match="single_proposal_required"):
        audit_proposal_request(crawl, project_id=PROJECT_ID, task_id=TASK_ID, url=TARGET_URL)
    request = audit_proposal_request(crawl, project_id=PROJECT_ID, task_id=TASK_ID,
                                     url=TARGET_URL, operation="description")
    assert [op.value for op in request.requested_operations()] == ["description"]
    assert request.facts.description is None
    assert request.evidence[0].sha256 == crawl["pages"][0]["page_evidence"]["content_hash"]


def test_api_rejects_conflicting_proposal_selection():
    from pydantic import ValidationError
    from services.management_service.api.endpoints.internal import OptimizationRunRequest

    request, _, _ = management_proposal_fixture()
    with pytest.raises(ValidationError, match="either proposal_request or proposal_operation"):
        OptimizationRunRequest(project_id=PROJECT_ID, url=TARGET_URL, task_id=TASK_ID,
                               proposal_request=request, proposal_operation="description")


def test_worker_handoff_rejects_conflicting_selection_without_silent_precedence():
    request, _, _ = management_proposal_fixture()
    with pytest.raises(ProposalHandoffError, match="proposal_request_invalid"):
        audit_proposal_request(audit_fixture(), project_id=PROJECT_ID, task_id=TASK_ID,
                              url=TARGET_URL, requested=request, operation="description")


def test_multiple_jsonld_blocks_require_manual_input_even_for_explicit_operation():
    crawl = audit_fixture()
    snapshot = crawl["pages"][0]["page_evidence"]["snapshot"]
    snapshot["jsonld_blocks"] = 2
    snapshot["facts"].update(jsonld_present=True, jsonld=None)
    crawl["pages"][0]["page_evidence"] = seal_evidence(snapshot)
    with pytest.raises(ProposalHandoffError, match="proposal_evidence_ambiguous"):
        audit_proposal_request(crawl, project_id=PROJECT_ID, task_id=TASK_ID,
                              url=TARGET_URL, operation="schema_jsonld")


def test_real_al5_builder_round_trip_and_exact_preview():
    request, response, original = management_proposal_fixture()
    result = proposal_result(response, request_of(request))
    assert result.version == original
    diff = version_diff(result.version)
    assert diff["recommendation_version"] == response["items"][0]["version"]
    assert diff["before"]["head.title"] == {"state": "value", "value": "Мастерская обуви"}
    assert diff["after"]["head.title"] == {"mode": "set", "value": "Мастерская зимней обуви"}


@pytest.mark.parametrize("field,value", [
    ("project_id", "33333333-3333-4333-8333-333333333333"),
    ("task_id", None), ("task_id", "other-task"),
    ("target_url", "https://other.example.com/catalog"),
    ("evidence", []), ("operations", ["title", "description"]),
])
def test_request_cannot_change_task_project_target_or_drop_evidence(field, value):
    request, _, _ = management_proposal_fixture()
    request[field] = value
    with pytest.raises(ProposalHandoffError):
        request_of(request)


@pytest.mark.parametrize("field,value", [
    ("content_hash", "0" * 64),
    ("task_id", "other-task"),
    ("project_id", "33333333-3333-4333-8333-333333333333"),
    ("operation_type", "description"),
    ("target", {"origin": "https://other.example.com", "url": "https://other.example.com/catalog"}),
    ("evidence", [{"evidence_id": "other", "sha256": "a" * 64}]),
    ("preconditions", []), ("expected_checks", []),
])
def test_response_rejects_tampered_version_or_binding(field, value):
    request, response, _ = management_proposal_fixture()
    response["items"][0]["version"][field] = value
    with pytest.raises(ProposalHandoffError):
        proposal_result(response, request_of(request))


@pytest.mark.parametrize("state", ["needs_input", "no_change"])
def test_non_publishable_result_is_preserved_without_version(state):
    request, response, _ = management_proposal_fixture()
    response["items"][0].update(status=state, version=None,
                                needs_input={"missing": ["facts"], "reason": "insufficient_facts"})
    result = proposal_result(response, request_of(request))
    assert result.status == state and result.version is None


def test_manual_proposal_stays_manual():
    request, response, _ = management_proposal_fixture("h1")
    result = proposal_result(response, request_of(request))
    assert result.version.execution_mode.value == "manual"
    assert result.version.changes[0].cms_field is None


@pytest.mark.parametrize("mutation", ["empty", "duplicate", "unknown", "wrong_project", "bad_before"])
def test_response_cannot_silently_drop_or_replace_operations(mutation):
    request, response, _ = management_proposal_fixture()
    if mutation == "empty":
        response["items"] = []
    elif mutation == "duplicate":
        response["items"] *= 2
    elif mutation == "unknown":
        response["items"][0]["status"] = "applied"
    elif mutation == "wrong_project":
        response["project_id"] = "other"
    else:
        request["facts"]["title"] = "Иное исходное название"
    with pytest.raises(ProposalHandoffError):
        proposal_result(response, request_of(request))


class Database:
    def __init__(self):
        self.added = []
        self.commits = 0
        self.closed = False

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1

    def refresh(self, obj):
        pass

    def close(self):
        self.closed = True


def test_saga_uses_only_proposals_and_saves_version_bound_pending_approval(monkeypatch):
    async def scenario():
        request, response, version = management_proposal_fixture()
        requests = []
        events = []

        def respond(http_request):
            requests.append(http_request)
            return httpx.Response(200, json=response)

        async def publish(**kwargs):
            events.append(kwargs)

        monkeypatch.setattr(orchestrator, "publish_event", publish)
        saga = orchestrator.OptimizationSaga(PROJECT_ID, TARGET_URL, TASK_ID, request)
        # Caller не может незаметно переписать сохранённый запрос по alias.
        request["task_id"] = "changed-after-construction"
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            saga.client = client
            await saga._trigger_content_generation()
        await saga._wait_for_content_generation()
        db = Database()
        approval = await saga._create_hitl_decision(db)
        assert len(requests) == 1 and requests[0].url.path == "/semantic/proposals"
        assert approval.status is orchestrator.HITLStatus.PENDING
        assert approval.meta["recommendation_version_id"] == version.version_id
        assert approval.meta["recommendation_content_hash"] == version.content_hash
        assert approval.diff_data["recommendation_version"] == version.model_dump(mode="json")
        assert db.commits == 1 and saga.state is orchestrator.SagaState.AWAITING_HITL
        assert events[0]["event_type"] == "HITLApprovalRequired"

    asyncio.run(scenario())


def test_missing_evidence_prevents_any_generation_http_request():
    saga = orchestrator.OptimizationSaga(PROJECT_ID, TARGET_URL, TASK_ID)
    saga.client = AsyncMock()
    with pytest.raises(ProposalHandoffError, match="proposal_evidence_required"):
        asyncio.run(saga._trigger_content_generation())
    saga.client.post.assert_not_called()


def test_saved_saga_context_does_not_mutate_previous_jsonb_or_follow_live_changes():
    original = {"saga_state": "initiated", "saga_context": {"previous": True}}
    task = SimpleNamespace(meta=original)
    db = Mock()
    db.query.return_value.filter.return_value.first.return_value = task
    saga = orchestrator.OptimizationSaga(PROJECT_ID, TARGET_URL, TASK_ID)
    saga.context = {"evidence": {"hash": "captured"}}
    asyncio.run(saga._save_saga_state(db))
    assert original == {"saga_state": "initiated", "saga_context": {"previous": True}}
    assert task.meta is not original
    saga.context["evidence"]["hash"] = "later"
    assert task.meta["saga_context"]["evidence"]["hash"] == "captured"


def test_no_task_never_creates_session_or_bypasses_approval(monkeypatch):
    from services.management_service.db import session
    session_factory = AsyncMock(side_effect=AssertionError("DB must not be opened"))
    monkeypatch.setattr(session, "SessionLocal", session_factory)
    saga = orchestrator.OptimizationSaga(PROJECT_ID, TARGET_URL)
    assert asyncio.run(saga.execute()) is False
    assert saga.state is orchestrator.SagaState.NEEDS_INPUT
    session_factory.assert_not_called()


@pytest.mark.parametrize("method", ["_apply_changes", "_wait_for_changes_applied"])
def test_legacy_mutation_and_false_verification_paths_are_closed(method):
    saga = orchestrator.OptimizationSaga(PROJECT_ID, TARGET_URL, TASK_ID)
    saga.context = {"generated_content": {"title": "unapproved"}, "change_id": "legacy"}
    saga.client = AsyncMock()
    with pytest.raises(ProposalHandoffError):
        asyncio.run(getattr(saga, method)())
    saga.client.post.assert_not_called()
    saga.client.get.assert_not_called()


def test_default_decision_and_batch_never_request_auto_deploy():
    assert HITLDecision().auto_deploy is False
    assert HITLBatchApproveRequest(task_ids=[], approved_by="fixture").auto_deploy is False


def test_http_decision_cannot_trust_body_user_id_without_service_and_session():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.management_service.api.endpoints import hitl

    app = FastAPI()
    app.include_router(hitl.router, prefix="/hitl")
    db = Mock()
    db.query.side_effect = AssertionError("Unauthenticated request reached business DB")
    app.dependency_overrides[hitl.get_db] = lambda: db
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(f"/hitl/tasks/{TASK_ID}/approve", json={"user_id": "forged-actor"})
    assert response.status_code == 401
    db.query.assert_not_called()


def test_auto_deploy_is_rejected_before_any_database_access():
    db = AsyncMock()
    handler = HITLHandler(db)
    with pytest.raises(ProposalHandoffError, match="version_bound_executor_required"):
        asyncio.run(handler.approve_task(TASK_ID, "untrusted-body-user", HITLDecision(auto_deploy=True)))
    db.query.assert_not_called()


def test_bound_version_cannot_be_approved_by_legacy_user_id_only_call():
    db = Mock()
    task_query = Mock()
    task_query.filter.return_value.first.return_value = SimpleNamespace(id=TASK_ID)
    approval_query = Mock()
    approval_query.filter.return_value.first.return_value = SimpleNamespace(
        diff_data={"recommendation_version": {"version_id": "fixture"}},
    )
    db.query.side_effect = [task_query, approval_query]
    with pytest.raises(ProposalHandoffError, match="version_bound_authorization_required"):
        asyncio.run(HITLHandler(db).approve_task(TASK_ID, "untrusted-body-user", HITLDecision()))
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_http_legacy_auto_deploy_returns_conflict_without_database_access():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.management_service.api.endpoints import hitl
    from services.management_service.approval_identity import authenticated_actor
    from services.management_service.approval_policy import UserActor

    app = FastAPI()
    app.include_router(hitl.router, prefix="/hitl")
    db = Mock()
    app.dependency_overrides[hitl.get_db] = lambda: db
    app.dependency_overrides[authenticated_actor] = lambda: UserActor(TASK_ID)
    with TestClient(app) as client:
        response = client.post(f"/hitl/tasks/{TASK_ID}/approve", json={
            "approval_id": TASK_ID, "recommendation_version_id": PROJECT_ID,
            "content_hash": "a" * 64, "auto_deploy": True,
        })
    assert response.status_code == 409
    assert response.json()["detail"] == "version_bound_executor_required"
    db.query.assert_not_called()


def test_worker_reports_pending_approval_not_completed_publication(monkeypatch):
    from services.management_service.tasks import orchestration_tasks

    request, _, _ = management_proposal_fixture()
    calls = []

    async def prepare(**kwargs):
        calls.append(kwargs)
        return {"status": "awaiting_hitl", "success": True, "publication_status": "not_requested"}

    monkeypatch.setattr(orchestration_tasks, "run_optimization_cycle", prepare)
    result = orchestration_tasks.run_optimization_cycle_task.run(
        PROJECT_ID, TARGET_URL, TASK_ID, proposal_request=request,
    )
    assert result["status"] == "awaiting_hitl"
    assert result["publication_status"] == "not_requested"
    assert calls[0]["proposal_request"] == request


def test_prepare_does_not_wait_for_approval_or_emit_completed(monkeypatch):
    from services.management_service.db import session
    db = Database()
    monkeypatch.setattr(session, "SessionLocal", lambda: db)
    request, _, _ = management_proposal_fixture()
    saga = orchestrator.OptimizationSaga(PROJECT_ID, TARGET_URL, TASK_ID, request)
    monkeypatch.setattr(saga, "_bind_audit_evidence", Mock())
    for method in ("_save_saga_state", "_run_crawl", "_wait_for_crawl_completion",
                   "_trigger_scores_calculation", "_wait_for_scores_completion",
                   "_trigger_content_generation", "_wait_for_content_generation"):
        monkeypatch.setattr(saga, method, AsyncMock())

    async def create_approval(database):
        saga.state = orchestrator.SagaState.AWAITING_HITL

    monkeypatch.setattr(saga, "_create_hitl_decision", create_approval)
    apply = AsyncMock(side_effect=AssertionError("No automatic apply"))
    completion = AsyncMock(side_effect=AssertionError("No false completed event"))
    monkeypatch.setattr(saga, "_apply_changes", apply)
    monkeypatch.setattr(saga, "_publish_completion_event", completion)
    assert asyncio.run(saga.execute()) is True
    assert saga.state is orchestrator.SagaState.AWAITING_HITL and db.closed
    apply.assert_not_called()
    completion.assert_not_called()
