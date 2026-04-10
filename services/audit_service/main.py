import asyncio
import uuid
from datetime import datetime, timezone
from typing import Type

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select

from services.audit_service.config import settings
from services.audit_service.db.session import get_session, init_db
from services.audit_service.db.models import CrawlResult, PublicAuditResult
from services.audit_service.schemas.audit import (
    AuditQueuedResponse,
    AuditStatusResponse,
    FullAuditRequest,
    FullAuditResponse,
    PublicAuditRequest,
    PublicAuditResponse,
)
from services.audit_service.crawler.technical_audit import run_full_audit_pipeline, run_public_audit_pipeline
from services.audit_service.events.crawl_completed import publish_crawl_completed
from services.audit_service.events.task_created_handler import maybe_start_task_created_consumer

app = FastAPI(title="Audit Service", version="0.1.0")


@app.on_event("startup")
async def _startup() -> None:
    await init_db()
    asyncio.create_task(maybe_start_task_created_consumer())


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "audit_service", "ts": datetime.now(timezone.utc).isoformat()}


def _serialize_seeds(seeds: list[str]) -> list[str]:
    return [str(seed) for seed in seeds]


def _compact_pages(pages: list) -> list[dict]:
    compacted = []
    for page in pages[:10]:
        if not isinstance(page, dict):
            continue
        compacted.append(
            {
                "url": page.get("url"),
                "status_code": page.get("status_code"),
                "title": page.get("title"),
                "description": page.get("description"),
                "h1": page.get("h1"),
                "error": page.get("error"),
            }
        )
    return compacted


def _build_content_snapshot(result: dict) -> str:
    parts: list[str] = []
    for page in result.get("pages", [])[:10]:
        if not isinstance(page, dict):
            continue
        parts.extend(
            [
                str(page.get("title") or ""),
                str(page.get("description") or ""),
                str(page.get("h1") or ""),
            ]
        )

    for finding in result.get("findings", [])[:20]:
        if not isinstance(finding, dict):
            continue
        parts.append(str(finding.get("title") or finding.get("description") or finding.get("code") or ""))

    return "\n".join(part for part in parts if part).strip()


async def _enqueue_audit(
    *,
    payload: PublicAuditRequest | FullAuditRequest,
    row_cls: Type[PublicAuditResult] | Type[CrawlResult],
    mode: str,
    runner,
    audit_id: str | None = None,
    extra_options: dict | None = None,
) -> AuditQueuedResponse:
    audit_id = audit_id or str(uuid.uuid4())
    row_options = payload.options.model_dump()
    if extra_options:
        row_options.update(extra_options)
    async with get_session() as session:
        row = row_cls(
            audit_id=audit_id,
            project_id=payload.project_id,
            root_url=str(payload.root_url),
            mode=mode,
            site_type_hint=payload.site_type_hint or "unknown",
            platform=payload.platform or "generic",
            seeds=_serialize_seeds(payload.seeds),
            status="queued",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            summary={},
            findings=[],
            pages=[],
            options=row_options,
        )
        session.add(row)
        await session.commit()

    asyncio.create_task(_run_audit_background(audit_id=audit_id, row_cls=row_cls, runner=runner))
    return AuditQueuedResponse(audit_id=audit_id, status="queued", mode=mode, project_id=payload.project_id)


@app.post("/audit/public", response_model=PublicAuditResponse)
async def start_public_audit(payload: PublicAuditRequest) -> PublicAuditResponse:
    return PublicAuditResponse(
        **(
            await _enqueue_audit(
                payload=payload,
                row_cls=PublicAuditResult,
                mode="public",
                runner=run_public_audit_pipeline,
            )
        ).model_dump()
    )


@app.post("/audit/full", response_model=FullAuditResponse)
async def start_full_audit(payload: FullAuditRequest) -> FullAuditResponse:
    return FullAuditResponse(
        **(
            await _enqueue_audit(
                payload=payload,
                row_cls=CrawlResult,
                mode="full",
                runner=run_full_audit_pipeline,
            )
        ).model_dump()
    )


async def _run_audit_background(
    *,
    audit_id: str,
    row_cls: Type[PublicAuditResult] | Type[CrawlResult],
    runner,
) -> None:
    async with get_session() as session:
        res = await session.execute(select(row_cls).where(row_cls.audit_id == audit_id))
        row = res.scalar_one_or_none()
        if row is None:
            return
        row.status = "running"
        row.updated_at = datetime.now(timezone.utc)
        await session.commit()

    try:
        result = await runner(audit_id=audit_id)
        async with get_session() as session:
            res = await session.execute(select(row_cls).where(row_cls.audit_id == audit_id))
            row = res.scalar_one_or_none()
            if row is None:
                return
            row.status = "completed"
            row.summary = result["summary"]
            row.findings = result["findings"]
            row.pages = result["pages"]
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()

        await publish_crawl_completed(
            audit_id=audit_id,
            project_id=result.get("project_id"),
            root_url=result["root_url"],
            mode=result.get("mode", "public"),
            summary=result["summary"],
            pages=_compact_pages(result["pages"]),
            content_text=_build_content_snapshot(result),
        )
    except Exception as e:
        async with get_session() as session:
            res = await session.execute(select(row_cls).where(row_cls.audit_id == audit_id))
            row = res.scalar_one_or_none()
            if row is None:
                return
            row.status = "failed"
            row.summary = {"error": str(e)}
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()


async def _get_audit_row(audit_id: str) -> PublicAuditResult | CrawlResult | None:
    async with get_session() as session:
        public_res = await session.execute(select(PublicAuditResult).where(PublicAuditResult.audit_id == audit_id))
        row = public_res.scalar_one_or_none()
        if row is not None:
            return row

        full_res = await session.execute(select(CrawlResult).where(CrawlResult.audit_id == audit_id))
        return full_res.scalar_one_or_none()


async def queue_full_audit_for_task_created(
    *,
    task_id: str,
    project_id: str,
    root_url: str,
    task_type: str,
    metadata: dict | None = None,
    correlation_id: str | None = None,
) -> AuditQueuedResponse:
    audit_id = f"task-{task_id}"
    existing = await _get_audit_row(audit_id)
    if existing is not None:
        return AuditQueuedResponse(
            audit_id=existing.audit_id,
            status=existing.status,
            mode=existing.mode,
            project_id=existing.project_id,
        )

    payload = FullAuditRequest(
        project_id=project_id,
        root_url=root_url,
        site_type_hint="unknown",
        platform=str((metadata or {}).get("platform") or "generic"),
        seeds=[str(seed) for seed in ((metadata or {}).get("seed_urls") or []) if seed],
    )
    return await _enqueue_audit(
        payload=payload,
        row_cls=CrawlResult,
        mode="full",
        runner=run_full_audit_pipeline,
        audit_id=audit_id,
        extra_options={
            "trigger": "task_created",
            "source_task_id": task_id,
            "source_task_type": task_type,
            "correlation_id": correlation_id,
        },
    )


@app.get("/audit/{audit_id}", response_model=AuditStatusResponse)
async def get_audit_status(audit_id: str) -> AuditStatusResponse:
    row = await _get_audit_row(audit_id)
    if row is None:
        raise HTTPException(status_code=404, detail="audit_not_found")

    return AuditStatusResponse(
        audit_id=row.audit_id,
        project_id=row.project_id,
        root_url=row.root_url,
        mode=row.mode,
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
