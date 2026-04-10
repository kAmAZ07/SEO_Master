import uuid
from datetime import datetime, timezone
from typing import Any

from services.semantic_service.analysis.content_gap import analyze_content_gap
from services.semantic_service.analysis.keyword_coverage import keyword_coverage
from services.semantic_service.analysis.semantic_distance import serp_minus_10_distance
from services.semantic_service.db.models import SemanticAnalysisRow
from services.semantic_service.db.session import get_session


def build_target_text(content_text: str | None = None, pages: list[dict] | None = None) -> str:
    if isinstance(content_text, str) and content_text.strip():
        return content_text.strip()

    parts: list[str] = []
    for page in (pages or [])[:10]:
        if not isinstance(page, dict):
            continue
        parts.extend(
            [
                str(page.get("title") or ""),
                str(page.get("description") or ""),
                str(page.get("h1") or ""),
            ]
        )

    return "\n".join(part for part in parts if part).strip()


def _normalize_keywords(values: list[Any] | None) -> list[str]:
    normalized: list[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text:
            normalized.append(text)
    return normalized


def _normalize_serp_texts(values: list[Any] | None) -> list[str]:
    normalized: list[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text:
            normalized.append(text)
    return normalized


async def create_semantic_analysis(
    *,
    root_url: str,
    project_id: str | None = None,
    audit_id: str | None = None,
    analysis_id: str | None = None,
    mode: str | None = None,
    content_text: str | None = None,
    pages: list[dict] | None = None,
    keywords: list[Any] | None = None,
    serp_top10_texts: list[Any] | None = None,
) -> dict:
    target_text = build_target_text(content_text=content_text, pages=pages)
    normalized_keywords = _normalize_keywords(keywords)
    normalized_serp = _normalize_serp_texts(serp_top10_texts)

    unavailable: list[str] = []
    if not normalized_keywords:
        unavailable.append("keywords")
    if not normalized_serp:
        unavailable.append("serp_top10_texts")

    kc = keyword_coverage(target_text, normalized_keywords)
    dist = serp_minus_10_distance(target_text, normalized_serp)
    gap = analyze_content_gap(target_text, normalized_serp, normalized_keywords)

    persisted_analysis_id = str(analysis_id or f"analysis-{uuid.uuid4()}")
    inputs = {
        "keywords_count": len(normalized_keywords),
        "serp_n": len(normalized_serp),
        "mode": mode or "unknown",
        "audit_id": audit_id,
        "content_length": len(target_text),
        "unavailable": unavailable,
    }

    async with get_session() as session:
        session.add(
            SemanticAnalysisRow(
                analysis_id=persisted_analysis_id,
                project_id=project_id,
                root_url=root_url,
                created_at=datetime.now(timezone.utc),
                content_gap=gap,
                semantic_distance=dist,
                keyword_coverage=kc,
                inputs=inputs,
            )
        )
        await session.commit()

    return {
        "analysis_id": persisted_analysis_id,
        "project_id": project_id,
        "root_url": root_url,
        "content_gap": gap,
        "semantic_distance": dist,
        "keyword_coverage": kc,
        "inputs": inputs,
    }
