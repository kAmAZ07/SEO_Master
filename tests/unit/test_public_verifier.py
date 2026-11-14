"""Public facts are fetched independently and ambiguous predicates cannot verify."""
from datetime import datetime, timezone
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.client_api_gateway.public_verifier import PublicHTML, PublicPageReader, PublicReadError, evaluate_public
from shared.hitl_verification import PublicObservation
from shared.netpolicy import FetchResult, NetPolicy, SafeHttpClient

URL = "https://example.com/page"
NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)


def observation(html):
    body = html.encode()
    return PublicObservation(url=URL, final_url=URL, observed_at=NOW, status=200, headers={"content-type": "text/html"},
        body_sha256=hashlib.sha256(body).hexdigest(), facts=PublicHTML().facts(html))


def record(field="title", before="Old", after="Новый & 'кавычки'"):
    path = {"title": "head.title", "description": "head.description", "jsonld": "head.jsonld"}[field]
    old = {"state": "missing"} if before is None else {"state": "value", "value": before}
    new = {"state": "missing"} if after is None else {"state": "value", "value": after}
    predicate = {"kind": "field_removed", "path": path} if after is None else {
        "kind": "field_equals_after", "path": path, "sha256": hashlib.sha256(after.encode()).hexdigest()}
    return {"before": {"fields": {field: old}}, "after": {"fields": {field: new}}, "version": {
        "target": {"url": URL}, "changes": [{"cms_field": field, "path": path, "mode": "remove" if after is None else "set"}],
        "expected_checks": [{"kind": "target_http_ok", "url": URL}, predicate]}}


def test_decoded_public_values_match_exactly_without_comparing_dynamic_html():
    value = record()
    result = evaluate_public(value, observation("<html><head><title>Новый &amp; 'кавычки'</title></head><body>dynamic nonce=123</body></html>"))
    assert result.matches
    assert not evaluate_public(value, observation("<head><title>Old</title></head>")).matches


@pytest.mark.parametrize("html", ["<head><title>New</title><title>New</title></head>",
    '<head><title>New</title><meta name="description" content="A"><meta name="description" content="A"></head>'])
def test_duplicate_head_fields_cannot_be_verified(html):
    assert not evaluate_public(record(after="New"), observation(html)).matches


@pytest.mark.parametrize("source", ['{"@type":"Thing","@type":"Person"}', '{"score":NaN}', '"scalar"', '{invalid}'])
def test_invalid_jsonld_never_becomes_a_successful_observation(source):
    with pytest.raises(PublicReadError, match="invalid_html"):
        observation('<head><title>New</title><script type="application/ld+json">' + source + '</script></head>')


def test_jsonld_preserves_third_party_graphs_and_removal_is_not_global_deletion():
    baseline = observation('<head><title>A</title><script type="application/ld+json">{"@type":"WebSite"}</script></head>')
    desired = '{"@type":"Product","name":"Зима"}'
    value = record("jsonld", before=None, after=desired)
    page = observation('<head><script type="application/ld+json">{"name":"Зима","@type":"Product"}</script><script type="application/ld+json">{"@type":"WebSite"}</script></head>')
    assert evaluate_public(value, page, baseline).matches
    assert not evaluate_public(value, observation('<head><script type="application/ld+json">' + desired + '</script></head>'), baseline).matches
    duplicate = observation('<head><script type="application/ld+json">' + desired + '</script><script type="application/ld+json">' + desired + '</script></head>')
    assert not evaluate_public(value, duplicate).matches


def test_rollback_missing_or_empty_uses_the_saved_public_baseline_not_admin_absence():
    before = observation('<head><title>Theme default</title></head>')
    value = record(before=None, after="Managed")
    value["operation_type"] = "rollback"
    value["before"], value["after"] = value["after"], value["before"]
    assert evaluate_public(value, before, before).matches
    assert evaluate_public(value, before).error_code == "baseline_required"


def test_public_empty_string_and_missing_element_are_distinct():
    value = record("description", before=None, after="")
    assert not evaluate_public(value, observation("<head></head>")).matches
    assert evaluate_public(value, observation('<head><meta name="description" content=""></head>')).matches


def test_legacy_invalid_managed_jsonld_is_an_explicit_unsupported_result():
    before = observation('<head><script type="application/ld+json">{"@type":"WebSite"}</script></head>')
    result = evaluate_public(record("jsonld", before="{invalid", after='{"@type":"Product"}'), before, before)
    assert not result.matches and result.error_code == "unsupported_predicate"
    assert result.observation == before


def test_xhtml_namespaces_and_deep_jsonld_cannot_be_used_as_html_proof():
    page = observation('<html xmlns="urn:not-html"><head><title>New</title></head></html>')
    page = page.model_copy(update={"headers": {"content-type": "application/xhtml+xml"}})
    assert evaluate_public(record(after="New"), page).error_code == "non_html"
    with pytest.raises(PublicReadError):
        observation('<head><script type="application/ld+json">' + '[' * 1000 + '0' + ']' * 1000 + '</script></head>')


@pytest.mark.asyncio
async def test_worker_spends_authority_lease_before_public_read_and_preserves_wire_evidence():
    from uuid import uuid4
    from services.client_api_gateway.public_verifier import PublicVerifier
    value = {"operation_id": str(uuid4()), "record": record(after="New")}
    token = str(uuid4())
    authority = AsyncMock(side_effect=[{"operation": value, "lease_id": token}, {"state": "verified"}])
    async def read(url):
        assert authority.await_count == 1 and url == URL
        return observation('<head><title>New</title></head>')
    result = await PublicVerifier(authority=authority, reader=SimpleNamespace(read=read)).once()
    assert result["state"] == "verified"
    payload = authority.await_args.args[-1]
    assert payload["lease_id"] == token and payload["result"]["observation"]["body_sha256"]
    assert payload["result"]["checks"][-1]["matches"]


@pytest.mark.asyncio
async def test_worker_with_no_durable_lease_never_reads_public_url():
    from services.client_api_gateway.public_verifier import PublicVerifier
    reader = SimpleNamespace(read=AsyncMock())
    await PublicVerifier(authority=AsyncMock(return_value={"operation": {}, "lease_id": None}), reader=reader).once()
    reader.read.assert_not_called()


@pytest.mark.parametrize("tag", ["template", "noscript", "svg", "math"])
def test_inert_head_metadata_cannot_prove_public_values(tag):
    with pytest.raises(PublicReadError):
        observation(f"<head><{tag}><title>New</title></{tag}></head>")


def test_body_svg_title_and_inert_jsonld_are_not_public_head_facts():
    page = observation('<head><title>Old</title></head><body><svg><title>New</title></svg><template><script type="application/ld+json">{"x":1}</script></template></body>')
    assert page.facts.titles == ("Old",)
    assert page.facts.jsonld == ()


@pytest.mark.asyncio
async def test_reader_forces_anonymous_headers_budgets_and_does_not_store_set_cookie():
    body = b"<head><title>A</title></head>"
    http = SimpleNamespace(policy=NetPolicy(), fetch=AsyncMock(return_value=FetchResult(URL, URL, 200,
        {"content-type": "text/html", "set-cookie": "not-stored", "age": "120"}, body, [], "8.8.8.8", .1)))
    result = await PublicPageReader(http=http, clock=lambda: NOW).read(URL)
    kwargs = http.fetch.await_args.kwargs
    assert not {name.lower() for name in kwargs["headers"]} & {"cookie", "authorization", "x-seo-signature"}
    assert kwargs["policy"].max_redirects == 0 and kwargs["policy"].total_timeout == 10
    assert result.headers == {"content-type": "text/html", "age": "120"}


@pytest.mark.asyncio
async def test_real_dns_policy_blocks_private_destination_before_transport():
    async def resolver(host, port):
        return ["127.0.0.1"]
    with pytest.raises(PublicReadError, match="network_policy"):
        await PublicPageReader(http=SafeHttpClient(resolver=resolver)).read(URL)
