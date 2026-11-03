import uuid
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, root_validator
from sqlalchemy.orm import Session

from config.database_config import get_db
from config.logging_config import get_logger
from database.models import (
    SemanticAnalysis,
    SemanticEvent,
    User,
)
from services.api_gateway.auth import (
    get_current_user,
)
from services.api_gateway.config import settings

from services.api_gateway.routes.protected.common import (
    _get_owned_project,
    _get_owned_projects,
    _project_ids,
    _proxy_service,
    _resolve_project_for_private_flow,
    _serialize_datetime,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["Protected"])


class AnalyzeContentRequest(BaseModel):
    content: str
    keyword: Optional[str] = None
    targetKeyword: Optional[str] = None
    url: Optional[str] = None
    projectId: Optional[str] = None

    @root_validator(pre=True)
    def normalize_keyword(cls, values):
        if not values.get("keyword") and values.get("targetKeyword"):
            values["keyword"] = values["targetKeyword"]
        return values


def _load_content_history(db: Session, project_ids: List[str], *, limit: int = 50) -> List[Dict[str, Any]]:
    if not project_ids:
        return []

    events = (
        db.query(SemanticEvent)
        .filter(SemanticEvent.project_id.in_(project_ids), SemanticEvent.event_type == "content_analyzed")
        .order_by(SemanticEvent.created_at.desc())
        .limit(limit)
        .all()
    )
    items = [
        {
            "id": str((event.event_data or {}).get("analysis_id") or event.id),
            "url": str((event.event_data or {}).get("url") or ""),
            "keyword": str((event.event_data or {}).get("keyword") or ""),
            "score": float((event.event_data or {}).get("score") or 0),
            "projectId": str(event.project_id) if event.project_id else None,
            "analyzedAt": _serialize_datetime(event.created_at) or "",
            "source": "semantic_service",
        }
        for event in events
        if isinstance(event.event_data, dict)
    ]
    known_ids = {item["id"] for item in items}

    analyses = (
        db.query(SemanticAnalysis)
        .filter(SemanticAnalysis.project_id.in_(project_ids))
        .order_by(SemanticAnalysis.created_at.desc())
        .limit(limit)
        .all()
    )
    for analysis in analyses:
        analysis_id = str(analysis.analysis_id)
        if analysis_id in known_ids:
            continue
        keyword_coverage = analysis.keyword_coverage if isinstance(analysis.keyword_coverage, dict) else {}
        items.append(
            {
                "id": analysis_id,
                "url": analysis.root_url,
                "keyword": "",
                "score": float(keyword_coverage.get("coverage") or 0),
                "projectId": str(analysis.project_id) if analysis.project_id else None,
                "analyzedAt": _serialize_datetime(analysis.created_at) or "",
                "source": "semantic_schema.semantic_analysis",
            }
        )

    return sorted(items, key=lambda item: item.get("analyzedAt") or "", reverse=True)[:limit]


def _semantic_analysis_to_content_result(payload: Dict[str, Any], content: str, keyword: str) -> Dict[str, Any]:
    keyword_coverage = payload.get("keyword_coverage") if isinstance(payload.get("keyword_coverage"), dict) else {}
    content_gap = payload.get("content_gap") if isinstance(payload.get("content_gap"), dict) else {}
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    words = [word for word in re.findall(r"\b[\w-]+\b", content, flags=re.UNICODE) if word.strip()]
    normalized_keyword = keyword.strip().lower()
    keyword_density = 0.0
    if normalized_keyword and words:
        keyword_density = (content.lower().count(normalized_keyword) / max(len(words), 1)) * 100.0
    coverage_score = float(keyword_coverage.get("coverage") or 0.0)
    missing_keywords = keyword_coverage.get("missing") if isinstance(keyword_coverage.get("missing"), list) else []
    recommendations = []
    if missing_keywords:
        recommendations.append(
            {
                "title": "Улучшите покрытие ключевых слов",
                "description": "Добавьте в текст отсутствующие целевые термины: " + ", ".join(str(item) for item in missing_keywords[:5]),
            }
        )
    if "serp_top10_texts" in (inputs.get("unavailable") or []):
        recommendations.append(
            {
                "title": "Нет данных о топ-10 выдачи",
                "description": "Подключите данные SERP, чтобы сравнить текст с лидерами по запросу.",
            }
        )

    issues = []
    gap_missing = content_gap.get("missing_keywords") if isinstance(content_gap.get("missing_keywords"), list) else []
    if gap_missing:
        issues.append(
            {
                "title": "Обнаружены пробелы в семантике",
                "description": "Семантический анализ выявил отсутствие важных тематических блоков.",
            }
        )

    return {
        "score": int(round(max(0.0, min(100.0, coverage_score)))),
        "wordCount": len(words),
        "keywordDensity": round(keyword_density, 2),
        "uniqueness": 0,
        "recommendations": recommendations,
        "issues": issues,
        "analysisId": payload.get("analysis_id"),
        "source": "semantic_service",
    }


def _local_content_analysis_result(content: str, keyword: str, *, reason: str) -> Dict[str, Any]:
    words = [word for word in re.findall(r"\b[\w-]+\b", content, flags=re.UNICODE) if word.strip()]
    normalized_keyword = keyword.strip().lower()
    keyword_hits = content.lower().count(normalized_keyword) if normalized_keyword else 0
    keyword_density = (keyword_hits / max(len(words), 1)) * 100.0 if words else 0.0

    length_score = min(45.0, (len(words) / 800.0) * 45.0)
    keyword_score = 0.0
    if normalized_keyword:
        if 0.5 <= keyword_density <= 3.0:
            keyword_score = 35.0
        elif keyword_density > 0:
            keyword_score = 20.0
    structure_score = 20.0 if "\n" in content.strip() else 10.0
    score = int(round(max(0.0, min(100.0, length_score + keyword_score + structure_score))))

    recommendations = [
        {
            "title": "Семантический сервис временно недоступен",
            "description": "Показана базовая локальная проверка текста. Повторите анализ позже, чтобы получить полный FF-Score и семантические рекомендации.",
        }
    ]
    if normalized_keyword and keyword_hits == 0:
        recommendations.append(
            {
                "title": "Добавьте целевой запрос",
                "description": "Используйте целевой запрос естественно в заголовке, вступлении и релевантных блоках текста.",
            }
        )
    if len(words) < 300:
        recommendations.append(
            {
                "title": "Расширьте содержание",
                "description": "Добавьте больше полезных деталей, примеров и ответов на возможные вопросы пользователя.",
            }
        )

    issues = []
    if not words:
        issues.append({"title": "Текст пустой", "description": "Добавьте черновик страницы перед анализом."})
    if normalized_keyword and keyword_density > 5:
        issues.append({"title": "Риск переспама", "description": "Сократите повторения ключевого запроса и сохраните читаемость текста."})

    return {
        "score": score,
        "wordCount": len(words),
        "keywordDensity": round(keyword_density, 2),
        "uniqueness": 0,
        "recommendations": recommendations,
        "issues": issues,
        "analysisId": f"local-{uuid.uuid4()}",
        "source": "local_fallback",
        "fallbackReason": reason,
    }


@router.get("/content/optimized")
async def get_optimized_content(
    projectId: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if projectId:
        project_ids = [str(_get_owned_project(projectId, current_user, db).id)]
    else:
        project_ids = _project_ids(_get_owned_projects(current_user, db))
    return _load_content_history(db, project_ids)


@router.get("/projects/{project_id}/content/optimized")
async def get_project_optimized_content(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_owned_project(project_id, current_user, db)
    return _load_content_history(db, [str(project.id)])


@router.post("/content/analyze")
async def analyze_content(
    request: AnalyzeContentRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _resolve_project_for_private_flow(request.projectId, current_user, db)
    root_url = request.url or project.url
    keyword = request.keyword or ""
    try:
        response = await _proxy_service(
            settings.SEMANTIC_SERVICE_URL,
            "POST",
            "/semantic/analyze",
            payload={
                "project_id": str(project.id),
                "root_url": root_url,
                "mode": "private_content_review",
                "content_text": request.content,
                "keywords": [keyword] if keyword else [],
            },
            timeout=30.0,
        )
        analysis_payload = response.json()
        result = _semantic_analysis_to_content_result(analysis_payload, request.content, keyword)
    except HTTPException as exc:
        if exc.status_code < 500:
            raise
        logger.warning(
            "Semantic content analysis unavailable; using local fallback",
            extra={"project_id": str(project.id), "status_code": exc.status_code, "detail": exc.detail},
        )
        result = _local_content_analysis_result(request.content, keyword, reason=str(exc.detail))
        analysis_payload = {"analysis_id": result["analysisId"]}

    db.add(
        SemanticEvent(
            id=str(uuid.uuid4()),
            event_type="content_analyzed",
            project_id=str(project.id),
            event_data={
                "analysis_id": analysis_payload.get("analysis_id"),
                "url": root_url,
                "keyword": keyword,
                "score": result["score"],
                "source": result.get("source", "semantic_service"),
                "fallback_reason": result.get("fallbackReason"),
            },
        )
    )
    db.commit()
    return result
