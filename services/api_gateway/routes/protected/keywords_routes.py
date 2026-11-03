import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from config.database_config import get_db
from config.logging_config import get_logger
from database.models import (
    GSCData,
    SemanticEvent,
    User,
)
from services.api_gateway.auth import (
    get_current_user,
)

from services.api_gateway.routes.protected.common import (
    _get_owned_project,
    _get_owned_projects,
    _load_tracked_keywords,
    _project_ids,
    _resolve_project_for_private_flow,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["Protected"])


class KeywordSearchRequest(BaseModel):
    keyword: str
    projectId: Optional[str] = None


class TrackKeywordRequest(BaseModel):
    keyword: str
    projectId: Optional[str] = None
    volume: Optional[int] = None
    difficulty: Optional[float] = None
    cpc: Optional[float] = None


def _lookup_keyword_metrics_from_gsc(db: Session, project_id: str, keyword: str) -> Dict[str, Any]:
    normalized = keyword.strip()
    if not normalized:
        return {}

    row = (
        db.query(
            GSCData.query.label("keyword"),
            func.sum(GSCData.impressions).label("volume"),
            func.avg(GSCData.position).label("position"),
            func.sum(GSCData.clicks).label("clicks"),
        )
        .filter(GSCData.project_id == project_id, GSCData.query.ilike(normalized), GSCData.query.isnot(None))
        .group_by(GSCData.query)
        .order_by(func.sum(GSCData.impressions).desc())
        .first()
    )
    if row is None:
        return {}
    return {
        "volume": int(row.volume or 0),
        "position": round(float(row.position), 2) if row.position is not None else None,
        "clicks": int(row.clicks or 0),
    }


def _search_keywords_from_gsc(db: Session, project_id: str, keyword: str, *, limit: int = 10) -> List[Dict[str, Any]]:
    normalized = keyword.strip()
    if not normalized:
        return []

    rows = (
        db.query(
            GSCData.query.label("keyword"),
            func.sum(GSCData.impressions).label("volume"),
            func.avg(GSCData.position).label("position"),
            func.sum(GSCData.clicks).label("clicks"),
            func.avg(GSCData.ctr).label("ctr"),
        )
        .filter(GSCData.project_id == project_id, GSCData.query.ilike(f"%{normalized}%"), GSCData.query.isnot(None))
        .group_by(GSCData.query)
        .order_by(func.sum(GSCData.impressions).desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{project_id}:{row.keyword}")),
            "keyword": row.keyword,
            "volume": int(row.volume or 0),
            "difficulty": None,
            "cpc": None,
            "position": round(float(row.position), 2) if row.position is not None else None,
            "change": None,
            "clicks": int(row.clicks or 0),
            "ctr": round(float(row.ctr), 4) if row.ctr is not None else None,
            "source": "reporting_schema.gsc_data",
        }
        for row in rows
        if row.keyword
    ]


@router.post("/keywords/search")
async def search_keywords(
    request: KeywordSearchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _resolve_project_for_private_flow(request.projectId, current_user, db)
    keyword = request.keyword.strip()
    if not keyword:
        return []
    return _search_keywords_from_gsc(db, str(project.id), keyword)


@router.get("/keywords/tracked")
async def get_tracked_keywords(
    projectId: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if projectId:
        project_ids = [str(_get_owned_project(projectId, current_user, db).id)]
    else:
        project_ids = _project_ids(_get_owned_projects(current_user, db))
    return _load_tracked_keywords(db, project_ids)


@router.post("/keywords/tracked")
async def add_tracked_keyword(
    request: TrackKeywordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _resolve_project_for_private_flow(request.projectId, current_user, db)
    metrics = _lookup_keyword_metrics_from_gsc(db, str(project.id), request.keyword)
    record = {
        "id": str(uuid.uuid4()),
        "keyword": request.keyword,
        "volume": request.volume if request.volume is not None else metrics.get("volume"),
        "difficulty": request.difficulty,
        "cpc": request.cpc,
        "position": metrics.get("position"),
        "change": None,
        "projectId": str(project.id),
        "createdAt": datetime.utcnow().isoformat(),
        "source": "semantic_events",
    }
    db.add(
        SemanticEvent(
            id=str(uuid.uuid4()),
            event_type="keyword_tracked",
            project_id=str(project.id),
            event_data=record,
        )
    )
    db.commit()
    return record


@router.delete("/keywords/tracked/{keyword_id}")
async def remove_tracked_keyword(keyword_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project_ids = _project_ids(_get_owned_projects(current_user, db))
    tracked = _load_tracked_keywords(db, project_ids)
    record = next((item for item in tracked if str(item.get("id")) == str(keyword_id)), None)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ключевое слово не найдено.")

    db.add(
        SemanticEvent(
            id=str(uuid.uuid4()),
            event_type="keyword_untracked",
            project_id=str(record.get("projectId")),
            event_data={
                "keyword_id": keyword_id,
                "keyword": record.get("keyword"),
                "source": "api_gateway",
            },
        )
    )
    db.commit()
    return {"success": True}
