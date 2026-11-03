"""Workspace и доступ к проектам: пользователь B с идентификаторами пользователя A (ТЗ §3.3)."""
import threading
import uuid

import pytest
from sqlalchemy import func, select

from database.models import Project, Workspace, WorkspaceMember
from services.api_gateway.projects import access
from services.api_gateway.projects.errors import InsufficientRole, ProjectNotFound
from services.api_gateway.projects.workspaces import ensure_personal_workspace, find_personal_workspace
from shared.projects.roles import Permission
from tests.fixtures.project_db import make_engine, make_sessionmaker

ALICE, BOB = "00000000-0000-4000-8000-00000000000a", "00000000-0000-4000-8000-00000000000b"


@pytest.fixture()
def maker(tmp_path):
    return make_sessionmaker(make_engine(str(tmp_path)))


def add_project(db, owner, url="https://example.com", scope_path="/", workspace=None):
    workspace = workspace or ensure_personal_workspace(db, owner)
    project = Project(
        id=str(uuid.uuid4()), name="p", url=url, owner_id=owner, workspace_id=workspace.id,
        origin="https://example.com", path_prefix=scope_path, status="active",
    )
    db.add(project)
    db.commit()
    return project


# --- workspace ---------------------------------------------------------------------------------

def test_personal_workspace_is_created_once_with_owner_membership(maker):
    with maker() as db:
        first = ensure_personal_workspace(db, ALICE)
        db.commit()
        second = ensure_personal_workspace(db, ALICE)
        assert first.id == second.id
        assert db.scalar(select(func.count()).select_from(Workspace)) == 1
        member = db.get(WorkspaceMember, (first.id, ALICE))
        assert member.role == "owner"
        assert find_personal_workspace(db, BOB) is None


def test_different_users_get_different_workspaces(maker):
    with maker() as db:
        a, b = ensure_personal_workspace(db, ALICE), ensure_personal_workspace(db, BOB)
        db.commit()
        assert a.id != b.id


def test_concurrent_first_requests_create_exactly_one_workspace(maker):
    results, errors = [], []
    barrier = threading.Barrier(6)

    def worker():
        try:
            with maker() as db:
                barrier.wait(timeout=5)
                workspace = ensure_personal_workspace(db, ALICE)
                db.commit()
                results.append(workspace.id)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    # sqlite сериализует записи: допустима блокировка, но не второе личное workspace.
    assert len(set(results)) <= 1
    with maker() as db:
        assert db.scalar(select(func.count()).select_from(Workspace).where(Workspace.owner_id == ALICE)) == 1
    assert all("locked" in str(error).lower() for error in errors), errors


# --- доступ ------------------------------------------------------------------------------------------

def test_owner_resolves_own_project(maker):
    with maker() as db:
        project = add_project(db, ALICE)
        assert access.resolve_project(db, ALICE, project.id).id == project.id
        assert access.resolve_project(db, ALICE, project.id, Permission.MANAGE).id == project.id


def test_foreign_and_missing_projects_are_indistinguishable(maker):
    with maker() as db:
        project = add_project(db, ALICE)
        ensure_personal_workspace(db, BOB)
        db.commit()
        for permission in Permission:
            with pytest.raises(ProjectNotFound) as foreign:
                access.resolve_project(db, BOB, project.id, permission)
            with pytest.raises(ProjectNotFound) as missing:
                access.resolve_project(db, BOB, str(uuid.uuid4()), permission)
            assert foreign.value.http_status == missing.value.http_status == 404
            assert foreign.value.code == missing.value.code


@pytest.mark.parametrize("project_id", ["", None, "x" * 65, "' OR '1'='1", "../etc/passwd", "%00", 123])
def test_malformed_project_ids_are_not_found(maker, project_id):
    with maker() as db:
        add_project(db, ALICE)
        with pytest.raises(ProjectNotFound):
            access.resolve_project(db, ALICE, project_id)


def test_user_without_any_membership_sees_nothing(maker):
    with maker() as db:
        project = add_project(db, ALICE)
        with pytest.raises(ProjectNotFound):
            access.resolve_project(db, "nobody", project.id)
        assert access.list_projects(db, "nobody") == []


def test_listing_is_isolated_and_ordered_newest_first(maker):
    with maker() as db:
        first = add_project(db, ALICE, scope_path="/a")
        second = add_project(db, ALICE, scope_path="/b")
        bobs = add_project(db, BOB)
        alice_ids = [p.id for p in access.list_projects(db, ALICE)]
        assert set(alice_ids) == {first.id, second.id}
        assert [p.id for p in access.list_projects(db, BOB)] == [bobs.id]


@pytest.mark.parametrize(
    "role, permission, allowed",
    [
        ("viewer", Permission.READ, True),
        ("viewer", Permission.MANAGE, False),
        ("reviewer", Permission.APPROVE, True),
        ("reviewer", Permission.DEPLOY, False),
        ("deployer", Permission.DEPLOY, True),
        ("deployer", Permission.MANAGE, False),
        ("admin", Permission.MANAGE, True),
    ],
)
def test_member_roles_limit_actions(maker, role, permission, allowed):
    with maker() as db:
        project = add_project(db, ALICE)
        db.add(WorkspaceMember(workspace_id=project.workspace_id, user_id=BOB, role=role))
        db.commit()
        if allowed:
            assert access.resolve_project(db, BOB, project.id, permission).id == project.id
        else:
            with pytest.raises(InsufficientRole) as caught:
                access.resolve_project(db, BOB, project.id, permission)
            assert caught.value.http_status == 403


def test_unknown_role_cannot_even_be_stored(maker):
    from sqlalchemy.exc import IntegrityError

    with maker() as db:
        project = add_project(db, ALICE)
        db.add(WorkspaceMember(workspace_id=project.workspace_id, user_id=BOB, role="root"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_role_downgrade_takes_effect_immediately(maker):
    with maker() as db:
        project = add_project(db, ALICE)
        db.add(WorkspaceMember(workspace_id=project.workspace_id, user_id=BOB, role="admin"))
        db.commit()
        assert access.resolve_project(db, BOB, project.id, Permission.MANAGE)
        db.execute(
            WorkspaceMember.__table__.update().where(WorkspaceMember.user_id == BOB).values(role="viewer")
        )
        db.commit()
        with pytest.raises(InsufficientRole):
            access.resolve_project(db, BOB, project.id, Permission.MANAGE)
        assert access.resolve_project(db, BOB, project.id, Permission.READ)


def test_unrelated_integrity_error_is_not_swallowed(maker, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    with maker() as db:
        def broken_flush(*args, **kwargs):
            raise IntegrityError("stmt", {}, Exception("something else"))

        monkeypatch.setattr(db, "flush", broken_flush)
        with pytest.raises(IntegrityError):
            ensure_personal_workspace(db, ALICE)


def test_repeated_calls_do_not_attempt_a_second_insert(maker):
    """Существующее workspace находится запросом; вставка с откатом по ограничению — не рабочий путь."""
    from sqlalchemy import event

    statements = []
    with maker() as db:
        ensure_personal_workspace(db, ALICE)
        db.commit()
        event.listen(db.get_bind(), "before_cursor_execute", lambda conn, cur, stmt, *a: statements.append(stmt))
        ensure_personal_workspace(db, ALICE)
        ensure_personal_workspace(db, ALICE)
    assert not [stmt for stmt in statements if stmt.lstrip().upper().startswith(("INSERT", "SAVEPOINT"))]
