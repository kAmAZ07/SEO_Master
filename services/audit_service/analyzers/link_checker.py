"""Проверка внутренних ссылок: HEAD с ограниченным GET-фолбэком, различие 404 / запрет / сбой сети."""
from __future__ import annotations

from urllib.parse import urlsplit

from shared.netpolicy import NetPolicyError

from services.audit_service.config import settings
from services.audit_service.netio import AuditFetcher, BudgetExhausted
from services.audit_service.public_policy import PUBLIC_MAX_LINK_PROBES

_HEAD_UNSUPPORTED = frozenset({405, 501})
_GET_FALLBACK_BODY_LIMIT = 256 * 1024


def _is_internal(root_url: str, candidate: str) -> bool:
    r = urlsplit(root_url)
    c = urlsplit(candidate)
    return (r.scheme, r.netloc.lower()) == (c.scheme, c.netloc.lower())


def _classify(url: str, status: int) -> dict | None:
    if status in (404, 410):
        return {"code": "broken_link_404", "severity": "medium", "confidence": "high", "details": {"url": url, "status": status}}
    if 500 <= status <= 599:
        return {"code": "broken_link_server_error", "severity": "low", "confidence": "medium", "details": {"url": url, "status": status}}
    if status in (401, 403, 429):
        return {"code": "link_check_blocked", "severity": "info", "confidence": "high", "details": {"url": url, "status": status}}
    return None


async def check_links_404(
    root_url: str,
    pages: list[dict],
    fetcher: AuditFetcher | None = None,
    limit: int | None = None,
) -> tuple[list[dict], int]:
    """Возвращает (findings, число проверенных ссылок). Порядок ссылок и findings детерминирован."""
    fetcher = fetcher or AuditFetcher.create()
    limit = min(limit or settings.max_internal_link_checks, PUBLIC_MAX_LINK_PROBES)

    crawled_status = {page.get("url"): page.get("status_code") for page in pages}
    findings: list[dict] = []
    # Уже обойденные страницы: их статус известен без дополнительного запроса.
    for url, status in crawled_status.items():
        if isinstance(status, int) and (finding := _classify(url, status)) and finding["code"] == "broken_link_404":
            findings.append(finding)

    candidates: list[str] = []
    seen: set[str] = set()
    for page in pages:
        for link in page.get("links") or []:
            if link in seen or link in crawled_status or not _is_internal(root_url, link):
                continue
            seen.add(link)
            candidates.append(link)
            if len(candidates) >= limit:
                break
        if len(candidates) >= limit:
            break

    checked = 0
    for url in candidates:
        try:
            response = await fetcher.fetch(url, method="HEAD", kind="aux")
            if response.status in _HEAD_UNSUPPORTED:
                response = await fetcher.fetch(url, method="GET", kind="aux", max_body_bytes=_GET_FALLBACK_BODY_LIMIT)
        except BudgetExhausted:
            break
        except NetPolicyError as exc:
            if exc.code == "response_too_large":  # страница ответила, тело нам не нужно
                checked += 1
                continue
            checked += 1
            findings.append({"code": "link_check_error", "severity": "info", "confidence": "medium",
                             "details": {"url": url, "reason": exc.code}})
            continue
        checked += 1
        finding = _classify(url, response.status)
        if finding:
            findings.append(finding)

    return findings, checked
