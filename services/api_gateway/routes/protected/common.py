from typing import Any, Dict, Iterable, List, Optional

import httpx
from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from config.logging_config import get_logger
from database.models import (
    Backlink,
    Crawl,
    CrawlResult,
    FFScore,
    Page,
    Project,
    PublicAuditResult,
    SemanticEvent,
    User,
)
from services.api_gateway.config import settings
from services.api_gateway.projects import access
from services.api_gateway.projects.errors import ProjectError
from services.api_gateway.projects.http import to_http_exception
from services.api_gateway.projects.verification import DomainVerificationService, VerificationConfig, VerificationState
from shared.projects.roles import Permission

logger = get_logger(__name__)


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
        "description": project.description,
        "scope": {"origin": project.origin, "pathPrefix": project.path_prefix},
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


def _serialize_datetime(value: Any) -> Optional[str]:
    return value.isoformat() if value else None


def _get_owned_project(
    project_id: str,
    current_user: User,
    db: Session,
    *,
    not_found_detail: str = "Project not found",
    permission: Permission = Permission.READ,
) -> Project:
    """Проект, доступный пользователю через членство в workspace. Чужой проект — 404, как и несуществующий."""
    try:
        return access.resolve_project(db, str(current_user.id), project_id, permission)
    except ProjectError as exc:
        raise to_http_exception(exc, not_found_detail=not_found_detail) from exc


def _get_owned_projects(current_user: User, db: Session) -> List[Project]:
    return access.list_projects(db, str(current_user.id))


def _resolve_project_for_private_flow(
    project_id: Optional[str],
    current_user: User,
    db: Session,
) -> Project:
    if project_id:
        return _get_owned_project(project_id, current_user, db)

    projects = access.list_projects(db, str(current_user.id))
    if not projects:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сначала создайте проект, чтобы использовать этот инструмент.")
    return projects[0]


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


async def _no_network(_url: str):
    raise RuntimeError("the state summary must not use the network")


def _serialize_verification(state: VerificationState) -> Dict[str, Any]:
    return {
        "state": state.state,
        "method": "http_challenge",
        "scope": {"origin": state.scope_origin, "pathPrefix": state.scope_path_prefix},
        "freshDays": state.fresh_days,
        "verifiedAt": _serialize_datetime(state.verified_at),
        "freshUntil": _serialize_datetime(state.fresh_until),
        "challengeExpiresAt": _serialize_datetime(state.challenge_expires_at),
        "attempts": state.attempts,
        "lastErrorCode": state.last_error_code,
        "lastStatus": state.last_status,
    }


def _project_verification(project: Project, db: Session) -> Dict[str, Any]:
    service = DomainVerificationService(db, _no_network, VerificationConfig.from_settings(settings))
    return _serialize_verification(service.state(project))


def _serialize_project_private(project: Project, db: Session) -> Dict[str, Any]:
    project_id = str(project.id)
    tracked_keywords = _load_tracked_keywords(db, [project_id])
    backlink_count = _count_project_backlinks(db, [project_id])
    return {
        **_serialize_project(project),
        "verification": _project_verification(project, db),
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
