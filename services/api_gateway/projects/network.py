"""Сетевой доступ проверки владения: SafeHttpClient без редиректов и с малым телом."""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

from shared.netpolicy import FetchResult, NetPolicy, SafeHttpClient

CHALLENGE_USER_AGENT = "SEOMasterVerifier/1.0"
_POLICY = NetPolicy(
    max_redirects=0,  # редирект не расширяет область подтверждения
    connect_timeout=3.0,
    read_timeout=5.0,
    total_timeout=10.0,
    max_body_bytes=4096,
    user_agent=CHALLENGE_USER_AGENT,
)


def make_fetch(client: Optional[SafeHttpClient] = None) -> Callable[[str], Awaitable[FetchResult]]:
    http = client or SafeHttpClient(_POLICY)

    async def fetch(url: str) -> FetchResult:
        return await http.fetch(url, method="GET", policy=_POLICY)

    return fetch
