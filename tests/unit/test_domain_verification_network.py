"""Сетевая политика проверки владения на настоящем SafeHttpClient (подставной бэкенд, без сети)."""
import asyncio
from datetime import datetime, timedelta, timezone

import httpcore
import pytest

from database.models import Project
from services.api_gateway.projects import network
from services.api_gateway.projects.verification import DomainVerificationService, VerificationConfig
from services.api_gateway.projects.workspaces import ensure_personal_workspace
from shared.netpolicy import NetPolicyError, SafeHttpClient
from tests.fixtures.project_db import make_engine, make_sessionmaker

UTC = timezone.utc
ALICE = "00000000-0000-4000-8000-00000000000a"


class Stream(httpcore.AsyncNetworkStream):
    def __init__(self, payload):
        self.payload, self.written = payload, bytearray()

    async def read(self, max_bytes, timeout=None):
        chunk, self.payload = self.payload[:max_bytes], self.payload[max_bytes:]
        return chunk

    async def write(self, buffer, timeout=None):
        self.written += buffer

    async def aclose(self):
        pass

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        return self  # TLS в тесте не нужен: проверяется политика, а не шифрование

    def get_extra_info(self, info):
        return None


class Backend(httpcore.AsyncNetworkBackend):
    def __init__(self, responses):
        self.responses, self.connects, self.streams = list(responses), [], []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connects.append((host, port))
        self.streams.append(Stream(self.responses.pop(0)))
        return self.streams[-1]

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise AssertionError("no unix sockets")

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)


def response(body=b"", status=200, headers=None):
    lines = [f"HTTP/1.1 {status} X", f"Content-Length: {len(body)}", *(f"{k}: {v}" for k, v in (headers or {}).items())]
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


def fetcher(responses, mapping=None):
    mapping = mapping or {"example.com": ["93.184.216.34"], "internal.test": ["10.0.0.5"]}

    async def resolver(host, port):
        return mapping[host]

    backend = Backend(responses)
    return network.make_fetch(SafeHttpClient(network._POLICY, resolver=resolver, network_backend=backend)), backend


def run(coro):
    return asyncio.run(coro)


def test_challenge_fetch_is_a_single_get_with_the_verifier_user_agent():
    fetch, backend = fetcher([response(b"body")])
    result = run(fetch("https://example.com/blog/seo-master-challenge.txt"))
    assert result.status == 200 and result.body == b"body"
    sent = bytes(backend.streams[0].written).lower()
    assert sent.startswith(b"get /blog/seo-master-challenge.txt http/1.1")
    assert b"user-agent: seomasterverifier/1.0" in sent
    assert backend.connects == [("93.184.216.34", 443)]


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirects_are_not_followed(status):
    fetch, backend = fetcher([response(b"", status=status, headers={"Location": "https://example.com/elsewhere"})])
    with pytest.raises(NetPolicyError) as caught:
        run(fetch("https://example.com/seo-master-challenge.txt"))
    assert caught.value.code == "redirect_limit"
    assert len(backend.connects) == 1  # на новый адрес не ходили


def test_private_resolution_is_blocked_before_any_connection():
    fetch, backend = fetcher([response(b"x")])
    with pytest.raises(NetPolicyError) as caught:
        run(fetch("https://internal.test/seo-master-challenge.txt"))
    assert caught.value.code == "address_blocked" and backend.connects == []


def test_oversized_answer_is_cut_off():
    fetch, _ = fetcher([response(b"x" * 10_000)])
    with pytest.raises(NetPolicyError) as caught:
        run(fetch("https://example.com/seo-master-challenge.txt"))
    assert caught.value.code == "response_too_large"


def test_policy_limits_are_tight():
    policy = network._POLICY
    assert policy.max_redirects == 0 and policy.max_body_bytes <= 4096 and policy.total_timeout <= 10.0


def test_whole_flow_through_the_real_client_verifies_and_a_redirect_does_not():
    maker = make_sessionmaker(make_engine(__import__("tempfile").mkdtemp()))
    with maker() as db:
        workspace = ensure_personal_workspace(db, ALICE)
        project = Project(id="p1", name="p", url="https://example.com/", owner_id=ALICE, workspace_id=workspace.id,
                          origin="https://example.com", path_prefix="/", status="active")
        db.add(project)
        db.commit()
        now = datetime(2026, 11, 3, tzinfo=UTC)
        clock = [now]

        def tick():
            return clock[0]

        # 1) сайт редиректит файл на другое место
        fetch, _ = fetcher([response(b"", status=301, headers={"Location": "https://example.com/other/seo-master-challenge.txt"})])
        service = DomainVerificationService(db, fetch, VerificationConfig(min_check_interval=timedelta(0)), tick)
        issued = service.issue_challenge(project, ALICE)
        clock[0] += timedelta(seconds=5)
        assert run(service.check(project)).error_code == "redirected"

        # 2) файл на месте
        fetch_ok, _ = fetcher([response(issued.file_body.encode(), headers={"Content-Type": "text/plain"})])
        service = DomainVerificationService(db, fetch_ok, VerificationConfig(min_check_interval=timedelta(0)), tick)
        clock[0] += timedelta(seconds=5)
        assert run(service.check(project)).verified
