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
from services.semantic_service.schemas.eeat import EEATRequest, EEATResponse
from services.semantic_service.scoring.ff_score_calculator import calculate_ff_score
from services.semantic_service.scoring.eeat_analyzer import analyze_eeat
from services.semantic_service.llm.llm_client import generate_drafts
from services.semantic_service.events.ff_score_recalculated import publish_ffscore_recalculated
from services.semantic_service.events.crawl_completed_handler import maybe_start_crawl_completed_consumer

app = FastAPI(title="Semantic Service", version="0.1.0")


@app.on_event("startup")
async def _startup() -> None:
    await init_db()
    asyncio.create_task(maybe_start_crawl_completed_consumer())


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "semantic_service", "ts": datetime.now(timezone.utc).isoformat()}


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


@app.post("/semantic/ff-score", response_model=FFScoreResponse)
async def ff_score(payload: FFScoreRequest) -> FFScoreResponse:
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
        freshness_days_since_update=payload.freshness_days_since_update,
        serp_shift=payload.serp_shift,
        link_velocity=payload.link_velocity,
        semantic_distance=payload.semantic_distance,
        keyword_coverage=payload.keyword_coverage,
        eeat_score=eeat_res["score"],
        cwv_grade=payload.cwv_grade,
        broken_links_count=payload.broken_links_count,
        schema_errors_count=payload.schema_errors_count,
    )

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
        ff = ff_res.scalar_one_or_none()
        eeat = eeat_res.scalar_one_or_none()
        return {
            "project_id": project_id,
            "ff_score": None if ff is None else {"id": ff.score_id, "score": ff.ff_score, "components": ff.components, "created_at": ff.created_at},
            "eeat": None if eeat is None else {"id": eeat.score_id, "score": eeat.score, "breakdown": eeat.breakdown, "created_at": eeat.created_at},
        }


@app.exception_handler(ValueError)
async def _value_error_handler(_, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("services.semantic_service.main:app", host="0.0.0.0", port=settings.port, reload=False)