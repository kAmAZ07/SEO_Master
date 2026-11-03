import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select

from services.semantic_service.config import settings
from services.semantic_service.db.session import init_db, get_session
from services.semantic_service.db.models import FFScoreRow, EEATScoreRow, ContentDraftRow, SemanticAnalysisRow
from services.semantic_service.schemas.ff_score import FFScoreRequest, FFScoreResponse
from services.semantic_service.schemas.analysis import SemanticAnalysisRequest, SemanticAnalysisResponse
from services.semantic_service.schemas.eeat import EEATRequest, EEATResponse
from services.semantic_service.scoring.assemble import StoredAnalysis, build_ff_readings, eeat_signals_from_request
from services.semantic_service.scoring.ff_score_calculator import calculate_ff_score
from services.semantic_service.scoring.eeat_analyzer import analyze_eeat
from services.semantic_service.scoring.persistence import eeat_row_fields, eeat_view, ff_row_fields, ff_view
from services.semantic_service.scoring.readings import ReadingError
from services.semantic_service.llm.llm_client import LlmRuntime, open_runtime
from services.semantic_service.events.ff_score_recalculated import publish_ffscore_recalculated
from services.semantic_service.events.crawl_completed_handler import maybe_start_crawl_completed_consumer
from services.semantic_service.events.task_created_handler import maybe_start_task_created_consumer
from services.semantic_service.analysis.pipeline import create_semantic_analysis
from services.semantic_service.proposals.facts import ProposalRequest
from services.semantic_service.proposals.generator import generate_proposals
from services.semantic_service.proposals.model import recommendation_version_json_schema
from services.semantic_service.proposals.repository import CorruptedVersion, SqlProposalStore
from services.semantic_service.proposals.store import ProposalStore, VersionConflict
from services.semantic_service.proposals.validators import ProposalRejected

logger = logging.getLogger(__name__)

app = FastAPI(title="Semantic Service", version="0.2.0")

_runtime: LlmRuntime | None = None


@app.on_event("startup")
async def _startup() -> None:
    await init_db()
    asyncio.create_task(maybe_start_crawl_completed_consumer())
    asyncio.create_task(maybe_start_task_created_consumer())


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _runtime is not None:
        await _runtime.aclose()


def get_runtime() -> LlmRuntime:
    """Один LLM-слой на процесс: провайдеры из настроек и общий кэш. Подменяется в тестах."""
    global _runtime
    if _runtime is None:
        _runtime = open_runtime()
    return _runtime


def get_store() -> ProposalStore:
    return SqlProposalStore(get_session)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "semantic_service", "ts": datetime.now(timezone.utc).isoformat()}


async def _latest_analysis(project_id: str | None) -> StoredAnalysis | None:
    if not project_id:
        return None
    async with get_session() as session:
        result = await session.execute(
            select(SemanticAnalysisRow)
            .where(SemanticAnalysisRow.project_id == project_id)
            .order_by(SemanticAnalysisRow.created_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
    if row is None:
        return None
    return StoredAnalysis(
        semantic_distance=row.semantic_distance if isinstance(row.semantic_distance, dict) else None,
        keyword_coverage=row.keyword_coverage if isinstance(row.keyword_coverage, dict) else None,
        observed_at=row.created_at.isoformat() if row.created_at else None,
    )


def _eeat_kwargs(payload) -> dict:
    return dict(
        backlinks_count=payload.backlinks_count,
        has_https=payload.has_https,
        has_privacy_policy=payload.has_privacy_policy,
        has_contacts=payload.has_contacts,
        has_author_schema=payload.has_author_schema,
        authoritative_outbound_links=payload.authoritative_outbound_links,
        brand_mentions=payload.brand_mentions,
    )


@app.post("/semantic/eeat", response_model=EEATResponse)
async def eeat(payload: EEATRequest) -> EEATResponse:
    res = analyze_eeat(text=payload.text, root_url=str(payload.root_url), **_eeat_kwargs(payload))
    score_id = str(uuid.uuid4())
    async with get_session() as session:
        session.add(
            EEATScoreRow(
                score_id=score_id,
                project_id=payload.project_id,
                root_url=str(payload.root_url),
                created_at=datetime.now(timezone.utc),
                **eeat_row_fields(res),
            )
        )
        await session.commit()
    return EEATResponse(
        score_id=score_id,
        formula_version=res["formula_version"],
        status=res["status"],
        score=res["score"],
        breakdown=res["breakdown"],
        partial=res["partial"],
        missing_inputs=res["missing_inputs"],
        heuristic=res["heuristic"],
        signals=res["signals"],
    )


@app.post("/semantic/analyze", response_model=SemanticAnalysisResponse)
async def analyze_semantic(payload: SemanticAnalysisRequest) -> SemanticAnalysisResponse:
    result = await create_semantic_analysis(
        project_id=payload.project_id,
        root_url=str(payload.root_url),
        audit_id=payload.audit_id,
        analysis_id=payload.analysis_id,
        mode=payload.mode,
        content_text=payload.content_text,
        pages=payload.pages,
        keywords=payload.keywords,
        serp_top10_texts=payload.serp_top10_texts,
    )
    return SemanticAnalysisResponse(**result)


@app.post("/semantic/ff-score", response_model=FFScoreResponse)
async def ff_score(payload: FFScoreRequest) -> FFScoreResponse:
    """Считает FF-Score `ff_score_v1`. Полный балл равен `null` со статусом `insufficient_data`, если
    хотя бы одному компоненту не хватает измеренных данных; частичный балл возвращается отдельно."""
    root_url = str(payload.root_url)
    values = payload.model_dump()
    sources = payload.input_sources if isinstance(payload.input_sources, dict) else {}
    eeat_res = analyze_eeat(
        text=payload.content_text,
        root_url=root_url,
        **eeat_signals_from_request(values, sources),
    )
    analysis = await _latest_analysis(payload.project_id)
    try:
        readings = build_ff_readings(
            values=values,
            sources=sources,
            availability=payload.input_availability,
            audit_summary=payload.audit_summary,
            audit_findings=payload.audit_findings,
            analysis=analysis,
            eeat_result=eeat_res,
        )
    except ReadingError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc
    ff = calculate_ff_score(readings)

    ff_id = str(uuid.uuid4())
    eeat_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc)
    async with get_session() as session:
        session.add(
            EEATScoreRow(score_id=eeat_id, project_id=payload.project_id, root_url=root_url, created_at=created_at, **eeat_row_fields(eeat_res))
        )
        session.add(
            FFScoreRow(
                score_id=ff_id,
                project_id=payload.project_id,
                root_url=root_url,
                created_at=created_at,
                eeat_score_id=eeat_id,
                **ff_row_fields(ff),
            )
        )
        await session.commit()

    eeat_details = {
        "score_id": eeat_id,
        "formula_version": eeat_res["formula_version"],
        "status": eeat_res["status"],
        "score": eeat_res["score"],
        "breakdown": eeat_res["breakdown"],
        "partial": eeat_res["partial"],
        "missing_inputs": eeat_res["missing_inputs"],
        "heuristic": eeat_res["heuristic"],
    }
    await publish_ffscore_recalculated(
        project_id=payload.project_id,
        root_url=root_url,
        ff_score_id=ff_id,
        ff_score=ff["ff_score"],
        components=ff["components"],
        status=ff["status"],
        formula_version=ff["formula_version"],
        inputs=ff["inputs"],
        eeat_details=eeat_details,
    )

    return FFScoreResponse(
        ff_score_id=ff_id,
        eeat_score_id=eeat_id,
        formula_version=ff["formula_version"],
        status=ff["status"],
        ff_score=ff["ff_score"],
        components=ff["components"],
        weights=ff["weights"],
        thresholds=ff["thresholds"],
        mode=ff["mode"],
        coverage=ff["coverage"],
        partial=ff["partial"],
        heuristic=ff["heuristic"],
        note=ff["note"],
        inputs=ff["inputs"],
        eeat=eeat_details,
    )


# --- типизированные предложения (RecommendationVersion) ------------------------------------------------------------

@app.get("/semantic/schemas/recommendation-version")
async def recommendation_version_schema() -> dict:
    """Единственная опубликованная схема предложения. Источник — модель в коде semantic_service."""
    return recommendation_version_json_schema()


@app.post("/semantic/proposals")
async def create_proposals(
    payload: ProposalRequest,
    runtime: LlmRuntime = Depends(get_runtime),
    store: ProposalStore = Depends(get_store),
) -> dict:
    """Создаёт предложения для запрошенных типов. По каждому типу статус: `created`, `existing`
    (то же содержимое уже последняя версия), `no_change` или `needs_input` (не хватает фактов)."""
    try:
        result = await generate_proposals(
            payload,
            runtime=runtime,
            store=store,
            now=lambda: datetime.now(timezone.utc),
            new_id=lambda: str(uuid.uuid4()),
        )
    except ProposalRejected as exc:
        raise HTTPException(status_code=422, detail={"code": "proposal_rejected", "issues": [i.as_dict() for i in exc.issues]}) from exc
    except VersionConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "version_conflict", "message": "concurrent regeneration, retry"}) from exc
    return {"project_id": payload.project_id, "target_url": payload.target_url, "items": [item.as_dict() for item in result.items]}


@app.get("/semantic/proposals/{version_id}")
async def get_proposal(version_id: str, store: ProposalStore = Depends(get_store)) -> dict:
    try:
        version = await store.get(version_id)
    except CorruptedVersion as exc:
        raise HTTPException(status_code=500, detail={"code": "stored_version_corrupted", "version_id": version_id}) from exc
    if version is None:
        raise HTTPException(status_code=404, detail={"code": "version_not_found"})
    return version.model_dump(mode="json")


@app.get("/semantic/recommendations/{recommendation_id}/versions")
async def recommendation_history(recommendation_id: str, store: ProposalStore = Depends(get_store)) -> dict:
    try:
        versions = await store.history(recommendation_id)
    except CorruptedVersion as exc:
        raise HTTPException(status_code=500, detail={"code": "stored_version_corrupted", "version_id": str(exc)}) from exc
    if not versions:
        raise HTTPException(status_code=404, detail={"code": "recommendation_not_found"})
    return {"recommendation_id": recommendation_id, "versions": [v.model_dump(mode="json") for v in versions]}


@app.get("/semantic/projects/{project_id}/proposals")
async def project_proposals(project_id: str, limit: int = 100, store: ProposalStore = Depends(get_store)) -> dict:
    try:
        versions = await store.list_for_project(project_id, limit)
    except CorruptedVersion as exc:
        raise HTTPException(status_code=500, detail={"code": "stored_version_corrupted", "version_id": str(exc)}) from exc
    return {"project_id": project_id, "versions": [v.model_dump(mode="json") for v in versions]}


@app.post("/semantic/drafts")
async def drafts(_: dict | None = None) -> JSONResponse:
    """Снят с эксплуатации. Прежний обработчик подставлял шаблонные тексты («Официальная страница»,
    «Заголовок страницы»), когда LLM был недоступен; такие «черновики» нельзя публиковать (ТЗ §9.3).
    Замена — `POST /semantic/proposals`: он требует факты страницы и evidence и возвращает `needs_input`
    вместо выдуманного текста."""
    return JSONResponse(
        status_code=410,
        content={
            "detail": {
                "code": "drafts_endpoint_removed",
                "replacement": "POST /semantic/proposals",
                "schema": "GET /semantic/schemas/recommendation-version",
            }
        },
    )


@app.get("/semantic/latest/{project_id}")
async def latest(project_id: str) -> dict:
    async with get_session() as session:
        ff_res = await session.execute(
            select(FFScoreRow).where(FFScoreRow.project_id == project_id).order_by(FFScoreRow.created_at.desc()).limit(1)
        )
        eeat_res = await session.execute(
            select(EEATScoreRow).where(EEATScoreRow.project_id == project_id).order_by(EEATScoreRow.created_at.desc()).limit(1)
        )
        draft_res = await session.execute(
            select(ContentDraftRow).where(ContentDraftRow.project_id == project_id).order_by(ContentDraftRow.created_at.desc()).limit(1)
        )
        ff = ff_res.scalar_one_or_none()
        eeat_row = eeat_res.scalar_one_or_none()
        draft = draft_res.scalar_one_or_none()
        return {
            "project_id": project_id,
            "ff_score": None if ff is None else ff_view(ff),
            "eeat": None if eeat_row is None else eeat_view(eeat_row),
            "draft": None if draft is None else {"id": draft.draft_id, "root_url": draft.root_url, "drafts": draft.drafts, "created_at": draft.created_at, "legacy": True},
        }


@app.exception_handler(ValueError)
async def _value_error_handler(_, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("services.semantic_service.main:app", host="0.0.0.0", port=settings.port, reload=False)
