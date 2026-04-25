import asyncio
from datetime import datetime, timezone
from typing import Any
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy import select

from services.reporting_service.config import settings
from services.reporting_service.db.session import init_db, get_session
from services.reporting_service.db.models import ReportRow, MetricsHistoryRow
from services.reporting_service.exporters.csv_exporter import export_report_slice
from services.reporting_service.metrics.roi_calculator import calculate_roi_metrics
from services.reporting_service.metrics.calculator import calculate_trust_sentiment
from services.reporting_service.events.ff_score_recalculated_handler import maybe_start_ffscore_consumer
from services.reporting_service.connectors.gsc_connector import fetch_gsc_summary
from services.reporting_service.report_builders import build_report
from services.reporting_service.schemas.report import MetricsCalculationRequest, ReportGenerationRequest, ReportSlice

app = FastAPI(title="Reporting Service", version="0.1.0")


def _lookup_nested(payload: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        current: Any = payload
        found = True
        for key in path:
            if not isinstance(current, dict) or key not in current:
                found = False
                break
            current = current[key]
        if found:
            return current
    return None


def _coerce_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _derive_days_since_update(report: ReportRow | None, metrics: MetricsHistoryRow | None) -> tuple[int | None, str | None]:
    payloads: list[tuple[dict[str, Any], str]] = []
    if report and isinstance(report.data, dict):
        payloads.append((report.data, "report"))
    if metrics and isinstance(metrics.metrics, dict):
        payloads.append((metrics.metrics, "metrics"))

    for payload, prefix in payloads:
        explicit_days = _coerce_int(
            _lookup_nested(
                payload,
                ("freshness_days_since_update",),
                ("freshness", "days_since_update"),
                ("signals", "freshness_days_since_update"),
                ("inputs", "freshness_days_since_update"),
            )
        )
        if explicit_days is not None:
            return explicit_days, f"{prefix}_payload"

        for raw_key in (
            ("last_updated_at",),
            ("content_last_updated_at",),
            ("page_last_updated_at",),
            ("freshness", "last_updated_at"),
            ("signals", "last_updated_at"),
        ):
            dt_value = _coerce_datetime(_lookup_nested(payload, raw_key))
            if dt_value is not None:
                delta = datetime.now(timezone.utc) - dt_value.astimezone(timezone.utc)
                return max(0, delta.days), f"{prefix}_timestamp"

    if report and report.created_at:
        delta = datetime.now(timezone.utc) - report.created_at.astimezone(timezone.utc)
        return max(0, delta.days), "report_created_at_proxy"

    return None, None


def _derive_signal_from_payload(report: ReportRow | None, metrics: MetricsHistoryRow | None, *paths: tuple[str, ...]) -> tuple[float | None, str | None]:
    if report and isinstance(report.data, dict):
        value = _coerce_float(_lookup_nested(report.data, *paths))
        if value is not None:
            return value, "report_payload"
    if metrics and isinstance(metrics.metrics, dict):
        value = _coerce_float(_lookup_nested(metrics.metrics, *paths))
        if value is not None:
            return value, "metrics_payload"
    return None, None


def _derive_link_velocity(recent_gsc: dict | None, previous_gsc: dict | None) -> tuple[float | None, str | None]:
    if not recent_gsc or not previous_gsc:
        return None, None
    recent_click_rate = float(recent_gsc.get("clicks", 0.0) or 0.0) / max(1.0, float(recent_gsc.get("range_days", 14) or 14))
    previous_click_rate = float(previous_gsc.get("clicks", 0.0) or 0.0) / max(1.0, float(previous_gsc.get("range_days", 14) or 14))
    return round(max(0.0, recent_click_rate - previous_click_rate), 4), "gsc_click_momentum_proxy"


def _derive_serp_shift(recent_gsc: dict | None, previous_gsc: dict | None) -> tuple[float | None, str | None]:
    if not recent_gsc or not previous_gsc:
        return None, None
    recent_position = _coerce_float(recent_gsc.get("avg_position"))
    previous_position = _coerce_float(previous_gsc.get("avg_position"))
    if recent_position is None or previous_position is None:
        return None, None
    return round(recent_position - previous_position, 4), "gsc_avg_position_delta"


@app.on_event("startup")
async def _startup() -> None:
    await init_db()
    asyncio.create_task(maybe_start_ffscore_consumer())


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "reporting_service", "ts": datetime.now(timezone.utc).isoformat()}


@app.get("/reporting/projects/{project_id}/summary")
async def get_project_summary(project_id: str, root_url: str | None = None) -> dict:
    async with get_session() as session:
        report_res = await session.execute(
            select(ReportRow).where(ReportRow.project_id == project_id).order_by(ReportRow.created_at.desc()).limit(1)
        )
        metrics_res = await session.execute(
            select(MetricsHistoryRow).where(MetricsHistoryRow.project_id == project_id).order_by(MetricsHistoryRow.created_at.desc()).limit(1)
        )
        latest_report = report_res.scalar_one_or_none()
        latest_metrics = metrics_res.scalar_one_or_none()

    resolved_root_url = root_url or (latest_report.root_url if latest_report else None) or (latest_metrics.root_url if latest_metrics else None)

    freshness_days_since_update, freshness_source = _derive_days_since_update(latest_report, latest_metrics)
    serp_shift, serp_source = _derive_signal_from_payload(
        latest_report,
        latest_metrics,
        ("serp_shift",),
        ("signals", "serp_shift"),
        ("inputs", "serp_shift"),
    )
    link_velocity, link_source = _derive_signal_from_payload(
        latest_report,
        latest_metrics,
        ("link_velocity",),
        ("backlink_velocity",),
        ("signals", "link_velocity"),
        ("inputs", "link_velocity"),
    )
    backlinks_count, backlinks_source = _derive_signal_from_payload(
        latest_report,
        latest_metrics,
        ("backlinks_count",),
        ("signals", "backlinks_count"),
        ("inputs", "backlinks_count"),
    )
    brand_mentions, brand_mentions_source = _derive_signal_from_payload(
        latest_report,
        latest_metrics,
        ("brand_mentions",),
        ("signals", "brand_mentions"),
        ("inputs", "brand_mentions"),
    )

    gsc_recent = None
    gsc_previous = None
    if resolved_root_url:
        try:
            gsc_recent = await asyncio.to_thread(fetch_gsc_summary, project_id, resolved_root_url, 14, 0)
            gsc_previous = await asyncio.to_thread(fetch_gsc_summary, project_id, resolved_root_url, 14, 14)
        except Exception:
            gsc_recent = None
            gsc_previous = None

    if serp_shift is None:
        serp_shift, serp_source = _derive_serp_shift(gsc_recent, gsc_previous)
    if link_velocity is None:
        link_velocity, link_source = _derive_link_velocity(gsc_recent, gsc_previous)

    return {
        "project_id": project_id,
        "root_url": resolved_root_url,
        "signals": {
            "freshness_days_since_update": freshness_days_since_update,
            "serp_shift": serp_shift,
            "link_velocity": link_velocity,
            "backlinks_count": int(backlinks_count) if backlinks_count is not None else None,
            "brand_mentions": int(brand_mentions) if brand_mentions is not None else None,
        },
        "sources": {
            "freshness_days_since_update": freshness_source,
            "serp_shift": serp_source,
            "link_velocity": link_source,
            "backlinks_count": backlinks_source,
            "brand_mentions": brand_mentions_source,
        },
        "gsc": {
            "recent": gsc_recent,
            "previous": gsc_previous,
        },
        "report": None if latest_report is None else {
            "report_id": latest_report.report_id,
            "created_at": latest_report.created_at,
        },
        "metrics": None if latest_metrics is None else {
            "metric_id": latest_metrics.metric_id,
            "created_at": latest_metrics.created_at,
        },
    }


@app.post("/reporting/reports")
async def create_report(request: ReportGenerationRequest) -> dict:
    async with get_session() as session:
        report = await build_report(session, request)
    return {
        "report_id": report.report_id,
        "status": "created",
        "report_type": report.data.get("report_type") if isinstance(report.data, dict) else None,
        "period": report.data.get("period") if isinstance(report.data, dict) else None,
        "sources": report.data.get("sources") if isinstance(report.data, dict) else None,
    }


@app.get("/reporting/reports/{report_id}")
async def get_report(report_id: str) -> dict:
    async with get_session() as session:
        res = await session.execute(select(ReportRow).where(ReportRow.report_id == report_id))
        row = res.scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="report_not_found")
        return {
            "report_id": row.report_id,
            "project_id": row.project_id,
            "root_url": row.root_url,
            "report_type": row.data.get("report_type") if isinstance(row.data, dict) else None,
            "created_at": row.created_at,
            "data": row.data,
        }


@app.post("/reporting/metrics")
async def calculate_metrics(payload: MetricsCalculationRequest) -> dict:
    roi = calculate_roi_metrics(
        cost=payload.cost,
        revenue=payload.revenue,
        hitl_actions=payload.hitl_actions,
        automated_actions=payload.automated_actions,
    )
    ts = calculate_trust_sentiment(**payload.trust_inputs)

    metric_id = payload.metric_id or f"mh-{int(datetime.now(timezone.utc).timestamp())}"
    async with get_session() as session:
        session.add(
            MetricsHistoryRow(
                metric_id=str(metric_id),
                project_id=payload.project_id,
                root_url=payload.root_url,
                created_at=datetime.now(timezone.utc),
                metrics={"roi": roi, "trust": ts},
            )
        )
        await session.commit()
    return {"metric_id": str(metric_id), "roi": roi, "trust": ts}


@app.get("/reporting/export/csv/{report_id}")
async def export_csv(
    report_id: str,
    slice_name: ReportSlice = Query(default=ReportSlice.REPORT_SNAPSHOT, alias="slice"),
):
    async with get_session() as session:
        res = await session.execute(select(ReportRow).where(ReportRow.report_id == report_id))
        row = res.scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="report_not_found")
        csv_bytes = export_report_slice(row.data, slice_name.value)

    filename = f"{report_id}-{slice_name.value}.csv"
    return StreamingResponse(iter([csv_bytes]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.exception_handler(ValueError)
async def _value_error_handler(_, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("services.reporting_service.main:app", host="0.0.0.0", port=settings.port, reload=False)

