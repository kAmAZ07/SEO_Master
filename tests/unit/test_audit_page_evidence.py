"""Real HTML extraction and content-addressed evidence; no network is used."""

from copy import deepcopy

import pytest

from services.audit_service.page_evidence import attach_page_evidence
from services.audit_service.public_view import sanitize_page
from shared.page_evidence import verify_evidence


def snapshot(html='<html lang="ru"><title></title><body><h1>Один</h1><h1>Два</h1>'
                  '<main>Факты<script>secret()</script></main></body></html>'):
    return attach_page_evidence(
        [{"url": "https://example.com/", "status_code": 200, "html": html}],
        audit_id="audit-1", project_id="project-1", recorded_at="2026-11-09T12:00:00+00:00",
    )[0]


def test_real_html_preserves_missing_empty_multiple_headings_and_excludes_script_text():
    page = snapshot()
    evidence = page["page_evidence"]
    assert verify_evidence(evidence)
    facts = evidence["snapshot"]["facts"]
    assert facts["title"] == "" and facts["description"] is None
    assert facts["h1"] == ["Один", "Два"]
    assert facts["text"] == "Факты"
    assert facts["jsonld_present"] is False and facts["jsonld"] is None
    assert "page_evidence" not in sanitize_page(page)
    assert snapshot() == page


@pytest.mark.parametrize("field,value", [("project_id", "other"), ("url", "https://other.com/"),
                                        ("html_hash", "0" * 64), ("facts", {})])
def test_any_snapshot_mutation_invalidates_evidence(field, value):
    evidence = deepcopy(snapshot()["page_evidence"])
    evidence["snapshot"][field] = value
    assert not verify_evidence(evidence)


def test_multiple_jsonld_blocks_never_become_an_arbitrary_single_before():
    evidence = snapshot('<script type="application/ld+json">{ "a": 1 }</script>'
                        '<script type="application/ld+json">{ "b": 2 }</script>')["page_evidence"]
    assert evidence["snapshot"]["jsonld_blocks"] == 2
    assert evidence["snapshot"]["facts"]["jsonld_present"] is True
    assert evidence["snapshot"]["facts"]["jsonld"] is None


def test_single_jsonld_preserves_exact_string():
    source = '{ "@type": "WebPage" }'
    facts = snapshot(f'<script type="application/ld+json">{source}</script>')["page_evidence"]["snapshot"]["facts"]
    assert facts["jsonld"] == source


def test_html_attribute_value_case_does_not_turn_existing_fields_into_missing():
    facts = snapshot('<meta name="Description" content=""><script type="APPLICATION/LD+JSON">{}</script>')["page_evidence"]["snapshot"]["facts"]
    assert facts["description"] == ""
    assert facts["jsonld_present"] is True and facts["jsonld"] == "{}"


@pytest.mark.parametrize("status", [0, 301, 404, 500, True])
def test_unsuccessful_fetch_does_not_create_evidence(status):
    page = {"url": "https://example.com/", "status_code": status, "html": "<title>x</title>",
            "page_evidence": {"stale": True}}
    result = attach_page_evidence([page], audit_id="a", project_id="p", recorded_at="now")
    assert "page_evidence" not in result[0]
