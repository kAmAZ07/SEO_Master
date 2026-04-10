import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select

from services.semantic_service.config import settings
from services.semantic_service.db.session import init_db, get_session
from services.semantic_service.db.models import FFScoreRow, EEATScoreRow, ContentDraftRow, SemanticAnalysisRow
from services.semantic_service.schemas.ff_score import FFScoreRequest, FFScoreResponse
from services.semantic_service.schemas.analysis import SemanticAnalysisRequest, SemanticAnalysisResponse
from services.semantic_service.schemas.eeat import EEATRequest, EEATResponse
from services.semantic_service.scoring.ff_score_calculator import calculate_ff_score
from services.semantic_service.scoring.eeat_analyzer import analyze_eeat
from services.semantic_service.llm.llm_client import generate_drafts
from services.semantic_service.events.ff_score_recalculated import publish_ffscore_recalculated
from services.semantic_service.events.crawl_completed_handler import maybe_start_crawl_completed_consumer
from services.semantic_service.events.task_created_handler import maybe_start_task_created_consumer
from services.semantic_service.analysis.pipeline import create_semantic_analysis

app = FastAPI(title="Semantic Service", version="0.1.0")


_CWV_RANK = {"poor": 0, "needs_improvement": 1, "good": 2, "unknown": 3}


@app.on_event("startup")
async def _startup() -> None:
    await init_db()
    asyncio.create_task(maybe_start_crawl_completed_consumer())
    asyncio.create_task(maybe_start_task_created_consumer())


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "semantic_service", "ts": datetime.now(timezone.utc).isoformat()}


def _worst_cwv_grade(summary: dict | None) -> str | None:
    if not isinstance(summary, dict):
        return None
    cwv = summary.get("cwv")
    if not isinstance(cwv, dict):
        return None

    grades = [
        str(cwv.get("LCP_grade") or "unknown").lower(),
        str(cwv.get("FID_grade") or "unknown").lower(),
        str(cwv.get("CLS_grade") or "unknown").lower(),
    ]
    grades = [grade for grade in grades if grade in _CWV_RANK]
    if not grades:
        return None
    return min(grades, key=lambda grade: _CWV_RANK.get(grade, 99))


def _count_findings(findings: list[dict], *, prefixes: tuple[str, ...] = (), codes: set[str] | None = None) -> int:
    total = 0
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        code = str(finding.get("code") or "")
        if codes and code in codes:
            total += 1
            continue
        if prefixes and any(code.startswith(prefix) for prefix in prefixes):
            total += 1
    return total


async def _resolve_ffscore_inputs(payload: FFScoreRequest) -> dict:
    sources: dict[str, str] = {}
    unavailable: list[str] = []
    input_sources = payload.input_sources if isinstance(payload.input_sources, dict) else {}

    latest_analysis = None
    if payload.project_id:
        async with get_session() as session:
            analysis_res = await session.execute(
                select(SemanticAnalysisRow)
                .where(SemanticAnalysisRow.project_id == payload.project_id)
                .order_by(SemanticAnalysisRow.created_at.desc())
                .limit(1)
            )
            latest_analysis = analysis_res.scalar_one_or_none()

    findings = [item for item in payload.audit_findings if isinstance(item, dict)]
    summary = payload.audit_summary if isinstance(payload.audit_summary, dict) else {}

    semantic_distance = payload.semantic_distance
    if semantic_distance is None and latest_analysis and isinstance(latest_analysis.semantic_distance, dict):
        semantic_distance = latest_analysis.semantic_distance.get("semantic_distance")
        if semantic_distance is not None:
            sources["semantic_distance"] = "semantic_analysis"
    elif semantic_distance is not None:
        sources["semantic_distance"] = str(input_sources.get("semantic_distance") or "request")
    else:
        semantic_distance = 50.0
        sources["semantic_distance"] = "estimate"
        unavailable.append("semantic_distance")

    keyword_coverage = payload.keyword_coverage
    if keyword_coverage is None and latest_analysis and isinstance(latest_analysis.keyword_coverage, dict):
        keyword_coverage = latest_analysis.keyword_coverage.get("coverage")
        if keyword_coverage is not None:
            sources["keyword_coverage"] = "semantic_analysis"
    elif keyword_coverage is not None:
        sources["keyword_coverage"] = str(input_sources.get("keyword_coverage") or "request")
    else:
        keyword_coverage = 50.0
        sources["keyword_coverage"] = "estimate"
        unavailable.append("keyword_coverage")

    freshness_days_since_update = payload.freshness_days_since_update
    if freshness_days_since_update is None:
        freshness_days_since_update = 30
        sources["freshness_days_since_update"] = "estimate"
        unavailable.append("freshness_days_since_update")
    else:
        sources["freshness_days_since_update"] = str(input_sources.get("freshness_days_since_update") or "request")

    serp_shift = payload.serp_shift
    if serp_shift is None:
        serp_shift = 0.0
        sources["serp_shift"] = "estimate"
        unavailable.append("serp_shift")
    else:
        sources["serp_shift"] = str(input_sources.get("serp_shift") or "request")

    link_velocity = payload.link_velocity
    if link_velocity is None:
        link_velocity = 0.0
        sources["link_velocity"] = "estimate"
        unavailable.append("link_velocity")
    else:
        sources["link_velocity"] = str(input_sources.get("link_velocity") or "request")

    cwv_grade = payload.cwv_grade
    if cwv_grade:
        sources["cwv_grade"] = str(input_sources.get("cwv_grade") or "request")
    else:
        cwv_grade = _worst_cwv_grade(summary) or "unknown"
        sources["cwv_grade"] = "audit_summary" if cwv_grade != "unknown" else "estimate"
        if cwv_grade == "unknown":
            unavailable.append("cwv_grade")

    broken_links_count = payload.broken_links_count
    if broken_links_count is None:
        broken_links_count = _count_findings(findings, codes={"broken_link_404"})
        sources["broken_links_count"] = "audit_findings" if findings else "estimate"
        if not findings:
            unavailable.append("broken_links_count")
    else:
        sources["broken_links_count"] = str(input_sources.get("broken_links_count") or "request")

    schema_errors_count = payload.schema_errors_count
    if schema_errors_count is None:
        schema_errors_count = _count_findings(
            findings,
            prefixes=("jsonld_",),
            codes={"jsonld_missing", "jsonld_empty", "jsonld_invalid_json", "jsonld_invalid_structure", "jsonld_missing_context", "jsonld_missing_type"},
        )
        sources["schema_errors_count"] = "audit_findings" if findings else "estimate"
        if not findings:
            unavailable.append("schema_errors_count")
    else:
        sources["schema_errors_count"] = str(input_sources.get("schema_errors_count") or "request")

    return {
        "freshness_days_since_update": int(freshness_days_since_update),
        "serp_shift": float(serp_shift),
        "link_velocity": float(link_velocity),
        "semantic_distance": float(semantic_distance),
        "keyword_coverage": float(keyword_coverage),
        "cwv_grade": str(cwv_grade),
        "broken_links_count": int(broken_links_count),
        "schema_errors_count": int(schema_errors_count),
        "sources": sources,
        "unavailable": sorted(set(unavailable)),
    }


@app.post("/semantic/eeat", response_model=EEATResponse)
async def eeat(payload: EEATRequest) -> EEATResponse:
    res = analyze_eeat(
        text=payload.text,
        root_url=str(payload.root_url),
        backlinks_count=payload.backlinks_count,
        has_https=payload.has_https,
        has_privacy_policy=payload.has_privacy_policy,
        has_contacts=payload.has_contacts,
        has_author_schema=payload.has_author_schema,
        authoritative_outbound_links=payload.authoritative_outbound_links,
        brand_mentions=payload.brand_mentions,
    )
    score_id = str(uuid.uuid4())
    async with get_session() as session:
        session.add(
            EEATScoreRow(
                score_id=score_id,
                project_id=payload.project_id,
                root_url=str(payload.root_url),
                created_at=datetime.now(timezone.utc),
                breakdown=res["breakdown"],
                score=res["score"],
                signals=res["signals"],
            )
        )
        await session.commit()
    return EEATResponse(score_id=score_id, score=res["score"], breakdown=res["breakdown"], signals=res["signals"])


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
    resolved_inputs = await _resolve_ffscore_inputs(payload)
    eeat_res = analyze_eeat(
        text=payload.content_text,
        root_url=str(payload.root_url),
        backlinks_count=payload.backlinks_count,
        has_https=payload.has_https,
        has_privacy_policy=payload.has_privacy_policy,
        has_contacts=payload.has_contacts,
        has_author_schema=payload.has_author_schema,
        authoritative_outbound_links=payload.authoritative_outbound_links,
        brand_mentions=payload.brand_mentions,
    )
    ff = calculate_ff_score(
        freshness_days_since_update=resolved_inputs["freshness_days_since_update"],
        serp_shift=resolved_inputs["serp_shift"],
        link_velocity=resolved_inputs["link_velocity"],
        semantic_distance=resolved_inputs["semantic_distance"],
        keyword_coverage=resolved_inputs["keyword_coverage"],
        eeat_score=eeat_res["score"],
        cwv_grade=resolved_inputs["cwv_grade"],
        broken_links_count=resolved_inputs["broken_links_count"],
        schema_errors_count=resolved_inputs["schema_errors_count"],
    )
    ff["inputs"]["sources"] = resolved_inputs["sources"]
    ff["inputs"]["unavailable"] = resolved_inputs["unavailable"]

    ff_id = str(uuid.uuid4())
    eeat_id = str(uuid.uuid4())

    async with get_session() as session:
        session.add(
            EEATScoreRow(
                score_id=eeat_id,
                project_id=payload.project_id,
                root_url=str(payload.root_url),
                created_at=datetime.now(timezone.utc),
                breakdown=eeat_res["breakdown"],
                score=eeat_res["score"],
                signals=eeat_res["signals"],
            )
        )
        session.add(
            FFScoreRow(
                score_id=ff_id,
                project_id=payload.project_id,
                root_url=str(payload.root_url),
                created_at=datetime.now(timezone.utc),
                ff_score=ff["ff_score"],
                components=ff["components"],
                inputs=ff["inputs"],
                thresholds=ff["thresholds"],
                eeat_score_id=eeat_id,
            )
        )
        await session.commit()

    await publish_ffscore_recalculated(
        project_id=payload.project_id,
        root_url=str(payload.root_url),
        ff_score_id=ff_id,
        ff_score=ff["ff_score"],
        components=ff["components"],
        inputs=ff["inputs"],
        eeat={"score": eeat_res["score"], "breakdown": eeat_res["breakdown"]},
    )

    return FFScoreResponse(
        ff_score_id=ff_id,
        eeat_score_id=eeat_id,
        ff_score=ff["ff_score"],
        components=ff["components"],
        inputs=ff["inputs"],
        eeat={"score": eeat_res["score"], "breakdown": eeat_res["breakdown"]},
    )


@app.post("/semantic/drafts")
async def drafts(payload: dict) -> dict:
    root_url = payload.get("root_url")
    content = payload.get("content", "")
    project_id = payload.get("project_id")
    if not root_url or not isinstance(root_url, str):
        raise HTTPException(status_code=400, detail="root_url_required")
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="content_must_be_string")

    drafts_res = await generate_drafts(root_url=root_url, content=content)
    draft_id = str(uuid.uuid4())
    async with get_session() as session:
        session.add(
            ContentDraftRow(
                draft_id=draft_id,
                project_id=project_id,
                root_url=root_url,
                created_at=datetime.now(timezone.utc),
                drafts=drafts_res,
            )
        )
        await session.commit()
    return {"draft_id": draft_id, "drafts": drafts_res}


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
        eeat = eeat_res.scalar_one_or_none()
        draft = draft_res.scalar_one_or_none()
        return {
            "project_id": project_id,
            "ff_score": None if ff is None else {"id": ff.score_id, "score": ff.ff_score, "components": ff.components, "created_at": ff.created_at},
            "eeat": None if eeat is None else {"id": eeat.score_id, "score": eeat.score, "breakdown": eeat.breakdown, "created_at": eeat.created_at},
            "draft": None if draft is None else {"id": draft.draft_id, "root_url": draft.root_url, "drafts": draft.drafts, "created_at": draft.created_at},
        }


@app.exception_handler(ValueError)
async def _value_error_handler(_, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("services.semantic_service.main:app", host="0.0.0.0", port=settings.port, reload=False)
