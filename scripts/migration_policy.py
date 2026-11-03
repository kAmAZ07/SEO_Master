"""Чистые границы владения таблицами, не разрешение на применение DDL."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re
from typing import Literal


Scope = Literal["core", "management", "client"]
ErrorCode = Literal[
    "MIGRATION_SCOPE_INVALID", "MIGRATION_IDENTITY_INVALID",
    "MIGRATION_METADATA_DUPLICATE", "MIGRATION_METADATA_FOREIGN",
    "MIGRATION_METADATA_INCOMPLETE", "MIGRATION_REFLECTED_TABLE_UNMODELED",
    "MIGRATION_DSN_MISSING", "MIGRATION_DSN_INVALID",
    "MIGRATION_AUTOGENERATE_BLOCKED", "MIGRATION_OFFLINE_CONNECTION_UNSUPPORTED",
    "MIGRATION_CONNECTION_FAILED",
]


class MigrationPolicyError(ValueError):
    def __init__(
        self, code: ErrorCode, tables: frozenset["TableIdentity"] = frozenset(),
    ) -> None:
        self.code = code
        self.tables = tables
        # Exceptions deliberately contain no DSN, secret, or external object repr.
        super().__init__(code)


@dataclass(frozen=True)
class TableIdentity:
    schema: str | None
    name: str

    def __post_init__(self) -> None:
        schema = "public" if self.schema is None else self.schema
        for identifier in (schema, self.name):
            if not isinstance(identifier, str) or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", identifier) is None:
                raise MigrationPolicyError("MIGRATION_IDENTITY_INVALID")
        object.__setattr__(self, "schema", schema)


CORE_TABLES = frozenset(
    TableIdentity(schema, name)
    for schema, names in (
        ("audit_schema", (
            "projects", "project_integrations", "crawls", "pages", "core_web_vitals",
            "schema_validations", "backlinks", "crawl_results", "public_audit_results",
            "crawl_events", "workspaces", "workspace_members", "domain_authorizations",
            "legacy_project_id_map",
        )),
        ("semantic_schema", (
            "ff_scores", "eeat_scores", "content_drafts", "semantic_analysis",
            "content_gaps", "llm_generations", "semantic_events", "recommendation_versions",
        )),
        ("reporting_schema", (
            "gsc_data", "ga4_data", "yandex_webmaster_data", "reports",
            "metrics_history", "cost_efficiency",
        )),
        ("public", ("users", "changelog", "domain_events", "processed_events", "failed_events")),
    )
    for name in names
)
MANAGEMENT_TABLES = frozenset(
    TableIdentity("public", name)
    for name in ("projects", "tasks", "hitl_approvals", "management_changelog")
)
CLIENT_TABLES = frozenset(
    TableIdentity("public", name) for name in ("client_keys", "deployment_log")
)
CHILD_TYPES = frozenset(("column", "index", "unique_constraint", "foreign_key_constraint"))


def owned_tables(scope: str) -> frozenset[TableIdentity]:
    if scope == "core":
        return CORE_TABLES
    if scope == "management":
        return MANAGEMENT_TABLES
    if scope == "client":
        return CLIENT_TABLES
    raise MigrationPolicyError("MIGRATION_SCOPE_INVALID")


def validate_metadata(scope: str, tables: Iterable[TableIdentity]) -> None:
    expected = owned_tables(scope)
    supplied: set[TableIdentity] = set()
    for table in tables:
        if table in supplied:
            raise MigrationPolicyError("MIGRATION_METADATA_DUPLICATE", frozenset((table,)))
        supplied.add(table)
    foreign = supplied - expected
    if foreign:
        raise MigrationPolicyError("MIGRATION_METADATA_FOREIGN", frozenset(foreign))
    missing = expected - supplied
    if missing:
        raise MigrationPolicyError("MIGRATION_METADATA_INCOMPLETE", missing)


def identify_table(candidate: object) -> TableIdentity | None:
    schema: object = getattr(candidate, "schema", None)
    name: object = getattr(candidate, "name", None)
    if (schema is not None and not isinstance(schema, str)) or not isinstance(name, str):
        return None
    try:
        return TableIdentity(schema, name)
    except MigrationPolicyError:
        return None


@dataclass(frozen=True)
class OwnershipFilters:
    scope: Scope

    def include_name(
        self, name: str | None, type_: str, parent_names: Mapping[str, str | None],
    ) -> bool:
        owned = owned_tables(self.scope)
        if type_ == "schema":
            schema = "public" if name is None else name
            return any(table.schema == schema for table in owned)
        if type_ not in ("table", *CHILD_TYPES) or "schema_name" not in parent_names:
            return False
        table_name = name if type_ == "table" else parent_names.get("table_name")
        if table_name is None:
            return False
        try:
            table = TableIdentity(parent_names["schema_name"], table_name)
        except MigrationPolicyError:
            return False
        return table in owned

    def include_object(
        self, object_: object, name: str | None, type_: str,
        reflected: bool, compare_to: object | None,
    ) -> bool:
        if type_ != "table" and type_ not in CHILD_TYPES:
            return False
        candidate = object_ if type_ == "table" else getattr(object_, "table", None)
        table = identify_table(candidate)
        if table is None or table not in owned_tables(self.scope):
            return False
        if type_ == "table" and table.name != name:
            return False
        if compare_to is not None:
            comparison = compare_to if type_ == "table" else getattr(compare_to, "table", None)
            if identify_table(comparison) != table:
                return False
        elif reflected and type_ == "table":
            raise MigrationPolicyError("MIGRATION_REFLECTED_TABLE_UNMODELED", frozenset((table,)))
        return True
