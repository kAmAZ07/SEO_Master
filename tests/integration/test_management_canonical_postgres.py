"""Canonical Management IDs, real FK enforcement and conservative legacy refusal."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from database.models import User, Workspace, Project
from services.management_service.canonical_projects import (
    active_project_targets,
    project_exists,
)
from services.management_service.migrations.runner import migrate
from services.management_service.migrations.schema import (
    HEAD,
    LEDGER_HEAD,
    require_schema,
)
from services.management_service.models import Task
from tests.integration.test_migrations_postgres import ADMIN_URL, TempDatabase
from tests.integration.test_management_migrations_postgres import legacy

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_PG_ADMIN_URL is not set")
PROJECT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


def core_fixture(database):
    with database.engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA audit_schema"))
        for table in (User.__table__, Workspace.__table__, Project.__table__):
            table.create(connection)
    with Session(database.engine) as session:
        for identity in (PROJECT, OTHER):
            session.add(
                User(
                    id=identity,
                    email=identity + "@example.test",
                    hashed_password="fixture",
                    is_active=True,
                )
            )
            session.add(
                Workspace(
                    id=identity,
                    name="Separate owner",
                    owner_id=identity,
                    kind="personal",
                )
            )
        session.flush()
        for identity in (PROJECT, OTHER):
            session.add(
                Project(
                    id=identity,
                    workspace_id=identity,
                    owner_id=identity,
                    name="Same host",
                    url="https://example.test/",
                    origin="https://example.test",
                    path_prefix="/",
                    status="active",
                )
            )
        session.commit()


def upgrade(database, target="head"):
    with database.engine.connect() as connection:
        migrate(connection, expected_role="postgres", target=target)


@pytest.fixture()
def canonical():
    with TempDatabase() as database:
        core_fixture(database)
        upgrade(database)
        yield database


def insert_task(database, project=PROJECT):
    identity = uuid.uuid4()
    database.run(
        "INSERT INTO public.tasks (id,project_id,task_type,status,url) "
        "VALUES (:id,:project,'UPDATE_META','PENDING','https://example.test/')",
        id=identity,
        project=uuid.UUID(project),
    )
    return identity


def test_task_accepts_core_identity_without_shadow_project_and_preserves_distinct_owners(
    canonical,
):
    first = insert_task(canonical)
    second = insert_task(canonical, OTHER)
    assert canonical.scalar("SELECT count(*) FROM public.projects") == 0
    assert canonical.rows(
        "SELECT project_id::text,canonical_project_id FROM public.tasks ORDER BY project_id"
    ) == [(PROJECT, PROJECT), (OTHER, OTHER)]
    with Session(canonical.engine) as session:
        task = session.get(Task, first)
        assert task.project is None and str(task.project_id) == PROJECT
        assert session.get(Task, second).canonical_project_id == OTHER
        assert {target.id for target in active_project_targets(session)} == {
            PROJECT,
            OTHER,
        }
        assert project_exists(session, PROJECT) and not project_exists(
            session, str(uuid.uuid4())
        )


def test_unknown_id_generated_binding_override_and_legacy_shadow_creation_are_rejected(
    canonical,
):
    with pytest.raises(DBAPIError):
        insert_task(canonical, str(uuid.uuid4()))
    with pytest.raises(DBAPIError):
        canonical.run(
            "INSERT INTO public.tasks (id,project_id,canonical_project_id,task_type,status,url) "
            "VALUES (:id,:project,:other,'UPDATE_META','PENDING','https://example.test/')",
            id=uuid.uuid4(),
            project=uuid.UUID(PROJECT),
            other=OTHER,
        )
    with pytest.raises(DBAPIError, match="MANAGEMENT_LEGACY_PROJECT_READ_ONLY"):
        canonical.run(
            "INSERT INTO public.projects (id,name,domain,platform,is_active) VALUES (:id,'shadow','example.test','wordpress',true)",
            id=uuid.uuid4(),
        )
    assert canonical.scalar("SELECT count(*) FROM public.tasks") == 0


def test_canonical_delete_is_restricted_and_downgrade_preserves_binding(canonical):
    identity = insert_task(canonical)
    with pytest.raises(DBAPIError):
        canonical.run("DELETE FROM audit_schema.projects WHERE id=:id", id=PROJECT)
    with canonical.engine.connect() as connection, pytest.raises(
        RuntimeError, match="DOWNGRADE_WOULD_LOSE_BINDING"
    ):
        migrate(connection, expected_role="postgres", direction="downgrade")
    assert (
        canonical.scalar("SELECT version_num FROM public.management_alembic_version")
        == HEAD
    )
    assert (
        canonical.scalar(
            "SELECT canonical_project_id FROM public.tasks WHERE id=:id", id=identity
        )
        == PROJECT
    )


def test_legacy_data_are_not_reassigned_even_if_core_uuid_and_host_match():
    with TempDatabase() as database:
        projects, tasks = legacy(database)
        core_fixture(database)
        # Even explicitly equal UUID is not evidence of the legacy row's owner.
        database.run(
            "UPDATE audit_schema.projects SET id=:id WHERE id=:old",
            id=projects[0][0]["id"],
            old=PROJECT,
        )
        upgrade(database, LEDGER_HEAD)
        with pytest.raises(RuntimeError, match="CANONICAL_LEGACY_REVIEW_REQUIRED"):
            upgrade(database)
        assert database.rows("SELECT row_to_json(p) FROM public.projects p") == projects
        assert database.rows("SELECT row_to_json(t) FROM public.tasks t") == tasks
        assert (
            database.scalar("SELECT version_num FROM public.management_alembic_version")
            == LEDGER_HEAD
        )
        with database.engine.connect() as connection, pytest.raises(
            RuntimeError, match="SCHEMA_NOT_READY"
        ):
            require_schema(connection)


def test_missing_core_refuses_entire_new_upgrade_without_partial_management_ddl():
    with TempDatabase() as database:
        with pytest.raises(RuntimeError, match="CANONICAL_CORE_REQUIRED"):
            upgrade(database)
        assert database.scalar("SELECT to_regclass('public.tasks')") is None
        assert (
            database.scalar("SELECT to_regclass('public.management_alembic_version')")
            is None
        )


def test_core_id_table_without_workspace_scope_cannot_satisfy_canonical_dependency():
    with TempDatabase() as database:
        database.run(
            "CREATE SCHEMA audit_schema; CREATE TABLE audit_schema.projects (id varchar(36) PRIMARY KEY)"
        )
        with pytest.raises(RuntimeError, match="CANONICAL_CORE_REQUIRED"):
            upgrade(database)
        assert database.scalar("SELECT to_regclass('public.tasks')") is None


def test_empty_round_trip_preserves_core_and_readiness_rejects_missing_canonical_fk(
    canonical,
):
    with canonical.engine.connect() as connection:
        require_schema(connection)
    canonical.run("ALTER TABLE public.tasks DROP CONSTRAINT fk_tasks_canonical_project")
    with canonical.engine.connect() as connection, pytest.raises(
        RuntimeError, match="SCHEMA_NOT_READY"
    ):
        require_schema(connection)
    canonical.run(
        "ALTER TABLE public.tasks ADD CONSTRAINT fk_tasks_canonical_project FOREIGN KEY (canonical_project_id) REFERENCES audit_schema.projects(id) ON DELETE RESTRICT"
    )
    with canonical.engine.connect() as connection:
        migrate(connection, expected_role="postgres", direction="downgrade")
    assert canonical.scalar("SELECT count(*) FROM audit_schema.projects") == 2
    upgrade(canonical)


def test_real_core_alembic_head_then_management_head():
    with TempDatabase() as database:
        database.alembic("upgrade", "head")
        core_head = database.scalar("SELECT version_num FROM public.alembic_version")
        upgrade(database)
        assert (
            database.scalar("SELECT version_num FROM public.alembic_version")
            == core_head
        )
        with database.engine.connect() as connection:
            require_schema(connection)


def test_canonical_step_failure_rolls_back_baseline_ledger_and_stamp():
    with TempDatabase() as database:
        core_fixture(database)
        database.run(
            "CREATE FUNCTION public.reject_legacy_management_project_write() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$"
        )
        with pytest.raises(DBAPIError):
            upgrade(database)
        assert database.scalar("SELECT to_regclass('public.tasks')") is None
        assert (
            database.scalar("SELECT to_regclass('public.management_alembic_version')")
            is None
        )
        assert database.scalar("SELECT count(*) FROM audit_schema.projects") == 2


def test_dedicated_migrator_needs_only_core_reference_privilege():
    from tests.integration.test_management_migrations_postgres import acting_as

    with TempDatabase() as database:
        core_fixture(database)
        role = "canonical_migrator_" + uuid.uuid4().hex[:12]
        database.run(
            f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE; '
            f'GRANT USAGE,CREATE ON SCHEMA public TO "{role}"; '
            f'GRANT USAGE ON SCHEMA audit_schema TO "{role}"; '
            f'GRANT REFERENCES ON audit_schema.projects TO "{role}"'
        )
        try:
            with acting_as(database, role) as connection:
                migrate(connection, expected_role=role)
                require_schema(connection)
                with pytest.raises(DBAPIError):
                    connection.execute(
                        text(
                            "ALTER TABLE audit_schema.projects ADD COLUMN forbidden text"
                        )
                    )
            assert database.scalar("SELECT count(*) FROM audit_schema.projects") == 2
        finally:
            database.run(f'DROP OWNED BY "{role}"; DROP ROLE "{role}"')
