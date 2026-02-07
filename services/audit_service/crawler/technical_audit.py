import ipaddress
import socket
from urllib.parse import urlparse

from services.audit_service.crawler.public_crawler import crawl_public
from services.audit_service.analyzers.meta_checker import check_meta
from services.audit_service.analyzers.link_checker import check_links_404
from services.audit_service.analyzers.schema_validator import validate_jsonld
from services.audit_service.analyzers.robots_checker import check_robots
from services.audit_service.analyzers.cwv_analyzer import analyze_cwv
from services.audit_service.config import settings
from services.audit_service.db.session import get_session
from services.audit_service.db.models import PublicAuditResult


def _is_private_ip(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for family, _, _, _, sockaddr in infos:
        ip = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip)
            if addr.is_private or addr.is_loopback or addr.is_link_local:
                return True
        except ValueError:
            continue
    return False


def _precheck_url(root_url: str) -> list[dict]:
    findings = []
    p = urlparse(root_url)
    if p.scheme not in ("http", "https") or not p.netloc:
        findings.append({"code": "invalid_url", "severity": "high", "confidence": "high", "details": {"root_url": root_url}})
        return findings
    if _is_private_ip(p.hostname or ""):
        findings.append({"code": "unsafe_target", "severity": "high", "confidence": "high", "details": {"host": p.hostname}})
    return findings


async def run_public_audit_pipeline(audit_id: str) -> dict:
    async with get_session() as session:
        row = await session.get(PublicAuditResult, audit_id)
        if row is None:
            raise ValueError("audit_not_found")
        root_url = row.root_url
        options = row.options or {}

    findings = []
    pre = _precheck_url(root_url)
    findings.extend(pre)
    if any(f["code"] in ("invalid_url", "unsafe_target") for f in pre):
        summary = {"coverage": {"attempted": 0, "processed": 0, "max_pages": int(options.get("max_pages", 10))}, "precheck_failed": True}
        return {"root_url": root_url, "summary": summary, "findings": findings, "pages": []}

    max_pages = int(options.get("max_pages", 10))
    js_render = bool(options.get("js_render", False))
    respect_robots = bool(options.get("respect_robots", True))
    timeout = float(options.get("timeout", settings.default_timeout_s))

    robots = await check_robots(root_url=root_url)
    if respect_robots and robots.get("blocked_root", False):
        findings.append({"code": "blocked_by_robots", "severity": "medium", "confidence": "high", "details": robots})
        summary = {"coverage": {"attempted": 0, "processed": 0, "max_pages": max_pages}, "blocked_pages_count": 0, "precheck_failed": False}
        return {"root_url": root_url, "summary": summary, "findings": findings, "pages": []}

    crawled = await crawl_public(root_url=root_url, max_pages=max_pages, js_render=js_render, timeout_s=timeout, respect_robots=respect_robots)
    pages = crawled["pages"]
    summary = crawled["summary"]

    for p in pages:
        meta_f = check_meta(p.get("url"), p.get("title"), p.get("description"), p.get("h1"))
        findings.extend(meta_f)

        html = p.get("html")
        if html:
            schema_f = validate_jsonld(p.get("url"), html)
            findings.extend(schema_f)

    link_findings, links_checked = await check_links_404(root_url=root_url, pages=pages)
    findings.extend(link_findings)
    summary["links_checked"] = links_checked

    cwv = await analyze_cwv(root_url=root_url)
    if cwv:
        summary["cwv"] = cwv.get("summary", {})
        findings.extend(cwv.get("findings", []))
    else:
        findings.append({"code": "cwv_unavailable", "severity": "info", "confidence": "high", "details": {"reason": "psi_api_key_missing_or_error"}})

    if summary.get("spa_detected") and not js_render:
        findings.append({"code": "spa_detected_enable_js_render", "severity": "medium", "confidence": "medium", "details": {"root_url": root_url}})

    if summary.get("anti_bot_detected"):
        findings.append({"code": "anti_bot_detected", "severity": "medium", "confidence": "medium", "details": {"hint": "reduce_concurrency_or_use_js_render"}})

    return {"root_url": root_url, "summary": summary, "findings": findings, "pages": pages}