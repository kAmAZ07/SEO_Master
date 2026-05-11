import re
from urllib.parse import urlparse, urlunparse

from bs4 import BeautifulSoup


def _normalize_url(url: str) -> str:
    """Strip fragment, trailing slash, and lowercase scheme+host for canonical comparison."""
    try:
        parsed = urlparse(url.strip())
        # Rebuild without fragment; preserve path trailing slash as-is
        normalized = urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            parsed.params,
            parsed.query,
            "",  # drop fragment
        ))
        return normalized
    except Exception:
        return url.strip()


def check_canonical(url: str | None, html: str) -> list[dict]:
    """Check for canonical link tag presence and consistency with the page URL."""
    findings = []
    u = url or ""

    soup = BeautifulSoup(html, "lxml")
    canonical_tags = soup.find_all("link", rel=re.compile(r"^canonical$", re.I))

    if not canonical_tags:
        findings.append({
            "code": "canonical_missing",
            "severity": "low",
            "confidence": "high",
            "details": {"url": u},
        })
        return findings

    canonical_href = (canonical_tags[0].get("href") or "").strip()

    # Multiple canonical tags — only the first is used by search engines; extra ones are an error
    if len(canonical_tags) > 1:
        findings.append({
            "code": "canonical_multiple",
            "severity": "medium",
            "confidence": "high",
            "details": {"url": u, "count": len(canonical_tags)},
        })

    if canonical_href and u:
        norm_page = _normalize_url(u)
        norm_canonical = _normalize_url(canonical_href)
        if norm_canonical != norm_page:
            findings.append({
                "code": "canonical_mismatch",
                "severity": "medium",
                "confidence": "high",
                "details": {"url": u, "canonical": canonical_href},
            })

    return findings


def check_meta(url: str | None, title: str | None, description: str | None, h1: str | None) -> list[dict]:
    findings = []
    u = url or ""

    if not title or not title.strip():
        findings.append({"code": "title_missing", "severity": "medium", "confidence": "high", "details": {"url": u}})
    else:
        tl = len(title.strip())
        if tl < 10:
            findings.append({"code": "title_too_short", "severity": "low", "confidence": "high", "details": {"url": u, "len": tl}})
        if tl > 70:
            findings.append({"code": "title_too_long", "severity": "low", "confidence": "high", "details": {"url": u, "len": tl}})

    if not description or not description.strip():
        findings.append({"code": "description_missing", "severity": "low", "confidence": "high", "details": {"url": u}})
    else:
        dl = len(description.strip())
        if dl < 50:
            findings.append({"code": "description_too_short", "severity": "low", "confidence": "high", "details": {"url": u, "len": dl}})
        if dl > 160:
            findings.append({"code": "description_too_long", "severity": "low", "confidence": "high", "details": {"url": u, "len": dl}})

    if not h1 or not h1.strip():
        findings.append({"code": "h1_missing", "severity": "medium", "confidence": "high", "details": {"url": u}})
    else:
        hl = len(h1.strip())
        if hl < 5:
            findings.append({"code": "h1_too_short", "severity": "low", "confidence": "high", "details": {"url": u, "len": hl}})

    return findings