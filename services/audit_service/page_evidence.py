"""Evidence is extracted from fetched HTML before committing the full audit result."""

import hashlib

from bs4 import BeautifulSoup

from shared.page_evidence import seal_evidence


def attach_page_evidence(pages: list, *, audit_id: str, project_id: str,
                         recorded_at: str) -> list:
    result = []
    for page in pages:
        # Never carry an untrusted or stale envelope into a new audit snapshot.
        item = {k: v for k, v in page.items() if k != "page_evidence"}
        html = item.get("html")
        status = item.get("status_code")
        if (isinstance(html, str) and html and isinstance(status, int)
                and not isinstance(status, bool) and 200 <= status < 300 and not item.get("error")):
            soup = BeautifulSoup(html, "html.parser")
            title = soup.find("title")
            description = soup.find("meta", attrs={
                "name": lambda value: isinstance(value, str) and value.lower() == "description",
            })
            jsonld = soup.find_all("script", attrs={
                "type": lambda value: isinstance(value, str) and value.strip().lower() == "application/ld+json",
            })
            lang = soup.find("html")
            facts = {
                "title": title.get_text() if title is not None else None,
                "description": description.get("content") if description is not None else None,
                "h1": [h.get_text() for h in soup.find_all("h1")],
                "lang": lang.get("lang") if lang is not None else None,
                "jsonld_present": bool(jsonld),
                # Multiple blocks are not interchangeable with a single CMS field.
                "jsonld": jsonld[0].get_text() if len(jsonld) == 1 else None,
            }
            for node in soup(["script", "style", "noscript", "template"]):
                node.decompose()
            main = soup.find("main") or soup.find("article") or soup.find("body")
            text = main.get_text(" ", strip=True) if main is not None else None
            facts["text"] = text[:50_000] if text is not None else None
            item["page_evidence"] = seal_evidence({
                "schema_version": "page_evidence_v1", "audit_id": audit_id,
                "project_id": project_id, "url": item["url"],
                "final_url": item.get("final_url") or item["url"],
                "status_code": status, "recorded_at": recorded_at,
                "text_truncated": text is not None and len(text) > 50_000,
                "html_hash": hashlib.sha256(html.encode("utf-8")).hexdigest(),
                "jsonld_blocks": len(jsonld), "facts": facts,
            })
        result.append(item)
    return result
