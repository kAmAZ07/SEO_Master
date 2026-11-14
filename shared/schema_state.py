"""Read-only runtime gates. Schema creation is exclusively a migration operation."""
from sqlalchemy import text
from sqlalchemy.engine import Connection

CORE_HEAD = "20261109_001_hitl_barrier"
MANAGEMENT_HEAD = "20261114_007_verification"
HITL_STATE_TABLES = ("audit_schema.crawl_results", "semantic_schema.recommendation_versions", "public.users",
                     "audit_schema.projects", "audit_schema.workspace_members", "audit_schema.domain_authorizations")
CLIENT_COLUMNS = {
    "client_keys": frozenset({"id", "project_id", "key_id", "secret_ref", "is_active", "created_at", "expires_at",
                              "rotated_at", "grace_until", "last_used_at", "metadata"}),
    "deployment_log": frozenset({"id", "project_id", "task_id", "change_type", "entity_id", "entity_type", "status",
                                 "error_message", "changes", "metadata", "source_ip", "user_agent", "correlation_id", "created_at", "applied_at"}),
}


def require_runtime_role(connection: Connection, schemas: tuple[str, ...]) -> None:
    unsafe = connection.scalar(text(
        "SELECT r.rolsuper OR r.rolcreatedb OR r.rolcreaterole "
        "OR has_database_privilege(current_user,current_database(),'CREATE') "
        "OR EXISTS(SELECT 1 FROM pg_namespace n WHERE n.nspname=ANY(:schemas) "
        "AND has_schema_privilege(current_user,n.oid,'CREATE')) "
        "OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=ANY(:schemas) AND pg_has_role(current_user,c.relowner,'MEMBER')) "
        "FROM pg_roles r WHERE r.rolname=current_user"
    ), {"schemas": list(schemas)})
    if unsafe is not False:
        raise RuntimeError("RUNTIME_DDL_PRIVILEGES_FORBIDDEN")


def _stamp(connection: Connection, table: str, expected: str) -> None:
    # table is a private constant selected below, never user input.
    if connection.scalar(text("SELECT to_regclass(:name)"), {"name": "public." + table}) is None:
        raise RuntimeError("RUNTIME_SCHEMA_NOT_READY")
    if tuple(connection.scalars(text("SELECT version_num FROM public." + table))) != (expected,):
        raise RuntimeError("RUNTIME_SCHEMA_NOT_READY")


def require_core_schema(connection: Connection, tables: tuple[str, ...], *, crawl_partitions: bool = False) -> None:
    require_producer_barrier(connection)
    for table in tables:
        if connection.scalar(text("SELECT to_regclass(:name)"), {"name": table}) is None:
            raise RuntimeError("RUNTIME_SCHEMA_NOT_READY")
    if crawl_partitions:
        bounds = set(connection.execute(text(
            "SELECT pg_get_expr(c.relpartbound,c.oid) FROM pg_inherits i JOIN pg_class c ON c.oid=i.inhrelid "
            "WHERE i.inhparent=to_regclass('audit_schema.crawl_results')"
        )).scalars())
        if bounds != {f"FOR VALUES WITH (modulus 8, remainder {number})" for number in range(8)}:
            raise RuntimeError("RUNTIME_PARTITIONS_NOT_READY")
    schemas = tuple(sorted({"public", *(table.split(".", 1)[0] for table in tables)}))
    require_runtime_role(connection, schemas)


def require_producer_barrier(connection: Connection) -> None:
    _stamp(connection, "alembic_version", CORE_HEAD)
    parents = set(connection.scalars(text(
        "SELECT oid FROM pg_class WHERE oid=ANY(CAST(:tables AS regclass[]))"
    ), {"tables": list(HITL_STATE_TABLES)}))
    children = set(connection.scalars(text("SELECT inhrelid FROM pg_inherits WHERE inhparent=ANY(:parents)"), {"parents": list(parents)}))
    actual = set(connection.execute(text(
        "SELECT tgrelid,tgtype FROM pg_trigger WHERE tgfoid=to_regprocedure('public.hitl_producer_barrier()') "
        "AND tgenabled IN ('O','A') AND tgrelid=ANY(:tables)"
    ), {"tables": list(parents | children)}).tuples())
    if len(parents) != len(HITL_STATE_TABLES) or not ({(oid, 62) for oid in parents | children} | {(oid, 31) for oid in parents | children}) <= actual:
        raise RuntimeError("RUNTIME_PRODUCER_BARRIER_NOT_READY")


def require_client_schema(connection: Connection) -> None:
    require_core_schema(connection, ("public.users", "audit_schema.projects", "audit_schema.project_integrations"))
    _stamp(connection, "management_alembic_version", MANAGEMENT_HEAD)
    for table, expected in CLIENT_COLUMNS.items():
        actual = frozenset(connection.scalars(text(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=:table"
        ), {"table": table}))
        if actual != expected or connection.scalar(text(
            "SELECT to_regclass(:qualified)=to_regclass(:unqualified)"
        ), {"qualified": "public." + table, "unqualified": table}) is not True:
            raise RuntimeError("CLIENT_SCHEMA_NOT_READY")
    if connection.scalar(text("SELECT value FROM public.management_migration_state WHERE key='client_baseline_origin'")) not in {"created", "adopted"}:
        raise RuntimeError("CLIENT_SCHEMA_NOT_READY")
