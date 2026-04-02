import asyncio

import pytest

pytest.importorskip("httpx")
respx = pytest.importorskip("respx")

from services.audit_service.analyzers.cwv_analyzer import analyze_cwv
from services.audit_service.config import settings


def test_cwv_analyzer_parses_metrics(monkeypatch):
    async def _run():
        monkeypatch.setattr(settings, "psi_api_key", "fake")

        with respx.mock:
            respx.get("https://www.googleapis.com/pagespeedonline/v5/runPagespeed").respond(
                200,
                json={
                    "lighthouseResult": {
                        "audits": {
                            "largest-contentful-paint": {"numericValue": 5000},
                            "max-potential-fid": {"numericValue": 400},
                            "cumulative-layout-shift": {"numericValue": 0.3},
                        }
                    }
                },
            )

            result = await analyze_cwv("https://example.com/")
            assert result is not None
            assert result["summary"]["LCP_grade"] == "poor"
            assert result["summary"]["FID_grade"] == "poor"
            assert result["summary"]["CLS_grade"] == "poor"
            codes = {finding["code"] for finding in result["findings"]}
            assert "cwv_lcp_poor" in codes
            assert "cwv_fid_poor" in codes
            assert "cwv_cls_poor" in codes

    asyncio.run(_run())
