"""Стык AL-5 → Management: HTTP, события, null и отсутствие побочных записей."""

import asyncio
from copy import deepcopy
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest

from services.management_service import orchestrator
from services.management_service.events.ff_score_recalculated_handler import handle_ff_score_recalculated_event
from services.management_service.models import Project, Task, TaskType
from services.management_service.score_contract import (
    ScoreContractError,
    parse_score_event,
    parse_score_response,
)


PROJECT = "11111111-1111-4111-8111-111111111111"


def response_payload(score=None, eeat=None):
    return {
        "ff_score_id": "score-1", "eeat_score_id": "eeat-1",
        "formula_version": "ff_score_v1",
        "status": "insufficient_data" if score is None else "complete",
        "ff_score": score, "components": {}, "weights": {}, "thresholds": {},
        "mode": None, "coverage": {},
        "partial": {"formula_version": "ff_score_partial_v1", "score": 65.0},
        "heuristic": True, "note": "fixture", "inputs": {},
        "eeat": {
            "score_id": "eeat-1", "formula_version": "eeat_v1",
            "status": "insufficient_data" if eeat is None else "complete",
            "score": eeat, "partial": None,
        },
    }


def event_payload(score=None, eeat=None):
    return {"payload": {
        "schema_version": "ffscore_recalculated/2", "project_id": PROJECT,
        "ff_score": score, "status": "insufficient_data" if score is None else "complete",
        "formula_version": "ff_score_v1", "eeat_score": eeat, "eeat": eeat,
        "eeat_details": response_payload(score, eeat)["eeat"],
    }}


@pytest.mark.parametrize("score,eeat", [(None, None), (0, 0), (100, 100), (None, 72.5), (60.5, None)])
def test_http_preserves_missing_scores_and_separate_partial_result(score, eeat):
    result = parse_score_response(response_payload(score, eeat))
    assert result.ff_score == score and result.eeat["score"] == eeat
    assert result.partial["score"] == 65.0  # partial не заменяет полный балл


@pytest.mark.parametrize("field,value", [
    ("ff_score", float("nan")), ("ff_score", float("inf")),
    ("ff_score", -1), ("ff_score", 101), ("ff_score", True), ("ff_score", "60"),
    ("ff_score", None), ("status", "pending"), ("status", "insufficient_data"),
    ("formula_version", "ff_score_v2"), ("eeat_score_id", "other"),
])
def test_invalid_http_response_is_rejected_with_safe_diagnostic(field, value):
    payload = response_payload(60, 70)
    payload[field] = value
    with pytest.raises(ScoreContractError, match="^semantic_score_contract_invalid$"):
        parse_score_response(payload)


@pytest.mark.parametrize("field,value", [
    ("score", {}), ("score", True), ("score", float("inf")),
    ("status", "insufficient_data"), ("formula_version", "legacy"),
])
def test_invalid_http_eeat_is_rejected(field, value):
    payload = response_payload(60, 70)
    payload["eeat"][field] = value
    with pytest.raises(ScoreContractError):
        parse_score_response(payload)


def test_saga_accepts_insufficient_data_from_real_http_schema(monkeypatch):
    async def scenario():
        payload = response_payload()
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json=payload)

        saga = orchestrator.OptimizationSaga(PROJECT, "https://example.com/page", "task-1")
        monkeypatch.setattr(saga, "_fetch_reporting_summary", AsyncMock(return_value={}))
        monkeypatch.setattr(saga, "_run_semantic_analysis", AsyncMock(return_value={}))
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            saga.client = client
            await saga._trigger_scores_calculation()
        await saga._wait_for_scores_completion()
        assert len(requests) == 1 and requests[0].url.path == "/semantic/ff-score"
        assert saga.state is orchestrator.SagaState.SCORES_COMPLETED
        assert saga.context["ffscore"] is None and saga.context["eeat_score"] is None
        assert saga.context["ffscore_status"] == "insufficient_data"
        assert saga.context["score_result"] == payload

    asyncio.run(scenario())


def test_saga_does_not_complete_scores_without_validated_response():
    saga = orchestrator.OptimizationSaga(PROJECT, "https://example.com/page", "task-1")
    saga.context = {"ffscore": None}
    with pytest.raises(ScoreContractError):
        asyncio.run(saga._wait_for_scores_completion())
    assert saga.state is orchestrator.SagaState.INITIATED


def test_saga_rejects_invalid_score_without_repeating_non_idempotent_post(monkeypatch):
    async def scenario():
        requests = []

        def respond(request):
            requests.append(request)
            payload = response_payload(60, 70)
            payload["status"] = "insufficient_data"
            return httpx.Response(200, json=payload)

        saga = orchestrator.OptimizationSaga(PROJECT, "https://example.com/page", "task-1")
        monkeypatch.setattr(saga, "_fetch_reporting_summary", AsyncMock(return_value={}))
        monkeypatch.setattr(saga, "_run_semantic_analysis", AsyncMock(return_value={}))
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            saga.client = client
            with pytest.raises(ScoreContractError):
                await saga._trigger_scores_calculation()
        assert len(requests) == 1
        assert "score_result" not in saga.context
        assert saga.state is not orchestrator.SagaState.SCORES_COMPLETED

    asyncio.run(scenario())


class Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class Database:
    def __init__(self):
        self.project = Project(id=uuid.UUID(PROJECT), meta={"keep": True, "ffscore": 85})
        self.task = Task(
            id=uuid.uuid4(), project_id=uuid.UUID(PROJECT), task_type=TaskType.UPDATE_META,
            url="https://example.com/page", impact_score=0.5, effort_score=0.5,
            meta={"current_ffscore": 85, "current_eeat": 90, "keep": True},
        )
        self.queries = 0
        self.added = []
        self.commits = 0

    def query(self, model):
        self.queries += 1
        return Query([self.project] if model is Project else [self.task])

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1


def test_event_clears_stale_scores_without_mutating_old_json_object():
    db = Database()
    original_meta = db.task.meta
    original_copy = deepcopy(original_meta)
    handle_ff_score_recalculated_event(db, event_payload())
    assert db.task.meta["current_ffscore"] is None and db.task.meta["current_eeat"] is None
    assert db.project.meta["ffscore"] is None and db.project.meta["eeat_score"] is None
    assert db.task.meta["ffscore_status"] == "insufficient_data"
    assert db.task.meta["eeat_status"] == "insufficient_data"
    assert db.task.meta["ffscore_formula_version"] == "ff_score_v1"
    assert db.task.meta["keep"] is True and db.commits == 1
    assert original_meta == original_copy and db.task.meta is not original_meta


def test_event_preserves_measured_zero():
    db = Database()
    handle_ff_score_recalculated_event(db, event_payload(0, 0))
    assert db.task.meta["current_ffscore"] == 0 and db.task.meta["current_eeat"] == 0
    assert db.task.meta["ffscore_status"] == "complete"


@pytest.mark.parametrize("field,value", [
    ("ff_score", float("nan")), ("ff_score", float("inf")),
    ("ff_score", 10 ** 1000), ("ff_score", -1), ("ff_score", True),
    ("ff_score", "60"), ("ff_score", None), ("status", "insufficient_data"),
    ("status", "unknown"), ("eeat", {"score": 70}), ("eeat", 99),
    ("eeat_score", None), ("project_id", None), ("schema_version", "future/3"),
    ("formula_version", "unknown"), ("eeat_details", {"score": 70, "status": "insufficient_data"}),
])
def test_invalid_event_does_not_query_or_write_database(field, value):
    event = event_payload(60, 70)
    event["payload"][field] = value
    db = Database()
    with pytest.raises(ScoreContractError):
        handle_ff_score_recalculated_event(db, event)
    assert db.queries == 0 and db.commits == 0 and db.added == []
    assert db.task.meta["current_ffscore"] == 85


@pytest.mark.parametrize("event", [None, [], {}, {"payload": None}, {"payload": []}])
def test_malformed_envelope_is_rejected(event):
    with pytest.raises(ScoreContractError):
        parse_score_event(event)


def test_legacy_explicit_null_clears_ff_without_clearing_absent_eeat():
    db = Database()
    handle_ff_score_recalculated_event(db, {"project_id": PROJECT, "ff_score": None})
    assert db.task.meta["current_ffscore"] is None
    assert db.task.meta["current_eeat"] == 90
    assert db.task.meta["ffscore_formula_version"] == "legacy"


def test_legacy_aliases_work_but_cannot_replace_canonical_null():
    observation = parse_score_event({"project_id": PROJECT, "ffscore": 0, "eeat": 70})
    assert observation.ff_score == 0 and observation.eeat_score == 70
    with pytest.raises(ScoreContractError):
        parse_score_event({"project_id": PROJECT, "ff_score": None, "ffscore": 90})
