from services.audit_service.integrations.psi_api import fetch_pagespeed_insights


def _classify(value: float | None, metric: str) -> str:
    if value is None:
        return "unknown"
    if metric == "LCP":
        return "good" if value <= 2500 else "needs_improvement" if value <= 4000 else "poor"
    if metric == "FID":
        return "good" if value <= 100 else "needs_improvement" if value <= 300 else "poor"
    if metric == "CLS":
        return "good" if value <= 0.1 else "needs_improvement" if value <= 0.25 else "poor"
    return "unknown"


async def analyze_cwv(root_url: str) -> dict | None:
    data = await fetch_pagespeed_insights(url=root_url, strategy="mobile")
    if not data:
        return None

    metrics = data.get("metrics", {})
    lcp = metrics.get("LCP")
    fid = metrics.get("FID")
    cls = metrics.get("CLS")

    summary = {
        "LCP_ms": lcp,
        "FID_ms": fid,
        "CLS": cls,
        "LCP_grade": _classify(lcp, "LCP"),
        "FID_grade": _classify(fid, "FID"),
        "CLS_grade": _classify(cls, "CLS"),
    }

    findings = []
    if summary["LCP_grade"] == "poor":
        findings.append({"code": "cwv_lcp_poor", "severity": "high", "confidence": "high", "details": {"value_ms": lcp}})
    if summary["FID_grade"] == "poor":
        findings.append({"code": "cwv_fid_poor", "severity": "high", "confidence": "high", "details": {"value_ms": fid}})
    if summary["CLS_grade"] == "poor":
        findings.append({"code": "cwv_cls_poor", "severity": "high", "confidence": "high", "details": {"value": cls}})

    return {"summary": summary, "findings": findings, "raw": data.get("raw", {})}