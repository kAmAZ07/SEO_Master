"""Real migrations, conservative client adoption and readonly runtime permissions."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import uuid

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from services.client_api_gateway.models import Base as ClientBase
from services.event_resilience import ensure_event_tracking_tables_sync
from services.management_service.migrations.runner import migrate
from shared.schema_state import require_client_schema, require_producer_barrier
from tests.integration.test_migrations_postgres import ADMIN_URL, TempDatabase

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_PG_ADMIN_URL is not set")


def upgrade(database):
    with database.engine.connect() as connection:
        migrate(connection, expected_role=database.engine.url.username or "")


@pytest.fixture()
def foundation():
    with TempDatabase() as database:
        database.alembic("upgrade", "head")
        upgrade(database)
        yield database


def test_runtime_readiness_works_with_select_only_and_no_ddl(foundation):
    role = "ar4_read_" + uuid.uuid4().hex[:12]
    foundation.run(f'CREATE ROLE "{role}" NOLOGIN; GRANT USAGE ON SCHEMA public,audit_schema,semantic_schema TO "{role}"; '
                   f'GRANT SELECT ON ALL TABLES IN SCHEMA public,audit_schema,semantic_schema TO "{role}"')
    try:
        with foundation.engine.connect() as conn:
            conn.execute(text(f'SET ROLE "{role}"'))
            conn.commit()
            statements = []
            def record(_conn, _cursor, statement, _params, _context, _many):
                statements.append(statement)
            event.listen(conn, "before_cursor_execute", record)
            require_client_schema(conn)
            ensure_event_tracking_tables_sync(conn)
            assert statements and all(item.lstrip().upper().startswith("SELECT") for item in statements)
            event.remove(conn, "before_cursor_execute", record)
            for mutation in ("CREATE TABLE public.forbidden(id int)", "ALTER TABLE public.client_keys ADD COLUMN forbidden int",
                             "UPDATE public.alembic_version SET version_num='forged'", "UPDATE audit_schema.workspace_members SET role='owner'"):
                conn.rollback()
                with pytest.raises(DBAPIError):
                    conn.execute(text(mutation))
            conn.rollback()
    finally:
        # SET ROLE survives transaction rollback and pooled connection reuse.
        with foundation.engine.connect() as conn:
            conn.execute(text('RESET ROLE'))
            conn.commit()
        foundation.run(f'DROP OWNED BY "{role}"; DROP ROLE "{role}"')


def test_client_adoption_preserves_opaque_legacy_rows_without_authorizing_them():
    with TempDatabase() as database:
        database.alembic("upgrade", "head")
        with database.engine.begin() as conn:
            ClientBase.metadata.create_all(conn)  # Explicit fixture of the previous client startup schema.
            conn.execute(text("INSERT INTO public.client_keys(id,project_id,key_id,secret_ref,is_active,expires_at) VALUES (:id,'opaque-legacy','legacy','env:DISABLED_FIXTURE',true,now())"), {"id": str(uuid.uuid4())})
        before = database.rows("SELECT row_to_json(k) FROM public.client_keys k")
        upgrade(database)
        assert database.rows("SELECT row_to_json(k) FROM public.client_keys k") == before
        assert database.scalar("SELECT value FROM public.management_migration_state WHERE key='client_baseline_origin'") == "adopted"
        assert database.scalar("SELECT count(*) FROM public.management_changelog") == 0
        with database.engine.connect() as conn:
            migrate(conn, expected_role=database.engine.url.username or "", direction="downgrade")
        assert database.rows("SELECT row_to_json(k) FROM public.client_keys k") == before


@pytest.mark.parametrize("drift", ["plaintext", "partial", "nullable", "index"])
def test_partial_or_unsafe_client_legacy_refuses_upgrade_without_data_changes(drift):
    with TempDatabase() as database:
        database.alembic("upgrade", "head")
        with database.engine.begin() as conn:
            ClientBase.metadata.create_all(conn)
            if drift == "plaintext":
                conn.execute(text("ALTER TABLE public.client_keys ADD COLUMN secret text"))
            elif drift == "partial":
                conn.execute(text("DROP TABLE public.deployment_log"))
            elif drift == "nullable":
                conn.execute(text("ALTER TABLE public.client_keys ALTER COLUMN secret_ref DROP NOT NULL"))
            else:
                conn.execute(text("DROP INDEX public.idx_client_keys_project"))
        with pytest.raises(RuntimeError, match="CLIENT_BASELINE_"):
            upgrade(database)
        assert database.scalar("SELECT to_regclass('public.management_alembic_version')") is None
        if drift == "plaintext":
            assert database.scalar("SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='client_keys' AND column_name='secret'") == 1


def test_new_client_data_prevents_downgrade_before_any_table_is_removed(foundation):
    foundation.run("INSERT INTO public.deployment_log(id,project_id,change_type,entity_id,entity_type,status,changes) VALUES (:id,'opaque','meta','42','post','received','{}')", id=str(uuid.uuid4()))
    with foundation.engine.connect() as conn, pytest.raises(RuntimeError, match="CLIENT_BASELINE_DOWNGRADE_WOULD_LOSE_DATA"):
        migrate(conn, expected_role=foundation.engine.url.username or "", direction="downgrade")
    assert foundation.scalar("SELECT count(*) FROM public.deployment_log") == 1
    assert foundation.scalar("SELECT to_regclass('public.client_keys')") is not None


def test_barrier_covers_direct_partition_truncate_and_role_writes(foundation):
    with foundation.engine.connect() as conn:
        require_producer_barrier(conn)
    for statement in ("TRUNCATE audit_schema.crawl_results_p0", "UPDATE audit_schema.workspace_members SET role='viewer'"):
        entered = Event()
        with foundation.engine.begin() as holder:
            holder.execute(text("SELECT pg_advisory_xact_lock(74219031109004)"))
            def attempt():
                with foundation.engine.begin() as writer:
                    writer.execute(text("SET LOCAL lock_timeout='200ms'"))
                    entered.set()
                    with pytest.raises(DBAPIError):
                        writer.execute(text(statement))
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(attempt)
                assert entered.wait(5)
                future.result(timeout=5)


def test_disabled_child_barrier_fails_readiness(foundation):
    foundation.run("ALTER TABLE audit_schema.crawl_results_p0 DISABLE TRIGGER hitl_producer_barrier")
    with foundation.engine.connect() as conn, pytest.raises(RuntimeError, match="RUNTIME_PRODUCER_BARRIER_NOT_READY"):
        require_producer_barrier(conn)
