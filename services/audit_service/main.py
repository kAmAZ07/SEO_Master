import asyncio
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from services.audit_service.config import settings
from services.audit_service.db.session import get_session, init_db
from services.audit_service.db.models import PublicAuditResult
from services.audit_service.schemas.audit import PublicAuditRequest, PublicAuditResponse, AuditStatusResponse
from services.audit_service.crawler.technical_audit import run_public_audit_pipeline
from services.audit_service.events.public_audit_completed import publish_public_audit_completed

app = FastAPI(title="Audit Service", version="0.1.0")


@app.on_event("startup")
async def _startup() -> None:
    await init_db()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "audit_service", "ts": datetime.now(timezone.utc).isoformat()}


@app.post("/audit/public", response_model=PublicAuditResponse)
async def start_public_audit(payload: PublicAuditRequest) -> PublicAuditResponse:
    audit_id = str(uuid.uuid4())
    async with get_session() as session:
        row = PublicAuditResult(
            audit_id=audit_id,
            root_url=str(payload.root_url),
            status="queued",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            summary={},
            findings=[],
            pages=[],
            options=payload.options.model_dump(),
        )
        session.add(row)
        await session.commit()

    asyncio.create_task(_run_public_background(audit_id))
    return PublicAuditResponse(audit_id=audit_id, status="queued")


async def _run_public_background(audit_id: str) -> None:
    async with get_session() as session:
        res = await session.execute(select(PublicAuditResult).where(PublicAuditResult.audit_id == audit_id))
        row = res.scalar_one_or_none()
        if row is None:
            return
        row.status = "running"
        row.updated_at = datetime.now(timezone.utc)
        await session.commit()

    try:
        result = await run_public_audit_pipeline(audit_id=audit_id)
        async with get_session() as session:
            res = await session.execute(select(PublicAuditResult).where(PublicAuditResult.audit_id == audit_id))
            row = res.scalar_one_or_none()
            if row is None:
                return
            row.status = "completed"
            row.summary = result["summary"]
            row.findings = result["findings"]
            row.pages = result["pages"]
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()

        await publish_public_audit_completed(audit_id=audit_id, root_url=result["root_url"], summary=result["summary"])
    except Exception as e:
        async with get_session() as session:
            res = await session.execute(select(PublicAuditResult).where(PublicAuditResult.audit_id == audit_id))
            row = res.scalar_one_or_none()
            if row is None:
                return
            row.status = "failed"
            row.summary = {"error": str(e)}
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()


@app.get("/audit/{audit_id}", response_model=AuditStatusResponse)
async def get_audit_status(audit_id: str) -> AuditStatusResponse:
    async with get_session() as session:
        res = await session.execute(select(PublicAuditResult).where(PublicAuditResult.audit_id == audit_id))
        row = res.scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="audit_not_found")

        return AuditStatusResponse(
            audit_id=row.audit_id,
            root_url=row.root_url,
            status=row.status,
            summary=row.summary or {},
            findings=row.findings or [],
            pages=row.pages or [],
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


@app.exception_handler(ValueError)
async def value_error_handler(_, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("services.audit_service.main:app", host="0.0.0.0", port=settings.port, reload=False)