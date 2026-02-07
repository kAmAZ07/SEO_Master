import pytest
import respx
from services.audit_service.analyzers.cwv_analyzer import analyze_cwv
from services.audit_service.config import settings


@pytest.mark.asyncio
async def test_cwv_analyzer_parses_metrics(monkeypatch):
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

        r = await analyze_cwv("https://example.com/")
        assert r is not None
        assert r["summary"]["LCP_grade"] == "poor"
        assert r["summary"]["FID_grade"] == "poor"
        assert r["summary"]["CLS_grade"] == "poor"
        codes = {f["code"] for f in r["findings"]}
        assert "cwv_lcp_poor" in codes
        assert "cwv_fid_poor" in codes
        assert "cwv_cls_poor" in codes