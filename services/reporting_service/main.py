import asyncio
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy import select

from services.reporting_service.config import settings
from services.reporting_service.db.session import init_db, get_session
from services.reporting_service.db.models import ReportRow, MetricsHistoryRow
from services.reporting_service.exporters.csv_exporter import export_raw_data
from services.reporting_service.metrics.roi_calculator import calculate_roi_metrics
from services.reporting_service.metrics.calculator import calculate_trust_sentiment
from services.reporting_service.events.ff_score_recalculated_handler import maybe_start_ffscore_consumer

app = FastAPI(title="Reporting Service", version="0.1.0")


@app.on_event("startup")
async def _startup() -> None:
    await init_db()
    asyncio.create_task(maybe_start_ffscore_consumer())


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "reporting_service", "ts": datetime.now(timezone.utc).isoformat()}


@app.post("/reporting/reports")
async def create_report(payload: dict) -> dict:
    project_id = payload.get("project_id")
    root_url = payload.get("root_url")
    if not root_url or not isinstance(root_url, str):
        raise HTTPException(status_code=400, detail="root_url_required")

    report_id = payload.get("report_id") or f"rep-{int(datetime.now(timezone.utc).timestamp())}"
    async with get_session() as session:
        session.add(
            ReportRow(
                report_id=str(report_id),
                project_id=project_id,
                root_url=root_url,
                created_at=datetime.now(timezone.utc),
                data=payload.get("data") or {},
            )
        )
        await session.commit()
    return {"report_id": str(report_id), "status": "created"}


@app.get("/reporting/reports/{report_id}")
async def get_report(report_id: str) -> dict:
    async with get_session() as session:
        res = await session.execute(select(ReportRow).where(ReportRow.report_id == report_id))
        row = res.scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="report_not_found")
        return {"report_id": row.report_id, "project_id": row.project_id, "root_url": row.root_url, "created_at": row.created_at, "data": row.data}


@app.post("/reporting/metrics")
async def calculate_metrics(payload: dict) -> dict:
    cost = float(payload.get("cost", 0.0) or 0.0)
    revenue = float(payload.get("revenue", 0.0) or 0.0)
    hitl_actions = int(payload.get("hitl_actions", 0) or 0)
    automated_actions = int(payload.get("automated_actions", 0) or 0)
    trust_inputs = payload.get("trust_inputs") or {}

    roi = calculate_roi_metrics(cost=cost, revenue=revenue, hitl_actions=hitl_actions, automated_actions=automated_actions)
    ts = calculate_trust_sentiment(**trust_inputs)

    metric_id = payload.get("metric_id") or f"mh-{int(datetime.now(timezone.utc).timestamp())}"
    async with get_session() as session:
        session.add(
            MetricsHistoryRow(
                metric_id=str(metric_id),
                project_id=payload.get("project_id"),
                root_url=payload.get("root_url") or "",
                created_at=datetime.now(timezone.utc),
                metrics={"roi": roi, "trust": ts},
            )
        )
        await session.commit()
    return {"metric_id": str(metric_id), "roi": roi, "trust": ts}


@app.get("/reporting/export/csv/{report_id}")
async def export_csv(report_id: str):
    async with get_session() as session:
        res = await session.execute(select(ReportRow).where(ReportRow.report_id == report_id))
        row = res.scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="report_not_found")
        csv_bytes = export_raw_data(row.data)

    filename = f"{report_id}.csv"
    return StreamingResponse(iter([csv_bytes]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.exception_handler(ValueError)
async def _value_error_handler(_, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("services.reporting_service.main:app", host="0.0.0.0", port=settings.port, reload=False)