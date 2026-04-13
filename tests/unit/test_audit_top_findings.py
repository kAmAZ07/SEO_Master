import pytest

pytest.importorskip("httpx")
pytest.importorskip("bs4")
pytest.importorskip("tldextract")
pytest.importorskip("playwright")
pytest.importorskip("sqlalchemy")
pytest.importorskip("pydantic_settings")

from services.audit_service.crawler.technical_audit import select_top_findings


def test_select_top_findings_prioritizes_problem_findings():
    findings = [
        {"code": "description_missing", "severity": "low", "confidence": "high"},
        {"code": "audit_score_explanation", "severity": "info", "confidence": "high"},
        {"code": "sitemap_missing", "severity": "medium", "confidence": "high"},
        {"code": "page_fetch_error", "severity": "high", "confidence": "medium"},
        {"code": "title_missing", "severity": "medium", "confidence": "medium"},
        {"code": "cwv_lcp_poor", "severity": "high", "confidence": "high"},
        {"code": "cwv_unavailable", "severity": "info", "confidence": "high"},
        {"code": "h1_missing", "severity": "medium", "confidence": "high"},
    ]

    result = select_top_findings(findings)

    assert [finding["code"] for finding in result] == [
        "cwv_lcp_poor",
        "page_fetch_error",
        "sitemap_missing",
        "h1_missing",
        "title_missing",
    ]
