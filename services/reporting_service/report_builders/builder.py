import asyncio
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from services.reporting_service.connectors.ga4_connector import fetch_ga4_rows
from services.reporting_service.connectors.gsc_connector import fetch_gsc_rows
from services.reporting_service.connectors.yandex_connector import fetch_yandex_rows
from services.reporting_service.db.models import (
    ChangelogRow,
    GA4DataRow,
    GSCDataRow,
    MetricsHistoryRow,
    ReportRow,
    YandexWebmasterDataRow,
)
from services.reporting_service.schemas.report import ReportGenerationRequest, ReportSource, ReportType


def _resolve_period(request: ReportGenerationRequest) -> tuple[date, date]:
    end = request.period_end or date.today()
    if request.period_start:
        return request.period_start, end

    if request.report_type == ReportType.WEEKLY:
        return end - timedelta(days=6), end
    if request.report_type == ReportType.MONTHLY:
        return end - timedelta(days=29), end
    if request.report_type == ReportType.CHANGELOG:
        return end - timedelta(days=29), end
    return end - timedelta(days=6), end


def _dt_bounds(period_start: date, period_end: date) -> tuple[datetime, datetime]:
    start = datetime.combine(period_start, time.min, tzinfo=timezone.utc)
    end = datetime.combine(period_end, time.max, tzinfo=timezone.utc)
    return start, end


def _stable_id(source: str, *parts: Any) -> str:
    raw = ":".join(str(part or "") for part in (source, *parts))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, raw))


def _parse_date(value: Any, fallback: date) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return fallback
    return fallback


async def _upsert_rows(session: AsyncSession, model, rows: list[dict[str, Any]], update_columns: tuple[str, ...]) -> None:
    for row in rows:
        stmt = pg_insert(model).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=["date", "id"],
            set_={column: getattr(stmt.excluded, column) for column in update_columns},
        )
        await session.execute(stmt)


async def _persist_raw_rows(
    session: AsyncSession,
    project_id: str,
    period_end: date,
    gsc_result: dict[str, Any],
    ga4_result: dict[str, Any],
    yandex_result: dict[str, Any],
) -> None:
    gsc_rows: list[dict[str, Any]] = []
    for row in gsc_result.get("records", []) or []:
        row_date = _parse_date(row.get("date"), period_end)
        query = row.get("query")
        page = row.get("page")
        gsc_rows.append(
            {
                "id": _stable_id("gsc", project_id, row_date.isoformat(), query, page),
                "project_id": project_id,
                "date": row_date,
                "query": query,
                "page": page,
                "clicks": int(row.get("clicks", 0) or 0),
                "impressions": int(row.get("impressions", 0) or 0),
                "ctr": float(row.get("ctr", 0.0) or 0.0),
                "position": row.get("position"),
                "raw_data": row.get("raw_data") or row,
            }
        )

    ga4_rows: list[dict[str, Any]] = []
    for row in ga4_result.get("records", []) or []:
        row_date = _parse_date(row.get("date"), period_end)
        page_path = row.get("page_path")
        ga4_rows.append(
            {
                "id": _stable_id("ga4", project_id, row_date.isoformat(), page_path),
                "project_id": project_id,
                "date": row_date,
                "page_path": page_path,
                "sessions": int(row.get("sessions", 0) or 0),
                "users": int(row.get("users", 0) or 0),
                "pageviews": int(row.get("pageviews", 0) or 0),
                "avg_session_duration": float(row.get("avg_session_duration", 0.0) or 0.0),
                "bounce_rate": float(row.get("bounce_rate", 0.0) or 0.0),
                "conversions": int(row.get("conversions", 0) or 0),
                "revenue": float(row.get("revenue", 0.0) or 0.0),
                "raw_data": row.get("raw_data") or row,
            }
        )

    yandex_rows: list[dict[str, Any]] = []
    for row in yandex_result.get("records", []) or []:
        row_date = _parse_date(row.get("date"), period_end)
        query = row.get("query")
        url = row.get("url")
        yandex_rows.append(
            {
                "id": _stable_id("yandex", project_id, row_date.isoformat(), query, url),
                "project_id": project_id,
                "date": row_date,
                "query": query,
                "url": url,
                "shows": int(row.get("shows", 0) or 0),
                "clicks": int(row.get("clicks", 0) or 0),
                "ctr": float(row.get("ctr", 0.0) or 0.0),
                "position": row.get("position"),
                "raw_data": row.get("raw_data") or row,
            }
        )

    await _upsert_rows(
        session,
        GSCDataRow,
        gsc_rows,
        ("query", "page", "clicks", "impressions", "ctr", "position", "raw_data", "updated_at"),
    )
    await _upsert_rows(
        session,
        GA4DataRow,
        ga4_rows,
        (
            "page_path",
            "sessions",
            "users",
            "pageviews",
            "avg_session_duration",
            "bounce_rate",
            "conversions",
            "revenue",
            "raw_data",
            "updated_at",
        ),
    )
    await _upsert_rows(
        session,
        YandexWebmasterDataRow,
        yandex_rows,
        ("query", "url", "shows", "clicks", "ctr", "position", "raw_data", "updated_at"),
    )


async def _load_ffscore_history(
    session: AsyncSession,
    project_id: str,
    period_start: date,
    period_end: date,
) -> dict[str, Any]:
    start_dt, end_dt = _dt_bounds(period_start, period_end)
    result = await session.execute(
        select(MetricsHistoryRow)
        .where(MetricsHistoryRow.project_id == project_id)
        .where(MetricsHistoryRow.created_at >= start_dt)
        .where(MetricsHistoryRow.created_at <= end_dt)
        .order_by(MetricsHistoryRow.created_at.asc())
    )
    rows = [row for row in result.scalars().all() if isinstance(row.metrics, dict) and row.metrics.get("ff_score") is not None]
    scores = [float(row.metrics.get("ff_score")) for row in rows]
    latest = rows[-1] if rows else None
    return {
        "count": len(scores),
        "first": scores[0] if scores else None,
        "latest": scores[-1] if scores else None,
        "delta": None if len(scores) < 2 else round(scores[-1] - scores[0], 4),
        "latest_components": None if latest is None else latest.metrics.get("components"),
        "latest_inputs": None if latest is None else latest.metrics.get("inputs"),
        "history": [
            {
                "metric_id": row.metric_id,
                "created_at": row.created_at.isoformat(),
                "ff_score": row.metrics.get("ff_score"),
                "components": row.metrics.get("components"),
            }
            for row in rows
        ],
    }


async def _load_changelog(
    session: AsyncSession,
    project_id: str,
    period_start: date,
    period_end: date,
) -> dict[str, Any]:
    start_dt, end_dt = _dt_bounds(period_start, period_end)
    result = await session.execute(
        select(ChangelogRow)
        .where(ChangelogRow.entity_id == project_id)
        .where(ChangelogRow.created_at >= start_dt)
        .where(ChangelogRow.created_at <= end_dt)
        .order_by(ChangelogRow.created_at.desc())
    )
    rows = result.scalars().all()
    applied = [row for row in rows if row.applied]
    impact_scores = [float(row.impact_score) for row in rows if row.impact_score is not None]
    return {
        "summary": {
            "changes_count": len(rows),
            "applied_count": len(applied),
            "avg_impact_score": None if not impact_scores else round(sum(impact_scores) / len(impact_scores), 4),
        },
        "events": [
            {
                "id": row.id,
                "entity_id": row.entity_id,
                "entity_type": row.entity_type,
                "change_type": row.change_type,
                "impact_score": row.impact_score,
                "applied": row.applied,
                "applied_at": None if row.applied_at is None else row.applied_at.isoformat(),
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
    }


def _source_status(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": result.get("source"),
        "available": result.get("available", False),
        "status": result.get("status", "degraded"),
        "reason": result.get("reason"),
        "rows_count": len(result.get("records", []) or []),
    }


def _snapshot(
    project_id: str,
    root_url: str,
    report_type: ReportType,
    period_start: date,
    period_end: date,
    aggregates: dict[str, Any],
    changelog: dict[str, Any],
) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "root_url": root_url,
        "report_type": report_type.value,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "traffic": {
            "gsc_clicks": aggregates["gsc"].get("clicks", 0),
            "gsc_impressions": aggregates["gsc"].get("impressions", 0),
            "ga4_sessions": aggregates["ga4"].get("sessions", 0),
            "ga4_users": aggregates["ga4"].get("users", 0),
            "yandex_shows": aggregates["yandex"].get("shows", 0),
        },
        "ff_score": aggregates["ff_score"],
        "changelog": changelog["summary"],
    }


async def build_report(session: AsyncSession, request: ReportGenerationRequest) -> ReportRow:
    period_start, period_end = _resolve_period(request)
    source_set = set(request.include_sources)

    gsc_result = {"source": "gsc", "available": False, "status": "skipped", "reason": "not_requested", "records": [], "aggregate": {}}
    ga4_result = {"source": "ga4", "available": False, "status": "skipped", "reason": "not_requested", "records": [], "aggregate": {}}
    yandex_result = {"source": "yandex", "available": False, "status": "skipped", "reason": "not_requested", "records": [], "aggregate": {}}

    tasks: dict[ReportSource, asyncio.Task] = {}
    if ReportSource.GSC in source_set:
        tasks[ReportSource.GSC] = asyncio.create_task(
            asyncio.to_thread(fetch_gsc_rows, request.project_id, request.root_url, period_start, period_end)
        )
    if ReportSource.GA4 in source_set:
        tasks[ReportSource.GA4] = asyncio.create_task(
            asyncio.to_thread(fetch_ga4_rows, request.project_id, period_start, period_end)
        )
    if ReportSource.YANDEX in source_set:
        tasks[ReportSource.YANDEX] = asyncio.create_task(
            asyncio.to_thread(fetch_yandex_rows, request.project_id, period_start, period_end)
        )

    for source, task in tasks.items():
        result = await task
        if source == ReportSource.GSC:
            gsc_result = result
        elif source == ReportSource.GA4:
            ga4_result = result
        elif source == ReportSource.YANDEX:
            yandex_result = result

    await _persist_raw_rows(session, request.project_id, period_end, gsc_result, ga4_result, yandex_result)

    ff_score = await _load_ffscore_history(session, request.project_id, period_start, period_end)
    changelog = await _load_changelog(session, request.project_id, period_start, period_end)
    aggregates = {
        "gsc": gsc_result.get("aggregate", {}),
        "ga4": ga4_result.get("aggregate", {}),
        "yandex": yandex_result.get("aggregate", {}),
        "ff_score": ff_score,
        "changelog": changelog["summary"],
    }
    report_id = f"{request.report_type.value}-{uuid.uuid4().hex[:12]}"
    data = {
        "report_type": request.report_type.value,
        "period": {"start": period_start.isoformat(), "end": period_end.isoformat()},
        "sources": [_source_status(gsc_result), _source_status(ga4_result), _source_status(yandex_result)],
        "raw": {
            "gsc": gsc_result.get("records", []),
            "ga4": ga4_result.get("records", []),
            "yandex": yandex_result.get("records", []),
        },
        "aggregates": aggregates,
        "changelog": changelog,
        "report_snapshot": _snapshot(
            request.project_id,
            request.root_url,
            request.report_type,
            period_start,
            period_end,
            aggregates,
            changelog,
        ),
        "metadata": request.metadata,
    }
    report = ReportRow(
        report_id=report_id,
        project_id=request.project_id,
        root_url=request.root_url,
        created_at=datetime.now(timezone.utc),
        data=data,
    )
    session.add(report)
    session.add(
        MetricsHistoryRow(
            metric_id=f"report-aggregates-{report_id}",
            project_id=request.project_id,
            root_url=request.root_url,
            created_at=datetime.now(timezone.utc),
            metrics={"report_id": report_id, "report_type": request.report_type.value, "aggregates": aggregates},
        )
    )
    await session.commit()
    await session.refresh(report)
    return report
