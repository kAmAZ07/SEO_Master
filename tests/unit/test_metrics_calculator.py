from services.reporting_service.metrics.roi_calculator import calculate_roi_metrics
from services.reporting_service.metrics.calculator import calculate_trust_sentiment


def test_roi_calculator_basic():
    r = calculate_roi_metrics(cost=100.0, revenue=250.0, hitl_actions=2, automated_actions=8)
    assert r["profit"] == 150.0
    assert r["roi_percent"] > 0.0
    assert 0.0 <= r["hitl_efficiency_percent"] <= 100.0


def test_trust_sentiment_range():
    r = calculate_trust_sentiment(
        has_https=True,
        has_privacy_policy=True,
        has_contacts=True,
        brand_mentions=2,
        negative_reviews=0,
        text="Отличное качество, рекомендую. Надежный сервис.",
    )
    assert 0.0 <= r["trust_score"] <= 100.0
    assert 0.0 <= r["sentiment_score"] <= 100.0
import asyncio
from contextlib import asynccontextmanager
from datetime import date
from unittest.mock import AsyncMock, Mock

import pytest



@pytest.mark.parametrize("rows", [[], [{"clicks": 0, "impressions": 10, "ctr": 0.0, "position": 6.5}]])
def test_gsc_summary_uses_property_aggregate_and_distinguishes_no_data(monkeypatch, rows):
    from services.reporting_service.connectors import gsc_connector

    property_url = "https://fixture.test/"
    monkeypatch.setattr(gsc_connector, "_resolve_property_url", Mock(return_value=({}, property_url)))
    monkeypatch.setattr(gsc_connector, "_load_credentials", Mock(return_value=object()))
    service = Mock()
    query = service.searchanalytics.return_value.query
    query.return_value.execute.return_value = {"rows": rows}
    monkeypatch.setattr(gsc_connector, "build", Mock(return_value=service))

    result = gsc_connector.fetch_gsc_summary("project")

    assert query.call_args.kwargs["siteUrl"] == property_url
    body = query.call_args.kwargs["body"]
    assert body["aggregationType"] == "byProperty" and "dimensions" not in body
    assert result["status"] == ("ok" if rows else "no_data")
    assert result["clicks"] == (0 if rows else None)
    assert result["avg_position"] == (6.5 if rows else None)


def test_gsc_traffic_is_kept_out_of_backlink_and_serp_signals(monkeypatch):
    from services.reporting_service import main

    @asynccontextmanager
    async def session():
        yield Mock(execute=AsyncMock(return_value=Mock(scalar_one_or_none=Mock(return_value=None))))

    def read_gsc(project_id, url, days, offset):
        return {"status": "ok", "clicks": 42 if offset == 0 else 14, "range_days": days,
                "avg_position": 6.5 if offset == 0 else 9.0}

    monkeypatch.setattr(main, "get_session", session)
    monkeypatch.setattr(main, "fetch_gsc_summary", read_gsc)
    result = asyncio.run(main.get_project_summary("project", "https://fixture.test/"))
    assert result["signals"]["link_velocity"] is None
    assert result["signals"]["serp_shift"] is None
    assert result["signals"]["backlinks_count"] is None
    assert result["gsc"]["click_delta_per_day"] == 2.0


@pytest.mark.parametrize("failure", [None, "detail", "total"])
def test_report_gsc_totals_do_not_come_from_limited_query_rows(monkeypatch, failure):
    from services.reporting_service.connectors import gsc_connector

    property_url = "https://fixture.test/"
    monkeypatch.setattr(gsc_connector, "_resolve_property_url", Mock(return_value=({}, property_url)))
    monkeypatch.setattr(gsc_connector, "_load_credentials", Mock(return_value=object()))
    service = Mock()
    query = service.searchanalytics.return_value.query

    def execute_query(*, siteUrl, body):
        assert siteUrl == property_url
        is_detail = "dimensions" in body
        if failure == ("detail" if is_detail else "total"):
            return Mock(execute=Mock(side_effect=RuntimeError("fixture-provider-details")))
        rows = (
            [{"keys": ["2026-11-01", "фикстура", "/"], "clicks": 1, "impressions": 3, "position": 2.0}]
            if is_detail else [{"clicks": 20, "impressions": 60, "ctr": 1 / 3, "position": 4.2}]
        )
        return Mock(execute=Mock(return_value={"rows": rows}))

    query.side_effect = execute_query
    monkeypatch.setattr(gsc_connector, "build", Mock(return_value=service))
    start, end = date(2026, 11, 1), date(2026, 11, 7)
    result = gsc_connector.fetch_gsc_rows("project", property_url, start, end)

    assert "fixture-provider-details" not in str(result)
    if failure == "total":
        assert result["status"] == "degraded" and result["available"] is False
        assert result["aggregate"]["clicks"] is None and result["aggregate"]["impressions"] is None
    else:
        assert result["aggregate"]["clicks"] == 20 and result["aggregate"]["avg_position"] == 4.2
        assert result["aggregate"]["aggregation"] == "by_property"
        assert result["status"] == ("partial" if failure else "ok")
        assert len(result["records"]) == (0 if failure else 1)


@pytest.mark.parametrize("aggregate, expected", [({}, None), ({"clicks": 0}, 0)])
def test_report_snapshot_preserves_gsc_missing_and_measured_zero(aggregate, expected):
    from services.reporting_service.report_builders.builder import _snapshot
    from services.reporting_service.schemas.report import ReportType

    start, end = date(2026, 11, 1), date(2026, 11, 7)
    snapshot = _snapshot("project", "https://fixture.test/", ReportType.WEEKLY, start, end,
                         {"gsc": aggregate, "ga4": {}, "yandex": {}, "ff_score": {}}, {"summary": {}})
    assert snapshot["traffic"]["gsc_clicks"] == expected
