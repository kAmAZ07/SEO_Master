"""HITL proxy forwards the authenticated token and exact preview references, with role checks."""

from types import SimpleNamespace

from fastapi import FastAPI, Header
from fastapi.testclient import TestClient
import httpx
import pytest

from config.database_config import get_db
from database.models import Project, Workspace, WorkspaceMember
from services.api_gateway.auth import get_current_user
from services.api_gateway.routes.protected import hitl_routes
from tests.fixtures.project_db import make_engine, make_sessionmaker

ALICE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
BOB = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
WORKSPACE = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
OTHER_WORKSPACE = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
PROJECT_ID = "11111111-1111-4111-8111-111111111111"
TASK_ID = "22222222-2222-4222-8222-222222222222"
APPROVAL_ID = "33333333-3333-4333-8333-333333333333"
VERSION_ID = "44444444-4444-4444-8444-444444444444"


@pytest.fixture()
def stand(tmp_path, monkeypatch):
    maker = make_sessionmaker(make_engine(str(tmp_path)))
    with maker() as db:
        db.add_all([Workspace(id=WORKSPACE, name="A", kind="personal", owner_id=ALICE),
                    Workspace(id=OTHER_WORKSPACE, name="B", kind="personal", owner_id=BOB)])
        db.flush()
        db.add_all([WorkspaceMember(workspace_id=WORKSPACE, user_id=ALICE, role="reviewer"),
                    WorkspaceMember(workspace_id=OTHER_WORKSPACE, user_id=BOB, role="owner"),
                    Project(id=PROJECT_ID, name="A", owner_id=ALICE, workspace_id=WORKSPACE,
                            url="https://example.com/", origin="https://example.com", path_prefix="/"),
                    Project(id=VERSION_ID, name="B same domain", owner_id=BOB, workspace_id=OTHER_WORKSPACE,
                            url="https://example.com/", origin="https://example.com", path_prefix="/")])
        db.commit()
    app = FastAPI()
    app.include_router(hitl_routes.router)

    def database():
        with maker() as db:
            yield db

    async def user(x_user: str = Header(default=ALICE)):
        return SimpleNamespace(id=x_user, is_active=True)

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = user
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.path.startswith("/internal/executions/"):
            return httpx.Response(200, json={"state": "cancelled" if request.method == "POST" else "queued", "record": {"task_id": TASK_ID, "project_id": PROJECT_ID}})
        if request.url.path.endswith("/history"):
            return httpx.Response(200, json=[])
        if request.method == "GET":
            return httpx.Response(200, json={"id": APPROVAL_ID, "task_id": TASK_ID, "project_id": PROJECT_ID})
        return httpx.Response(200, json={"status": "approved", "publication_status": "not_requested"})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(hitl_routes.httpx, "AsyncClient",
                        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(hitl_routes.settings, "INTERNAL_API_KEY", "k" * 32)
    with TestClient(app) as client:
        yield client, maker, calls


def command():
    return {"approval_id": APPROVAL_ID, "recommendation_version_id": VERSION_ID,
            "content_hash": "a" * 64, "comment": "Reviewed"}


def test_exact_version_and_original_access_token_reach_management(stand):
    import json
    client, _, calls = stand
    response = client.post(f"/api/hitl/tasks/{TASK_ID}/approve", json=command(),
                           headers={"Authorization": "Bearer fixture-access"})
    assert response.status_code == 200
    assert response.json()["publication_status"] == "not_requested"
    assert [call.method for call in calls] == ["GET", "POST"]
    assert json.loads(calls[-1].content) == command()
    assert all(call.headers["authorization"] == "Bearer fixture-access" for call in calls)
    assert all(call.headers["x-internal-api-key"] == "k" * 32 for call in calls)
    assert "user_id" not in json.loads(calls[-1].content)


@pytest.mark.parametrize("role", ["viewer", "deployer"])
def test_readable_task_is_not_approvable_by_reader_or_deployer(stand, role):
    client, maker, calls = stand
    with maker() as db:
        db.get(WorkspaceMember, (WORKSPACE, ALICE)).role = role
        db.commit()
    response = client.post(f"/api/hitl/tasks/{TASK_ID}/approve", json=command())
    assert response.status_code == 403
    assert [call.method for call in calls] == ["GET"]


@pytest.mark.parametrize("action", ["approve", "reject", "revoke"])
def test_other_workspace_same_domain_is_not_ownership(stand, action):
    client, _, calls = stand
    response = client.post(f"/api/hitl/tasks/{TASK_ID}/{action}", json=command(), headers={"X-User": BOB})
    assert response.status_code == 404
    assert all(call.method == "GET" for call in calls)


def test_comment_only_or_forged_body_identity_is_rejected_before_proxy(stand):
    client, _, calls = stand
    for body in ({"comment": "old client"}, {**command(), "user_id": BOB}):
        assert client.post(f"/api/hitl/tasks/{TASK_ID}/approve", json=body).status_code == 422
    assert calls == []


def test_foreign_user_cannot_read_approval_history(stand):
    client, _, calls = stand
    response = client.get(f"/api/hitl/tasks/{TASK_ID}/history", headers={"X-User": BOB})
    assert response.status_code == 404
    assert len(calls) == 1 and not calls[0].url.path.endswith("/history")


def test_history_forwards_bounded_pagination_and_authenticated_headers(stand):
    client, _, calls = stand
    response = client.get(f"/api/hitl/tasks/{TASK_ID}/history", params={"limit": 2, "offset": 4},
                           headers={"Authorization": "Bearer fixture-access"})
    assert response.status_code == 200 and response.json() == []
    assert calls[-1].url.params["limit"] == "2" and calls[-1].url.params["offset"] == "4"
    assert calls[-1].headers["authorization"] == "Bearer fixture-access"


def test_apply_requires_deploy_role_and_sends_only_original_references_to_client_gateway(stand):
    import json
    client, maker, calls = stand
    body = {key: value for key, value in command().items() if key != "comment"}
    body.update(task_id=TASK_ID, operation_id=BOB)
    assert client.post(f"/api/hitl/tasks/{TASK_ID}/apply", json=body).status_code == 403
    assert [call.method for call in calls] == ["GET"]
    with maker() as db:
        db.get(WorkspaceMember, (WORKSPACE, ALICE)).role = "deployer"
        db.commit()
    calls.clear()
    response = client.post(f"/api/hitl/tasks/{TASK_ID}/apply", json=body, headers={"Authorization": "Bearer original-access"})
    assert response.status_code == 200
    assert calls[-1].url.path == "/internal/executions"
    assert json.loads(calls[-1].content) == body
    assert calls[-1].headers["authorization"] == "Bearer original-access"


def test_apply_rejects_mutable_changes_and_path_task_mismatch_before_proxy(stand):
    client, _, calls = stand
    body = {key: value for key, value in command().items() if key != "comment"}
    body.update(task_id=TASK_ID, operation_id=BOB)
    assert client.post(f"/api/hitl/tasks/{TASK_ID}/apply", json={**body, "changes": {"title": "forged"}}).status_code == 422
    assert client.post(f"/api/hitl/tasks/{TASK_ID}/apply", json={**body, "task_id": BOB}).status_code == 409
    assert calls == []


def test_cancel_requires_deploy_permission_and_checks_task_binding_before_command(stand):
    client, maker, calls = stand
    path = f"/api/hitl/tasks/{TASK_ID}/executions/{BOB}/cancel"
    assert client.post(path).status_code == 403
    assert all(call.method == "GET" for call in calls)
    with maker() as db:
        db.get(WorkspaceMember, (WORKSPACE, ALICE)).role = "deployer"
        db.commit()
    calls.clear()
    response = client.post(path, headers={"Authorization": "Bearer original-access"})
    assert response.status_code == 200 and response.json()["state"] == "cancelled"
    assert calls[-2].url.path == f"/internal/executions/{BOB}"
    assert calls[-1].url.path == f"/internal/executions/{BOB}/cancel"
    assert calls[-1].headers["authorization"] == "Bearer original-access"


def test_foreign_actor_cannot_cancel_an_operation(stand):
    client, _, calls = stand
    assert client.post(f"/api/hitl/tasks/{TASK_ID}/executions/{BOB}/cancel", headers={"X-User": BOB}).status_code == 404
    assert all(call.method == "GET" for call in calls)


def test_cancel_rejects_an_operation_bound_to_another_task_before_post(stand):
    client, maker, calls = stand
    with maker() as db:
        db.get(WorkspaceMember, (WORKSPACE, ALICE)).role = "deployer"
        db.commit()
    assert client.post(f"/api/hitl/tasks/{BOB}/executions/{BOB}/cancel").status_code == 404
    assert all(call.method == "GET" for call in calls)
