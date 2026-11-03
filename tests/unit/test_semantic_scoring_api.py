"""AL-5: сборка входов FF-Score, хранение, событие v2 и HTTP-слой semantic_service (без сети и БД)."""
import asyncio
import contextlib
import json
import math
import types
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from services.semantic_service import main
from services.semantic_service.events.ff_score_recalculated import (
    SCHEMA_VERSION,
    FFScoreRecalculatedEvent,
    publish_ffscore_recalculated,
)
from services.semantic_service.llm.llm_client import LlmRuntime
from services.semantic_service.llm.policy import Budget
from services.semantic_service.proposals.store import InMemoryProposalStore, VersionConflict
from services.semantic_service.scoring import assemble
from services.semantic_service.scoring.assemble import (
    StoredAnalysis,
    availability_for,
    build_ff_readings,
    count_findings,
    count_signal,
    cwv_grade_from_summary,
    eeat_signals_from_request,
)
from services.semantic_service.scoring.eeat_analyzer import analyze_eeat
from services.semantic_service.scoring.ff_score_calculator import calculate_ff_score
from services.semantic_service.scoring.persistence import LEGACY_NOTE, eeat_row_fields, eeat_view, ff_row_fields, ff_view
from services.semantic_service.scoring.readings import ReadingError

PROJECT = "11111111-1111-4111-8111-111111111111"
URL = "https://shop.example.com/"
LONG_TEXT = ("Мы лично использовали эту обувь, опыт эксплуатации подробно описан. " * 60).strip()
GOOD_CWV = {"cwv": {"LCP_grade": "good", "INP_grade": "good", "CLS_grade": "good", "FID_grade": "good"}}
STORED = StoredAnalysis(
    semantic_distance={"semantic_distance": 30.0, "n": 10},
    keyword_coverage={"coverage": 70.0, "total": 5},
    observed_at="2026-11-01T00:00:00+00:00",
)


def eeat_full():
    return analyze_eeat(
        LONG_TEXT, URL, backlinks_count=120, has_https=True, has_privacy_policy=True, has_contacts=True,
        has_author_schema=True, authoritative_outbound_links=4, brand_mentions=15,
    )


def full_values(**overrides):
    values = {
        "freshness_days_since_update": 20, "serp_shift": -1.0, "link_velocity": 2.5, "semantic_distance": None,
        "keyword_coverage": None, "cwv_grade": None, "broken_links_count": None, "schema_errors_count": None,
    }
    values.update(overrides)
    return values


def readings(**overrides):
    kwargs = dict(
        values=full_values(), sources={"freshness_days_since_update": "gsc", "serp_shift": "gsc", "link_velocity": "gsc"},
        availability={}, audit_summary=GOOD_CWV, audit_findings=[], analysis=STORED, eeat_result=eeat_full(),
    )
    kwargs.update(overrides)
    return build_ff_readings(**kwargs)


# --- availability_for ---------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "source,explicit,expected",
    [
        (None, None, "provided"),
        ("gsc", None, "provided"),
        ("estimate", None, "estimated"),
        ("ESTIMATE ", None, "estimated"),
        ("mock", None, "mock"),
        ("sample", None, "sample"),
        ("proxy", None, "proxy"),
        ("measured", None, "measured"),
        ("semantic_analysis_degraded", None, "estimated"),
        ("estimate", "measured", "measured"),   # явное указание главнее метки
        (None, "derived", "derived"),
    ],
)
def test_availability_from_source_label_or_explicit_value(source, explicit, expected):
    assert availability_for(source, explicit) == expected


@pytest.mark.parametrize("explicit", ["unavailable", "great", ""])
def test_explicit_availability_must_be_a_known_non_missing_value(explicit):
    with pytest.raises(ReadingError) as failure:
        availability_for("gsc", explicit)
    assert failure.value.code == "availability_invalid"


# --- сборка входов ----------------------------------------------------------------------------------------

def test_complete_inputs_give_a_full_score_without_any_estimate():
    r = readings()
    assert all(item.counted for item in r.values()), {k: v.availability for k, v in r.items()}
    result = calculate_ff_score(r)
    assert result["status"] == "complete" and isinstance(result["ff_score"], float)
    assert r["semantic_distance"].source == "semantic_analysis" and r["semantic_distance"].availability == "derived"
    assert r["cwv_grade"].value == "good" and r["cwv_grade"].availability == "measured"
    assert r["broken_links_count"].value == 0 and r["schema_errors_count"].value == 0
    assert r["eeat_score"].source == "eeat_v1"


def test_missing_request_values_stay_unavailable_not_estimated():
    r = readings(values=full_values(freshness_days_since_update=None, serp_shift=None, link_velocity=None))
    for name in ("freshness_days_since_update", "serp_shift", "link_velocity"):
        assert r[name].availability == "unavailable" and r[name].value is None and r[name].reason == "not_provided"
    result = calculate_ff_score(r)
    assert result["ff_score"] is None and result["status"] == "insufficient_data"
    assert result["components"]["freshness"]["status"] == "unavailable"
    assert result["partial"] is not None and result["partial"]["comparable_to_full_score"] is False


def test_estimated_sources_are_shown_but_excluded_from_the_full_score():
    r = readings(sources={"freshness_days_since_update": "estimate", "serp_shift": "gsc", "link_velocity": "gsc"})
    assert r["freshness_days_since_update"].availability == "estimated" and r["freshness_days_since_update"].value == 20
    result = calculate_ff_score(r)
    assert result["ff_score"] is None
    assert result["components"]["freshness"]["missing_reasons"] == {"freshness_days_since_update": "excluded_estimated"}


def test_explicit_availability_overrides_the_source_label():
    r = readings(availability={"serp_shift": "mock"})
    assert r["serp_shift"].availability == "mock"
    assert calculate_ff_score(r)["ff_score"] is None


def test_request_values_for_stored_analysis_inputs_win_over_the_stored_ones():
    r = readings(values=full_values(semantic_distance=12.0, keyword_coverage=88.0), sources={"semantic_distance": "serp_api"})
    assert (r["semantic_distance"].value, r["semantic_distance"].source) == (12.0, "serp_api")
    assert (r["keyword_coverage"].value, r["keyword_coverage"].source) == (88.0, "request")


@pytest.mark.parametrize(
    "analysis,reason",
    [
        (None, "analysis_missing"),
        (StoredAnalysis(), "analysis_missing"),
        (StoredAnalysis(semantic_distance={"semantic_distance": 100.0, "n": 0}), "serp_top10_texts_missing"),
        (StoredAnalysis(semantic_distance={"n": 5}), "analysis_missing"),
    ],
)
def test_semantic_distance_placeholders_and_gaps_are_unavailable(analysis, reason):
    item = readings(analysis=analysis)["semantic_distance"]
    assert item.availability == "unavailable" and item.reason == reason


@pytest.mark.parametrize(
    "analysis,reason",
    [
        (None, "analysis_missing"),
        (StoredAnalysis(), "analysis_missing"),
        (StoredAnalysis(keyword_coverage={"coverage": 0.0, "total": 0}), "keywords_missing"),
        (StoredAnalysis(keyword_coverage={"total": 3}), "analysis_missing"),
    ],
)
def test_keyword_coverage_placeholders_and_gaps_are_unavailable(analysis, reason):
    item = readings(analysis=analysis)["keyword_coverage"]
    assert item.availability == "unavailable" and item.reason == reason


def test_zero_distance_with_serp_texts_is_a_real_measurement():
    item = readings(analysis=StoredAnalysis(semantic_distance={"semantic_distance": 0.0, "n": 3}))["semantic_distance"]
    assert item.value == 0.0 and item.counted


def test_eeat_input_requires_a_complete_eeat_result():
    incomplete = analyze_eeat("", URL)
    item = readings(eeat_result=incomplete)["eeat_score"]
    assert item.availability == "unavailable" and item.reason == "eeat_insufficient_data"
    assert readings(eeat_result={"score": 80.0, "status": "insufficient_data"})["eeat_score"].availability == "unavailable"


def test_audit_findings_distinguish_not_provided_from_none_found():
    absent = readings(audit_findings=None)
    assert absent["broken_links_count"].reason == "audit_findings_not_provided"
    assert absent["schema_errors_count"].availability == "unavailable"
    none_found = readings(audit_findings=[])
    assert none_found["broken_links_count"].value == 0 and none_found["broken_links_count"].availability == "derived"
    found = readings(audit_findings=[{"code": "broken_link_404"}, {"code": "broken_link_404"}, {"code": "jsonld_missing_type"}, "junk", {"code": None}])
    assert found["broken_links_count"].value == 2 and found["schema_errors_count"].value == 1


def test_explicit_counts_in_the_request_beat_findings():
    r = readings(values=full_values(broken_links_count=7, schema_errors_count=3), audit_findings=[])
    assert (r["broken_links_count"].value, r["schema_errors_count"].value) == (7, 3)
    assert r["broken_links_count"].source == "request"


def test_count_findings_matches_codes_and_prefixes_only():
    findings = [{"code": "broken_link_404"}, {"code": "broken_link_500"}, {"code": "jsonld_x"}, {"code": "title_missing"}, 5, None]
    assert count_findings(findings, codes=frozenset({"broken_link_404"})) == 1
    assert count_findings(findings, prefixes=("jsonld_",)) == 1
    assert count_findings(findings) == 0


def test_request_cwv_grade_is_used_when_given():
    r = readings(values=full_values(cwv_grade="Poor"), audit_summary=None)
    assert r["cwv_grade"].value == "poor" and r["cwv_grade"].source == "request"


def test_invalid_request_values_are_rejected_with_a_code():
    with pytest.raises(ReadingError) as failure:
        readings(values=full_values(cwv_grade="excellent"))
    assert failure.value.code == "value_invalid"


# --- CWV из аудита -------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cwv,value,availability,reason",
    [
        ({"LCP_grade": "good", "INP_grade": "good", "CLS_grade": "good"}, "good", "measured", None),
        ({"LCP_grade": "good", "INP_grade": "poor", "CLS_grade": "needs_improvement"}, "poor", "measured", None),
        ({"LCP_grade": "needs_improvement", "INP_grade": "good", "CLS_grade": "good"}, "needs_improvement", "measured", None),
        ({"LCP_grade": "good", "INP_grade": "needs_improvement", "CLS_grade": "good"}, "needs_improvement", "measured", None),
        ({"LCP_grade": "needs_improvement", "INP_grade": "good", "CLS_grade": "poor"}, "poor", "measured", None),
        ({"LCP_grade": "good", "INP_grade": "poor", "CLS_grade": "needs_improvement"}, "poor", "measured", None),
        ({"LCP_grade": "needs_improvement", "INP_grade": "poor", "CLS_grade": "good"}, "poor", "measured", None),
        # FID как прокси: худшая из LCP, CLS и FID, порядок не влияет на результат
        ({"LCP_grade": "needs_improvement", "INP_grade": "unknown", "CLS_grade": "poor", "FID_grade": "good"}, "poor", "proxy", "inp_missing_fid_used"),
        ({"LCP_grade": "good", "INP_grade": "unknown", "CLS_grade": "good", "FID_grade": "needs_improvement"}, "needs_improvement", "proxy", "inp_missing_fid_used"),
        # INP неизвестен, FID есть: старая метрика не подменяет INP, оценка считается прокси
        ({"LCP_grade": "good", "INP_grade": "unknown", "CLS_grade": "good", "FID_grade": "poor"}, "poor", "proxy", "inp_missing_fid_used"),
        # известна только часть метрик
        ({"LCP_grade": "good", "INP_grade": "unknown", "CLS_grade": "unknown"}, "good", "proxy", "cwv_partial:CLS,INP"),
        ({"LCP_grade": "poor"}, "poor", "proxy", "cwv_partial:CLS,INP"),
    ],
)
def test_cwv_grade_from_audit_summary(cwv, value, availability, reason):
    item = cwv_grade_from_summary({"cwv": cwv})
    assert (item.value, item.availability, item.reason) == (value, availability, reason)
    assert item.counted == (availability == "measured")


@pytest.mark.parametrize(
    "summary,reason",
    [
        (None, "audit_summary_missing"),
        ([], "audit_summary_missing"),
        ({}, "audit_summary_missing"),
        ({"cwv": "good"}, "audit_summary_missing"),
        ({"cwv": {"LCP_grade": "unknown", "INP_grade": "unknown", "CLS_grade": "unknown", "FID_grade": "good"}}, "cwv_not_measured"),
        ({"cwv": {}}, "cwv_not_measured"),
    ],
)
def test_cwv_grade_unavailable_without_measurements(summary, reason):
    item = cwv_grade_from_summary(summary)
    assert item.availability == "unavailable" and item.reason == reason


# --- сигналы E-E-A-T из запроса -------------------------------------------------------------------------------------

def test_zero_counts_without_a_source_are_not_observations():
    values = {"backlinks_count": 0, "brand_mentions": 0, "authoritative_outbound_links": 0, "has_https": None}
    signals = eeat_signals_from_request(values, {})
    assert signals["backlinks_count"] is None and signals["brand_mentions"] is None and signals["authoritative_outbound_links"] is None
    with_source = eeat_signals_from_request(values, {"backlinks_count": "gsc", "brand_mentions": "yandex"})
    assert with_source["backlinks_count"] == 0 and with_source["brand_mentions"] == 0
    assert with_source["authoritative_outbound_links"] is None


def test_positive_counts_and_unknown_flags_pass_through():
    signals = eeat_signals_from_request({"backlinks_count": 5, "has_contacts": False, "has_privacy_policy": None}, {})
    assert signals["backlinks_count"] == 5 and signals["has_contacts"] is False and signals["has_privacy_policy"] is None
    assert count_signal(None, "backlinks_count", {}) is None and count_signal(3, "x", {}) == 3
    assert count_signal(0, "backlinks_count", {"backlinks_count": "   "}) is None  # пустая метка источника — не источник


def test_source_label_ignores_non_string_values():
    assert assemble._source_label({"a": 5, "b": " ", "c": "gsc"}, "a", "d") == "d"
    assert assemble._source_label({"b": " "}, "b", "d") == "d"
    assert assemble._source_label({"c": "gsc"}, "c", "d") == "gsc"


# --- сохранение ----------------------------------------------------------------------------------------------------------

def row(**fields):
    base = dict(score_id="s1", created_at=datetime(2026, 11, 4, tzinfo=timezone.utc))
    base.update(fields)
    return types.SimpleNamespace(**base)


def test_ff_roundtrip_preserves_the_whole_report():
    result = calculate_ff_score(readings())
    fields = ff_row_fields(result)
    assert fields["ff_score"] == result["ff_score"] and fields["status"] == "complete" and fields["formula_version"] == "ff_score_v1"
    assert set(fields["inputs"]) == {"readings", "coverage", "partial", "mode", "weights", "heuristic", "note"}
    view = ff_view(row(ff_score=fields["ff_score"], status=fields["status"], formula_version=fields["formula_version"],
                       components=fields["components"], inputs=fields["inputs"], thresholds=fields["thresholds"]))
    assert view["score"] == result["ff_score"] and view["inputs"] == result["inputs"]
    assert view["coverage"] == result["coverage"] and view["mode"] == result["mode"] and view["weights"] == result["weights"]
    assert view["heuristic"] is True and view["note"] == result["note"] and "legacy_estimated" not in view


def test_insufficient_ff_is_stored_with_a_null_score():
    result = calculate_ff_score(readings(values=full_values(serp_shift=None)))
    fields = ff_row_fields(result)
    assert fields["ff_score"] is None and fields["status"] == "insufficient_data"
    view = ff_view(row(ff_score=None, status="insufficient_data", formula_version="ff_score_v1", components=fields["components"],
                       inputs=fields["inputs"], thresholds=fields["thresholds"]))
    assert view["score"] is None and view["partial"]["comparable_to_full_score"] is False


def test_legacy_ff_rows_are_flagged_and_keep_their_original_inputs():
    legacy = row(ff_score=67.5, status="legacy_estimated", formula_version="legacy", components={"freshness": 1}, inputs={"sources": {"x": "estimate"}}, thresholds={})
    view = ff_view(legacy)
    assert view["legacy_estimated"] is True and view["note"] == LEGACY_NOTE and view["inputs"] == {"sources": {"x": "estimate"}}
    assert view["score"] == 67.5 and view["status"] == "legacy_estimated"


def test_ff_view_tolerates_a_malformed_inputs_column():
    view = ff_view(row(ff_score=50.0, status="complete", formula_version="ff_score_v1", components={}, inputs=None, thresholds={}))
    assert view["inputs"] == {} and view["heuristic"] is True and view["coverage"] is None


def test_eeat_roundtrip_and_legacy_marking():
    result = analyze_eeat(LONG_TEXT, URL, has_privacy_policy=True)
    fields = eeat_row_fields(result)
    assert fields["score"] is None and fields["status"] == "insufficient_data"
    assert fields["signals"]["partial"] == result["partial"] and fields["signals"]["missing_inputs"] == result["missing_inputs"]
    view = eeat_view(row(score=fields["score"], status=fields["status"], formula_version=fields["formula_version"],
                         breakdown=fields["breakdown"], signals=fields["signals"]))
    assert view["partial"] == result["partial"] and view["missing_inputs"] == result["missing_inputs"]
    assert "partial" not in view["signals"] and "word_count" in view["signals"] and "legacy_estimated" not in view
    legacy = eeat_view(row(score=40.97, status="legacy_estimated", formula_version="legacy", breakdown={}, signals=None))
    assert legacy["legacy_estimated"] is True and legacy["note"] == LEGACY_NOTE and legacy["signals"] == {}


# --- событие v2 -------------------------------------------------------------------------------------------------------------

def build_event(**overrides):
    details = overrides.pop("eeat_details", {"score": 57.09, "status": "complete", "breakdown": {"experience": 1.0}})
    kwargs = dict(
        project_id=PROJECT, root_url=URL, ff_score_id="f1", ff_score=71.2, components={"freshness": {}}, status="complete",
        formula_version="ff_score_v1", inputs={"x": 1}, eeat_details=details,
    )
    kwargs.update(overrides)
    return FFScoreRecalculatedEvent.build(**kwargs)


def test_event_carries_numeric_eeat_never_an_object():
    payload = json.loads(build_event().to_bytes())["payload"]
    assert payload["schema_version"] == SCHEMA_VERSION == "ffscore_recalculated/2"
    assert payload["eeat_score"] == 57.09 and payload["eeat"] == 57.09 and isinstance(payload["eeat"], float)
    assert payload["eeat_details"]["breakdown"] == {"experience": 1.0}
    assert payload["status"] == "complete" and payload["formula_version"] == "ff_score_v1"


def test_event_with_unknown_eeat_has_null_not_an_object():
    event = build_event(ff_score=None, status="insufficient_data", eeat_details={"score": None, "status": "insufficient_data"})
    payload = json.loads(event.to_bytes())["payload"]
    assert payload["ff_score"] is None and payload["eeat_score"] is None and payload["eeat"] is None
    assert payload["eeat_details"]["status"] == "insufficient_data"
    assert build_event(eeat_details=None).payload.eeat is None  # без деталей — тоже null, а не {}


def test_management_style_consumer_never_sees_an_object_in_eeat():
    """Потребитель читает `eeat_score`, а при None — `eeat`; в обоих полях число или None."""
    for details in ({"score": 40.97}, {"score": None}, {}):
        payload = json.loads(build_event(eeat_details=details).to_bytes())["payload"]
        fallback = payload.get("eeat_score") if payload.get("eeat_score") is not None else payload.get("eeat")
        assert fallback is None or isinstance(fallback, float)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, -0.1, 100.1, True, "80"])
def test_event_rejects_invalid_scores(bad):
    with pytest.raises((ValueError, ValidationError)):
        build_event(ff_score=bad)
    with pytest.raises((ValueError, ValidationError)):
        build_event(eeat_details={"score": bad})


@pytest.mark.parametrize("score,status", [(None, "complete"), (50.0, "insufficient_data"), (50.0, "legacy_estimated")])
def test_event_requires_null_score_exactly_for_insufficient_data(score, status):
    with pytest.raises((ValueError, ValidationError)):
        build_event(ff_score=score, status=status)


def test_event_boundaries_are_allowed():
    assert build_event(ff_score=0).payload.ff_score == 0.0
    assert build_event(ff_score=100, eeat_details={"score": 100}).payload.eeat_score == 100.0


def test_publish_is_skipped_without_a_broker(monkeypatch):
    monkeypatch.setattr("services.semantic_service.events.ff_score_recalculated.settings", types.SimpleNamespace(rabbitmq_url=None))
    asyncio.run(publish_ffscore_recalculated(PROJECT, URL, "f1", 50.0, {}, status="complete", formula_version="ff_score_v1"))


def test_publish_sends_a_persistent_message_to_the_topic_exchange(monkeypatch):
    sent = {}

    class Exchange:
        async def publish(self, message, routing_key):
            sent.update(body=json.loads(message.body), routing_key=routing_key, headers=message.headers,
                        content_type=message.content_type, delivery_mode=message.delivery_mode)

    class Channel:
        async def declare_exchange(self, name, kind, durable):
            sent.update(exchange=name, kind=kind, durable=durable)
            return Exchange()

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def channel(self):
            return Channel()

    async def connect(url):
        sent["url"] = url
        return Connection()

    module = "services.semantic_service.events.ff_score_recalculated"
    monkeypatch.setattr(module + ".settings", types.SimpleNamespace(rabbitmq_url="amqp://broker/"))
    monkeypatch.setattr(module + ".aio_pika.connect_robust", connect)
    asyncio.run(
        publish_ffscore_recalculated(
            PROJECT, URL, "f1", None, {}, status="insufficient_data", formula_version="ff_score_v1", eeat_details={"score": None}
        )
    )
    assert sent["url"] == "amqp://broker/" and sent["exchange"] == "seo_master.events"
    assert sent["durable"] is True and sent["kind"].value == "topic"
    assert sent["content_type"] == "application/json" and sent["delivery_mode"].name == "PERSISTENT"
    assert sent["routing_key"] == "semantic.ffscore.recalculated"
    assert sent["body"]["payload"]["ff_score"] is None and sent["headers"]["event_type"] == "FFScoreRecalculated"


# --- HTTP-слой -------------------------------------------------------------------------------------------------------------------

class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    def __init__(self, world):
        self.world = world

    def add(self, item):
        self.world.added.append(item)

    async def commit(self):
        self.world.commits += 1

    async def execute(self, statement):
        self.world.queries.append(str(statement))
        return FakeResult(self.world.results.pop(0) if self.world.results else None)


class World:
    def __init__(self):
        self.added, self.queries, self.results, self.commits = [], [], [], 0
        self.events = []


@pytest.fixture()
def world(monkeypatch):
    state = World()

    @contextlib.asynccontextmanager
    async def fake_session():
        yield FakeSession(state)

    async def fake_publish(**kwargs):
        state.events.append(kwargs)

    monkeypatch.setattr(main, "get_session", fake_session)
    monkeypatch.setattr(main, "publish_ffscore_recalculated", fake_publish)
    return state


@pytest.fixture()
def client(world):
    store = InMemoryProposalStore()
    stub = LlmRuntime(providers=[], cache=types.SimpleNamespace(get=None, set=None), budget=Budget(1.0, 1.0, 5.0))
    main.app.dependency_overrides[main.get_store] = lambda: store
    main.app.dependency_overrides[main.get_runtime] = lambda: stub
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def ff_request(**overrides):
    body = {
        "project_id": PROJECT, "root_url": URL, "content_text": LONG_TEXT,
        "freshness_days_since_update": 20, "serp_shift": -1.0, "link_velocity": 2.5,
        "semantic_distance": 30.0, "keyword_coverage": 70.0,
        "audit_summary": GOOD_CWV, "audit_findings": [],
        "backlinks_count": 120, "brand_mentions": 15, "authoritative_outbound_links": 4,
        "has_privacy_policy": True, "has_contacts": True, "has_author_schema": True,
        "input_sources": {"freshness_days_since_update": "gsc", "serp_shift": "gsc", "link_velocity": "gsc", "backlinks_count": "gsc", "brand_mentions": "gsc"},
    }
    body.update(overrides)
    return body


def test_ff_score_complete_inputs_return_a_number_and_persist_both_rows(client, world):
    response = client.post("/semantic/ff-score", json=ff_request())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "complete" and isinstance(body["ff_score"], float) and 0 <= body["ff_score"] <= 100
    assert body["formula_version"] == "ff_score_v1" and body["heuristic"] is True and body["mode"]["heuristic"] is True
    assert body["eeat"]["status"] == "complete" and body["eeat"]["score_id"] == body["eeat_score_id"]
    ff_row = next(r for r in world.added if r.__class__.__name__ == "FFScoreRow")
    eeat_row = next(r for r in world.added if r.__class__.__name__ == "EEATScoreRow")
    assert ff_row.ff_score == body["ff_score"] and ff_row.status == "complete" and ff_row.eeat_score_id == eeat_row.score_id
    assert eeat_row.score == body["eeat"]["score"] and eeat_row.formula_version == "eeat_v1"
    assert world.commits == 1
    event = world.events[0]
    assert event["ff_score"] == body["ff_score"] and event["status"] == "complete" and event["eeat_details"]["score"] == eeat_row.score


def test_ff_score_without_measurements_is_null_never_a_guess(client, world):
    """Запрос в форме прежнего вызова management: нет freshness-данных, нет аналитики, нет флагов сайта."""
    body = {"project_id": None, "root_url": URL, "content_text": "", "audit_summary": {}, "audit_findings": [], "backlinks_count": 0, "brand_mentions": 0}
    response = client.post("/semantic/ff-score", json=body)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ff_score"] is None and data["status"] == "insufficient_data" and data["mode"] is None
    assert data["partial"] is None or data["partial"]["comparable_to_full_score"] is False
    assert "freshness_days_since_update" in data["coverage"]["missing_inputs"]
    assert data["eeat"]["score"] is None and data["eeat"]["status"] == "insufficient_data"
    ff_row = next(r for r in world.added if r.__class__.__name__ == "FFScoreRow")
    assert ff_row.ff_score is None and ff_row.status == "insufficient_data"
    assert world.events[0]["ff_score"] is None and world.events[0]["status"] == "insufficient_data"


def test_ff_score_reads_the_latest_stored_analysis(client, world):
    analysis = types.SimpleNamespace(
        semantic_distance={"semantic_distance": 25.0, "n": 8}, keyword_coverage={"coverage": 60.0, "total": 4},
        created_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
    )
    world.results.append(analysis)
    body = ff_request(semantic_distance=None, keyword_coverage=None)
    data = client.post("/semantic/ff-score", json=body).json()
    assert data["status"] == "complete"
    assert data["inputs"]["semantic_distance"]["value"] == 25.0 and data["inputs"]["semantic_distance"]["source"] == "semantic_analysis"
    assert data["inputs"]["keyword_coverage"]["observed_at"].startswith("2026-11-02")
    assert "semantic_analysis" in world.queries[0]


def test_ff_score_ignores_a_stored_analysis_made_without_serp_texts(client, world):
    world.results.append(types.SimpleNamespace(
        semantic_distance={"semantic_distance": 100.0, "n": 0}, keyword_coverage={"coverage": 0.0, "total": 0}, created_at=None,
    ))
    data = client.post("/semantic/ff-score", json=ff_request(semantic_distance=None, keyword_coverage=None)).json()
    assert data["status"] == "insufficient_data"
    assert data["components"]["familiarity"]["missing_reasons"] == {"semantic_distance": "serp_top10_texts_missing", "keyword_coverage": "keywords_missing"}


def test_ff_score_without_project_does_not_query_the_analysis_table(client, world):
    client.post("/semantic/ff-score", json=ff_request(project_id=None))
    assert world.queries == []


def test_ff_score_excludes_estimated_inputs_reported_by_the_caller(client):
    body = ff_request(input_availability={"link_velocity": "estimated"})
    data = client.post("/semantic/ff-score", json=body).json()
    assert data["ff_score"] is None
    assert data["components"]["freshness"]["missing_reasons"] == {"link_velocity": "excluded_estimated"}
    assert data["inputs"]["link_velocity"]["value"] == 2.5  # значение показывается, но не считается


def test_ff_score_rejects_an_unknown_availability_with_a_code(client):
    response = client.post("/semantic/ff-score", json=ff_request(input_availability={"link_velocity": "excellent"}))
    assert response.status_code == 422 and response.json()["detail"]["code"] == "availability_invalid"


def test_ff_score_rejects_an_invalid_cwv_grade_with_a_code(client):
    response = client.post("/semantic/ff-score", json=ff_request(cwv_grade="superb"))
    assert response.status_code == 422 and response.json()["detail"]["code"] == "value_invalid"


@pytest.mark.parametrize("field,value", [("serp_shift", 51), ("link_velocity", -1), ("semantic_distance", 101), ("freshness_days_since_update", -1)])
def test_ff_score_validates_request_ranges(client, field, value):
    assert client.post("/semantic/ff-score", json=ff_request(**{field: value})).status_code == 422


def test_eeat_endpoint_reports_unknown_signals_honestly(client, world):
    response = client.post("/semantic/eeat", json={"root_url": URL, "text": LONG_TEXT})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["score"] is None and data["status"] == "insufficient_data" and data["formula_version"] == "eeat_v1"
    assert data["breakdown"]["authoritativeness"] is None and "authoritativeness" in data["missing_inputs"]
    assert data["heuristic"] is True and data["signals"]["backlinks_count"]["availability"] == "unavailable"
    row = world.added[0]
    assert row.score is None and row.status == "insufficient_data"


def test_eeat_endpoint_complete_signals(client, world):
    body = {
        "root_url": URL, "text": LONG_TEXT, "backlinks_count": 120, "brand_mentions": 15, "authoritative_outbound_links": 4,
        "has_https": True, "has_privacy_policy": True, "has_contacts": True, "has_author_schema": True, "project_id": PROJECT,
    }
    data = client.post("/semantic/eeat", json=body).json()
    assert data["status"] == "complete" and isinstance(data["score"], float) and data["partial"] is None
    assert world.added[0].score == data["score"] and world.added[0].project_id == PROJECT


def test_latest_returns_views_with_status(client, world):
    ff = types.SimpleNamespace(
        score_id="f1", status="legacy_estimated", formula_version="legacy", ff_score=50.0, components={}, inputs={}, thresholds={},
        created_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
    )
    ee = types.SimpleNamespace(
        score_id="e1", status="insufficient_data", formula_version="eeat_v1", score=None, breakdown={}, signals={"partial": None},
        created_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
    )
    world.results.extend([ff, ee, None])
    data = client.get(f"/semantic/latest/{PROJECT}").json()
    assert data["ff_score"]["legacy_estimated"] is True and data["ff_score"]["status"] == "legacy_estimated"
    assert data["eeat"]["score"] is None and data["eeat"]["status"] == "insufficient_data"
    assert data["draft"] is None


def test_latest_for_an_unknown_project_is_empty(client, world):
    assert client.get(f"/semantic/latest/{PROJECT}").json() == {"project_id": PROJECT, "ff_score": None, "eeat": None, "draft": None}


def test_latest_marks_stored_drafts_as_legacy(client, world):
    draft = types.SimpleNamespace(draft_id="d1", root_url=URL, drafts={"title": "x"}, created_at=datetime(2026, 11, 1, tzinfo=timezone.utc))
    world.results.extend([None, None, draft])
    assert client.get(f"/semantic/latest/{PROJECT}").json()["draft"]["legacy"] is True


def test_drafts_endpoint_is_gone_and_names_its_replacement(client):
    for payload in ({"root_url": URL, "content": "x"}, None):
        response = client.post("/semantic/drafts", json=payload)
        assert response.status_code == 410
        detail = response.json()["detail"]
        assert detail["code"] == "drafts_endpoint_removed" and detail["replacement"] == "POST /semantic/proposals"


def proposal_body(**overrides):
    body = {
        "project_id": PROJECT, "target_url": "https://shop.example.com/catalog/boots", "evidence": [{"evidence_id": "page-1", "sha256": "a" * 64}],
        "finding_codes": ["title_missing", "h1_missing"],
        "facts": {"title": None, "h1": [], "text": "Магазин зимней обуви в Москве. Мы продаём кожаные ботинки и сапоги ручной работы с доставкой по России. В каталоге есть модели для мужчин и женщин.", "lang": "ru", "site_name": "Северный след"},
    }
    body.update(overrides)
    return body


def test_proposals_create_and_read_back(client):
    response = client.post("/semantic/proposals", json=proposal_body(operations=["title"]))
    assert response.status_code == 200, response.text
    data = response.json()
    assert [i["operation_type"] for i in data["items"]] == ["title"] and data["items"][0]["status"] == "created"
    version = data["items"][0]["version"]
    assert version["execution_mode"] == "managed" and version["changes"][0]["before_state"] == "missing"
    assert client.get(f"/semantic/proposals/{version['version_id']}").json() == version
    history = client.get(f"/semantic/recommendations/{version['recommendation_id']}/versions").json()
    assert [v["version"] for v in history["versions"]] == [1]
    listing = client.get(f"/semantic/projects/{PROJECT}/proposals").json()
    assert [v["version_id"] for v in listing["versions"]] == [version["version_id"]]
    again = client.post("/semantic/proposals", json=proposal_body(operations=["title"])).json()
    assert again["items"][0]["status"] == "existing" and again["items"][0]["version"]["version_id"] == version["version_id"]


def test_proposals_report_needs_input_instead_of_inventing_text(client):
    body = proposal_body(facts={"title": None, "h1": [], "text": None}, operations=["title", "description"])
    data = client.post("/semantic/proposals", json=body).json()
    assert [i["status"] for i in data["items"]] == ["needs_input", "needs_input"]
    assert all(i["version"] is None and i["needs_input"]["missing"] for i in data["items"])


def test_proposals_without_any_requested_operation_are_rejected(client):
    response = client.post("/semantic/proposals", json=proposal_body(finding_codes=["unrelated"]))
    assert response.status_code == 422
    assert response.json()["detail"] == {"code": "proposal_rejected", "issues": [{"code": "no_operation_requested", "path": "operations", "detail": ""}]}


def test_proposals_reject_an_invalid_target_url(client):
    response = client.post("/semantic/proposals", json=proposal_body(target_url="ftp://shop.example.com/x"))
    assert response.status_code == 422 and response.json()["detail"]["issues"][0]["code"] == "target_url_invalid"


@pytest.mark.parametrize(
    "mutation",
    [
        {"project_id": "not-a-uuid"},
        {"evidence": []},
        {"unknown_field": 1},
        {"operations": ["content_edit"]},
        {"operations": ["title", "title"]},
        {"facts": {"title": "x", "surprise": 1}},
    ],
)
def test_proposals_validate_the_request_shape(client, mutation):
    assert client.post("/semantic/proposals", json=proposal_body(**mutation)).status_code == 422


def test_proposals_map_concurrent_regeneration_to_409(client, monkeypatch):
    async def conflict(*args, **kwargs):
        raise VersionConflict("too_many_concurrent_regenerations")

    monkeypatch.setattr(main, "generate_proposals", conflict)
    response = client.post("/semantic/proposals", json=proposal_body(operations=["title"]))
    assert response.status_code == 409 and response.json()["detail"]["code"] == "version_conflict"


def test_unknown_versions_and_recommendations_are_404(client):
    missing = str(uuid.uuid4())
    assert client.get(f"/semantic/proposals/{missing}").status_code == 404
    assert client.get(f"/semantic/recommendations/{missing}/versions").status_code == 404
    assert client.get(f"/semantic/projects/{PROJECT}/proposals").json() == {"project_id": PROJECT, "versions": []}


def test_corrupted_stored_versions_are_reported_not_hidden(client):
    from services.semantic_service.proposals.repository import CorruptedVersion

    class Broken:
        async def get(self, version_id):
            raise CorruptedVersion(version_id)

        async def history(self, recommendation_id):
            raise CorruptedVersion("v-1")

        async def list_for_project(self, project_id, limit=100):
            raise CorruptedVersion("v-2")

    main.app.dependency_overrides[main.get_store] = lambda: Broken()
    for url in ("/semantic/proposals/v-1", "/semantic/recommendations/r-1/versions", f"/semantic/projects/{PROJECT}/proposals"):
        response = client.get(url)
        assert response.status_code == 500 and response.json()["detail"]["code"] == "stored_version_corrupted", url


def test_published_schema_is_the_model_schema(client):
    schema = client.get("/semantic/schemas/recommendation-version").json()
    assert schema["title"] == "RecommendationVersion" and schema["additionalProperties"] is False
    assert "content_hash" in schema["required"] and "changes" in schema["required"]


def test_runtime_is_created_once_and_closed_on_shutdown(monkeypatch):
    closed = []

    class Stub:
        async def aclose(self):
            closed.append(True)

    created = []
    monkeypatch.setattr(main, "_runtime", None)
    monkeypatch.setattr(main, "open_runtime", lambda: created.append(1) or Stub())
    first, second = main.get_runtime(), main.get_runtime()
    assert first is second and created == [1]
    asyncio.run(main._shutdown())
    assert closed == [True]
    monkeypatch.setattr(main, "_runtime", None)
    asyncio.run(main._shutdown())  # без созданного runtime ничего не падает
    assert closed == [True]


def test_default_store_is_the_sql_store_over_the_service_session():
    store = main.get_store()
    assert store.__class__.__name__ == "SqlProposalStore"


def test_value_errors_still_map_to_400_outside_proposals(client, monkeypatch):
    async def explode(**kwargs):
        raise ValueError("bad input")

    monkeypatch.setattr(main, "create_semantic_analysis", explode)
    response = client.post("/semantic/analyze", json={"root_url": URL, "content_text": "text"})
    assert response.status_code == 400 and response.json() == {"detail": "bad input"}


def test_health_reports_the_service(client):
    data = client.get("/health").json()
    assert data["status"] == "ok" and data["service"] == "semantic_service" and data["ts"].endswith("+00:00")


def test_analyze_returns_the_pipeline_result(client, monkeypatch):
    async def fake(**kwargs):
        return {
            "analysis_id": "a1", "project_id": kwargs["project_id"], "root_url": kwargs["root_url"],
            "content_gap": {}, "semantic_distance": {"semantic_distance": 100.0, "n": 0}, "keyword_coverage": {"coverage": 0.0, "total": 0}, "inputs": {},
        }

    monkeypatch.setattr(main, "create_semantic_analysis", fake)
    response = client.post("/semantic/analyze", json={"root_url": URL, "project_id": PROJECT, "keywords": ["a"]})
    assert response.status_code == 200 and response.json()["analysis_id"] == "a1" and response.json()["project_id"] == PROJECT


def test_startup_initialises_the_database_and_starts_both_consumers(monkeypatch):
    calls = []

    async def init():
        calls.append("db")

    async def crawl():
        calls.append("crawl")

    async def task():
        calls.append("task")

    monkeypatch.setattr(main, "init_db", init)
    monkeypatch.setattr(main, "maybe_start_crawl_completed_consumer", crawl)
    monkeypatch.setattr(main, "maybe_start_task_created_consumer", task)

    async def run():
        await main._startup()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(run())
    assert calls[0] == "db" and sorted(calls[1:]) == ["crawl", "task"]


def test_readability_proxy_of_empty_text_is_zero():
    from services.semantic_service.scoring import eeat_analyzer

    assert eeat_analyzer._readability_proxy("") == 0.0
    assert eeat_analyzer._readability_proxy("   ") == 0.0


def test_stored_analysis_is_immutable():
    with pytest.raises(Exception) as failure:
        STORED.keyword_coverage = {}  # type: ignore[misc]
    assert failure.type.__name__ == "FrozenInstanceError"


def test_text_size_is_bounded_for_scores(client):
    big = "я" * 1_000_001
    assert client.post("/semantic/ff-score", json=ff_request(content_text=big)).status_code == 422
    assert client.post("/semantic/eeat", json={"root_url": URL, "text": big}).status_code == 422
    assert client.post("/semantic/eeat", json={"root_url": URL, "text": "я" * 1_000_000}).status_code == 200


def test_event_bytes_are_utf8_and_reject_non_finite_numbers_anywhere():
    event = build_event(root_url="https://пример.рф/", components={"freshness": {"note": "свежесть"}})
    raw = event.to_bytes()
    assert "свежесть".encode("utf-8") in raw and b"\\u" not in raw
    broken = build_event(components={"freshness": {"score": math.nan}})
    with pytest.raises(ValueError):
        broken.to_bytes()


def test_views_default_the_heuristic_flag_and_mark_legacy_rows_as_heuristic():
    legacy = ff_view(row(ff_score=50.0, status="legacy_estimated", formula_version="legacy", components={}, inputs={}, thresholds={}))
    assert legacy["heuristic"] is True
    plain = eeat_view(row(score=50.0, status="complete", formula_version="eeat_v1", breakdown={}, signals={}))
    assert plain["heuristic"] is True and plain["partial"] is None and plain["missing_inputs"] == {}
