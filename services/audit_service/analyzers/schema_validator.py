import json
import re
from bs4 import BeautifulSoup


def validate_jsonld(url: str | None, html: str) -> list[dict]:
    findings = []
    u = url or ""
    soup = BeautifulSoup(html, "lxml")
    scripts = soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)})
    if not scripts:
        findings.append({"code": "jsonld_missing", "severity": "low", "confidence": "high", "details": {"url": u}})
        return findings

    for i, s in enumerate(scripts):
        txt = (s.string or s.get_text() or "").strip()
        if not txt:
            findings.append({"code": "jsonld_empty", "severity": "low", "confidence": "high", "details": {"url": u, "index": i}})
            continue
        try:
            data = json.loads(txt)
        except Exception as e:
            findings.append({"code": "jsonld_invalid_json", "severity": "medium", "confidence": "high", "details": {"url": u, "index": i, "error": str(e)}})
            continue

        objs = data if isinstance(data, list) else [data]
        for obj in objs:
            if not isinstance(obj, dict):
                findings.append({"code": "jsonld_invalid_structure", "severity": "low", "confidence": "medium", "details": {"url": u, "index": i}})
                continue
            if "@context" not in obj:
                findings.append({"code": "jsonld_missing_context", "severity": "low", "confidence": "high", "details": {"url": u, "index": i}})
            if "@type" not in obj:
                findings.append({"code": "jsonld_missing_type", "severity": "low", "confidence": "high", "details": {"url": u, "index": i}})
    return findings