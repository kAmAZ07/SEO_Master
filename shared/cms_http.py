"""Bounded CMS requests through the existing DNS/connect/TLS policy backend.

Unlike the public crawler, mutation requests never follow redirects or retry.
Only server-owned WordPress bindings enter this transport; bodies are exact bytes.
"""
import asyncio
from dataclasses import replace
import time

import httpcore

from shared.netpolicy import NetPolicy, NetPolicyError, Resolver, _GuardedBackend, default_resolver, parse_target
from shared.hitl_execution import ExecutionError


class CMSHttpClient:
    def __init__(self, *, policy: NetPolicy | None = None, resolver: Resolver = default_resolver,
                 network_backend: httpcore.AsyncNetworkBackend | None = None):
        self.policy = replace(policy or NetPolicy(), max_redirects=0, max_body_bytes=131072, total_timeout=10)
        self.resolver, self.backend = resolver, network_backend

    async def request(self, method: str, url: str, body: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
        if method not in {"GET", "POST"} or len(body) > 16384 or (method == "GET" and body):
            raise ExecutionError("cms_request_invalid")
        try:
            return await asyncio.wait_for(self._request(method, url, body, headers), self.policy.total_timeout)
        except (httpcore.NetworkError, httpcore.ProtocolError, httpcore.TimeoutException, NetPolicyError, TimeoutError):
            raise ExecutionError("cms_transport_unavailable") from None

    async def _request(self, method: str, url: str, body: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
        target = parse_target(url, self.policy)
        backend = _GuardedBackend(self.policy, self.resolver, self.backend or httpcore.AnyIOBackend())
        wire_headers = [(b"host", target.host_header.encode()), (b"accept-encoding", b"identity"), (b"connection", b"close")]
        for key, value in headers.items():
            if key.lower() in {"host", "content-length", "transfer-encoding", "connection", "accept-encoding"}:
                raise ExecutionError("cms_header_invalid")
            wire_headers.append((key.lower().encode("ascii"), value.encode("ascii")))
        started = time.monotonic()
        async with httpcore.AsyncConnectionPool(network_backend=backend, max_connections=1, retries=0) as pool:
            async with pool.stream(method, target.url, headers=wire_headers, content=body,
                                   extensions={"timeout": {"connect": 3, "read": 5, "write": 5, "pool": 3}}) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    raise ExecutionError("cms_redirect_forbidden")
                result = bytearray()
                for response_key, response_value in response.headers:
                    if response_key.lower() == b"content-encoding" and response_value.lower() not in {b"", b"identity"}:
                        raise ExecutionError("cms_encoding_forbidden")
                async for chunk in response.aiter_stream():
                    result.extend(chunk)
                    if len(result) > self.policy.max_body_bytes or time.monotonic() - started > self.policy.total_timeout:
                        raise ExecutionError("cms_response_budget_exceeded")
                return response.status, bytes(result)
