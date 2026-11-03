import uuid
from datetime import datetime
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config.database_config import get_db
from config.logging_config import get_logger
from database.models import (
    Backlink,
    Crawl,
    Page,
    User,
)
from services.api_gateway.auth import (
    get_current_user,
)

from services.api_gateway.routes.protected.common import (
    _get_owned_project,
    _get_owned_projects,
    _project_ids,
    _resolve_project_for_private_flow,
    _serialize_datetime,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["Protected"])


class AnalyzeBacklinkRequest(BaseModel):
    url: str
    projectId: Optional[str] = None


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


def _normalize_domain(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        host = ""
    return host[4:] if host.startswith("www.") else host


def _extract_links_from_html(html: str) -> List[Dict[str, str]]:
    """Parse <a> tags and return structured link data with href, rel-type, and anchor text."""
    tag_re = re.compile(r'<a\b([^>]*)>(.*?)</a>', re.IGNORECASE | re.DOTALL)
    href_re = re.compile(r'''href=["']([^"'#]+)["']''', re.IGNORECASE)
    rel_re = re.compile(r'''rel=["']([^"']*)["']''', re.IGNORECASE)
    result: List[Dict[str, str]] = []
    for m in tag_re.finditer(html):
        attrs, inner = m.group(1), m.group(2)
        href_m = href_re.search(attrs)
        if not href_m:
            continue
        href = href_m.group(1).strip()
        if not href.startswith(("http://", "https://")):
            continue
        rel_m = rel_re.search(attrs)
        rel_val = rel_m.group(1).lower() if rel_m else ""
        link_type = "nofollow" if "nofollow" in rel_val else "dofollow"
        anchor = re.sub(r'<[^>]+>', '', inner).strip() or href
        result.append({"href": href, "type": link_type, "anchor": anchor[:200]})
    return result


async def _analyze_backlinks_live(url: str) -> List[Dict[str, Any]]:
    """Scan outbound external links on the target page.

    Note: this function discovers links going OUT from the target page, not
    inbound backlinks pointing to it. True backlink data requires a third-party
    index (e.g. Ahrefs, Moz) or Google Search Console.
    Domain authority is not computable without such an API and is omitted.
    """
    target_host = _normalize_domain(url)
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
    links = _extract_links_from_html(response.text)

    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for link in links:
        href = link["href"]
        source_host = _normalize_domain(href)
        if not source_host or source_host == target_host:
            continue
        if href in seen:
            continue
        seen.add(href)
        rows.append(
            {
                "id": str(uuid.uuid4()),
                "sourceUrl": href,
                "targetUrl": url,
                "type": link["type"],
                "domainAuthority": None,
                "anchorText": link["anchor"],
                "discoveredAt": datetime.utcnow().isoformat(),
            }
        )
        if len(rows) >= 25:
            break

    return rows


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
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Не удалось загрузить страницу для анализа ссылок. Проверьте URL и попробуйте снова.") from exc

    return rows
