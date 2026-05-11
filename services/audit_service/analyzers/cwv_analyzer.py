from services.audit_service.integrations.psi_api import fetch_pagespeed_insights


def _classify(value: float | None, metric: str) -> str:
    if value is None:
        return "unknown"
    if metric == "LCP":
        # Google thresholds: good ≤2500ms, needs_improvement ≤4000ms, poor >4000ms
        return "good" if value <= 2500 else "needs_improvement" if value <= 4000 else "poor"
    if metric == "INP":
        # INP replaced FID as Core Web Vital in March 2024
        # Google thresholds: good ≤200ms, needs_improvement ≤500ms, poor >500ms
        return "good" if value <= 200 else "needs_improvement" if value <= 500 else "poor"
    if metric == "CLS":
        # Google thresholds: good ≤0.1, needs_improvement ≤0.25, poor >0.25
        return "good" if value <= 0.1 else "needs_improvement" if value <= 0.25 else "poor"
    return "unknown"


async def analyze_cwv(root_url: str) -> dict | None:
    data = await fetch_pagespeed_insights(url=root_url, strategy="mobile")
    if not data:
        return None

    metrics = data.get("metrics", {})
    lcp = metrics.get("LCP")
    inp = metrics.get("INP")
    cls = metrics.get("CLS")

    summary = {
        "LCP_ms": lcp,
        "INP_ms": inp,
        "CLS": cls,
        "LCP_grade": _classify(lcp, "LCP"),
        "INP_grade": _classify(inp, "INP"),
        "CLS_grade": _classify(cls, "CLS"),
    }

    findings = []
    if summary["LCP_grade"] == "poor":
        findings.append({"code": "cwv_lcp_poor", "severity": "high", "confidence": "high", "details": {"value_ms": lcp}})
    if summary["INP_grade"] == "poor":
        findings.append({"code": "cwv_inp_poor", "severity": "high", "confidence": "high", "details": {"value_ms": inp}})
    if summary["CLS_grade"] == "poor":
        findings.append({"code": "cwv_cls_poor", "severity": "high", "confidence": "high", "details": {"value": cls}})

    return {"summary": summary, "findings": findings, "raw": data.get("raw", {})}