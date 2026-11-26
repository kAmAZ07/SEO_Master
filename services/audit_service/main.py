import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from config.logging_config import get_logger

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
from services.audit_service.crawler.technical_audit import (
    run_full_audit_pipeline,
    run_public_audit_pipeline,
    select_top_findings,
)
from services.audit_service.events.crawl_completed import publish_crawl_completed
from services.audit_service.events.task_created_handler import maybe_start_task_created_consumer
from services.audit_service.public_policy import (
    FINAL_STATUSES,
    PUBLIC_JOB_DEADLINE_S,
    PUBLIC_MAX_CONCURRENT_JOBS,
    PUBLIC_RETENTION,
    public_options,
)
from services.audit_service.public_view import build_public_view, is_expired
from services.audit_service.page_evidence import attach_page_evidence
from shared.projects.authorization_state import is_fresh
from shared.projects.claims import hash_claim_token, new_claim_token
from shared.projects.scope import ScopeError, parse_scope

logger = get_logger(__name__)

app = FastAPI(title="Audit Service", version="0.1.0")
AuditModel = type[PublicAuditResult] | type[CrawlResult]
AuditRunner = Callable[..., Awaitable[dict]]

# Ссылки на фоновые задания: без них сборщик мусора может прервать задачу, а отмена не найдёт её.
_JOBS: dict[str, asyncio.Task] = {}
_SLOTS: dict[int, asyncio.Semaphore] = {}
_ORPHAN_AFTER = timedelta(seconds=PUBLIC_JOB_DEADLINE_S + 30)
_PROGRESS_MIN_INTERVAL_S = 0.5
_QUEUE_POLL_SECONDS = 1.0
_ORPHAN_SWEEP_SECONDS = 30.0
_FULL_CONCURRENT_JOBS = 1


def _public_slots() -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    if loop_id not in _SLOTS:
        _SLOTS[loop_id] = asyncio.Semaphore(PUBLIC_MAX_CONCURRENT_JOBS)
    return _SLOTS[loop_id]


def active_job_count() -> int:
    """Число заданий, выполняющихся или ожидающих слот в этом процессе (для проверки утечек)."""
    return len([task for task in _JOBS.values() if not task.done()])


@app.on_event("startup")
async def _startup() -> None:
    await init_db()
    await purge_expired_public_audits()
    await recover_orphaned_public_audits()
    stop_dispatch = asyncio.Event()
    app.state.stop_audit_dispatch = stop_dispatch
    app.state.audit_dispatcher = asyncio.create_task(_dispatch_saved_audits(stop_dispatch))
    app.state.task_created_consumer = asyncio.create_task(maybe_start_task_created_consumer())


@app.on_event("shutdown")
async def _shutdown() -> None:
    app.state.stop_audit_dispatch.set()
    app.state.task_created_consumer.cancel()
    await asyncio.gather(app.state.audit_dispatcher, app.state.task_created_consumer, return_exceptions=True)
    tasks = list(_JOBS.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _SLOTS.pop(id(asyncio.get_running_loop()), None)


async def purge_expired_public_audits() -> int:
    """Удаляет публичные результаты старше срока хранения. Возвращает число удалённых строк."""
    cutoff = datetime.now(timezone.utc) - PUBLIC_RETENTION
    async with get_session() as session:
        result = await session.execute(delete(PublicAuditResult).where(PublicAuditResult.created_at < cutoff))
        await session.commit()
    return result.rowcount or 0


async def recover_orphaned_public_audits() -> int:
    """Задания, у которых давно нет обновлений (процесс перезапущен), получают конечное состояние."""
    cutoff = datetime.now(timezone.utc) - _ORPHAN_AFTER
    async with get_session() as session:
        result = await session.execute(
            update(PublicAuditResult)
            .where(PublicAuditResult.status == "running", PublicAuditResult.updated_at < cutoff)
            .values(status="failed", summary={"error_code": "interrupted"}, updated_at=datetime.now(timezone.utc))
        )
        await session.commit()
    return result.rowcount or 0


def _schedule_audit(audit_id: str, row_cls: AuditModel, runner: AuditRunner) -> None:
    current = _JOBS.get(audit_id)
    if current is not None and not current.done():
        return
    task = asyncio.create_task(
        _run_audit_background(audit_id=audit_id, row_cls=row_cls, runner=runner),
        name=row_cls.__tablename__,
    )
    _JOBS[audit_id] = task

    def release(finished: asyncio.Task) -> None:
        if _JOBS.get(audit_id) is finished:
            _JOBS.pop(audit_id)
        if not finished.cancelled() and finished.exception() is not None:
            logger.error("Audit worker stopped unexpectedly", extra={"audit_id": audit_id})

    task.add_done_callback(release)


async def _dispatch_saved_audits(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    next_sweep = loop.time() + _ORPHAN_SWEEP_SECONDS
    while not stop.is_set():
        try:
            if loop.time() >= next_sweep:
                await recover_orphaned_public_audits()
                next_sweep = loop.time() + _ORPHAN_SWEEP_SECONDS
            await _dispatch_queued_audits(PublicAuditResult, run_public_audit_pipeline, PUBLIC_MAX_CONCURRENT_JOBS, stop)
            await _dispatch_queued_audits(CrawlResult, run_full_audit_pipeline, _FULL_CONCURRENT_JOBS, stop)
        except Exception as exc:
            logger.warning("Audit dispatch unavailable", extra={"error_type": type(exc).__name__})
        try:
            await asyncio.wait_for(stop.wait(), timeout=_QUEUE_POLL_SECONDS)
        except asyncio.TimeoutError:
            continue


async def _dispatch_queued_audits(row_cls: AuditModel, runner: AuditRunner, slots: int, stop: asyncio.Event) -> None:
    active = {audit_id for audit_id, task in _JOBS.items()
              if task.get_name() == row_cls.__tablename__ and not task.done()}
    if stop.is_set() or len(active) >= slots:
        return
    mode = "public" if row_cls is PublicAuditResult else "full"
    query = select(row_cls.audit_id).where(row_cls.mode == mode, row_cls.status == "queued", row_cls.audit_id.not_in(active))
    if row_cls is PublicAuditResult:
        query = query.where(row_cls.created_at >= datetime.now(timezone.utc) - PUBLIC_RETENTION)
    query = query.order_by(row_cls.created_at, row_cls.audit_id).limit(slots - len(active))
    async with get_session() as session:
        audit_ids = (await session.execute(query)).scalars().all()
    if stop.is_set():
        return
    for audit_id in audit_ids:
        _schedule_audit(audit_id, row_cls, runner)


async def _claim_queued_audit(audit_id: str, row_cls: AuditModel) -> bool:
    now = datetime.now(timezone.utc)
    claim = update(row_cls).where(row_cls.audit_id == audit_id, row_cls.status == "queued")
    if row_cls is PublicAuditResult:
        claim = claim.where(row_cls.created_at >= now - PUBLIC_RETENTION)
    async with get_session() as session:
        if row_cls is CrawlResult:
            await _lock_full_audit_authority(session)
        row = (await session.execute(
            claim.values(status="running", updated_at=now).returning(row_cls.project_id, row_cls.root_url)
        )).one_or_none()
        if row is None:
            return False
        denial = await _full_audit_denial(session, row.project_id, row.root_url) if row_cls is CrawlResult else None
        if denial is not None:
            await session.execute(
                update(CrawlResult)
                .where(CrawlResult.audit_id == audit_id, CrawlResult.project_id == row.project_id)
                .values(status="failed", summary={"error_code": denial})
            )
        await session.commit()
    return denial is None


async def _lock_full_audit_authority(session: AsyncSession) -> None:
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
    await session.execute(text("SET LOCAL lock_timeout='5s'"))
    # Use the existing producer trigger's key before the crawl_results UPDATE takes a shared lock.
    await session.execute(text("SELECT pg_advisory_xact_lock(74219031109004)"))


async def _full_audit_denial(session: AsyncSession, project_id: str, root_url: str) -> str | None:
    binding = (await session.execute(text("""
        SELECT p.origin, p.path_prefix, a.status, a.verified_at
        FROM audit_schema.projects p
        JOIN audit_schema.domain_authorizations a
          ON a.project_id = p.id AND a.workspace_id = p.workspace_id
         AND a.scope_origin = p.origin AND a.scope_path_prefix = p.path_prefix
        WHERE p.id = :project_id AND p.status = 'active'
          AND a.status = 'verified' AND a.revoked_at IS NULL
    """), {"project_id": project_id})).one_or_none()
    if binding is None or not is_fresh(
        binding.status, binding.verified_at, settings.domain_verification_fresh_days, datetime.now(timezone.utc),
    ):
        return "project_authorization_required"
    try:
        scope = parse_scope(binding.origin + binding.path_prefix)
    except ScopeError:
        return "project_scope_changed"
    return None if scope.contains(root_url) else "project_scope_changed"


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
    row_cls: AuditModel,
    mode: str,
    audit_id: str | None = None,
    extra_options: dict | None = None,
    claim_token_hash: str | None = None,
) -> AuditQueuedResponse:
    audit_id = audit_id or str(uuid.uuid4())
    row_options = payload.options.model_dump()
    if mode == "public":
        # Лимиты публичного режима задаются здесь, а не клиентом: прямой вызов сервиса их не обходит.
        row_options = public_options(row_options)
    if extra_options:
        row_options.update(extra_options)
    now = datetime.now(timezone.utc)
    row_kwargs = {}
    if row_cls is CrawlResult:
        row_kwargs["crawled_at"] = now
    if claim_token_hash is not None and row_cls is PublicAuditResult:
        row_kwargs["claim_token_hash"] = claim_token_hash
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
            created_at=now,
            updated_at=now,
            summary={},
            findings=[],
            pages=[],
            options=row_options,
            **row_kwargs,
        )
        session.add(row)
        await session.commit()

    return AuditQueuedResponse(audit_id=audit_id, status="queued", mode=mode, project_id=payload.project_id)


@app.post("/audit/public", response_model=PublicAuditResponse)
async def start_public_audit(payload: PublicAuditRequest) -> PublicAuditResponse:
    # Токен показывается только в этом ответе; хранится его хэш (см. shared/projects/claims.py).
    claim_token = new_claim_token()
    queued = await _enqueue_audit(
        payload=payload,
        row_cls=PublicAuditResult,
        mode="public",
        claim_token_hash=hash_claim_token(claim_token),
    )
    return PublicAuditResponse(**queued.model_dump(), claim_token=claim_token)


@app.post("/audit/full", response_model=FullAuditResponse)
async def start_full_audit(payload: FullAuditRequest) -> FullAuditResponse:
    return FullAuditResponse(
        **(
            await _enqueue_audit(
                payload=payload,
                row_cls=CrawlResult,
                mode="full",
            )
        ).model_dump()
    )


async def _update_row(audit_id: str, row_cls, **values) -> bool:
    async with get_session() as session:
        res = await session.execute(select(row_cls).where(row_cls.audit_id == audit_id).with_for_update())
        row = res.scalar_one_or_none()
        if row is None or row.status in FINAL_STATUSES:
            return False
        if row_cls is CrawlResult and "pages" in values:
            values["pages"] = attach_page_evidence(
                values["pages"], audit_id=audit_id, project_id=row.project_id,
                recorded_at=datetime.now(timezone.utc).isoformat(),
            )
        for name, value in values.items():
            setattr(row, name, value)
        row.updated_at = datetime.now(timezone.utc)
        await session.commit()
    return True


def _progress_writer(audit_id: str, row_cls):
    """Запись этапа и счётчиков в строку задания. Частые обновления одного этапа прореживаются."""
    state = {"stage": None, "at": 0.0}

    async def _write(stage: str, **counters) -> None:
        now = time.monotonic()
        if stage == state["stage"] and now - state["at"] < _PROGRESS_MIN_INTERVAL_S:
            return
        state.update(stage=stage, at=now)
        async with get_session() as session:
            res = await session.execute(select(row_cls).where(row_cls.audit_id == audit_id).with_for_update())
            row = res.scalar_one_or_none()
            if row is None or row.status != "running":
                raise asyncio.CancelledError
            row.summary = {**(row.summary or {}), "progress": {"stage": stage, **counters}}
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()

    return _write


async def _finalize(audit_id: str, row_cls, status: str, summary: dict, **extra) -> bool:
    return await _update_row(audit_id, row_cls, status=status, summary=summary, **extra)


async def _run_audit_background(
    *,
    audit_id: str,
    row_cls: AuditModel,
    runner: AuditRunner,
) -> None:
    public = row_cls is PublicAuditResult
    claimed = False
    result: dict | None = None
    try:
        slot = _public_slots() if public else None
        if slot is not None:
            await slot.acquire()
        try:
            claimed = await _claim_queued_audit(audit_id, row_cls)
            if not claimed:
                return
            writer = _progress_writer(audit_id, row_cls)
            await writer("starting")
            job = runner(audit_id=audit_id, on_progress=writer)
            # Жёсткая граница времени публичного задания; бюджет внутри пайплайна заканчивается раньше.
            result = await (asyncio.wait_for(job, PUBLIC_JOB_DEADLINE_S) if public else job)
        finally:
            if slot is not None:
                slot.release()
    except asyncio.CancelledError:
        if claimed:
            await _finalize(audit_id, row_cls, "failed", {"error_code": "interrupted"})
        return
    except asyncio.TimeoutError:
        if not claimed:
            raise
        await _finalize(audit_id, row_cls, "failed", {"error_code": "deadline_exceeded"})
        return
    except Exception as e:
        if not claimed:
            raise
        logger.exception("Audit failed", extra={"audit_id": audit_id})
        # Текст исключения может содержать внутренние адреса и пути: наружу идёт только код.
        summary = {"error_code": "internal_error"} if public else {"error": str(e)}
        await _finalize(audit_id, row_cls, "failed", summary)
        return

    outcome = result.get("outcome", "completed")
    if outcome not in FINAL_STATUSES:
        outcome = "completed"
    extra = {"crawled_at": datetime.now(timezone.utc)} if row_cls is CrawlResult else {}
    summary = {**result["summary"], "progress": {"stage": "done"}}
    saved = await _finalize(
        audit_id, row_cls, outcome, summary,
        findings=result["findings"], pages=result["pages"], **extra,
    )

    if saved and outcome in ("completed", "partial"):
        try:
            await publish_crawl_completed(
                audit_id=audit_id,
                project_id=result.get("project_id"),
                root_url=result["root_url"],
                mode=result.get("mode", "public"),
                summary=result["summary"],
                pages=_compact_pages(result["pages"]),
                content_text=_build_content_snapshot(result),
            )
        except Exception:
            logger.exception("Publishing crawl_completed failed", extra={"audit_id": audit_id})


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
        audit_id=audit_id,
        extra_options={
            "trigger": "task_created",
            "source_task_id": task_id,
            "source_task_type": task_type,
            "correlation_id": correlation_id,
        },
    )


async def _get_public_row(audit_id: str) -> PublicAuditResult:
    async with get_session() as session:
        res = await session.execute(select(PublicAuditResult).where(PublicAuditResult.audit_id == audit_id))
        row = res.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="audit_not_found")
    if is_expired(row.created_at):
        raise HTTPException(status_code=410, detail="audit_expired")
    return row


@app.get("/public/audit/{audit_id}")
async def get_public_audit(audit_id: str) -> dict:
    """Безопасный статус и отчёт публичного аудита. Полные аудиты через этот путь недоступны."""
    return build_public_view(await _get_public_row(audit_id))


@app.post("/public/audit/{audit_id}/cancel")
async def cancel_public_audit(audit_id: str) -> dict:
    row = await _get_public_row(audit_id)
    if row.status in FINAL_STATUSES:
        return build_public_view(row)
    cancelled = await _finalize(audit_id, PublicAuditResult, "cancelled", {"error_code": "cancelled"})
    task = _JOBS.get(audit_id)
    if cancelled and task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return build_public_view(await _get_public_row(audit_id))


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
        top_findings=select_top_findings(row.findings or []),
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
