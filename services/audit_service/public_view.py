"""Публичное представление результата аудита.

Наружу отдаётся только явно перечисленное: без HTML страниц, внутренних текстов ошибок,
служебных полей и данных других аудитов. Всё, что не входит в списки ниже, отбрасывается.
"""
from __future__ import annotations

from datetime import datetime, timezone

from services.audit_service.public_policy import FINAL_STATUSES, PUBLIC_RETENTION

_FINDING_FIELDS = ("code", "title", "description", "recommendation", "category", "severity", "confidence", "affected_count")
_EVIDENCE_FIELDS = ("url", "root_url", "host", "status", "status_code", "canonical", "count", "len", "value", "value_ms", "reason", "source")
_PAGE_FIELDS = ("url", "final_url", "status_code", "title", "description", "h1", "h1_count", "error")
_SUMMARY_FIELDS = (
    "coverage", "score", "score_breakdown", "score_explanation", "issue_counts", "checks", "partial_reasons",
    "robots", "sitemap", "psi", "cwv", "links_checked", "progress", "error_code", "precheck_failed",
    "timeout_count", "ssl_errors_count", "redirect_loops_count", "server_errors_count", "blocked_pages_count",
    "anti_bot_detected", "spa_detected",
)
_ROBOTS_FIELDS = ("robots_url", "available", "state", "blocked_root", "status_code")
_SITEMAP_FIELDS = ("sitemap_url", "available", "state", "status_code", "url_count", "truncated")
_MAX_TEXT = 500


def _text(value):
    if isinstance(value, str):
        return value[:_MAX_TEXT]
    return value


def _pick(source: dict, fields) -> dict:
    return {name: _text(source[name]) for name in fields if name in source and not isinstance(source[name], (dict, list))}


def sanitize_finding(finding: dict) -> dict:
    out = _pick(finding, _FINDING_FIELDS)
    details = finding.get("details") if isinstance(finding.get("details"), dict) else {}
    out["evidence"] = _pick(details, _EVIDENCE_FIELDS)
    return out


def sanitize_page(page: dict) -> dict:
    return _pick(page, _PAGE_FIELDS)


def sanitize_summary(summary: dict) -> dict:
    out: dict = {}
    for name in _SUMMARY_FIELDS:
        if name not in summary:
            continue
        value = summary[name]
        if name == "robots" and isinstance(value, dict):
            value = _pick(value, _ROBOTS_FIELDS)
        elif name == "sitemap" and isinstance(value, dict):
            value = _pick(value, _SITEMAP_FIELDS)
        elif name == "cwv" and isinstance(value, dict):
            value = {k: v for k, v in value.items() if isinstance(v, (str, int, float, bool, type(None)))}
        elif name == "criteria":
            continue
        out[name] = value
    criteria = summary.get("criteria")
    if isinstance(criteria, list):
        out["criteria"] = [
            {k: c.get(k) for k in ("key", "title", "score", "status", "issue_count", "info_count") if isinstance(c, dict)}
            for c in criteria
            if isinstance(c, dict)
        ]
    return out


def expires_at(created_at: datetime) -> datetime:
    return created_at + PUBLIC_RETENTION


def is_expired(created_at: datetime, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return now >= expires_at(created_at)


def build_public_view(row) -> dict:
    """Безопасное представление строки публичного аудита. Результат отдаётся только для завершённых заданий."""
    summary = row.summary if isinstance(row.summary, dict) else {}
    findings = row.findings if isinstance(row.findings, list) else []
    problems = [f for f in findings if isinstance(f, dict)]
    finished = row.status in FINAL_STATUSES and row.status not in ("failed", "cancelled")
    top = []
    if finished:
        from services.audit_service.crawler.technical_audit import select_top_findings

        top = [sanitize_finding(f) for f in select_top_findings(problems)]
    progress = summary.get("progress") if isinstance(summary.get("progress"), dict) else {}
    created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
    return {
        "audit_id": row.audit_id,
        "root_url": _text(row.root_url),
        "mode": "public",
        "status": row.status,
        "stage": progress.get("stage"),
        "progress": {k: progress[k] for k in ("pages_done", "pages_max") if k in progress},
        "summary": sanitize_summary(summary),
        "top_findings": top,
        "findings_count": len([f for f in problems if str(f.get("severity", "info")).lower() != "info"]) if finished else 0,
        "pages": [sanitize_page(p) for p in (row.pages or []) if isinstance(p, dict)] if finished else [],
        "created_at": created.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "expires_at": expires_at(created).isoformat(),
    }
