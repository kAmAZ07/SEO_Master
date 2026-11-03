"""Публичный аудит: ядро (AC-01 happy path, AC-02 защита, REQ-PUBLIC §6).

Сеть заменена `FixtureClient`, который повторяет контракт SafeHttpClient и записывает запросы.
Тесты на эталонной фикстуре версии 1: 10 страниц, известные проблемы, детерминированный top-5.
"""
import asyncio
import gzip

import pytest

pytest.importorskip("bs4")
pytest.importorskip("lxml")
pytest.importorskip("defusedxml")
pytest.importorskip("sqlalchemy")
pytest.importorskip("pydantic_settings")

from services.audit_service.analyzers import cwv_analyzer
from services.audit_service.analyzers.sitemap_checker import check_sitemap
from services.audit_service.crawler.technical_audit import run_audit_core, select_top_findings
from services.audit_service.netio import AuditFetcher, Budget, build_policy
from services.audit_service.public_policy import (
    PUBLIC_MAX_DEPTH,
    PUBLIC_MAX_LINK_PROBES,
    PUBLIC_MAX_PAGES,
    SITEMAP_MAX_BYTES,
    public_options,
)
from tests.fixtures.public_site import (
    BASE,
    CRAWLED_PATHS,
    EXPECTED_CODES,
    EXPECTED_TOP5,
    FixtureClient,
    Resp,
    default_site,
    html_page,
)

POOR_PSI = {
    "metrics": {"LCP": 6000, "INP": None, "FID": None, "CLS": 0.01},
    "source": "pagespeed_insights_lighthouse_lab",
    "field_data_available": False,
    "raw": {},
}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def psi_poor(monkeypatch):
    async def _fake(url, strategy="mobile", timeout=15.0):
        return POOR_PSI, None

    monkeypatch.setattr(cwv_analyzer, "fetch_pagespeed_result", _fake)


@pytest.fixture
def psi_quota(monkeypatch):
    async def _fake(url, strategy="mobile", timeout=15.0):
        return None, "quota_exceeded"

    monkeypatch.setattr(cwv_analyzer, "fetch_pagespeed_result", _fake)


def audit(client, root=f"{BASE}/", options=None, deadline_s=100.0, mode="public"):
    fetcher = AuditFetcher(client=client, budget=Budget(deadline_s=deadline_s), base_policy=build_policy())
    return run(run_audit_core(root_url=root, mode=mode, project_id=None, options=options or {}, fetcher=fetcher))


def codes(result):
    return {f["code"] for f in result["findings"]}


# --- AC-01: happy path -------------------------------------------------------

def test_fixture_site_gives_expected_findings_and_checks(psi_poor):
    client = FixtureClient()
    result = audit(client)

    assert result["outcome"] == "completed"
    assert EXPECTED_CODES <= codes(result)
    coverage = result["summary"]["coverage"]
    assert coverage["processed"] == 10 and coverage["max_depth_reached"] <= PUBLIC_MAX_DEPTH
    assert [p["url"] for p in result["pages"]] == [f"{BASE}{path}" for path in CRAWLED_PATHS]
    checks = result["summary"]["checks"]
    assert set(checks) >= {"precheck", "robots", "sitemap", "meta", "h1", "canonical", "structured_data", "links", "psi"}
    assert checks["sitemap"]["status"] == "failed" and checks["links"]["status"] == "failed"
    assert checks["psi"]["status"] == "failed" and result["summary"]["psi"]["source"] == "pagespeed_insights_lighthouse_lab"


def test_top5_is_exactly_five_and_deterministic(psi_poor):
    runs = [audit(FixtureClient()) for _ in range(3)]
    for result in runs:
        assert [f["code"] for f in result["top_findings"]] == EXPECTED_TOP5
    assert runs[0]["findings"] == runs[1]["findings"] == runs[2]["findings"]


def test_no_raw_html_or_link_lists_in_public_result(psi_poor):
    result = audit(FixtureClient())
    for page in result["pages"]:
        assert "html" not in page and "links" not in page


def test_depth_three_and_robots_disallowed_pages_are_not_fetched(psi_poor):
    client = FixtureClient()
    audit(client)
    fetched = client.paths("GET")
    assert "/deep/level3" not in fetched  # глубина 3
    assert "/private" not in fetched  # Disallow в robots.txt


def test_head_not_supported_falls_back_to_get(psi_poor):
    client = FixtureClient()
    audit(client)
    assert ("HEAD", f"{BASE}/nohead") in client.requests and ("GET", f"{BASE}/nohead") in client.requests


def test_small_site_reports_actual_coverage_without_invented_findings(psi_poor):
    site = {
        "/robots.txt": Resp(status=404),
        "/sitemap.xml": Resp(body='<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://fixture.test/</loc></url></urlset>',
                             headers={"content-type": "application/xml"}),
        "/": Resp(body=html_page(canonical=f"{BASE}/", jsonld='{"@context":"https://schema.org","@type":"Thing"}', links=["/a"])),
        "/a": Resp(body=html_page(canonical=f"{BASE}/a", jsonld='{"@context":"https://schema.org","@type":"Thing"}')),
    }
    result = audit(FixtureClient(site))
    assert result["outcome"] == "completed"
    assert result["summary"]["coverage"]["processed"] == 2
    assert result["top_findings"] == [] or {f["code"] for f in result["top_findings"]} <= {"cwv_lcp_poor"}
    assert codes(result) - {"cwv_lcp_poor", "audit_score_explanation"} == set()


# --- PSI ---------------------------------------------------------------------

def test_psi_quota_is_separate_unavailable_not_good_score_and_not_in_top(psi_quota):
    result = audit(FixtureClient())
    assert result["outcome"] == "completed"
    assert result["summary"]["psi"] == {"status": "unavailable", "reason": "quota_exceeded"}
    assert result["summary"]["checks"]["psi"]["status"] == "unavailable"
    assert "cwv_unavailable" not in {f["code"] for f in result["top_findings"]}
    performance = next(c for c in result["summary"]["criteria"] if c["key"] == "performance")
    assert performance["status"] == "unavailable"


# --- лимиты, которые пользователь не может поднять --------------------------------

def test_public_options_can_only_lower_limits():
    opts = public_options({"max_pages": 100000, "max_depth": 9, "js_render": True, "respect_robots": False, "timeout": 300})
    assert opts["max_pages"] == PUBLIC_MAX_PAGES and opts["max_depth"] == PUBLIC_MAX_DEPTH
    assert opts["js_render"] is False and opts["respect_robots"] is True
    assert public_options({"max_pages": 3, "max_depth": 1})["max_pages"] == 3
    assert public_options({"max_pages": "abc"})["max_pages"] == PUBLIC_MAX_PAGES


def test_core_enforces_limits_on_big_site_even_if_options_ask_for_more(psi_poor):
    site = {"/": Resp(body=html_page(links=[f"/p{i}" for i in range(40)]))}
    site.update({f"/p{i}": Resp(body=html_page(links=[f"/p{i}/x"])) for i in range(40)})
    client = FixtureClient(site)
    result = audit(client, options={"max_pages": 100000, "max_depth": 10, "js_render": True, "respect_robots": False})
    assert result["summary"]["coverage"]["processed"] == PUBLIC_MAX_PAGES
    assert result["summary"]["js_render_used"] is False
    assert len([p for m, p in client.requests if m == "GET" and not p.endswith(("robots.txt", "sitemap.xml"))]) <= 2 * PUBLIC_MAX_PAGES


def test_link_probes_are_capped(psi_poor):
    site = {"/": Resp(body=html_page(links=[f"/x{i}" for i in range(300)]))}
    client = FixtureClient(site)
    result = audit(client, options={"max_pages": 1})
    probes = [m for m, p in client.requests if p.split("/")[-1].startswith("x")]
    assert len(probes) <= PUBLIC_MAX_LINK_PROBES * 2
    assert result["summary"]["links_checked"] <= PUBLIC_MAX_LINK_PROBES


def test_link_probe_cap_holds_even_if_configured_limit_is_higher(monkeypatch):
    from services.audit_service.analyzers.link_checker import check_links_404
    from services.audit_service.config import settings

    monkeypatch.setattr(settings, "max_internal_link_checks", 500)
    pages = [{"url": f"{BASE}/", "status_code": 200, "links": [f"{BASE}/x{i}" for i in range(300)]}]
    client = FixtureClient({})
    fetcher = AuditFetcher(client=client, budget=Budget(deadline_s=50), base_policy=build_policy())
    _, checked = run(check_links_404(f"{BASE}/", pages, fetcher))
    assert checked == PUBLIC_MAX_LINK_PROBES and len(client.requests) == PUBLIC_MAX_LINK_PROBES


# --- robots -------------------------------------------------------------------

def test_robots_block_is_respected_and_result_is_partial(psi_poor):
    site = default_site()
    site["/robots.txt"] = Resp(body="User-agent: *\nDisallow: /\n", headers={"content-type": "text/plain"})
    client = FixtureClient(site)
    result = audit(client)
    assert result["outcome"] == "partial"
    assert "robots_blocked" in result["summary"]["partial_reasons"]
    assert "blocked_by_robots" in codes(result)
    assert result["pages"] == []
    assert set(client.paths("GET")) <= {"/robots.txt", "/sitemap.xml"}  # ни одной страницы


def test_robots_server_error_is_unreachable_not_missing(psi_poor):
    site = default_site()
    site["/robots.txt"] = Resp(status=503, body="down")
    client = FixtureClient(site)
    result = audit(client)
    assert result["outcome"] == "partial" and "robots_unreachable" in codes(result)
    assert result["summary"]["robots"]["state"] == "unreachable"
    assert "/" not in client.paths("GET")  # по RFC 9309 недоступный robots.txt = запрет обхода


def test_missing_robots_does_not_block(psi_poor):
    site = default_site()
    site["/robots.txt"] = Resp(status=404, body="")
    result = audit(FixtureClient(site))
    assert result["summary"]["robots"]["state"] == "missing"
    assert result["summary"]["coverage"]["processed"] >= 9


# --- AC-02: защита ------------------------------------------------------------

@pytest.mark.parametrize(
    "root, expected",
    [
        ("http://localhost/", "unsafe_target"),
        ("http://127.0.0.1/", "unsafe_target"),
        ("http://[::1]/", "unsafe_target"),
        ("http://169.254.169.254/latest/meta-data/", "unsafe_target"),
        ("http://10.0.0.5/", "unsafe_target"),
        ("http://[::ffff:127.0.0.1]/", "unsafe_target"),
        ("http://2130706433/", "unsafe_target"),
        ("http://user:pass@fixture.test/", "invalid_url"),
        ("ftp://fixture.test/", "invalid_url"),
        ("https://fixture.test:8443/", "invalid_url"),
        ("http://nxdomain.test/", "dns_failure"),
    ],
)
def test_unsafe_targets_fail_before_any_request(root, expected):
    client = FixtureClient()
    result = audit(client, root=root)
    assert result["outcome"] == "failed"
    assert expected in codes(result), codes(result)
    assert client.requests == []  # egress deny: сети не коснулись


def test_mixed_dns_answer_is_rejected_entirely():
    client = FixtureClient(dns={"mixed.test": ["93.184.216.34", "10.0.0.7"]})
    result = audit(client, root="https://mixed.test/")
    assert result["outcome"] == "failed" and "unsafe_target" in codes(result)
    assert client.requests == []


def test_redirect_to_internal_address_is_blocked_and_trap_gets_no_request(psi_poor):
    site = default_site()
    site["/blog"] = Resp(status=302, redirect="https://trap.test/steal")
    client = FixtureClient(site, dns={"fixture.test": ["93.184.216.34"], "trap.test": ["169.254.169.254"]})
    result = audit(client)
    blog = next(p for p in result["pages"] if p["url"].endswith("/blog"))
    assert blog["error"] == "blocked_address_blocked"
    assert result["summary"]["blocked_pages_count"] >= 1
    assert all("trap.test" not in url for _, url in client.requests)  # к ловушке не обращались


@pytest.mark.parametrize(
    "resp, expected_error, counter",
    [
        (Resp(raises="timeout"), "timeout", "timeout_count"),
        (Resp(raises="tls_error"), "ssl_error", "ssl_errors_count"),
        (Resp(raises="redirect_loop"), "redirect_loop", "redirect_loops_count"),
        (Resp(delay=999), "timeout", "timeout_count"),
    ],
)
def test_page_failures_give_final_state_and_counters(resp, expected_error, counter, psi_poor):
    site = default_site()
    site["/blog"] = resp
    result = audit(FixtureClient(site), deadline_s=2.0)
    assert result["outcome"] in ("completed", "partial")
    blog = next(p for p in result["pages"] if p["url"].endswith("/blog"))
    assert blog["error"] == expected_error
    assert result["summary"][counter] >= 1


def test_real_redirect_loop_between_pages(psi_poor):
    site = default_site()
    site["/blog"] = Resp(status=301, redirect="/loop-b")
    site["/loop-b"] = Resp(status=301, redirect="/blog")
    result = audit(FixtureClient(site))
    blog = next(p for p in result["pages"] if p["url"].endswith("/blog"))
    assert blog["error"] == "redirect_loop" and result["summary"]["redirect_loops_count"] == 1


def test_oversized_html_page_is_error_not_crash(psi_poor):
    site = default_site()
    site["/blog"] = Resp(body=b"x" * (2 * 1024 * 1024 + 1))
    result = audit(FixtureClient(site))
    blog = next(p for p in result["pages"] if p["url"].endswith("/blog"))
    assert blog["error"] == "blocked_response_too_large"


def test_non_html_pages_are_not_counted_as_pages(psi_poor):
    site = default_site()
    site["/blog"] = Resp(body=b"%PDF", headers={"content-type": "application/pdf"})
    result = audit(FixtureClient(site))
    assert result["summary"]["non_html_skipped"] == 1
    assert all(not p["url"].endswith("/blog") for p in result["pages"])


def test_deadline_gives_partial_result_with_actual_coverage(psi_poor):
    site = default_site()
    for path in ("/about", "/products", "/blog", "/contact", "/team", "/faq", "/broken-server"):
        site[path].delay = 0.15
    result = audit(FixtureClient(site), deadline_s=0.5)
    assert result["outcome"] == "partial"
    assert "deadline" in result["summary"]["partial_reasons"]
    assert 1 <= result["summary"]["coverage"]["processed"] < 10


def test_request_budget_is_enforced(psi_poor):
    site = {"/": Resp(body=html_page(links=[f"/p{i}" for i in range(30)]))}
    client = FixtureClient(site)
    fetcher = AuditFetcher(client=client, budget=Budget(deadline_s=50, max_page_requests=3, max_aux_requests=2), base_policy=build_policy())
    result = run(run_audit_core(root_url=f"{BASE}/", mode="public", project_id=None, options={}, fetcher=fetcher))
    assert len(client.requests) <= 5
    assert result["outcome"] == "partial"


# --- sitemap ------------------------------------------------------------------

def sitemap_client(body, headers=None):
    site = {"/sitemap.xml": Resp(body=body, headers=headers or {"content-type": "application/xml"})}
    return FixtureClient(site)


def check(client):
    fetcher = AuditFetcher(client=client, budget=Budget(deadline_s=50), base_policy=build_policy())
    return run(check_sitemap(f"{BASE}/", fetcher))


def test_sitemap_with_entities_is_rejected_without_expansion():
    bomb = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
            '<urlset><url><loc>&b;</loc></url></urlset>')
    result = check(sitemap_client(bomb))
    assert result["state"] == "invalid" and result["error"] == "sitemap_unsafe_xml"


def test_sitemap_external_entity_is_rejected_and_not_fetched():
    xxe = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "http://trap.test/x">]><urlset><url><loc>&e;</loc></url></urlset>'
    client = sitemap_client(xxe)
    result = check(client)
    assert result["error"] == "sitemap_unsafe_xml"
    assert all("trap.test" not in url for _, url in client.requests)


def test_gzip_bomb_sitemap_is_rejected():
    bomb = gzip.compress(b"<urlset>" + b"<url><loc>https://fixture.test/</loc></url>" * 400_000 + b"</urlset>")
    assert len(bomb) < SITEMAP_MAX_BYTES  # сжатый размер проходит лимит передачи
    result = check(sitemap_client(bomb, {"content-type": "application/gzip"}))
    assert result["state"] == "invalid" and result["error"] == "sitemap_too_large"


def test_sitemap_index_recursion_terminates():
    index = ('<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
             '<sitemap><loc>https://fixture.test/sitemap.xml</loc></sitemap></sitemapindex>')
    client = sitemap_client(index)
    result = check(client)
    assert result["state"] == "ok" and len(client.requests) == 1


def test_sitemap_index_depth_is_limited():
    site = {}
    for level in range(8):
        nxt = f"https://fixture.test/s{level + 1}.xml"
        site["/sitemap.xml" if level == 0 else f"/s{level}.xml"] = Resp(
            body=f'<sitemapindex><sitemap><loc>{nxt}</loc></sitemap></sitemapindex>')
    client = FixtureClient(site)
    result = check(client)
    assert result["truncated"] is True and len(client.requests) <= 3


def test_sitemap_entry_cap():
    urls = "".join(f"<url><loc>https://fixture.test/{i}</loc></url>" for i in range(50_050))
    result = check(sitemap_client(f"<urlset>{urls}</urlset>"))
    assert result["url_count"] == 50_000 and result["truncated"] is True


def test_sitemap_missing_vs_invalid_vs_ok():
    assert check(FixtureClient({}))["state"] == "missing"
    assert check(sitemap_client("<html>nope</html>"))["state"] == "invalid"
    ok = check(sitemap_client('<urlset><url><loc>https://fixture.test/a</loc></url></urlset>'))
    assert ok["state"] == "ok" and ok["url_count"] == 1


# --- top-5 ---------------------------------------------------------------------

def test_top_findings_one_per_code_with_affected_count_and_no_info():
    findings = [
        {"code": "title_missing", "severity": "medium", "confidence": "high", "details": {"url": "a"}},
        {"code": "title_missing", "severity": "medium", "confidence": "high", "details": {"url": "b"}},
        {"code": "cwv_unavailable", "severity": "info", "confidence": "high"},
        {"code": "h1_missing", "severity": "medium", "confidence": "high"},
    ]
    top = select_top_findings(findings)
    assert [f["code"] for f in top] == ["title_missing", "h1_missing"]
    assert top[0]["affected_count"] == 2


# --- реальный клиент: ловушка не получает соединений -------------------------------

def test_real_client_blocks_loopback_and_trap_sees_no_connection():
    from shared.netpolicy import NetPolicy, SafeHttpClient

    async def scenario():
        connections = []

        async def handler(reader, writer):
            connections.append(1)
            writer.close()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            async def resolver(host, p):
                return ["127.0.0.1"]

            policy = NetPolicy(allowed_ports=frozenset({port}))
            client = SafeHttpClient(policy, resolver=resolver)
            fetcher = AuditFetcher(client=client, budget=Budget(deadline_s=20), base_policy=policy)
            result = await run_audit_core(root_url=f"http://trap.test:{port}/", mode="public", project_id=None,
                                          options={}, fetcher=fetcher)
            return result, len(connections)
        finally:
            server.close()
            await server.wait_closed()

    result, connection_count = run(scenario())
    assert result["outcome"] == "failed" and "unsafe_target" in codes(result)
    assert connection_count == 0
