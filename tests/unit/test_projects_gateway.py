"""HTTP-слой проектов: изоляция пользователей, создание по области, подтверждение домена, присоединение аудита, шлюз полного аудита."""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("httpx")
pytest.importorskip("pydantic_settings")

from fastapi import Depends, FastAPI
from sqlalchemy import select
from starlette.testclient import TestClient

from config.database_config import get_db
from database.models import DomainAuthorization, Project, PublicAuditResult, WorkspaceMember
from services.api_gateway.auth import get_current_user
from services.api_gateway.config import settings
from services.api_gateway.projects.verification import DomainVerificationService, VerificationConfig
from services.api_gateway.routes import integrations_routes
from services.api_gateway.routes.protected import audit_routes, projects_routes
from shared.netpolicy import FetchResult
from shared.projects.claims import hash_claim_token
from tests.fixtures.project_db import make_engine, make_sessionmaker

UTC = timezone.utc
ALICE = "00000000-0000-4000-8000-00000000000a"
BOB = "00000000-0000-4000-8000-00000000000b"
AUDIT_ID = "0123abcd-0123-4abc-8def-0123456789ab"


class Site:
    def __init__(self):
        self.status, self.body, self.requests = 404, b"", []

    async def __call__(self, url):
        self.requests.append(url)
        return FetchResult(url, url, self.status, {"content-type": "text/plain"}, self.body, [], None, 0.0)


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.now


@pytest.fixture()
def stand(tmp_path, monkeypatch):
    maker = make_sessionmaker(make_engine(str(tmp_path)))
    site, clock = Site(), Clock()
    proxied = []

    app = FastAPI()
    app.include_router(projects_routes.router)
    app.include_router(audit_routes.router)
    app.include_router(integrations_routes.router)

    def db_override():
        db = maker()
        try:
            yield db
        finally:
            db.close()

    from fastapi import Header

    async def user_override(x_user: str = Header(...)):
        return SimpleNamespace(id=x_user, is_active=True)

    def service_override(db=Depends(db_override)):
        config = VerificationConfig(min_check_interval=timedelta(seconds=5))
        return DomainVerificationService(db, site, config, clock)

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = user_override
    app.dependency_overrides[projects_routes.get_verification_service] = service_override

    async def fake_proxy(service_url, method, path, **kwargs):
        proxied.append((method, path, kwargs.get("payload")))
        return SimpleNamespace(json=lambda: {"audit_id": "a1", "status": "queued", "mode": "full"})

    monkeypatch.setattr(audit_routes, "_proxy_service", fake_proxy)
    # Проверка свежести в /audit/full собирает свой сервис: подменяем сеть, чтобы тест не ходил наружу.
    monkeypatch.setattr(audit_routes, "make_fetch", lambda: site)

    client = TestClient(app, raise_server_exceptions=True)
    return SimpleNamespace(client=client, maker=maker, site=site, clock=clock, proxied=proxied)


def call(stand, user, method, path, **kwargs):
    headers = {"X-User": user, **kwargs.pop("headers", {})}
    return stand.client.request(method, path, headers=headers, **kwargs)


def create(stand, user, url="https://example.com", name="Site", **extra):
    return call(stand, user, "POST", "/api/projects", json={"name": name, "url": url, **extra})


def detail(response):
    return response.json()["detail"]


# --- создание --------------------------------------------------------------------------------------------------

def test_create_project_builds_scope_and_personal_workspace(stand):
    response = create(stand, ALICE, "https://Example.com/Blog/?utm=1", name="  My blog  ")
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "My blog"
    assert body["scope"] == {"origin": "https://example.com", "pathPrefix": "/Blog"}
    assert body["verification"]["state"] == "unverified" and body["verification"]["freshDays"] == 30
    with stand.maker() as db:
        project = db.get(Project, body["id"])
        assert project.owner_id == ALICE
        assert db.get(WorkspaceMember, (project.workspace_id, ALICE)).role == "owner"


def test_owner_and_workspace_in_the_body_are_ignored(stand):
    response = create(stand, ALICE, owner_id=BOB, ownerId=BOB, workspace_id="x", workspaceId="x")
    assert response.status_code == 201
    with stand.maker() as db:
        assert db.get(Project, response.json()["id"]).owner_id == ALICE
    assert call(stand, BOB, "GET", "/api/projects").json() == []


def test_creating_the_same_site_again_returns_the_same_project(stand):
    first = create(stand, ALICE, "https://example.com/")
    second = create(stand, ALICE, "HTTPS://EXAMPLE.COM:443")
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]
    assert len(call(stand, ALICE, "GET", "/api/projects").json()) == 1


def test_domain_is_not_a_global_lock_between_users(stand):
    a, b = create(stand, ALICE), create(stand, BOB)
    assert (a.status_code, b.status_code) == (201, 201)
    assert a.json()["id"] != b.json()["id"]
    assert [p["id"] for p in call(stand, ALICE, "GET", "/api/projects").json()] == [a.json()["id"]]
    assert [p["id"] for p in call(stand, BOB, "GET", "/api/projects").json()] == [b.json()["id"]]


def test_different_path_prefixes_on_one_host_are_separate_projects(stand):
    ids = {create(stand, ALICE, url).json()["id"] for url in ("https://example.com/", "https://example.com/blog", "https://example.com/shop")}
    assert len(ids) == 3


@pytest.mark.parametrize(
    "url",
    [
        "", "not a url", "example.com", "ftp://example.com", "javascript:alert(1)", "https://user:pw@example.com/",
        "https://example.com:8080/", "https://localhost/", "http://127.0.0.1/", "http://169.254.169.254/latest",
        "http://[::1]/", "http://10.0.0.5/", "https://2130706433/", "https://example.com/a%2fb", "https://example.com/../x",
        "https://example.com/" + "a" * 2100,
    ],
)
def test_unsafe_or_malformed_urls_are_rejected(stand, url):
    response = create(stand, ALICE, url)
    assert response.status_code == 422
    with stand.maker() as db:
        assert db.scalars(select(Project)).all() == []


@pytest.mark.parametrize("name", ["", "   ", "x" * 256])
def test_invalid_names_are_rejected(stand, name):
    assert create(stand, ALICE, name=name).status_code == 422


def test_public_ip_literal_is_accepted(stand):
    assert create(stand, ALICE, "https://93.184.216.34/").status_code == 201


# --- изоляция: пользователь B с идентификаторами пользователя A ----------------------------------------------------

@pytest.mark.parametrize(
    "method, suffix",
    [
        ("GET", ""), ("DELETE", ""),
        ("GET", "/verification"), ("POST", "/verification/challenge"), ("POST", "/verification/check"), ("DELETE", "/verification"),
        ("GET", "/integrations"), ("POST", "/integrations/wordpress/secret"), ("DELETE", "/integrations/wordpress"),
    ],
)
def test_foreign_project_is_indistinguishable_from_missing(stand, method, suffix):
    project_id = create(stand, ALICE).json()["id"]
    body = {"json": {"baseUrl": "https://example.com"}} if method == "POST" and "integrations" in suffix else {}
    foreign = call(stand, BOB, method, f"/api/projects/{project_id}{suffix}", **body)
    missing = call(stand, BOB, method, f"/api/projects/{uuid.uuid4()}{suffix}", **body)
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    with stand.maker() as db:
        assert db.get(Project, project_id) is not None  # чужой DELETE ничего не удалил


def test_foreign_user_cannot_start_a_full_audit_on_someone_elses_project(stand):
    project_id = create(stand, ALICE).json()["id"]
    response = call(stand, BOB, "POST", "/api/audit/full", json={"projectId": project_id})
    assert response.status_code == 404 and stand.proxied == []


def test_owner_can_delete_and_children_go_with_it(stand):
    project_id = create(stand, ALICE).json()["id"]
    call(stand, ALICE, "POST", f"/api/projects/{project_id}/verification/challenge")
    assert call(stand, ALICE, "DELETE", f"/api/projects/{project_id}").json() == {"success": True}
    assert call(stand, ALICE, "GET", f"/api/projects/{project_id}").status_code == 404
    with stand.maker() as db:
        assert db.scalars(select(DomainAuthorization)).all() == []


def test_viewer_can_read_but_not_manage(stand):
    project_id = create(stand, ALICE).json()["id"]
    with stand.maker() as db:
        workspace_id = db.get(Project, project_id).workspace_id
        db.add(WorkspaceMember(workspace_id=workspace_id, user_id=BOB, role="viewer"))
        db.commit()
    assert call(stand, BOB, "GET", f"/api/projects/{project_id}").status_code == 200
    assert call(stand, BOB, "GET", f"/api/projects/{project_id}/verification").status_code == 200
    for method, suffix in (("DELETE", ""), ("POST", "/verification/challenge"), ("POST", "/verification/check"), ("DELETE", "/verification")):
        response = call(stand, BOB, method, f"/api/projects/{project_id}{suffix}")
        assert response.status_code == 403 and detail(response)["error"] == "insufficient_role", (method, suffix)


# --- подтверждение через API ---------------------------------------------------------------------------------------------------

def test_verification_flow_end_to_end_over_http(stand):
    project_id = create(stand, ALICE, "https://example.com/blog").json()["id"]
    base = f"/api/projects/{project_id}/verification"

    assert call(stand, ALICE, "GET", base).json()["state"] == "unverified"
    assert call(stand, ALICE, "POST", base + "/check").status_code == 409  # challenge ещё не выдан

    issued = call(stand, ALICE, "POST", base + "/challenge")
    assert issued.status_code == 201
    payload = issued.json()
    assert payload["fileUrl"] == "https://example.com/blog/seo-master-challenge.txt"
    assert payload["fileBody"].startswith("seo-master-site-verification=")
    assert payload["verification"]["state"] == "pending"

    stand.clock.now += timedelta(seconds=10)
    missing_file = call(stand, ALICE, "POST", base + "/check").json()
    assert missing_file["verified"] is False and missing_file["errorCode"] == "http_404"

    stand.clock.now += timedelta(seconds=1)
    too_early = call(stand, ALICE, "POST", base + "/check")
    assert too_early.status_code == 429 and int(too_early.headers["Retry-After"]) >= 1
    assert detail(too_early)["error"] == "check_too_frequent"

    stand.clock.now += timedelta(seconds=10)
    stand.site.status, stand.site.body = 200, payload["fileBody"].encode()
    done = call(stand, ALICE, "POST", base + "/check").json()
    assert done["verified"] is True and done["verification"]["state"] == "verified"
    assert done["verification"]["freshUntil"].startswith("2026-12-03")

    token = payload["fileBody"].split("=", 1)[1]
    for path in (base, f"/api/projects/{project_id}", "/api/projects"):
        assert token not in call(stand, ALICE, "GET", path).text  # токен показывается один раз

    revoked = call(stand, ALICE, "DELETE", base).json()
    assert revoked["verification"]["state"] == "unverified"
    assert call(stand, ALICE, "DELETE", base).status_code == 409


def test_serialization_never_leaks_the_challenge_hash(stand):
    project_id = create(stand, ALICE).json()["id"]
    call(stand, ALICE, "POST", f"/api/projects/{project_id}/verification/challenge")
    with stand.maker() as db:
        stored_hash = db.scalars(select(DomainAuthorization.challenge_hash)).one()
    for path in (f"/api/projects/{project_id}", f"/api/projects/{project_id}/verification", "/api/projects"):
        assert stored_hash not in call(stand, ALICE, "GET", path).text


# --- присоединение публичного аудита -----------------------------------------------------------------------------------------------

def add_public_audit(stand, *, token="secret-claim-token", url="https://example.com/", mode="public", status="completed", project_id=None, audit_id=AUDIT_ID, with_hash=True):
    with stand.maker() as db:
        db.add(PublicAuditResult(
            audit_id=audit_id, project_id=project_id, root_url=url, mode=mode, status=status,
            claim_token_hash=hash_claim_token(token) if with_hash else None,
            seeds=[], summary={}, findings=[], pages=[], options={},
        ))
        db.commit()


def audit_row(stand, audit_id=AUDIT_ID):
    with stand.maker() as db:
        row = db.get(PublicAuditResult, audit_id)
        return row.project_id, row.claim_token_hash


def test_claim_with_the_creators_token_attaches_the_audit_once(stand):
    add_public_audit(stand)
    response = create(stand, ALICE, sourceAuditId=AUDIT_ID, claimToken="secret-claim-token")
    assert response.json()["claim"] == {"status": "claimed", "reason": None}
    project_id, token_hash = audit_row(stand)
    assert project_id == response.json()["id"] and token_hash is None  # токен одноразовый
    assert response.json()["stats"]["audits"] == 1


def test_audit_id_alone_does_not_attach_the_audit(stand):
    add_public_audit(stand)
    for extra in ({"sourceAuditId": AUDIT_ID}, {"sourceAuditId": AUDIT_ID, "claimToken": ""}, {"claimToken": "secret-claim-token"}):
        response = create(stand, BOB, **extra)
        assert response.status_code in (200, 201)
        assert response.json()["claim"]["status"] == "rejected"
    assert audit_row(stand)[0] is None


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"token_sent": "wrong"}, "invalid_claim"),
        ({"audit_id_sent": "ffffffff-ffff-4fff-8fff-ffffffffffff"}, "invalid_claim"),
        ({"audit_id_sent": "../etc/passwd"}, "invalid_claim"),
        ({"mode": "full"}, "invalid_claim"),
        ({"status": "expired"}, "invalid_claim"),
        ({"with_hash": False}, "invalid_claim"),
        ({"url": "https://other.org/"}, "out_of_scope"),
        ({"url": "https://example.com/other"}, "out_of_scope"),
        ({"project_id": "already-attached"}, "already_claimed"),
    ],
)
def test_rejected_claims_leave_the_audit_untouched_and_still_create_the_project(stand, overrides, reason):
    overrides = dict(overrides)
    token_sent = overrides.pop("token_sent", "secret-claim-token")
    audit_id_sent = overrides.pop("audit_id_sent", AUDIT_ID)
    add_public_audit(stand, **overrides)
    before = audit_row(stand)
    response = create(stand, ALICE, "https://example.com/blog" if overrides.get("url") == "https://example.com/other" else "https://example.com/", sourceAuditId=audit_id_sent, claimToken=token_sent)
    assert response.status_code == 201
    assert response.json()["claim"] == {"status": "rejected", "reason": reason}
    assert audit_row(stand) == before


def test_second_user_cannot_reuse_a_burned_token(stand):
    add_public_audit(stand)
    assert create(stand, ALICE, sourceAuditId=AUDIT_ID, claimToken="secret-claim-token").json()["claim"]["status"] == "claimed"
    reuse = create(stand, BOB, sourceAuditId=AUDIT_ID, claimToken="secret-claim-token")
    assert reuse.json()["claim"] == {"status": "rejected", "reason": "invalid_claim"}
    assert audit_row(stand)[0] != reuse.json()["id"]


def test_claim_can_target_an_existing_project_of_the_same_user(stand):
    existing = create(stand, ALICE).json()["id"]
    add_public_audit(stand)
    again = create(stand, ALICE, sourceAuditId=AUDIT_ID, claimToken="secret-claim-token")
    assert again.status_code == 200 and again.json()["id"] == existing
    assert audit_row(stand)[0] == existing


# --- полный аудит: область и подтверждение ------------------------------------------------------------------------------------------------

def verify_directly(stand, project_id, *, verified_at=None, status="verified"):
    now = verified_at or datetime.now(UTC)
    with stand.maker() as db:
        project = db.get(Project, project_id)
        db.add(DomainAuthorization(
            id=str(uuid.uuid4()), project_id=project_id, workspace_id=project.workspace_id, status=status,
            scope_origin=project.origin, scope_path_prefix=project.path_prefix, challenge_hash="0" * 64,
            subject_user_id=ALICE, issued_at=now, expires_at=now + timedelta(hours=1), verified_at=now, evidence={},
        ))
        db.commit()


def test_full_audit_requires_confirmation(stand):
    project_id = create(stand, ALICE).json()["id"]
    response = call(stand, ALICE, "POST", "/api/audit/full", json={"projectId": project_id})
    assert response.status_code == 403
    assert detail(response)["error"] == "domain_not_verified" and detail(response)["reason"] == "not_verified"
    assert stand.proxied == []


def test_full_audit_runs_after_confirmation_and_stays_inside_the_scope(stand):
    project_id = create(stand, ALICE, "https://example.com/blog").json()["id"]
    verify_directly(stand, project_id)

    ok = call(stand, ALICE, "POST", "/api/audit/full", json={"projectId": project_id})
    assert ok.status_code == 200 and ok.json()["project_id"] == project_id
    assert stand.proxied[-1][2]["root_url"] == "https://example.com/blog"
    inside = call(stand, ALICE, "POST", "/api/audit/full", json={"projectId": project_id, "url": "https://example.com/blog/post-1"})
    assert inside.status_code == 200

    count = len(stand.proxied)
    for outside in ("https://example.com/", "https://example.com/admin", "https://example.com/blog/../admin",
                    "https://example.com/blog%2f..%2fadmin", "https://evil.com/blog", "http://example.com/blog"):
        response = call(stand, ALICE, "POST", "/api/audit/full", json={"projectId": project_id, "url": outside})
        assert response.status_code == 422 and detail(response)["error"] == "url_out_of_scope", outside
    assert len(stand.proxied) == count


def test_stale_confirmation_blocks_full_audit(stand):
    project_id = create(stand, ALICE).json()["id"]
    verify_directly(stand, project_id, verified_at=datetime.now(UTC) - timedelta(days=31))
    response = call(stand, ALICE, "POST", "/api/audit/full", json={"projectId": project_id})
    assert response.status_code == 403 and detail(response)["reason"] == "stale"
    assert call(stand, ALICE, "GET", f"/api/projects/{project_id}").json()["verification"]["state"] == "stale"


def test_revoked_confirmation_blocks_full_audit(stand):
    project_id = create(stand, ALICE).json()["id"]
    verify_directly(stand, project_id, status="revoked")
    assert call(stand, ALICE, "POST", "/api/audit/full", json={"projectId": project_id}).status_code == 403


def test_enforcement_flag_off_still_enforces_the_scope(stand, monkeypatch):
    monkeypatch.setattr(settings, "DOMAIN_VERIFICATION_ENFORCED", False)
    project_id = create(stand, ALICE, "https://example.com/blog").json()["id"]
    assert call(stand, ALICE, "POST", "/api/audit/full", json={"projectId": project_id}).status_code == 200
    outside = call(stand, ALICE, "POST", "/api/audit/full", json={"projectId": project_id, "url": "https://example.com/admin"})
    assert outside.status_code == 422


def test_legacy_project_without_scope_cannot_start_a_full_audit(stand):
    project_id = create(stand, ALICE).json()["id"]
    with stand.maker() as db:
        project = db.get(Project, project_id)
        project.origin = project.path_prefix = None
        project.status = "scope_review"
        db.commit()
    response = call(stand, ALICE, "POST", "/api/audit/full", json={"projectId": project_id})
    assert response.status_code == 422 and detail(response)["error"] == "project_scope_unresolved"
    assert call(stand, ALICE, "GET", f"/api/projects/{project_id}").status_code == 200  # читать можно


def test_viewer_cannot_start_full_audit(stand):
    project_id = create(stand, ALICE).json()["id"]
    verify_directly(stand, project_id)
    with stand.maker() as db:
        db.add(WorkspaceMember(workspace_id=db.get(Project, project_id).workspace_id, user_id=BOB, role="viewer"))
        db.commit()
    assert call(stand, BOB, "POST", "/api/audit/full", json={"projectId": project_id}).status_code == 403
    assert stand.proxied == []


def test_claim_lost_to_a_concurrent_request_is_reported_as_already_claimed(stand, monkeypatch):
    from services.api_gateway.projects import claims

    add_public_audit(stand)
    real_execute = claims.update

    def thief_first(model):
        # Между проверкой и условным UPDATE аудит присоединил другой запрос.
        with stand.maker() as other:
            row = other.get(PublicAuditResult, AUDIT_ID)
            row.project_id = "someone-else"
            other.commit()
        return real_execute(model)

    monkeypatch.setattr(claims, "update", thief_first)
    response = create(stand, ALICE, sourceAuditId=AUDIT_ID, claimToken="secret-claim-token")
    assert response.json()["claim"] == {"status": "rejected", "reason": "already_claimed"}
    assert audit_row(stand)[0] == "someone-else"
