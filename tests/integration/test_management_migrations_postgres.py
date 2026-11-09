"""Explicit Management Alembic line on a private PostgreSQL database per test."""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import runpy
import os
import subprocess
import sys
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from services.management_service.migrations.runner import migrate
from services.management_service.migrations.schema import (
    HEAD,
    TABLES,
    require_runtime_role,
    require_schema,
)
from services.management_service.models import Base, Project, Task, TaskType
from tests.integration.test_migrations_postgres import ADMIN_URL, TempDatabase

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_PG_ADMIN_URL is not set")
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def database():
    with TempDatabase() as value:
        yield value


def run(database, direction="upgrade"):
    with database.engine.connect() as connection:
        migrate(connection, expected_role="postgres", direction=direction)


@contextmanager
def acting_as(database, role):
    with database.engine.connect() as connection:
        connection.execute(text(f'SET ROLE "{role}"'))
        connection.commit()
        try:
            yield connection
        finally:
            connection.rollback()
            connection.execute(text("RESET ROLE"))
            connection.commit()


def legacy(database):
    with database.engine.begin() as connection:
        for name in TABLES[:4]:
            Base.metadata.tables[name].create(connection)
        connection.execute(
            text(
                "CREATE VIEW public.retained_project_names AS SELECT id,name FROM public.projects"
            )
        )
    project, task = uuid.uuid4(), uuid.uuid4()
    with Session(database.engine) as session:
        session.add(
            Project(
                id=project,
                name="Preserve",
                domain="example.test",
                settings={"keep": None},
            )
        )
        session.flush()
        session.add(
            Task(
                id=task,
                project_id=project,
                task_type=TaskType.UPDATE_META,
                url="https://example.test/",
            )
        )
        session.commit()
    return database.rows("SELECT row_to_json(p) FROM public.projects p"), database.rows(
        "SELECT row_to_json(t) FROM public.tasks t"
    )


def test_empty_upgrade_is_versioned_idempotent_and_matches_frozen_baseline(database):
    database.run(
        "CREATE TABLE public.unrelated (value text); INSERT INTO public.unrelated VALUES ('keep')"
    )
    database.run(
        "CREATE TABLE public.alembic_version (version_num varchar(32)); INSERT INTO public.alembic_version VALUES ('core-head-untouched')"
    )
    run(database)
    run(database)
    assert (
        database.scalar("SELECT version_num FROM public.management_alembic_version")
        == HEAD
    )
    assert (
        database.scalar(
            "SELECT value FROM public.management_migration_state WHERE key='baseline_origin'"
        )
        == "created"
    )
    frozen = runpy.run_path(
        str(
            ROOT
            / "services/management_service/migrations/versions/20261109_000_management_base.py"
        )
    )
    with database.engine.connect() as connection:
        frozen["_verify"](connection, frozen["_snapshot"]())
        require_schema(connection)
        assert set(TABLES) <= set(inspect(connection).get_table_names(schema="public"))
    run(database, "downgrade")
    assert database.scalar("SELECT to_regclass('public.projects')") is None
    assert (
        database.scalar(
            "SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public' AND t.typname IN ('tasktype','taskstatus','hitlstatus')"
        )
        == 0
    )
    assert (
        database.scalar("SELECT version_num FROM public.alembic_version")
        == "core-head-untouched"
    )
    assert database.scalar("SELECT value FROM public.unrelated") == "keep"
    run(database)


def test_adoption_and_downgrade_preserve_legacy_rows_views_and_ownership(database):
    projects, tasks = legacy(database)
    owner = database.rows(
        "SELECT tablename,tableowner FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    )
    run(database)
    assert (
        database.scalar(
            "SELECT value FROM public.management_migration_state WHERE key='baseline_origin'"
        )
        == "adopted"
    )
    assert database.rows("SELECT row_to_json(p) FROM public.projects p") == projects
    assert database.rows("SELECT row_to_json(t) FROM public.tasks t") == tasks
    run(database, "downgrade")
    assert database.rows("SELECT row_to_json(p) FROM public.projects p") == projects
    assert database.rows("SELECT row_to_json(t) FROM public.tasks t") == tasks
    assert (
        database.scalar("SELECT name FROM public.retained_project_names") == "Preserve"
    )
    assert [
        item
        for item in database.rows(
            "SELECT tablename,tableowner FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        )
        if item[0] != "management_alembic_version"
    ] == owner


@pytest.mark.parametrize(
    "drift,code",
    [
        ("CREATE TABLE public.tasks (id uuid PRIMARY KEY)", "PARTIAL"),
        ("ALTER TABLE public.tasks ALTER COLUMN url TYPE text", "COLUMN_DRIFT"),
        ("ALTER TYPE public.taskstatus ADD VALUE 'UNKNOWN'", "ENUM_DRIFT"),
        ("DROP INDEX public.idx_tasks_project_status", "INDEX_DRIFT"),
        (
            "DROP INDEX public.idx_tasks_project_status; CREATE INDEX idx_tasks_project_status ON public.tasks (project_id,status) WHERE status='PENDING'",
            "INDEX_DRIFT",
        ),
        ("ALTER TABLE public.tasks DROP CONSTRAINT tasks_project_id_fkey", "KEY_DRIFT"),
        (
            "ALTER TABLE public.tasks DROP CONSTRAINT tasks_project_id_fkey; ALTER TABLE public.tasks ADD CONSTRAINT tasks_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE ON UPDATE CASCADE",
            "KEY_DRIFT",
        ),
        (
            "ALTER TABLE public.tasks ALTER CONSTRAINT tasks_project_id_fkey DEFERRABLE INITIALLY DEFERRED",
            "KEY_DRIFT",
        ),
    ],
)
def test_partial_or_drifted_legacy_schema_fails_without_stamping_or_changing_data(
    database, drift, code
):
    if code != "PARTIAL":
        original = legacy(database)
    database.run(drift)
    with pytest.raises(RuntimeError, match="MANAGEMENT_BASELINE_" + code):
        run(database)
    assert (
        database.scalar("SELECT to_regclass('public.management_alembic_version')")
        is None
    )
    assert database.scalar("SELECT to_regclass('public.hitl_history')") is None
    assert (
        database.scalar("SELECT to_regclass('public.management_migration_state')")
        is None
    )
    if code != "PARTIAL":
        assert (
            database.rows("SELECT row_to_json(p) FROM public.projects p"),
            database.rows("SELECT row_to_json(t) FROM public.tasks t"),
        ) == original


def test_later_revision_failure_rolls_back_baseline_and_stamp(database):
    database.run(
        "CREATE FUNCTION public.reject_hitl_history_change() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$"
    )
    with pytest.raises(DBAPIError):
        run(database)
    assert database.scalar("SELECT to_regclass('public.projects')") is None
    assert (
        database.scalar("SELECT to_regclass('public.management_alembic_version')")
        is None
    )
    assert (
        database.scalar("SELECT to_regprocedure('public.reject_hitl_history_change()')")
        is not None
    )


@pytest.mark.parametrize("configuration", ["engine", "connection"])
def test_autocommit_cannot_bypass_atomic_migration_transaction(database, configuration):
    engine = create_engine(
        database.url,
        **({"isolation_level": "AUTOCOMMIT"} if configuration == "engine" else {}),
    )
    try:
        with engine.connect() as connection:
            if configuration == "connection":
                connection = connection.execution_options(isolation_level="AUTOCOMMIT")
            with pytest.raises(RuntimeError, match="MIGRATION_TRANSACTION_REQUIRED"):
                migrate(connection, expected_role="postgres")
        assert (
            database.scalar("SELECT to_regclass('public.management_alembic_version')")
            is None
        )
        assert database.scalar("SELECT to_regclass('public.projects')") is None
    finally:
        engine.dispose()


def test_competing_runners_serialize_and_commit_one_head(database):
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run, database) for _ in range(2)]
        for future in futures:
            future.result(timeout=30)
    assert database.rows(
        "SELECT version_num FROM public.management_alembic_version"
    ) == [(HEAD,)]
    assert database.rows("SELECT value FROM public.management_migration_state") == [
        ("created",)
    ]


def test_new_baseline_downgrade_cannot_discard_business_rows(database):
    run(database)
    database.run(
        "INSERT INTO public.projects (id,name,domain,platform,is_active) VALUES (gen_random_uuid(),'keep','example.test','wordpress',true)"
    )
    with pytest.raises(
        RuntimeError, match="MANAGEMENT_BASELINE_DOWNGRADE_WOULD_LOSE_DATA"
    ):
        run(database, "downgrade")
    assert (
        database.scalar("SELECT version_num FROM public.management_alembic_version")
        == HEAD
    )
    assert database.scalar("SELECT count(*) FROM public.projects") == 1
    assert database.scalar("SELECT to_regclass('public.hitl_history')") is not None


def test_runtime_role_can_check_schema_but_cannot_migrate_or_alter_tables(database):
    run(database)
    role = "management_runtime_" + uuid.uuid4().hex[:12]
    # Trusted generated identifier; the entire PostgreSQL server belongs to this test run.
    database.run(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE')
    database.run(
        f'GRANT USAGE ON SCHEMA public TO "{role}"; GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{role}"'
    )
    try:
        with database.engine.connect() as connection:
            with pytest.raises(RuntimeError, match="ACTOR_MISMATCH"):
                migrate(connection, expected_role=role)
            with pytest.raises(RuntimeError, match="RUNTIME_DDL_PRIVILEGES_FORBIDDEN"):
                require_runtime_role(connection)
        with acting_as(database, role) as connection:
            require_schema(connection)
            require_runtime_role(connection)
            connection.commit()
            with pytest.raises(RuntimeError, match="MIGRATION_DDL_REQUIRED"):
                migrate(connection, expected_role=role)
            with pytest.raises(DBAPIError):
                connection.execute(
                    text("ALTER TABLE public.tasks ADD COLUMN forbidden text")
                )
            connection.rollback()
            with pytest.raises(DBAPIError):
                connection.execute(text("TRUNCATE public.hitl_history CASCADE"))
            connection.rollback()
    finally:
        database.run(f'DROP OWNED BY "{role}"; DROP ROLE "{role}"')


def test_dedicated_non_superuser_migration_actor_owns_only_its_new_objects(database):
    role = "management_migrator_" + uuid.uuid4().hex[:12]
    runtime = "management_runtime_" + uuid.uuid4().hex[:12]
    database.run(
        "CREATE TABLE public.core_fixture (value text); INSERT INTO public.core_fixture VALUES ('preserve')"
    )
    database.run(
        f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE; GRANT USAGE,CREATE ON SCHEMA public TO "{role}"'
    )
    database.run(
        f'CREATE ROLE "{runtime}" NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE; GRANT "{role}" TO "{runtime}"'
    )
    try:
        with acting_as(database, role) as connection:
            migrate(connection, expected_role=role)
            require_schema(connection)
        assert (
            database.scalar(
                "SELECT tableowner FROM pg_tables WHERE schemaname='public' AND tablename='tasks'"
            )
            == role
        )
        assert database.scalar("SELECT value FROM public.core_fixture") == "preserve"
        assert (
            database.scalar(
                "SELECT tableowner FROM pg_tables WHERE schemaname='public' AND tablename='core_fixture'"
            )
            == "postgres"
        )
        database.run(
            f'GRANT USAGE ON SCHEMA public TO "{runtime}"; GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{runtime}"'
        )
        with acting_as(database, runtime) as connection:
            require_schema(connection)
            with pytest.raises(RuntimeError, match="RUNTIME_DDL_PRIVILEGES_FORBIDDEN"):
                require_runtime_role(connection)
    finally:
        database.run(f'DROP OWNED BY "{runtime}"; DROP ROLE "{runtime}"')
        database.run(f'DROP OWNED BY "{role}"; DROP ROLE "{role}"')


def test_schema_readiness_rejects_disabled_history_protection_and_wrong_head(database):
    run(database)
    database.run(
        "ALTER TABLE public.hitl_history DISABLE TRIGGER hitl_history_immutable"
    )
    with database.engine.connect() as connection, pytest.raises(
        RuntimeError, match="SCHEMA_NOT_READY"
    ):
        require_schema(connection)

    database.run(
        "ALTER TABLE public.hitl_history ENABLE TRIGGER hitl_history_immutable"
    )
    database.run(
        "UPDATE public.management_alembic_version SET version_num='unknown-head'"
    )
    with database.engine.connect() as connection, pytest.raises(
        RuntimeError, match="SCHEMA_NOT_READY"
    ):
        require_schema(connection)


def test_schema_readiness_rejects_shadow_tables_in_effective_search_path(database):
    run(database)
    database.run("CREATE SCHEMA shadow; CREATE TABLE shadow.tasks (LIKE public.tasks)")
    with database.engine.begin() as connection:
        connection.execute(text("SET LOCAL search_path=shadow,public"))
        with pytest.raises(RuntimeError, match="SCHEMA_NOT_READY"):
            require_schema(connection)


def test_cli_status_does_not_create_missing_schema_and_upgrade_reaches_head(database):
    environment = dict(os.environ, MANAGEMENT_FIXTURE_DSN=database.url)
    arguments = [
        sys.executable,
        "-B",
        "-m",
        "services.management_service.migrations.runner",
        "status",
        "--url-env",
        "MANAGEMENT_FIXTURE_DSN",
        "--expected-role",
        "postgres",
    ]
    result = subprocess.run(
        arguments, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 1 and "MANAGEMENT_SCHEMA_NOT_READY" in result.stdout
    assert (
        database.scalar("SELECT to_regclass('public.management_alembic_version')")
        is None
    )
    arguments[4] = "upgrade"
    result = subprocess.run(
        arguments, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0 and result.stdout.strip() == "MANAGEMENT_SCHEMA_READY"
    assert (
        database.scalar("SELECT version_num FROM public.management_alembic_version")
        == HEAD
    )


def test_direct_alembic_and_autogenerate_cannot_bypass_explicit_runner(database):
    config = Config()
    config.set_main_option(
        "script_location", str(ROOT / "services/management_service/migrations")
    )
    with database.engine.connect() as connection:
        config.attributes["connection"] = connection
        with pytest.raises(RuntimeError, match="EXPLICIT_RUNNER_REQUIRED"):
            command.upgrade(config, "head")
        with pytest.raises(RuntimeError, match="AUTOGENERATE_BLOCKED"):
            command.check(config)
    assert (
        database.scalar("SELECT to_regclass('public.management_alembic_version')")
        is None
    )
