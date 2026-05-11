import uuid
from datetime import datetime
import re
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field, root_validator, validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from config.database_config import get_db
from config.logging_config import get_logger
from database.models import (
    Backlink,
    Crawl,
    CrawlResult,
    FFScore,
    GSCData,
    Page,
    Project,
    PublicAuditResult,
    SemanticAnalysis,
    SemanticEvent,
    User,
)
from services.api_gateway.events.hitl_approved import publish_hitl_approved_event
from services.api_gateway.auth import (
    authenticate_user,
    create_token_pair,
    create_user,
    get_current_user,
    get_password_hash,
    validate_password,
    verify_password,
    verify_refresh_token,
)
from services.api_gateway.config import settings

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["Protected"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    name: Optional[str] = None
    full_name: Optional[str] = None

    @root_validator(pre=True)
    def normalize_name(cls, values):
        if not values.get("name") and values.get("full_name"):
            values["name"] = values["full_name"]
        if not values.get("full_name") and values.get("name"):
            values["full_name"] = values["name"]
        return values

    @validator("password")
    def validate_password_value(cls, value: str) -> str:
        return validate_password(value)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class ApprovalRequest(BaseModel):
    comment: Optional[str] = None


class UpdateProfileRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    company: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    currentPassword: str
    newPassword: str = Field(..., min_length=8)

    @validator("newPassword")
    def validate_new_password(cls, value: str) -> str:
        return validate_password(value)


class CreateProjectRequest(BaseModel):
    name: str
    url: str
    description: Optional[str] = None


class StartProjectAuditRequest(BaseModel):
    projectId: str
    url: Optional[str] = None
    maxPages: Optional[int] = Field(default=None, ge=1, le=1000)
    maxDepth: Optional[int] = Field(default=None, ge=0, le=4)
    jsRender: Optional[bool] = None


class AnalyzeBacklinkRequest(BaseModel):
    url: str
    projectId: Optional[str] = None


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


class KeywordSearchRequest(BaseModel):
    keyword: str
    projectId: Optional[str] = None


class TrackKeywordRequest(BaseModel):
    keyword: str
    projectId: Optional[str] = None
    volume: Optional[int] = None
    difficulty: Optional[float] = None
    cpc: Optional[float] = None


def _serialize_user(user: User) -> Dict[str, Any]:
    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.full_name or "",
        "full_name": user.full_name or "",
        "role": "admin" if user.is_superuser else "user",
    }


def _serialize_project(project: Project) -> Dict[str, Any]:
    return {
        "id": str(project.id),
        "name": project.name,
        "url": project.url,
        "description": None,
        "status": project.status,
        "createdAt": project.created_at.isoformat() if project.created_at else None,
        "updatedAt": project.updated_at.isoformat() if project.updated_at else None,
    }


def _extract_score(results: Any) -> float:
    if not isinstance(results, dict):
        return 0.0
    if isinstance(results.get("score"), (int, float)):
        return float(results["score"])
    if isinstance(results.get("summary"), dict) and isinstance(results["summary"].get("score"), (int, float)):
        return float(results["summary"]["score"])
    return 0.0


def _serialize_audit_row(row: PublicAuditResult) -> Dict[str, Any]:
    summary = getattr(row, "summary", None)
    legacy_results = getattr(row, "results", None)
    results = summary if isinstance(summary, dict) else legacy_results

    return {
        "id": str(getattr(row, "audit_id", None) or getattr(row, "id", "")),
        "url": getattr(row, "root_url", None) or getattr(row, "url", ""),
        "score": _extract_score(results),
        "status": (getattr(row, "status", None) or "completed").lower(),
    }


def _audit_progress(status_value: str) -> int:
    if status_value == "completed":
        return 100
    if status_value == "running":
        return 60
    if status_value == "queued":
        return 10
    if status_value == "failed":
        return 100
    return 0


def _audit_status_response(result: Dict[str, Any]) -> Dict[str, Any]:
    status_value = str(result.get("status") or "unknown")
    all_findings = result.get("findings", [])
    top_findings = result.get("top_findings")
    if not isinstance(top_findings, list):
        top_findings = all_findings[:5] if isinstance(all_findings, list) else []

    return {
        "uid": result.get("audit_id"),
        "audit_id": result.get("audit_id"),
        "project_id": result.get("project_id"),
        "root_url": result.get("root_url"),
        "url": result.get("root_url"),
        "mode": result.get("mode"),
        "status": status_value,
        "progress": _audit_progress(status_value),
        "results": {
            "summary": result.get("summary", {}),
            "findings": top_findings,
            "top_findings": top_findings,
            "findings_count": len(all_findings) if isinstance(all_findings, list) else 0,
            "pages": result.get("pages", []),
            "root_url": result.get("root_url"),
        },
        "created_at": result.get("created_at"),
        "completed_at": result.get("updated_at") if status_value in {"completed", "failed"} else None,
    }


def _serialize_datetime(value: Any) -> Optional[str]:
    return value.isoformat() if value else None


def _active_status(status_value: str) -> bool:
    return status_value in {"queued", "running", "pending", "processing", "in_progress"}


def _get_owned_project(
    project_id: str,
    current_user: User,
    db: Session,
    *,
    not_found_detail: str = "Project not found",
) -> Project:
    project = db.query(Project).filter(Project.id == project_id, Project.owner_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=not_found_detail)
    return project


def _get_owned_projects(current_user: User, db: Session) -> List[Project]:
    return db.query(Project).filter(Project.owner_id == current_user.id).order_by(Project.created_at.desc()).all()


def _resolve_project_for_private_flow(
    project_id: Optional[str],
    current_user: User,
    db: Session,
) -> Project:
    if project_id:
        return _get_owned_project(project_id, current_user, db)

    project = db.query(Project).filter(Project.owner_id == current_user.id).order_by(Project.created_at.desc()).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project_required")
    return project


def _project_ids(projects: Iterable[Project]) -> List[str]:
    return [str(project.id) for project in projects]


def _normalize_keyword_record(row: SemanticEvent) -> Dict[str, Any]:
    data = row.event_data if isinstance(row.event_data, dict) else {}
    keyword = str(data.get("keyword") or "").strip()
    keyword_id = str(data.get("id") or data.get("keyword_id") or row.id)
    return {
        "id": keyword_id,
        "keyword": keyword,
        "volume": data.get("volume"),
        "difficulty": data.get("difficulty"),
        "cpc": data.get("cpc"),
        "position": data.get("position"),
        "change": data.get("change"),
        "projectId": str(row.project_id) if row.project_id else None,
        "createdAt": _serialize_datetime(row.created_at),
        "source": "semantic_events",
    }


def _load_tracked_keywords(db: Session, project_ids: List[str]) -> List[Dict[str, Any]]:
    if not project_ids:
        return []

    events = (
        db.query(SemanticEvent)
        .filter(
            SemanticEvent.project_id.in_(project_ids),
            SemanticEvent.event_type.in_(["keyword_tracked", "keyword_untracked"]),
        )
        .order_by(SemanticEvent.created_at.asc())
        .all()
    )
    tracked: Dict[str, Dict[str, Any]] = {}
    keyword_index: Dict[tuple[str, str], str] = {}

    for event in events:
        data = event.event_data if isinstance(event.event_data, dict) else {}
        project_id = str(event.project_id or "")
        keyword = str(data.get("keyword") or "").strip()
        keyword_key = (project_id, keyword.lower())
        keyword_id = str(data.get("id") or data.get("keyword_id") or event.id)

        if event.event_type == "keyword_untracked":
            tracked.pop(keyword_id, None)
            existing_id = keyword_index.pop(keyword_key, None)
            if existing_id:
                tracked.pop(existing_id, None)
            continue

        record = _normalize_keyword_record(event)
        if not record["keyword"]:
            continue
        existing_id = keyword_index.get(keyword_key)
        if existing_id:
            tracked.pop(existing_id, None)
        tracked[keyword_id] = record
        keyword_index[keyword_key] = keyword_id

    return sorted(tracked.values(), key=lambda item: item.get("createdAt") or "", reverse=True)


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
                "title": "Improve keyword coverage",
                "description": "Add missing target terms naturally: " + ", ".join(str(item) for item in missing_keywords[:5]),
            }
        )
    if "serp_top10_texts" in (inputs.get("unavailable") or []):
        recommendations.append(
            {
                "title": "SERP context unavailable",
                "description": "Connect SERP inputs to compare this draft against top-ranking pages.",
            }
        )

    issues = []
    gap_missing = content_gap.get("missing_keywords") if isinstance(content_gap.get("missing_keywords"), list) else []
    if gap_missing:
        issues.append(
            {
                "title": "Content gap detected",
                "description": "The semantic analysis found missing topical coverage.",
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


def _latest_ff_score(db: Session, project_id: str) -> Optional[FFScore]:
    return (
        db.query(FFScore)
        .filter(FFScore.project_id == project_id)
        .order_by(FFScore.created_at.desc())
        .first()
    )


def _serialize_ff_score(row: Optional[FFScore]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None

    components = row.components if isinstance(row.components, dict) else {}
    return {
        "total": float(row.ff_score),
        "freshness": float(components.get("freshness", 0) or 0),
        "familiarity": float(components.get("familiarity", 0) or 0),
        "quality": float(components.get("quality", 0) or 0),
        "timestamp": _serialize_datetime(row.created_at) or "",
    }


def _count_project_backlinks(db: Session, project_ids: List[str]) -> int:
    if not project_ids:
        return 0
    return int(
        db.query(func.count(Backlink.id))
        .join(Page, Backlink.page_id == Page.id)
        .join(Crawl, Page.crawl_id == Crawl.id)
        .filter(Crawl.project_id.in_(project_ids))
        .scalar()
        or 0
    )


def _serialize_backlink_row(row: Backlink, page: Page) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "sourceUrl": row.source_url,
        "targetUrl": page.url,
        "type": row.link_type or "unknown",
        "domainAuthority": None,
        "anchorText": row.anchor_text,
        "discoveredAt": _serialize_datetime(row.discovered_at or row.created_at) or "",
        "source": "audit_schema.backlinks",
    }


def _load_project_backlinks(db: Session, project_ids: List[str], *, limit: int = 200) -> List[Dict[str, Any]]:
    if not project_ids:
        return []

    rows = (
        db.query(Backlink, Page)
        .join(Page, Backlink.page_id == Page.id)
        .join(Crawl, Page.crawl_id == Crawl.id)
        .filter(Crawl.project_id.in_(project_ids))
        .order_by(Backlink.discovered_at.desc(), Backlink.created_at.desc())
        .limit(limit)
        .all()
    )
    return [_serialize_backlink_row(backlink, page) for backlink, page in rows]


def _count_project_pages(db: Session, project_id: str) -> int:
    return int(
        db.query(func.count(Page.id))
        .join(Crawl, Page.crawl_id == Crawl.id)
        .filter(Crawl.project_id == project_id)
        .scalar()
        or 0
    )


def _count_project_audits(db: Session, project_id: str) -> int:
    full_count = db.query(func.count(CrawlResult.audit_id)).filter(CrawlResult.project_id == project_id).scalar() or 0
    public_count = db.query(func.count(PublicAuditResult.audit_id)).filter(PublicAuditResult.project_id == project_id).scalar() or 0
    return int(full_count + public_count)


def _latest_project_audit_at(db: Session, project_id: str) -> Optional[str]:
    full_at = db.query(func.max(CrawlResult.created_at)).filter(CrawlResult.project_id == project_id).scalar()
    public_at = db.query(func.max(PublicAuditResult.created_at)).filter(PublicAuditResult.project_id == project_id).scalar()
    values = [value for value in (full_at, public_at) if value is not None]
    return max(values).isoformat() if values else None


def _serialize_project_private(project: Project, db: Session) -> Dict[str, Any]:
    project_id = str(project.id)
    tracked_keywords = _load_tracked_keywords(db, [project_id])
    backlink_count = _count_project_backlinks(db, [project_id])
    return {
        **_serialize_project(project),
        "ffScore": _serialize_ff_score(_latest_ff_score(db, project_id)),
        "lastAudit": _latest_project_audit_at(db, project_id),
        "stats": {
            "audits": _count_project_audits(db, project_id),
            "keywords": len(tracked_keywords),
            "pages": _count_project_pages(db, project_id),
            "backlinks": backlink_count,
        },
    }


def _collect_recent_audits(db: Session, project_ids: List[str], *, limit: int = 50) -> List[Dict[str, Any]]:
    if not project_ids:
        return []

    full_rows = (
        db.query(CrawlResult)
        .filter(CrawlResult.project_id.in_(project_ids))
        .order_by(CrawlResult.created_at.desc())
        .limit(limit)
        .all()
    )
    public_rows = (
        db.query(PublicAuditResult)
        .filter(PublicAuditResult.project_id.in_(project_ids))
        .order_by(PublicAuditResult.created_at.desc())
        .limit(limit)
        .all()
    )

    items: List[Dict[str, Any]] = []
    for row in [*full_rows, *public_rows]:
        audit_item = _serialize_audit_row(row)
        findings = getattr(row, "findings", None)
        pages = getattr(row, "pages", None)
        items.append(
            {
                "id": audit_item["id"],
                "uid": audit_item["id"],
                "projectId": str(getattr(row, "project_id", "") or ""),
                "url": audit_item["url"],
                "mode": getattr(row, "mode", None),
                "status": audit_item["status"],
                "score": audit_item["score"],
                "createdAt": _serialize_datetime(getattr(row, "created_at", None)),
                "updatedAt": _serialize_datetime(getattr(row, "updated_at", None)),
                "issues": findings if isinstance(findings, list) else [],
                "details": {
                    "summary": getattr(row, "summary", {}) or {},
                    "pages": pages if isinstance(pages, list) else [],
                    "source": row.__tablename__,
                },
            }
        )

    return sorted(items, key=lambda item: item.get("createdAt") or "", reverse=True)[:limit]


async def _proxy_service(
    service_url: str,
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
    timeout: float = 15.0,
) -> httpx.Response:
    url = f"{service_url.rstrip('/')}{path}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.request(method, url, params=params, json=payload)
        except httpx.RequestError as exc:
            logger.warning("Private service proxy unavailable", extra={"url": url, "error": str(exc)})
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="private_service_unavailable") from exc

    if response.status_code >= 400:
        detail: Any
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text[:300] or "private_service_error"
        raise HTTPException(status_code=response.status_code, detail=detail)

    return response


async def _proxy_management_response(
    method: str,
    path_candidates: List[str],
    *,
    params: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[httpx.Response]:
    not_found_response: Optional[httpx.Response] = None
    async with httpx.AsyncClient(timeout=10.0) as client:
        for path in path_candidates:
            url = f"{settings.MANAGEMENT_SERVICE_URL.rstrip('/')}{path}"
            try:
                response = await client.request(method, url, params=params, json=payload)
            except httpx.RequestError:
                continue

            if response.status_code == 404:
                not_found_response = response
                continue
            return response

    return not_found_response


async def _proxy_management_required_json(
    method: str,
    path_candidates: List[str],
    *,
    params: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
    unavailable_detail: str = "management_service_unavailable",
) -> Any:
    response = await _proxy_management_response(method, path_candidates, params=params, payload=payload)
    if response is None:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=unavailable_detail)
    if response.status_code >= 400:
        detail: Any
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text[:300] or unavailable_detail
        raise HTTPException(status_code=response.status_code, detail=detail)
    try:
        return response.json()
    except ValueError:
        return None


async def _get_owned_hitl_task(
    task_id: str,
    current_user: User,
    db: Session,
) -> Dict[str, Any]:
    data = await _proxy_management_required_json(
        "GET",
        [f"/api/v1/hitl/tasks/{task_id}", f"/api/hitl/tasks/{task_id}"],
        unavailable_detail="hitl_task_unavailable",
    )
    if not isinstance(data, dict):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="hitl_task_invalid_response")

    project_id = str(data.get("project_id") or data.get("projectId") or "")
    if not project_id:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="hitl_task_missing_project")
    _get_owned_project(project_id, current_user, db, not_found_detail="hitl_task_not_found")
    return data


def _normalize_domain(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        host = ""
    return host[4:] if host.startswith("www.") else host


def _extract_links_from_html(html: str) -> List[str]:
    links = re.findall(r"""href=["']([^"'#]+)["']""", html, flags=re.IGNORECASE)
    result: List[str] = []
    for link in links:
        link = link.strip()
        if link.startswith(("http://", "https://")):
            result.append(link)
    return result


def _estimate_domain_authority(source_url: str, target_host: str) -> int:
    source_host = _normalize_domain(source_url)
    if not source_host:
        return 0
    common = len(set(source_host.split(".")) & set(target_host.split(".")))
    base = 20 + min(35, len(source_host))
    bonus = 10 if source_host.endswith(target_host) else 0
    return max(1, min(100, base + bonus + common * 6))


async def _analyze_backlinks_live(url: str) -> List[Dict[str, Any]]:
    target_host = _normalize_domain(url)
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
    links = _extract_links_from_html(response.text)

    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for link in links:
        source_host = _normalize_domain(link)
        if not source_host or source_host == target_host:
            continue
        if link in seen:
            continue
        seen.add(link)
        rows.append(
            {
                "id": str(uuid.uuid4()),
                "sourceUrl": link,
                "targetUrl": url,
                "type": "dofollow",
                "domainAuthority": _estimate_domain_authority(link, target_host),
                "anchorText": source_host,
                "discoveredAt": datetime.utcnow().isoformat(),
            }
        )
        if len(rows) >= 25:
            break

    return rows


@router.post("/auth/register")
async def register(request: RegisterRequest, db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.email == request.email).first()
    if existing_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    try:
        user = create_user(
            db=db,
            email=request.email,
            password=request.password,
            full_name=request.full_name or request.name or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    tokens = create_token_pair(user_id=str(user.id), email=user.email)

    return {
        "success": True,
        "user": _serialize_user(user),
        "token": tokens.access_token,
        "refreshToken": tokens.refresh_token,
        "tokens": tokens.dict(),
    }


@router.post("/auth/login")
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate_user(db, request.email, request.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")

    tokens = create_token_pair(user_id=str(user.id), email=user.email)

    return {
        "success": True,
        "user": _serialize_user(user),
        "token": tokens.access_token,
        "refreshToken": tokens.refresh_token,
        "tokens": tokens.dict(),
    }


@router.post("/auth/refresh")
async def refresh_token(request: RefreshTokenRequest, db: Session = Depends(get_db)):
    token_data = verify_refresh_token(request.refresh_token)
    user = db.query(User).filter(User.id == token_data.user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    tokens = create_token_pair(user_id=str(user.id), email=user.email)
    return {"token": tokens.access_token, "refreshToken": tokens.refresh_token, "tokens": tokens.dict()}


@router.get("/auth/me")
@router.get("/me")
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    return _serialize_user(current_user)


@router.patch("/auth/profile")
async def update_profile(
    request: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if request.email:
        email_owner = db.query(User).filter(User.email == request.email, User.id != current_user.id).first()
        if email_owner:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use")
        current_user.email = request.email

    if request.name is not None:
        current_user.full_name = request.name

    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return _serialize_user(current_user)


@router.post("/auth/change-password")
async def change_password(
    request: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(request.currentPassword, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    if request.currentPassword == request.newPassword:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New password must differ from the current password")

    current_user.hashed_password = get_password_hash(request.newPassword)
    db.add(current_user)
    db.commit()
    return {"success": True}


@router.post("/auth/logout")
async def logout(_: User = Depends(get_current_user)):
    return {"success": True}


@router.get("/dashboard/stats")
async def get_dashboard_stats(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    projects = _get_owned_projects(current_user, db)
    recent_projects = [_serialize_project_private(project, db) for project in projects[:5]]
    project_ids = _project_ids(projects)

    recent_audit_items = _collect_recent_audits(db, project_ids, limit=50)
    active_audits = sum(1 for item in recent_audit_items if _active_status(str(item.get("status") or "")))
    recent_audits = [
        {
            "id": item["id"],
            "url": item["url"],
            "score": item["score"],
            "status": "completed" if item["status"] not in {"queued", "running", "pending", "processing", "in_progress", "failed"} else item["status"],
        }
        for item in recent_audit_items[:5]
    ]

    pending_tasks = 0
    completed_tasks = 0
    for project_id in project_ids:
        pending_data = await _proxy_management_required_json(
            "GET",
            ["/api/v1/tasks"],
            params={"project_id": project_id, "status": "pending", "limit": 200},
            unavailable_detail="management_tasks_unavailable",
        )
        completed_data = await _proxy_management_required_json(
            "GET",
            ["/api/v1/tasks"],
            params={"project_id": project_id, "status": "completed", "limit": 200},
            unavailable_detail="management_tasks_unavailable",
        )
        pending_tasks += len(pending_data or [])
        completed_tasks += len(completed_data or [])

    backlink_count = _count_project_backlinks(db, project_ids)
    tracked_keywords = _load_tracked_keywords(db, project_ids)
    latest_scores = [
        latest_score.ff_score
        for project_id in project_ids
        if (latest_score := _latest_ff_score(db, project_id)) is not None
    ]
    avg_ff_score = (sum(latest_scores) / len(latest_scores)) if latest_scores else 0.0

    return {
        "totalProjects": len(projects),
        "activeAudits": active_audits,
        "totalKeywords": len(tracked_keywords),
        "totalBacklinks": backlink_count,
        "pendingTasks": pending_tasks,
        "completedTasks": completed_tasks,
        "avgFFScore": round(avg_ff_score, 2),
        "recentProjects": recent_projects,
        "recentAudits": recent_audits,
    }


@router.get("/dashboard")
async def get_dashboard_alias(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stats = await get_dashboard_stats(current_user=current_user, db=db)
    return {
        "user": _serialize_user(current_user),
        "projects_count": stats["totalProjects"],
        "projects": stats["recentProjects"],
        **stats,
    }


@router.get("/projects")
async def list_projects(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    projects = db.query(Project).filter(Project.owner_id == current_user.id).order_by(Project.created_at.desc()).all()
    return [_serialize_project_private(project, db) for project in projects]


@router.post("/projects")
async def create_project_endpoint(
    request: CreateProjectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = db.query(Project).filter(Project.url == request.url).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Project with this URL already exists")

    project = Project(
        id=str(uuid.uuid4()),
        name=request.name,
        url=request.url,
        owner_id=str(current_user.id),
        status="active",
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return _serialize_project_private(project, db)


@router.get("/projects/{project_id}")
async def get_project(project_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _get_owned_project(project_id, current_user, db)
    return _serialize_project_private(project, db)


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id, Project.owner_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    db.delete(project)
    db.commit()
    return {"success": True}


@router.post("/audit/full")
async def start_project_audit(
    request: StartProjectAuditRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_owned_project(request.projectId, current_user, db)
    root_url = request.url or project.url
    payload = {
        "project_id": str(project.id),
        "root_url": root_url,
        "site_type_hint": "unknown",
        "platform": "generic",
        "options": {
            "max_pages": request.maxPages or 1000,
            "max_depth": request.maxDepth if request.maxDepth is not None else 4,
            "js_render": True if request.jsRender is None else request.jsRender,
            "timeout": 30.0,
        },
    }

    response = await _proxy_service(
        settings.AUDIT_SERVICE_URL,
        "POST",
        "/audit/full",
        payload=payload,
        timeout=30.0,
    )
    result = response.json()
    audit_id = result.get("audit_id")
    if not audit_id:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="audit_service_invalid_response")

    return {
        "success": True,
        "uid": audit_id,
        "audit_id": audit_id,
        "project_id": str(project.id),
        "url": root_url,
        "status": result.get("status", "queued"),
        "mode": result.get("mode", "full"),
        "message": "Project audit started",
        "estimated_time_seconds": 120,
    }


@router.get("/audit/status/{uid}")
async def get_project_audit_status(
    uid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response = await _proxy_service(
        settings.AUDIT_SERVICE_URL,
        "GET",
        f"/audit/{uid}",
        timeout=10.0,
    )
    result = response.json()
    project_id = str(result.get("project_id") or "")
    if not project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="audit_not_found")

    _get_owned_project(project_id, current_user, db, not_found_detail="audit_not_found")
    return _audit_status_response(result)


@router.get("/audit/history")
async def get_audit_history(
    projectId: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if projectId:
        project_ids = [str(_get_owned_project(projectId, current_user, db).id)]
    else:
        project_ids = _project_ids(_get_owned_projects(current_user, db))

    return _collect_recent_audits(db, project_ids, limit=50)


@router.get("/tasks")
async def get_management_tasks(
    project_id: Optional[str] = Query(None, alias="project_id"),
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if project_id:
        project_ids = [str(_get_owned_project(project_id, current_user, db).id)]
    else:
        project_ids = _project_ids(_get_owned_projects(current_user, db))

    items: List[Dict[str, Any]] = []
    for owned_project_id in project_ids:
        data = await _proxy_management_required_json(
            "GET",
            ["/api/v1/tasks"],
            params={
                "project_id": owned_project_id,
                "status": status_filter,
                "limit": limit,
            },
            unavailable_detail="management_tasks_unavailable",
        )
        if isinstance(data, list):
            items.extend(item for item in data if isinstance(item, dict))

    return sorted(
        items,
        key=lambda item: item.get("updated_at") or item.get("created_at") or "",
        reverse=True,
    )[:limit]


@router.get("/hitl/tasks")
async def get_hitl_tasks(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    status_filter: Optional[str] = "pending",
    limit: int = 50,
):
    items: List[Dict[str, Any]] = []
    project_ids = _project_ids(_get_owned_projects(current_user, db))
    for project_id in project_ids:
        data = await _proxy_management_required_json(
            "GET",
            ["/api/v1/hitl/tasks", "/api/hitl/tasks"],
            params={"status": status_filter, "limit": limit, "project_id": project_id},
            unavailable_detail="hitl_tasks_unavailable",
        )
        if isinstance(data, list):
            items.extend(item for item in data if isinstance(item, dict))

    return sorted(items, key=lambda item: item.get("created_at") or item.get("createdAt") or "", reverse=True)[:limit]


@router.get("/hitl/tasks/{task_id}")
async def get_hitl_task_details(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await _get_owned_hitl_task(task_id, current_user, db)


@router.post("/hitl/tasks/{task_id}/approve")
async def approve_task(
    task_id: str,
    request: ApprovalRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    await _get_owned_hitl_task(task_id, current_user, db)
    correlation_id = str(uuid.uuid4())
    payload = {"user_id": str(current_user.id), "comment": request.comment}
    response = await _proxy_management_response(
        "POST",
        [f"/api/v1/hitl/tasks/{task_id}/approve", f"/api/hitl/approve/{task_id}"],
        payload=payload,
    )
    if response is None:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="hitl_approval_unavailable")
    if response.status_code >= 400:
        detail = None
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text[:300] or "hitl_approval_failed"
        raise HTTPException(status_code=response.status_code, detail=detail)
    data = response.json() if response.content else {}

    project_id = data.get("project_id")
    approved_at = data.get("approved_at")
    approved_by = data.get("approved_by") or str(current_user.id)
    if not project_id or not approved_at:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="hitl_approval_missing_context")

    try:
        await publish_hitl_approved_event(
            task_id=task_id,
            project_id=str(project_id),
            approved_by=approved_by,
            approved_at=str(approved_at),
            auto_deployed=bool(data.get("auto_deployed", True)),
            notes=request.comment,
            correlation_id=correlation_id,
        )
    except Exception as exc:
        logger.error(
            "HITL approval succeeded but event publication failed",
            extra={"task_id": task_id, "project_id": project_id, "correlation_id": correlation_id},
            exc_info=True,
        )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="hitl_approval_event_unavailable") from exc

    return {
        **data,
        "event_published": True,
        "correlation_id": correlation_id,
    }


@router.post("/hitl/tasks/{task_id}/reject")
async def reject_task(
    task_id: str,
    request: ApprovalRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    await _get_owned_hitl_task(task_id, current_user, db)
    payload = {"user_id": str(current_user.id), "comment": request.comment}
    response = await _proxy_management_response(
        "POST",
        [f"/api/v1/hitl/tasks/{task_id}/reject", f"/api/hitl/reject/{task_id}"],
        payload=payload,
    )
    if response is None:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="hitl_rejection_unavailable")
    if response.status_code >= 400:
        detail = None
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text[:300] or "hitl_rejection_failed"
        raise HTTPException(status_code=response.status_code, detail=detail)
    data = response.json() if response.content else {}
    return data


@router.post("/hitl/approve/{task_id}")
async def approve_task_legacy(
    task_id: str,
    request: ApprovalRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await approve_task(task_id=task_id, request=request, current_user=current_user, db=db)


@router.post("/hitl/reject/{task_id}")
async def reject_task_legacy(
    task_id: str,
    request: ApprovalRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await reject_task(task_id=task_id, request=request, current_user=current_user, db=db)


@router.get("/backlinks")
async def get_backlinks(
    projectId: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if projectId:
        project_ids = [str(_get_owned_project(projectId, current_user, db).id)]
    else:
        project_ids = _project_ids(_get_owned_projects(current_user, db))
    return _load_project_backlinks(db, project_ids)


@router.get("/projects/{project_id}/backlinks")
async def get_project_backlinks(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _get_owned_project(project_id, current_user, db)
    return _load_project_backlinks(db, [str(project.id)])


@router.post("/backlinks/analyze")
async def analyze_backlink(
    request: AnalyzeBacklinkRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = _resolve_project_for_private_flow(request.projectId, current_user, db)
    try:
        rows = await _analyze_backlinks_live(request.url)
    except Exception as exc:
        logger.warning("Backlink live analysis failed", extra={"url": request.url, "project_id": str(project.id), "error": str(exc)})
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="backlink_analysis_unavailable") from exc

    return rows


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
                "source": "semantic_service",
            },
        )
    )
    db.commit()
    return result


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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="keyword_not_found")

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


