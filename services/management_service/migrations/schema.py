"""Read-only Management schema gate; importing it never configures an application."""

from sqlalchemy import text
from sqlalchemy.engine import Connection

LEDGER_HEAD = "20261109_001_hitl_ledger"
HEAD = "20261109_002_canonical"
TABLES = (
    "projects",
    "tasks",
    "hitl_approvals",
    "management_changelog",
    "hitl_history",
    "management_outbox",
)


def require_schema(connection: Connection, *, expected_head: str = HEAD) -> None:
    if (
        connection.scalar(
            text("SELECT to_regclass('public.management_alembic_version')")
        )
        is None
    ):
        raise RuntimeError("MANAGEMENT_SCHEMA_NOT_READY")
    versions = tuple(
        connection.scalars(
            text("SELECT version_num FROM public.management_alembic_version")
        )
    )
    if versions != (expected_head,):
        raise RuntimeError("MANAGEMENT_SCHEMA_NOT_READY")
    if not all(
        connection.scalar(
            text(
                "SELECT to_regclass(:table) IS NOT NULL AND to_regclass(:table) = to_regclass(:unqualified)"
            ),
            {"table": "public." + name, "unqualified": name},
        )
        is True
        for name in (*TABLES, "management_migration_state")
    ):
        raise RuntimeError("MANAGEMENT_SCHEMA_NOT_READY")
    if expected_head == HEAD:
        canonical_fks = connection.scalar(
            text(
                "SELECT count(*) FROM pg_constraint f JOIN pg_class t ON t.oid=f.conrelid "
                "JOIN pg_namespace n ON n.oid=t.relnamespace JOIN pg_class p ON p.oid=f.confrelid "
                "JOIN pg_namespace pn ON pn.oid=p.relnamespace "
                "WHERE f.contype='f' AND f.convalidated AND f.confdeltype='r' AND n.nspname='public' "
                "AND t.relname IN ('tasks','hitl_approvals','management_changelog','hitl_history') "
                "AND f.conname='fk_' || t.relname || '_canonical_project' "
                "AND pn.nspname='audit_schema' AND p.relname='projects'"
            )
        )
        if canonical_fks != 4:
            raise RuntimeError("MANAGEMENT_SCHEMA_NOT_READY")
    triggers = set(
        connection.execute(
            text(
                "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' "
                "AND t.tgenabled IN ('O','A') AND NOT t.tgisinternal"
            )
        ).tuples()
    )
    expected_triggers = {
        ("hitl_history", "hitl_history_immutable"),
        ("management_outbox", "management_outbox_envelope_immutable"),
    }
    if expected_head == HEAD:
        expected_triggers.add(("projects", "management_legacy_project_read_only"))
    if not expected_triggers <= triggers:
        raise RuntimeError("MANAGEMENT_SCHEMA_NOT_READY")


def require_runtime_role(connection: Connection) -> None:
    unsafe = connection.scalar(
        text(
            "SELECT r.rolsuper OR r.rolcreatedb OR r.rolcreaterole "
            "OR has_schema_privilege(current_user,'public','CREATE') "
            "OR has_database_privilege(current_user,current_database(),'CREATE') "
            "OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname = ANY(:tables) "
            "AND pg_has_role(current_user,c.relowner,'MEMBER')) "
            "FROM pg_roles r WHERE r.rolname=current_user"
        ),
        {
            "tables": [
                *TABLES,
                "management_migration_state",
                "management_alembic_version",
            ]
        },
    )
    if unsafe is not False:
        raise RuntimeError("MANAGEMENT_RUNTIME_DDL_PRIVILEGES_FORBIDDEN")
