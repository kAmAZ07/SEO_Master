"""Synthetic public facts for transport-stub tests; not a native HTTP proof."""
from datetime import datetime, timezone
from shared.hitl_verification import PublicFacts, PublicObservation


def public_observation(url="https://example.com/page", title="Old"):
    return PublicObservation(url=url, final_url=url, observed_at=datetime.now(timezone.utc), status=200,
        headers={"content-type": "text/html"}, body_sha256="b"*64,
        facts=PublicFacts(titles=(title,), descriptions=(), jsonld=(), h1=(), links=()))
