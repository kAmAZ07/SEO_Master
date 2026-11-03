import asyncio

import pytest

pytest.importorskip("bs4")
pytest.importorskip("lxml")
pytest.importorskip("pydantic_settings")

from services.audit_service.crawler.public_crawler import crawl_public
from services.audit_service.netio import AuditFetcher, Budget, build_policy
from tests.fixtures.public_site import BASE, FixtureClient, Resp, html_page


def _crawl(site, **kwargs):
    fetcher = AuditFetcher(client=FixtureClient(site), budget=Budget(deadline_s=30), base_policy=build_policy())
    return asyncio.run(crawl_public(f"{BASE}/", fetcher=fetcher, timeout_s=5.0, **kwargs))


def test_public_crawler_limits_pages():
    site = {
        "/": Resp(body=html_page(title="Home page title", links=["/a", "/b"])),
        "/a": Resp(body=html_page(title="Page A title")),
        "/b": Resp(body=html_page(title="Page B title")),
    }
    result = _crawl(site, max_pages=2, js_render=False)
    assert result["summary"]["coverage"]["processed"] == 2
    assert len(result["pages"]) == 2


def test_crawler_stays_on_one_host_and_counts_h1():
    site = {
        "/": Resp(body=html_page(h1s=("Один", "Два"), links=["https://other.test/x", "https://sub.fixture.test/y", "/ok", "mailto:a@b.c"])),
        "/ok": Resp(body=html_page()),
    }
    result = _crawl(site, max_pages=10)
    assert [p["url"] for p in result["pages"]] == [f"{BASE}/", f"{BASE}/ok"]
    assert result["pages"][0]["h1_count"] == 2
