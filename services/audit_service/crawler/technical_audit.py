import asyncio
import ipaddress
import socket
from typing import Type
from urllib.parse import urlparse

from sqlalchemy import select

from services.audit_service.crawler.public_crawler import crawl_public
from services.audit_service.analyzers.meta_checker import check_meta
from services.audit_service.analyzers.link_checker import check_links_404
from services.audit_service.analyzers.schema_validator import validate_jsonld
from services.audit_service.analyzers.robots_checker import check_robots
from services.audit_service.analyzers.sitemap_checker import check_sitemap
from services.audit_service.analyzers.cwv_analyzer import analyze_cwv
from services.audit_service.config import settings
from services.audit_service.db.session import get_session
from services.audit_service.db.models import CrawlResult, PublicAuditResult


FINDING_CATALOG = {
    "invalid_url": {
        "title": "Invalid audit URL",
        "description": "The target URL is malformed or missing the required scheme and host.",
        "recommendation": "Use a full public URL starting with http:// or https://.",
        "category": "precheck",
    },
    "unsafe_target": {
        "title": "Private or internal target",
        "description": "The requested host resolves to a private, loopback, or link-local address.",
        "recommendation": "Run the audit against a publicly accessible domain instead of an internal address.",
        "category": "precheck",
    },
    "blocked_by_robots": {
        "title": "Crawl blocked by robots.txt",
        "description": "The root path is disallowed for generic crawlers, so the audit cannot inspect the site safely.",
        "recommendation": "Allow the relevant crawler or audit the site in an environment where robots restrictions are relaxed.",
        "category": "crawlability",
    },
    "sitemap_missing": {
        "title": "Sitemap is missing or unavailable",
        "description": "The crawler could not access a valid sitemap.xml file on the target site.",
        "recommendation": "Publish a valid sitemap.xml file and ensure it is reachable without authentication.",
        "category": "crawlability",
    },
    "title_missing": {
        "title": "Missing page title",
        "description": "The page does not contain a title tag, so search engines receive no clear page label.",
        "recommendation": "Add a unique, descriptive title tag for the page.",
        "category": "metadata",
    },
    "title_too_short": {
        "title": "Title is too short",
        "description": "The title is present but too short to clearly describe the page intent in search results.",
        "recommendation": "Expand the title to roughly 10-70 characters while keeping it specific and readable.",
        "category": "metadata",
    },
    "title_too_long": {
        "title": "Title is too long",
        "description": "The title is likely to be truncated in search results and may dilute the main topic.",
        "recommendation": "Shorten the title to roughly 10-70 characters and keep the primary topic near the beginning.",
        "category": "metadata",
    },
    "description_missing": {
        "title": "Missing meta description",
        "description": "The page does not provide a meta description, so search engines may generate an uncontrolled snippet.",
        "recommendation": "Add a concise meta description explaining the page value and intent.",
        "category": "metadata",
    },
    "description_too_short": {
        "title": "Meta description is too short",
        "description": "The description is unlikely to provide enough context to attract qualified clicks from search results.",
        "recommendation": "Expand the meta description to roughly 50-160 characters.",
        "category": "metadata",
    },
    "description_too_long": {
        "title": "Meta description is too long",
        "description": "The description may be truncated in search snippets, weakening the message shown to users.",
        "recommendation": "Reduce the description to roughly 50-160 characters and keep the value proposition upfront.",
        "category": "metadata",
    },
    "h1_missing": {
        "title": "Missing H1 heading",
        "description": "The page lacks a primary heading, which weakens topical clarity for users and search engines.",
        "recommendation": "Add one clear H1 that reflects the page subject.",
        "category": "content_structure",
    },
    "h1_too_short": {
        "title": "H1 heading is too short",
        "description": "The primary heading is too terse to communicate the page topic clearly.",
        "recommendation": "Rewrite the H1 to describe the page topic in a fuller, user-facing phrase.",
        "category": "content_structure",
    },
    "jsonld_missing": {
        "title": "Structured data not found",
        "description": "No JSON-LD markup was detected on the page.",
        "recommendation": "Add relevant schema.org markup where it helps search engines understand the page entity or content type.",
        "category": "structured_data",
    },
    "jsonld_empty": {
        "title": "Empty structured data block",
        "description": "A JSON-LD script is present but contains no usable payload.",
        "recommendation": "Populate the structured data block with valid schema.org JSON-LD.",
        "category": "structured_data",
    },
    "jsonld_invalid_json": {
        "title": "Invalid structured data JSON",
        "description": "The JSON-LD block contains invalid JSON syntax and cannot be parsed.",
        "recommendation": "Fix the JSON syntax in the structured data block and validate it before publishing.",
        "category": "structured_data",
    },
    "jsonld_invalid_structure": {
        "title": "Unsupported structured data shape",
        "description": "The structured data block uses an unexpected structure that cannot be interpreted reliably.",
        "recommendation": "Ensure the JSON-LD payload is an object or list of objects with schema.org fields.",
        "category": "structured_data",
    },
    "jsonld_missing_context": {
        "title": "Structured data misses @context",
        "description": "The JSON-LD object has no @context, so schema consumers may interpret it incorrectly.",
        "recommendation": "Add \"@context\": \"https://schema.org\" to the structured data object.",
        "category": "structured_data",
    },
    "jsonld_missing_type": {
        "title": "Structured data misses @type",
        "description": "The JSON-LD object has no explicit type, so the entity represented by the markup is unclear.",
        "recommendation": "Add an appropriate schema.org @type for the page or entity.",
        "category": "structured_data",
    },
    "broken_link_404": {
        "title": "Broken internal link",
        "description": "An internal URL returned 404 during validation, creating a dead-end for crawlers and users.",
        "recommendation": "Fix the destination URL, restore the page, or remove the broken internal link.",
        "category": "links",
    },
    "link_check_blocked": {
        "title": "Link check was blocked",
        "description": "The server blocked verification of an internal URL, so the audit could not confirm its status.",
        "recommendation": "Review rate-limiting, bot protection, or permissions affecting crawler access.",
        "category": "links",
    },
    "link_check_error": {
        "title": "Link check failed",
        "description": "The crawler could not validate an internal URL because the request failed.",
        "recommendation": "Inspect the affected endpoint and retry after resolving connectivity or TLS issues.",
        "category": "links",
    },
    "cwv_lcp_poor": {
        "title": "Largest Contentful Paint is poor",
        "description": "The main content becomes visible too slowly, which hurts perceived loading performance.",
        "recommendation": "Optimize server response time, render-blocking assets, and large above-the-fold resources.",
        "category": "performance",
    },
    "cwv_fid_poor": {
        "title": "First Input Delay is poor",
        "description": "The page is slow to react to user input, often due to heavy JavaScript execution.",
        "recommendation": "Reduce long main-thread tasks and defer non-critical JavaScript.",
        "category": "performance",
    },
    "cwv_cls_poor": {
        "title": "Cumulative Layout Shift is poor",
        "description": "Visible elements move unexpectedly during load, creating a frustrating experience.",
        "recommendation": "Reserve space for media and dynamic elements and avoid injecting layout-changing content above the fold.",
        "category": "performance",
    },
    "cwv_unavailable": {
        "title": "Core Web Vitals were not collected",
        "description": "The audit could not fetch PageSpeed data for this URL, so performance scoring is partially estimated.",
        "recommendation": "Provide a valid PageSpeed API key or rerun the audit when external performance data is available.",
        "category": "performance",
    },
    "backlinks_unavailable": {
        "title": "Backlink data was not collected",
        "description": "The audit could not fetch backlink data from the configured external source.",
        "recommendation": "Check the external integration credentials and rerun the full audit.",
        "category": "links",
    },
    "spa_detected_enable_js_render": {
        "title": "SPA-like rendering detected",
        "description": "The page appears to rely heavily on client-side rendering, but JavaScript rendering was disabled for this crawl.",
        "recommendation": "Rerun the audit with JavaScript rendering enabled or add server-side rendering/prerendering.",
        "category": "rendering",
    },
    "anti_bot_detected": {
        "title": "Anti-bot protection detected",
        "description": "The target site responded in a way that suggests bot protection or request throttling.",
        "recommendation": "Reduce crawl aggressiveness, allowlist the crawler, or audit from an approved environment.",
        "category": "crawlability",
    },
    "page_timeout": {
        "title": "Page request timed out",
        "description": "The crawler could not complete the request within the configured timeout.",
        "recommendation": "Investigate server latency and large blocking resources on the affected page.",
        "category": "availability",
    },
    "page_fetch_error": {
        "title": "Page fetch failed",
        "description": "The crawler encountered a transport or rendering error while trying to fetch the page.",
        "recommendation": "Check server logs, TLS configuration, and bot access rules for the affected URL.",
        "category": "availability",
    },
    "page_server_error": {
        "title": "Page returned a server error",
        "description": "The page responded with a 5xx status code, which prevents reliable crawling and indexing.",
        "recommendation": "Resolve the server-side error for the affected URL and re-run the audit.",
        "category": "availability",
    },
}


SEVERITY_WEIGHTS = {
    "critical": 28.0,
    "high": 18.0,
    "medium": 4.0,
    "low": 1.5,
    "info": 0.0,
}
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}
NON_PROBLEM_CODES = {"audit_score_explanation"}


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
    parsed = urlparse(root_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        findings.append({"code": "invalid_url", "severity": "high", "confidence": "high", "details": {"root_url": root_url}})
        return findings
    if _is_private_ip(parsed.hostname or ""):
        findings.append({"code": "unsafe_target", "severity": "high", "confidence": "high", "details": {"host": parsed.hostname}})
    return findings


def _issue_key(finding: dict) -> tuple[str, str]:
    details = finding.get("details") or {}
    return (
        str(finding.get("code") or ""),
        str(details.get("url") or details.get("host") or details.get("root_url") or ""),
    )


def _decorate_finding(finding: dict) -> dict:
    decorated = dict(finding)
    code = str(decorated.get("code") or "")
    catalog_item = FINDING_CATALOG.get(code, {})
    details = decorated.get("details") or {}
    target = details.get("url") or details.get("root_url") or details.get("host") or ""

    if catalog_item:
        decorated.setdefault("title", catalog_item["title"])
        decorated.setdefault("description", catalog_item["description"])
        decorated.setdefault("recommendation", catalog_item["recommendation"])
        decorated.setdefault("category", catalog_item["category"])
    else:
        decorated.setdefault("title", code.replace("_", " ").title())
        decorated.setdefault("description", code.replace("_", " "))
        decorated.setdefault("recommendation", "Review the affected page and fix the issue before re-running the audit.")
        decorated.setdefault("category", "technical")

    if target:
        decorated["description"] = f"{decorated['description']} Affected target: {target}."

    return decorated


def _build_page_level_findings(pages: list[dict]) -> list[dict]:
    findings: list[dict] = []
    for page in pages:
        url = page.get("url")
        error = page.get("error")
        status_code = page.get("status_code")

        if error == "timeout":
            findings.append({
                "code": "page_timeout",
                "severity": "high",
                "confidence": "high",
                "details": {"url": url},
            })
        elif error:
            findings.append({
                "code": "page_fetch_error",
                "severity": "high",
                "confidence": "medium",
                "details": {"url": url, "error": error},
            })

        if isinstance(status_code, int) and 500 <= status_code <= 599:
            findings.append({
                "code": "page_server_error",
                "severity": "high",
                "confidence": "high",
                "details": {"url": url, "status_code": status_code},
            })
    return findings


def _compute_score(summary: dict, findings: list[dict]) -> dict:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for finding in findings:
        severity = str(finding.get("severity") or "info").lower()
        if severity not in counts:
            severity = "info"
        counts[severity] += 1

    coverage = summary.get("coverage") or {}
    processed_pages = int(coverage.get("processed") or 0)
    attempted_pages = int(coverage.get("attempted") or 0)
    max_pages = max(1, int(coverage.get("max_pages") or 1))
    crawl_completion = min(1.0, processed_pages / max_pages)

    penalty = sum(counts[level] * weight for level, weight in SEVERITY_WEIGHTS.items())
    coverage_bonus = min(8.0, processed_pages * 1.5)
    crawl_bonus = min(4.0, attempted_pages * 0.5)
    score = round(max(0.0, min(100.0, 100.0 - penalty + coverage_bonus + crawl_bonus)))

    return {
        "score": int(score),
        "issue_counts": counts,
        "score_breakdown": {
            "base_score": 100,
            "penalty_points": round(penalty, 2),
            "coverage_bonus": round(coverage_bonus, 2),
            "crawl_bonus": round(crawl_bonus, 2),
            "crawl_completion_ratio": round(crawl_completion, 2),
        },
        "score_explanation": (
            f"Processed pages: {processed_pages} | Critical: {counts['critical']} | "
            f"High: {counts['high']} | Medium: {counts['medium']} | Low: {counts['low']}"
        ),
    }


def select_top_findings(findings: list, limit: int = 5) -> list[dict]:
    if limit <= 0:
        return []

    ranked: list[tuple[int, int, int, dict]] = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            continue

        code = str(finding.get("code") or "")
        severity = str(finding.get("severity") or "info").lower()
        if code in NON_PROBLEM_CODES or severity == "info":
            continue

        confidence = str(finding.get("confidence") or "medium").lower()
        ranked.append(
            (
                SEVERITY_RANK.get(severity, SEVERITY_RANK["info"]),
                CONFIDENCE_RANK.get(confidence, CONFIDENCE_RANK["medium"]),
                index,
                dict(finding),
            )
        )

    ranked.sort(key=lambda item: item[:3])
    return [finding for _, _, _, finding in ranked[:limit]]


def _build_score_explanation_finding(summary: dict) -> dict:
    breakdown = summary.get("score_breakdown") or {}
    counts = summary.get("issue_counts") or {}
    coverage = summary.get("coverage") or {}
    cwv = summary.get("cwv") or {}
    cwv_text = "CWV not available"
    if cwv:
        cwv_text = (
            f"LCP: {cwv.get('LCP_grade', 'unknown')}, "
            f"FID: {cwv.get('FID_grade', 'unknown')}, "
            f"CLS: {cwv.get('CLS_grade', 'unknown')}"
        )

    return {
        "code": "audit_score_explanation",
        "severity": "info",
        "confidence": "high",
        "category": "scoring",
        "title": "How the audit score was calculated",
        "description": (
            "Base score starts at 100. Penalties depend on issue severity (critical/high/medium/low), "
            "then limited bonuses are added for crawl coverage. "
            f"Processed pages: {coverage.get('processed', 0)} of {coverage.get('max_pages', 0)}. "
            f"Issue counts - critical: {counts.get('critical', 0)}, high: {counts.get('high', 0)}, "
            f"medium: {counts.get('medium', 0)}, low: {counts.get('low', 0)}. "
            f"Penalty points: {breakdown.get('penalty_points', 0)}. CWV snapshot: {cwv_text}."
        ),
        "recommendation": "Fix high-severity issues first, then metadata and structured-data warnings, and rerun the audit after changes.",
    }


async def _load_audit_row(audit_id: str, row_cls: Type[PublicAuditResult] | Type[CrawlResult]) -> PublicAuditResult | CrawlResult:
    async with get_session() as session:
        result = await session.execute(select(row_cls).where(row_cls.audit_id == audit_id))
        row = result.scalar_one_or_none()
    if row is None:
        raise ValueError("audit_not_found")
    return row


async def _run_audit_pipeline(
    audit_id: str,
    *,
    row_cls: Type[PublicAuditResult] | Type[CrawlResult],
    include_external_data: bool,
) -> dict:
    row = await _load_audit_row(audit_id, row_cls)
    root_url = row.root_url
    options = row.options or {}

    findings = []
    precheck_findings = _precheck_url(root_url)
    findings.extend(precheck_findings)
    if any(finding["code"] in ("invalid_url", "unsafe_target") for finding in precheck_findings):
        summary = {
            "coverage": {"attempted": 0, "processed": 0, "max_pages": int(options.get("max_pages", 10))},
            "precheck_failed": True,
        }
        findings = [_decorate_finding(finding) for finding in findings]
        summary.update(_compute_score(summary, findings))
        findings.append(_build_score_explanation_finding(summary))
        return {"root_url": root_url, "summary": summary, "findings": findings, "top_findings": select_top_findings(findings), "pages": []}

    max_pages = int(options.get("max_pages", 10 if row.mode == "public" else 1000))
    max_depth = int(options.get("max_depth", 2 if row.mode == "public" else 4))
    if row.mode == "public":
        max_depth = min(max_depth, 2)
    js_render = bool(options.get("js_render", False))
    respect_robots = bool(options.get("respect_robots", True))
    timeout = float(options.get("timeout", settings.default_timeout_s))

    robots = await check_robots(root_url=root_url)
    sitemap = await check_sitemap(root_url=root_url)
    summary = {"robots": robots, "sitemap": sitemap}

    if not sitemap.get("available"):
        findings.append({"code": "sitemap_missing", "severity": "medium", "confidence": "high", "details": sitemap})

    if respect_robots and robots.get("blocked_root", False):
        findings.append({"code": "blocked_by_robots", "severity": "medium", "confidence": "high", "details": robots})
        summary.update({"coverage": {"attempted": 0, "processed": 0, "max_pages": max_pages, "max_depth": max_depth}, "blocked_pages_count": 0, "precheck_failed": False})
        findings = [_decorate_finding(finding) for finding in findings]
        summary.update(_compute_score(summary, findings))
        findings.append(_build_score_explanation_finding(summary))
        return {"root_url": root_url, "summary": summary, "findings": findings, "top_findings": select_top_findings(findings), "pages": []}

    crawled = await crawl_public(
        root_url=root_url,
        max_pages=max_pages,
        max_depth=max_depth,
        js_render=js_render,
        timeout_s=timeout,
        respect_robots=respect_robots,
    )
    pages = crawled["pages"]
    summary.update(crawled["summary"])
    findings.extend(_build_page_level_findings(pages))

    for page in pages:
        findings.extend(check_meta(page.get("url"), page.get("title"), page.get("description"), page.get("h1")))

        html = page.get("html")
        if html:
            findings.extend(validate_jsonld(page.get("url"), html))

    link_findings, links_checked = await check_links_404(root_url=root_url, pages=pages)
    findings.extend(link_findings)
    summary["links_checked"] = links_checked

    cwv = await analyze_cwv(root_url=root_url)
    if cwv:
        summary["cwv"] = cwv.get("summary", {})
        findings.extend(cwv.get("findings", []))
    else:
        findings.append({
            "code": "cwv_unavailable",
            "severity": "info",
            "confidence": "high",
            "details": {"reason": "psi_api_key_missing_or_error"},
        })

    if include_external_data:
        try:
            from services.audit_service.integrations.gsc_link_analyzer import analyze_links

            backlink_summary = await asyncio.to_thread(analyze_links, root_url)
            summary["backlinks"] = backlink_summary
        except Exception as exc:
            findings.append({
                "code": "backlinks_unavailable",
                "severity": "info",
                "confidence": "medium",
                "details": {"reason": str(exc)},
            })

    if summary.get("spa_detected") and not js_render:
        findings.append({
            "code": "spa_detected_enable_js_render",
            "severity": "medium",
            "confidence": "medium",
            "details": {"root_url": root_url},
        })

    if summary.get("anti_bot_detected"):
        findings.append({
            "code": "anti_bot_detected",
            "severity": "medium",
            "confidence": "medium",
            "details": {"hint": "reduce_concurrency_or_use_js_render"},
        })

    deduped = {}
    for finding in findings:
        deduped[_issue_key(finding)] = finding

    findings = [_decorate_finding(finding) for finding in deduped.values()]
    summary["mode"] = row.mode
    if row.project_id:
        summary["project_id"] = row.project_id
    summary.update(_compute_score(summary, findings))
    findings.append(_build_score_explanation_finding(summary))

    return {
        "project_id": row.project_id,
        "mode": row.mode,
        "root_url": root_url,
        "summary": summary,
        "findings": findings,
        "top_findings": select_top_findings(findings),
        "pages": pages,
    }


async def run_public_audit_pipeline(audit_id: str) -> dict:
    return await _run_audit_pipeline(
        audit_id,
        row_cls=PublicAuditResult,
        include_external_data=False,
    )


async def run_full_audit_pipeline(audit_id: str) -> dict:
    return await _run_audit_pipeline(
        audit_id,
        row_cls=CrawlResult,
        include_external_data=True,
    )
