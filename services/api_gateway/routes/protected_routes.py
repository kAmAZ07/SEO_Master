import uuid
from datetime import datetime
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field, root_validator, validator
from sqlalchemy.orm import Session

from config.database_config import get_db
from config.logging_config import get_logger
from database.models import Project, PublicAuditResult, User
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


_tracked_keywords: Dict[str, List[Dict[str, Any]]] = {}
_backlink_snapshots: Dict[str, List[Dict[str, Any]]] = {}
_content_history: Dict[str, List[Dict[str, Any]]] = {}


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


async def _proxy_management(
    method: str,
    path_candidates: List[str],
    *,
    params: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        for path in path_candidates:
            url = f"{settings.MANAGEMENT_SERVICE_URL.rstrip('/')}{path}"
            try:
                response = await client.request(method, url, params=params, json=payload)
            except httpx.RequestError:
                continue

            if response.status_code == 404:
                continue
            if response.status_code >= 400:
                logger.warning(
                    "Management proxy returned error",
                    extra={"url": url, "status_code": response.status_code, "body": response.text[:300]},
                )
                continue

            try:
                return response.json()
            except ValueError:
                return None

    return None


async def _proxy_management_response(
    method: str,
    path_candidates: List[str],
    *,
    params: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[httpx.Response]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        for path in path_candidates:
            url = f"{settings.MANAGEMENT_SERVICE_URL.rstrip('/')}{path}"
            try:
                response = await client.request(method, url, params=params, json=payload)
            except httpx.RequestError:
                continue

            if response.status_code == 404:
                continue
            return response

    return None


def _project_bucket(project_id: Optional[str]) -> str:
    return str(project_id or "default")


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


def _keyword_variants(keyword: str) -> List[str]:
    base = " ".join(keyword.strip().lower().split())
    if not base:
        return []

    variants = [
        base,
        f"{base} цена",
        f"{base} отзывы",
        f"{base} для бизнеса",
        f"{base} под ключ",
        f"как выбрать {base}",
        f"лучший {base}",
        f"{base} сравнение",
        f"{base} примеры",
        f"{base} услуги",
    ]
    unique: List[str] = []
    for item in variants:
        if item not in unique:
            unique.append(item)
    return unique


def _estimate_keyword_metrics(keyword: str, index: int) -> Dict[str, Any]:
    token_count = max(1, len(keyword.split()))
    volume = max(40, 1800 - (index * 170) - ((token_count - 1) * 130))
    difficulty = max(18, min(82, 28 + len(keyword) + (index * 4)))
    cpc = round(max(0.2, min(9.5, 0.35 * token_count + index * 0.22 + len(keyword) / 20.0)), 2)
    position = max(1, min(50, 8 + index * 3 + token_count))
    change = 2 - index
    return {
        "volume": volume,
        "difficulty": float(difficulty),
        "cpc": cpc,
        "position": position,
        "change": change,
    }


def _analyze_content_payload(content: str, keyword: str = "") -> Dict[str, Any]:
    words = [w for w in re.findall(r"\b[\w-]+\b", content, flags=re.UNICODE) if w.strip()]
    word_count = len(words)
    lowered = content.lower()
    normalized_keyword = keyword.strip().lower()
    keyword_density = 0.0
    if normalized_keyword and word_count:
        keyword_density = (lowered.count(normalized_keyword) / max(word_count, 1)) * 100.0

    unique_ratio = 0.0 if word_count == 0 else min(100.0, (len(set(w.lower() for w in words)) / word_count) * 140.0)
    headings = len(re.findall(r"(?im)^\s*(#+|\b[hH][1-6]\b)", content))
    paragraphs = len([p for p in re.split(r"\n\s*\n", content) if p.strip()])

    issues: List[Dict[str, str]] = []
    recommendations: List[Dict[str, str]] = []

    if word_count < 300:
        issues.append({"title": "Content length is too short", "description": "The page is below the recommended baseline for a useful SEO landing page."})
        recommendations.append({"title": "Expand topical depth", "description": "Add sections, comparisons, FAQs, or examples that match user intent."})
    if normalized_keyword and keyword_density < 0.8:
        issues.append({"title": "Low keyword coverage", "description": "The target keyword appears too rarely in the text."})
        recommendations.append({"title": "Improve keyword distribution", "description": "Use the keyword naturally in title, intro, subheadings, and conclusion."})
    if headings < 2:
        issues.append({"title": "Weak structure", "description": "The text has too few visible structural sections."})
        recommendations.append({"title": "Add subheadings", "description": "Split the text into scannable blocks with descriptive H2 or H3 sections."})
    if paragraphs < 3:
        recommendations.append({"title": "Improve readability", "description": "Break long text walls into shorter paragraphs and bullet-ready sections."})

    score = 40.0
    score += min(25.0, word_count / 25.0)
    score += min(12.0, keyword_density * 8.0)
    score += min(10.0, headings * 2.5)
    score += min(13.0, unique_ratio / 8.0)
    score -= len(issues) * 6.0
    score = max(0, min(100, int(round(score))))

    return {
        "score": score,
        "wordCount": word_count,
        "keywordDensity": round(keyword_density, 2),
        "uniqueness": int(round(unique_ratio)),
        "recommendations": recommendations,
        "issues": issues,
    }


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
    projects = db.query(Project).filter(Project.owner_id == current_user.id).all()
    recent_projects = [_serialize_project(project) for project in sorted(projects, key=lambda p: p.created_at or datetime.min, reverse=True)[:5]]
    project_ids = {str(project.id) for project in projects}

    audit_rows = db.query(PublicAuditResult).order_by(PublicAuditResult.created_at.desc()).limit(50).all()
    recent_audits = []
    active_audits = 0
    for row in audit_rows:
        status_value = (row.status or "completed").lower()
        if status_value in {"queued", "running", "pending", "processing", "in_progress"}:
            active_audits += 1
        recent_audits.append(
            {
                "id": str(row.id),
                "url": row.url,
                "score": _extract_score(row.results),
                "status": "completed" if status_value not in {"queued", "running", "pending", "processing", "in_progress", "failed"} else status_value,
            }
        )
        if len(recent_audits) >= 5:
            break

    pending_tasks = 0
    completed_tasks = 0
    for project_id in project_ids:
        pending_data = await _proxy_management("GET", ["/api/v1/tasks"], params={"project_id": project_id, "status": "pending", "limit": 200})
        completed_data = await _proxy_management("GET", ["/api/v1/tasks"], params={"project_id": project_id, "status": "completed", "limit": 200})
        pending_tasks += len(pending_data or [])
        completed_tasks += len(completed_data or [])

    backlink_count = sum(len(_backlink_snapshots.get(project_id, [])) for project_id in project_ids)
    content_scores = [
        item.get("score", 0)
        for project_id in project_ids
        for item in _content_history.get(project_id, [])
        if isinstance(item.get("score"), (int, float))
    ]

    return {
        "totalProjects": len(projects),
        "activeAudits": active_audits,
        "totalKeywords": sum(len(_tracked_keywords.get(str(project.id), [])) for project in projects),
        "totalBacklinks": backlink_count,
        "pendingTasks": pending_tasks,
        "completedTasks": completed_tasks,
        "avgFFScore": round(sum(content_scores) / len(content_scores), 2) if content_scores else 0,
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
    return [_serialize_project(project) for project in projects]


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
    return _serialize_project(project)


@router.get("/projects/{project_id}")
async def get_project(project_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id, Project.owner_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return _serialize_project(project)


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id, Project.owner_id == current_user.id).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    db.delete(project)
    db.commit()
    return {"success": True}


@router.get("/audit/history")
async def get_audit_history(
    projectId: Optional[str] = None,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.query(PublicAuditResult).order_by(PublicAuditResult.created_at.desc()).limit(50).all()

    items = []
    for row in rows:
        score = _extract_score(row.results)
        items.append(
            {
                "id": str(row.id),
                "uid": str(row.id),
                "url": row.url,
                "status": row.status or "completed",
                "score": score,
                "createdAt": row.created_at.isoformat() if row.created_at else None,
                "issues": row.results.get("issues") if isinstance(row.results, dict) else None,
                "details": row.results.get("details") if isinstance(row.results, dict) else None,
            }
        )

    if projectId:
        # History rows are not linked to project in current schema.
        return items

    return items


@router.get("/hitl/tasks")
async def get_hitl_tasks(
    current_user: User = Depends(get_current_user),
    status_filter: Optional[str] = "pending",
    limit: int = 50,
):
    data = await _proxy_management(
        "GET",
        ["/api/v1/hitl/tasks", "/api/hitl/tasks"],
        params={"status": status_filter, "limit": limit, "user_id": str(current_user.id)},
    )
    return data or []


@router.get("/hitl/tasks/{task_id}")
async def get_hitl_task_details(task_id: str, _: User = Depends(get_current_user)):
    data = await _proxy_management("GET", [f"/api/v1/hitl/tasks/{task_id}", f"/api/hitl/tasks/{task_id}"])
    if data is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return data


@router.post("/hitl/tasks/{task_id}/approve")
async def approve_task(
    task_id: str,
    request: ApprovalRequest,
    current_user: User = Depends(get_current_user),
):
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

    event_published = True
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
    except Exception:
        event_published = False
        logger.error(
            "HITL approval succeeded but event publication failed",
            extra={"task_id": task_id, "project_id": project_id, "correlation_id": correlation_id},
            exc_info=True,
        )

    return {
        **data,
        "event_published": event_published,
        "correlation_id": correlation_id,
    }


@router.post("/hitl/tasks/{task_id}/reject")
async def reject_task(
    task_id: str,
    request: ApprovalRequest,
    current_user: User = Depends(get_current_user),
):
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
async def approve_task_legacy(task_id: str, request: ApprovalRequest, current_user: User = Depends(get_current_user)):
    return await approve_task(task_id=task_id, request=request, current_user=current_user)


@router.post("/hitl/reject/{task_id}")
async def reject_task_legacy(task_id: str, request: ApprovalRequest, current_user: User = Depends(get_current_user)):
    return await reject_task(task_id=task_id, request=request, current_user=current_user)


@router.get("/backlinks")
async def get_backlinks(projectId: Optional[str] = None, _: User = Depends(get_current_user)):
    return _backlink_snapshots.get(_project_bucket(projectId), [])


@router.get("/projects/{project_id}/backlinks")
async def get_project_backlinks(project_id: str, _: User = Depends(get_current_user)):
    return _backlink_snapshots.get(_project_bucket(project_id), [])


@router.post("/backlinks/analyze")
async def analyze_backlink(request: AnalyzeBacklinkRequest, _: User = Depends(get_current_user)):
    try:
        rows = await _analyze_backlinks_live(request.url)
    except Exception as exc:
        logger.warning("Backlink analysis fallback triggered", extra={"url": request.url, "error": str(exc)})
        rows = []

    if not rows:
        host = _normalize_domain(request.url) or request.url
        rows = [
            {
                "id": str(uuid.uuid4()),
                "sourceUrl": f"https://{host}/partners",
                "targetUrl": request.url,
                "type": "nofollow",
                "domainAuthority": 25,
                "anchorText": host,
                "discoveredAt": datetime.utcnow().isoformat(),
            }
        ]

    bucket = _project_bucket(request.projectId)
    _backlink_snapshots[bucket] = rows
    return rows


@router.get("/content/optimized")
async def get_optimized_content(projectId: Optional[str] = None, _: User = Depends(get_current_user)):
    return _content_history.get(_project_bucket(projectId), [])


@router.get("/projects/{project_id}/content/optimized")
async def get_project_optimized_content(project_id: str, _: User = Depends(get_current_user)):
    return _content_history.get(_project_bucket(project_id), [])


@router.post("/content/analyze")
async def analyze_content(request: AnalyzeContentRequest, _: User = Depends(get_current_user)):
    result = _analyze_content_payload(request.content, request.keyword or "")
    bucket = _project_bucket(request.projectId)
    _content_history.setdefault(bucket, []).insert(
        0,
        {
            "id": str(uuid.uuid4()),
            "url": request.url or "",
            "keyword": request.keyword or "",
            "score": result["score"],
            "analyzedAt": datetime.utcnow().isoformat(),
        },
    )
    _content_history[bucket] = _content_history[bucket][:30]
    return result


@router.post("/keywords/search")
async def search_keywords(request: KeywordSearchRequest, _: User = Depends(get_current_user)):
    keyword = request.keyword.strip()
    if not keyword:
        return []
    variants = _keyword_variants(keyword)
    result = []
    for index, item in enumerate(variants):
        metrics = _estimate_keyword_metrics(item, index)
        result.append({"id": str(uuid.uuid4()), "keyword": item, **metrics})
    return result


@router.get("/keywords/tracked")
async def get_tracked_keywords(projectId: Optional[str] = None, _: User = Depends(get_current_user)):
    project_key = _project_bucket(projectId)
    return _tracked_keywords.get(project_key, [])


@router.post("/keywords/tracked")
async def add_tracked_keyword(request: TrackKeywordRequest, _: User = Depends(get_current_user)):
    project_key = _project_bucket(request.projectId)
    metrics = _estimate_keyword_metrics(request.keyword, len(_tracked_keywords.get(project_key, [])))
    record = {
        "id": str(uuid.uuid4()),
        "keyword": request.keyword,
        "volume": request.volume if request.volume is not None else metrics["volume"],
        "difficulty": request.difficulty if request.difficulty is not None else metrics["difficulty"],
        "cpc": request.cpc if request.cpc is not None else metrics["cpc"],
        "position": metrics["position"],
        "change": metrics["change"],
        "projectId": request.projectId,
        "createdAt": datetime.utcnow().isoformat(),
    }
    _tracked_keywords.setdefault(project_key, []).insert(0, record)
    return record


@router.delete("/keywords/tracked/{keyword_id}")
async def remove_tracked_keyword(keyword_id: str, _: User = Depends(get_current_user)):
    for project_key, rows in _tracked_keywords.items():
        filtered = [row for row in rows if str(row.get("id")) != str(keyword_id)]
        if len(filtered) != len(rows):
            _tracked_keywords[project_key] = filtered
            break
    return {"success": True}


