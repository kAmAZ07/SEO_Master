from services.audit_service.integrations.psi_api import fetch_pagespeed_result


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
    if metric == "FID":
        return "good" if value <= 100 else "needs_improvement" if value <= 300 else "poor"
    if metric == "CLS":
        # Google thresholds: good ≤0.1, needs_improvement ≤0.25, poor >0.25
        return "good" if value <= 0.1 else "needs_improvement" if value <= 0.25 else "poor"
    return "unknown"


async def analyze_cwv_detailed(root_url: str, timeout: float = 15.0) -> tuple[dict | None, str | None]:
    """Возвращает (результат, причина недоступности). Недоступность PSI — отдельное состояние, не оценка."""
    data, reason = await fetch_pagespeed_result(url=root_url, strategy="mobile", timeout=timeout)
    if not data:
        return None, reason or "unavailable"

    metrics = data.get("metrics", {})
    lcp = metrics.get("LCP")
    inp = metrics.get("INP")
    fid = metrics.get("FID")
    cls = metrics.get("CLS")

    summary = {
        "source": data.get("source", "pagespeed_insights_lighthouse_lab"),
        "field_data_available": bool(data.get("field_data_available")),
        "LCP_ms": lcp,
        "INP_ms": inp,
        "FID_ms": fid,
        "CLS": cls,
        "LCP_grade": _classify(lcp, "LCP"),
        "INP_grade": _classify(inp, "INP"),
        "FID_grade": _classify(fid, "FID"),
        "CLS_grade": _classify(cls, "CLS"),
    }

    findings = []
    if summary["LCP_grade"] == "poor":
        findings.append({"code": "cwv_lcp_poor", "severity": "high", "confidence": "high", "details": {"value_ms": lcp, "source": summary["source"]}})
    if summary["INP_grade"] == "poor":
        findings.append({"code": "cwv_inp_poor", "severity": "high", "confidence": "high", "details": {"value_ms": inp, "source": summary["source"]}})
    if fid is not None and summary["FID_grade"] == "poor":
        findings.append({"code": "cwv_fid_poor", "severity": "high", "confidence": "high", "details": {"value_ms": fid, "source": summary["source"]}})
    if summary["CLS_grade"] == "poor":
        findings.append({"code": "cwv_cls_poor", "severity": "high", "confidence": "high", "details": {"value": cls, "source": summary["source"]}})

    return {"summary": summary, "findings": findings, "raw": data.get("raw", {})}, None


async def analyze_cwv(root_url: str) -> dict | None:
    result, _ = await analyze_cwv_detailed(root_url)
    return result
