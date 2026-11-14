"""Fresh anonymous public HTML read. No CMS client, cookies or user token input."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import re
from typing import Any, Awaitable, Callable, Protocol

from fastapi import HTTPException

from shared.hitl_verification import evaluate_public as evaluate_public
from shared.hitl_verification import CACHE_HEADERS, PublicBaseline, PublicFacts, PublicObservation, VerificationResult, json_value
from shared.netpolicy import NetPolicyError, SafeHttpClient, parse_target


class PublicReadError(ValueError):
    pass


class PublicHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_head = False
        self.heads = self.closed_heads = self.tags = 0
        self.capture: str | None = None
        self.parts: list[str] = []
        self.capture_size = 0
        self.inert: list[str] = []
        self.titles: list[str] = []
        self.descriptions: list[str | None] = []
        self.scripts: list[str] = []
        self.h1: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags += 1
        if self.inert:
            if tag in {"template", "noscript", "svg", "math"}:
                self.inert.append(tag)
            return
        if self.tags > 20000 or (self.capture == "title"):
            raise PublicReadError("invalid_html")
        keys = [key for key, _ in attrs]
        if len(keys) != len(set(keys)):
            raise PublicReadError("invalid_html")
        attributes = dict(attrs)
        if tag in {"template", "noscript", "svg", "math"}:
            if self.in_head:
                raise PublicReadError("invalid_html")
            self.inert.append(tag)
            return
        if tag == "title" and not self.in_head:
            raise PublicReadError("invalid_html")
        if self.in_head and tag not in {"head", "title", "base", "link", "meta", "style", "script"}:
            raise PublicReadError("invalid_html")
        if tag == "head":
            self.heads += 1
            self.in_head = True
        if tag == "meta" and self.in_head and (attributes.get("name") or "").lower() == "description":
            self.descriptions.append(attributes.get("content"))
        if (tag == "title" and self.in_head) or tag == "h1" or (tag == "script" and (attributes.get("type") or "").lower() == "application/ld+json"):
            if self.capture is not None:
                raise PublicReadError("invalid_html")
            self.capture, self.parts = tag, []
            self.capture_size = 0
        if tag == "a" and attributes.get("href") is not None and len(self.links) < 128:
            self.links.append(attributes["href"] or "")
        if len(self.descriptions) > 4:
            raise PublicReadError("invalid_html")

    def handle_data(self, data: str) -> None:
        if self.inert:
            return
        if self.capture is not None:
            self.parts.append(data)
            self.capture_size += len(data)
            if self.capture_size > 32768:
                raise PublicReadError("invalid_html")

    def handle_endtag(self, tag: str) -> None:
        if self.inert:
            if tag == self.inert[-1]:
                self.inert.pop()
            return
        if self.capture == tag:
            value = "".join(self.parts)
            if tag == "title":
                self.titles.append(value)
            elif tag == "h1":
                self.h1.append(value)
            else:
                self.scripts.append(json_value(value))
            self.capture, self.parts = None, []
        if tag == "head":
            self.in_head = False
            self.closed_heads += 1

    def facts(self, source: str) -> PublicFacts:
        try:
            self.feed(source)
            self.close()
            if self.heads != 1 or self.closed_heads != 1 or self.capture is not None or self.inert:
                raise PublicReadError("invalid_html")
            return PublicFacts(titles=tuple(self.titles), descriptions=tuple(self.descriptions), jsonld=tuple(self.scripts),
                               h1=tuple(self.h1), links=tuple(self.links))
        except (ValueError, RecursionError) as exc:
            raise PublicReadError("invalid_html") from exc


class PublicPageReader:
    def __init__(self, *, http: SafeHttpClient | None = None, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        # Forbid redirects entirely: an approved target cannot expand scope at any hop.
        self.http = http or SafeHttpClient()
        self.policy = replace(self.http.policy, max_redirects=0, max_body_bytes=1024 * 1024, total_timeout=10,
                              user_agent="SEOMasterVerifier/1.0")
        self.clock = clock

    async def read(self, url: str) -> PublicObservation:
        try:
            target = parse_target(url, self.policy)
            result = await self.http.fetch(target.url, policy=self.policy,
                headers={"Accept": "text/html", "Cache-Control": "no-cache", "Pragma": "no-cache"})
        except NetPolicyError as exc:
            raise PublicReadError("network_policy" if exc.is_policy_block else "fetch_failed") from None
        if result.final_url != target.url or result.redirects:
            raise PublicReadError("network_policy")
        headers = {key: value[:2048] for key, value in result.headers.items() if key in CACHE_HEADERS}
        facts = None
        if result.status == 200:
            content_type = result.headers.get("content-type", "")
            is_html = content_type.split(";", 1)[0].strip().lower() == "text/html"
            charset = re.search(r"charset\s*=\s*[\"']?([A-Za-z0-9_-]+)", content_type, re.IGNORECASE)
            encoding = charset.group(1).lower() if charset else "utf-8"
            if is_html and encoding in {"utf-8", "utf8", "us-ascii", "windows-1251", "iso-8859-1"}:
                try:
                    facts = PublicHTML().facts(result.body.decode(encoding))
                except (UnicodeError, PublicReadError):
                    facts = None  # Keep HTTP/cache evidence even for unparseable HTML.
        return PublicObservation(url=target.url, final_url=result.final_url, observed_at=self.clock(), status=result.status,
            headers=headers, body_sha256=hashlib.sha256(result.body).hexdigest(), facts=facts)


class VerificationAuthority(Protocol):
    def __call__(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Awaitable[dict[str, Any]]: ...


class PublicVerifier:
    """Only anonymous HTTP and a leased report; no CMS mutation dependency."""
    def __init__(self, *, authority: VerificationAuthority | None = None, reader: PublicPageReader | None = None):
        if authority is None:
            from services.client_api_gateway.hitl_executor import authority_request
            authority = authority_request
        self.authority = authority
        self.reader = reader or PublicPageReader()

    async def once(self, operation_id: str | None = None) -> dict[str, Any] | None:
        claim = await self.authority("POST", "/verification/claim", {"operation_id": operation_id})
        operation, token = claim.get("operation"), claim.get("lease_id")
        if operation is None and token is None:
            return None
        if not isinstance(operation, dict):
            raise HTTPException(502, "verification_claim_invalid")
        if token is None:
            return operation
        from shared.hitl_execution import UUID_PATTERN
        if not isinstance(token, str) or re.fullmatch(UUID_PATTERN, token) is None:
            raise HTTPException(502, "verification_claim_invalid")
        try:
            observed = await self.reader.read(operation["record"]["version"]["target"]["url"])
        except PublicReadError as exc:
            result = VerificationResult(error_code="network_policy" if str(exc) == "network_policy" else "fetch_failed")
        else:
            baseline = PublicBaseline.load(operation["public_baseline"]).observation if operation.get("public_baseline") else None
            result = evaluate_public(operation["record"], observed, baseline)
        return await self.authority("POST", "/verification/" + operation["operation_id"] + "/report",
                                    {"lease_id": token, "result": result.model_dump(mode="json")})
