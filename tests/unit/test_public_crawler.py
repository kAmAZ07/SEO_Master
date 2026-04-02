import asyncio

import pytest

pytest.importorskip("httpx")
respx = pytest.importorskip("respx")
pytest.importorskip("bs4")
pytest.importorskip("tldextract")
pytest.importorskip("playwright")

from services.audit_service.crawler.public_crawler import crawl_public


def test_public_crawler_limits_pages():
    async def _run():
        with respx.mock:
            respx.get("https://example.com/").respond(
                200,
                text='<html><head><title>Home</title><meta name="description" content="d"></head><body><h1>H</h1><a href="/a">A</a><a href="/b">B</a></body></html>',
            )
            respx.head("https://example.com/a").respond(200)
            respx.head("https://example.com/b").respond(200)
            respx.get("https://example.com/a").respond(
                200,
                text='<html><head><title>A</title></head><body><h1>A</h1></body></html>',
            )
            respx.get("https://example.com/b").respond(
                200,
                text='<html><head><title>B</title></head><body><h1>B</h1></body></html>',
            )

            result = await crawl_public(
                "https://example.com/",
                max_pages=2,
                js_render=False,
                timeout_s=5.0,
            )
            assert result["summary"]["coverage"]["processed"] == 2
            assert len(result["pages"]) == 2

    asyncio.run(_run())
