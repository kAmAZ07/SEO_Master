"""Чистые ORM imports: stdlib-контракты и отдельные native subprocess probes."""

import ast
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Literal
import unittest
from unittest.mock import patch

from scripts.backend import get_profile


ROOT = Path(__file__).resolve().parents[2]
ModelProfile = Literal["management", "client"]
MODEL_EXPORTS = {
    "management": (
        "services.management_service",
        ("Base", "Task", "TaskType", "TaskStatus", "HITLStatus", "HITLApproval", "Changelog", "Project", "HITLHistory", "ManagementOutbox"),
    ),
    "client": (
        "services.client_api_gateway",
        ("Base", "ClientKey", "DeploymentLog"),
    ),
}


# Настоящие ORM definitions импортируются обычным Python import, не извлекаются
# через AST/runpy и не подменяются. Native source отдельно проверяется mypy
# с настоящими vendor types до исполнения; platform-suite остаётся stdlib-only.
NATIVE_PROBE = r"""
from collections.abc import Callable
import enum
from importlib import import_module
from importlib.metadata import version
import importlib.abc
import importlib.machinery
import math
import os
from pathlib import Path
import platform
import runpy
import sys
from types import ModuleType
from unittest.mock import patch

sys.path.insert(0, sys.argv[1])
from scripts.pytest_required import RequiredTests
guard = RequiredTests()
guard.block_network()
platform.machine()

profile = sys.argv[2]
expected_version = {"management": "2.0.55", "client": "2.0.25"}[profile]
assert version("sqlalchemy") == expected_version, "WRONG_LOCKED_SQLALCHEMY"

class DenyRuntimeImports(importlib.abc.MetaPathFinder):
    def find_spec(
        self, fullname: str, path: object = None, target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        root = fullname.split(".")[0]
        allowed_services = {
            "services", "services.management_service", "services.client_api_gateway",
            "services.management_service.models", "services.client_api_gateway.models",
        }
        # Legacy mode нужен только для сохранённого исходного RED: разрешаем
        # package evaluation, но не settings/logging/runtime collaborators.
        if sys.argv[3] == "legacy":
            allowed_services |= {
                "services.management_service.db", "services.management_service.db.models",
                "services.client_api_gateway.db", "services.client_api_gateway.db.models",
            }
        if root in {"config", "database", "shared", "dotenv", "pydantic_settings"}:
            raise AssertionError("FORBIDDEN_MODEL_RUNTIME_IMPORT:" + fullname)
        if root == "services" and fullname not in allowed_services:
            raise AssertionError("FORBIDDEN_MODEL_RUNTIME_IMPORT:" + fullname)
        return None

def audit(event: str, arguments: tuple[object, ...]) -> None:
    if event.startswith("socket."):
        raise AssertionError("MODEL_NETWORK_FORBIDDEN")
    if event == "open" and isinstance(arguments[0], (str, bytes)):
        if Path(os.fsdecode(arguments[0])).name.startswith(".env"):
            raise AssertionError("MODEL_DOTENV_FORBIDDEN")

sys.meta_path.insert(0, DenyRuntimeImports())
sys.addaudithook(audit)

import sqlalchemy
from sqlalchemy import Index, MetaData, String, Table, inspect
from sqlalchemy.dialects.postgresql import JSONB, UUID, dialect
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapper, configure_mappers, registry as MapperRegistry
from sqlalchemy.schema import CreateIndex, CreateTable
from scripts.migration_policy import TableIdentity, validate_metadata
# В pinned 2.0.25 у этих constructors нет полных annotations. Узкие callable
# contracts не подменяют функции: ниже вызываются реальные vendor objects.
create_dialect: Callable[[], Dialect] = dialect
create_index: Callable[[Index], CreateIndex] = CreateIndex

with (
    patch("sqlalchemy.create_engine", side_effect=AssertionError("MODEL_ENGINE_FORBIDDEN")),
    patch("sqlalchemy.engine.create_engine", side_effect=AssertionError("MODEL_ENGINE_FORBIDDEN")),
    patch("sqlalchemy.engine.create.create_engine", side_effect=AssertionError("MODEL_ENGINE_FORBIDDEN")),
    patch.object(MetaData, "create_all", side_effect=AssertionError("MODEL_DDL_FORBIDDEN")),
    patch.object(MetaData, "drop_all", side_effect=AssertionError("MODEL_DDL_FORBIDDEN")),
    patch.object(Table, "create", side_effect=AssertionError("MODEL_DDL_FORBIDDEN")),
    patch.object(Table, "drop", side_effect=AssertionError("MODEL_DDL_FORBIDDEN")),
):
    # Обычный import_module выполняет parent packages, не обходит __init__.
    # Выбор только из literal allowlist; внешнее имя модуля не принимается.
    module_names = {
        ("management", "legacy"): "services.management_service.db.models",
        ("management", "pure"): "services.management_service.models",
        ("client", "legacy"): "services.client_api_gateway.db.models",
        ("client", "pure"): "services.client_api_gateway.models",
    }
    models = import_module(module_names[(profile, sys.argv[3])])
    mapped_classes: tuple[object, ...]
    if profile == "management":
        expected_columns = {
            "projects": {
                "id", "name", "domain", "platform", "is_active", "settings", "metadata",
                "created_at", "updated_at",
            },
            "tasks": {
                "canonical_project_id",
                "id", "project_id", "task_type", "status", "url", "title", "description",
                "impact_score", "effort_score", "priority_score", "metadata", "assigned_to",
                "created_at", "updated_at", "started_at", "completed_at", "deployed_at",
            },
            "hitl_approvals": {
                "canonical_project_id",
                "id", "task_id", "project_id", "status", "diff_data", "impact_score",
                "recommendation", "approved_by", "approved_at", "rejected_by",
                "rejected_at", "rejection_reason", "metadata", "created_at", "updated_at",
            },
            "management_changelog": {
                "canonical_project_id",
                "id", "project_id", "task_id", "entity_id", "entity_type", "change_type",
                "before_value", "after_value", "applied", "applied_at", "source",
                "metadata", "created_at",
            },
            "hitl_history": {
                "canonical_project_id",
                "id", "approval_id", "task_id", "project_id", "version_id", "entry_type",
                "content_hash", "entry_hash", "metadata", "created_at",
            },
            "management_outbox": {
                "id", "history_id", "event_type", "routing_key", "status", "attempts", "available_at",
                "lease_token", "lease_expires_at", "published_at", "error_code", "metadata", "created_at", "updated_at",
            },
        }
        expected_lengths = {
            **{(name, "canonical_project_id"): 36 for name in ("tasks", "hitl_approvals", "management_changelog", "hitl_history")},
            ("projects", "name"): 255, ("projects", "domain"): 255, ("projects", "platform"): 50,
            ("tasks", "url"): 2048, ("tasks", "title"): 500, ("tasks", "assigned_to"): 255,
            ("hitl_approvals", "approved_by"): 255, ("hitl_approvals", "rejected_by"): 255,
            ("management_changelog", "entity_id"): 2048,
            ("management_changelog", "entity_type"): 100,
            ("management_changelog", "change_type"): 100,
            ("management_changelog", "source"): 50,
            ("hitl_history", "version_id"): 36, ("hitl_history", "entry_type"): 16,
            ("hitl_history", "content_hash"): 64, ("hitl_history", "entry_hash"): 64,
            ("management_outbox", "event_type"): 100, ("management_outbox", "routing_key"): 255,
            ("management_outbox", "status"): 16, ("management_outbox", "error_code"): 64,
        }
        expected_foreign_keys = {
            ("tasks", "canonical_project_id", "audit_schema.projects.id", "RESTRICT"),
            ("hitl_approvals", "task_id", "tasks.id", "CASCADE"),
            ("hitl_approvals", "canonical_project_id", "audit_schema.projects.id", "RESTRICT"),
            ("management_changelog", "canonical_project_id", "audit_schema.projects.id", "RESTRICT"),
            ("hitl_history", "canonical_project_id", "audit_schema.projects.id", "RESTRICT"),
            ("management_changelog", "task_id", "tasks.id", "SET NULL"),
            ("hitl_history", "approval_id", "hitl_approvals.id", "RESTRICT"),
            ("hitl_history", "task_id", "tasks.id", "RESTRICT"),
            ("management_outbox", "history_id", "hitl_history.id", "RESTRICT"),
        }
        mapped_classes = (models.Project, models.Task, models.HITLApproval, models.Changelog,
                          models.HITLHistory, models.ManagementOutbox)
    else:
        expected_columns = {
            "client_keys": {
                "id", "project_id", "key_id", "secret_ref", "is_active", "created_at",
                "expires_at", "rotated_at", "grace_until", "last_used_at", "metadata",
            },
            "deployment_log": {
                "id", "project_id", "task_id", "change_type", "entity_id", "entity_type",
                "status", "error_message", "changes", "metadata", "source_ip",
                "user_agent", "correlation_id", "created_at", "applied_at",
            },
        }
        expected_lengths = {
            ("client_keys", "project_id"): 255, ("client_keys", "key_id"): 100,
            ("client_keys", "secret_ref"): 255, ("deployment_log", "project_id"): 255,
            ("deployment_log", "task_id"): 255, ("deployment_log", "change_type"): 50,
            ("deployment_log", "entity_id"): 2048, ("deployment_log", "entity_type"): 100,
            ("deployment_log", "status"): 50, ("deployment_log", "source_ip"): 45,
            ("deployment_log", "user_agent"): 512, ("deployment_log", "correlation_id"): 255,
        }
        expected_foreign_keys = set()
        mapped_classes = (models.ClientKey, models.DeploymentLog)

    metadata: object = models.Base.metadata
    assert isinstance(metadata, MetaData), "NOT_NATIVE_METADATA"
    assert set(metadata.tables) == set(expected_columns), "WRONG_OWNED_TABLES"
    validate_metadata(profile, (
        TableIdentity(table.schema, table.name) for table in metadata.tables.values()
    ))
    configure_mappers()
    registry_ids: set[int] = set()
    for candidate in vars(models).values():
        if isinstance(candidate, MapperRegistry):
            registry_ids.add(id(candidate))
        if isinstance(candidate, type):
            candidate_registry: object = getattr(candidate, "registry", None)
            if isinstance(candidate_registry, MapperRegistry):
                registry_ids.add(id(candidate_registry))
    assert registry_ids == {id(models.Base.registry)}, "EXTRA_CANONICAL_REGISTRY"
    assert {mapper.class_ for mapper in models.Base.registry.mappers} == set(mapped_classes), "EXTRA_MAPPED_CLASS"
    actual_lengths = {
        (table.name, column.name): getattr(column.type, "length")
        for table in metadata.tables.values() for column in table.columns
        if type(column.type) is String
    }
    assert actual_lengths == expected_lengths, "STRING_LENGTH_DRIFT"
    actual_foreign_keys = {
        (table.name, reference.parent.name, reference.target_fullname, reference.ondelete)
        for table in metadata.tables.values() for reference in table.foreign_keys
    }
    assert actual_foreign_keys == expected_foreign_keys, "FOREIGN_KEY_ACTION_DRIFT"
    if profile == "management":
        for name in ("tasks", "hitl_approvals", "management_changelog", "hitl_history"):
            generated = metadata.tables[name].c.canonical_project_id
            assert generated.computed is not None and generated.computed.persisted is True, "CANONICAL_BINDING_NOT_GENERATED"
            assert str(generated.computed.sqltext) == "project_id::text" and not generated.nullable, "CANONICAL_BINDING_SUBSTITUTABLE"
        for name in ("tasks", "hitl_approvals", "changelogs"):
            assert inspect(models.Project).relationships[name].viewonly, "LEGACY_PROJECT_CAN_DELETE_CANONICAL_DATA"
    new_tables = {"hitl_history", "management_outbox"} if profile == "management" else set()
    json_defaults = [(table.name, "metadata") for table in metadata.tables.values() if table.name not in new_tables]
    if profile == "management":
        json_defaults.append(("projects", "settings"))
    for table_name, column_name in json_defaults:
        column = metadata.tables[table_name].c[column_name]
        assert isinstance(column.type, JSONB), "JSONB_TYPE_DRIFT"
        assert column.default is not None, "JSON_DEFAULT_MISSING"
        assert getattr(column.default, "is_scalar"), "JSON_DEFAULT_KIND_DRIFT"
        assert type(getattr(column.default, "arg")) is dict, "JSON_DEFAULT_TYPE_DRIFT"
        assert getattr(column.default, "arg") == {}, "JSON_DEFAULT_VALUE_DRIFT"
    for table_name in new_tables:
        column = metadata.tables[table_name].c.metadata
        assert isinstance(column.type, JSONB), "JSONB_TYPE_DRIFT"
        assert column.default is not None and getattr(column.default, "is_callable"), "JSON_DEFAULT_KIND_DRIFT"
        first = getattr(column.default, "arg")(None)
        second = getattr(column.default, "arg")(None)
        assert first == second == {} and first is not second, "JSON_DEFAULT_SHARED_INSTANCE"
    for owned_table in metadata.tables.values():
        assert isinstance(owned_table.c.id.type, UUID), "UUID_TYPE_DRIFT"
    mapper_ids = {id(mapper) for mapper in models.Base.registry.mappers}
    # Это проверка trusted ФАЙЛА shim, не purity обычного .db package import.
    # Canonical definitions загружены обычным package import выше.
    shim_path = (
        Path(sys.argv[1]) / module_names[(profile, "pure")].replace(".", "/")
    ).parent / "db/models.py"
    aliases = runpy.run_path(str(shim_path), run_name="model_compatibility_contract")
    exports = (
        ("Base", "Task", "TaskType", "TaskStatus", "HITLStatus", "HITLApproval", "Changelog", "Project", "HITLHistory", "ManagementOutbox")
        if profile == "management" else ("Base", "ClientKey", "DeploymentLog")
    )
    for name in exports:
        assert aliases[name] is getattr(models, name), "COMPATIBILITY_IDENTITY_DRIFT"
    assert mapper_ids == {id(mapper) for mapper in models.Base.registry.mappers}, "DUPLICATE_MAPPERS"
    for model in mapped_classes:
        mapper = inspect(model)
        assert isinstance(mapper, Mapper), "NOT_NATIVE_MAPPER"
        table: object = getattr(model, "__table__", None)
        assert isinstance(table, Table), "NOT_NATIVE_TABLE"
        assert mapper.local_table is table, "MAPPER_TABLE_IDENTITY"
        assert table.metadata is metadata, "METADATA_IDENTITY"
        assert metadata.tables[table.key] is table, "TABLE_IDENTITY"
        assert table.schema is None, "UNEXPECTED_SCHEMA_CHANGE"
        assert set(table.columns.keys()) == expected_columns[table.name], "COLUMN_DRIFT"
        assert "CREATE TABLE" in str(CreateTable(table).compile(dialect=create_dialect()))
        for index in table.indexes:
            assert "CREATE INDEX" in str(create_index(index).compile(dialect=create_dialect()))
        for reference in table.foreign_keys:
            referred_table = reference.column.table
            assert isinstance(referred_table, Table), "UNRESOLVED_FOREIGN_KEY"
            if profile == "management" and reference.parent.name == "canonical_project_id":
                assert referred_table.key == "audit_schema.projects" and reference.column.name == "id", "FOREIGN_CORE_REFERENCE_DRIFT"
                assert set(referred_table.columns.keys()) == {"id"} and referred_table not in metadata.tables.values(), "CORE_REFERENCE_BECAME_OWNED"
            else:
                assert referred_table.metadata is metadata, "UNRESOLVED_FOREIGN_KEY"
        assert getattr(mapper.attrs["meta"], "columns")[0] is table.c.metadata, "META_ATTRIBUTE_DRIFT"

    if profile == "management":
        project = models.Project(name="fixture", domain="fixture.invalid")
        task = models.Task(
            project=project, url="https://fixture.invalid/",
            task_type=models.TaskType.UPDATE_META, impact_score=0.4, effort_score=0.3,
        )
        approval = models.HITLApproval(task=task, project=project, diff_data={})
        changelog = models.Changelog(task=task, project=project)
        assert task.project is project and project.tasks == [], "LEGACY_RELATIONSHIP_WRITES_OWNERSHIP"
        assert task.hitl_approval is approval and approval.task is task, "APPROVAL_RELATIONSHIP"
        assert approval.project is project and project.hitl_approvals == [], "LEGACY_RELATIONSHIP_WRITES_OWNERSHIP"
        assert changelog.project is project and project.changelogs == [] and changelog in task.changelogs, "CHANGELOG_RELATIONSHIP"
        assert math.isclose(task.calculate_priority(), 0.68), "PRIORITY_BEHAVIOR_DRIFT"
        enum_contracts = {
            "TaskType": (
                "UPDATE_META", "UPDATE_CONTENT", "ADD_INTERNAL_LINKS", "UPDATE_SCHEMA",
                "FIX_404", "UPDATE_TILDA_PAGE", "OPTIMIZE_IMAGES", "FIX_BROKEN_LINKS",
            ),
            "TaskStatus": (
                "PENDING", "IN_PROGRESS", "APPROVED", "REJECTED", "DEPLOYED",
                "COMPLETED", "FAILED", "CANCELLED",
            ),
            "HITLStatus": ("PENDING", "APPROVED", "REJECTED"),
        }
        for enum_name, expected_members in enum_contracts.items():
            enumeration: object = getattr(models, enum_name)
            assert isinstance(enumeration, type) and issubclass(enumeration, enum.Enum)
            assert tuple(
                (declared_name, member.value)
                for declared_name, member in enumeration.__members__.items()
            ) == tuple(
                (member, member) for member in expected_members
            ), "ENUM_MEMBER_DRIFT"
    else:
        keys = metadata.tables["client_keys"]
        assert not keys.c.secret_ref.nullable and "secret" not in keys.c, "SECRET_SCHEMA_DRIFT"
        assert keys.c.key_id.unique, "KEY_UNIQUENESS_DRIFT"
        assert getattr(metadata.tables["deployment_log"].c.status.default, "arg") == "received"

assert not any(
    name.split(".")[0] in {"config", "database", "shared", "dotenv", "pydantic_settings"}
    or (name.startswith("services.") and ".db" in name)
    for name in sys.modules
), "RUNTIME_MODULE_LOADED"
guard.network_patch.undo()
print("MODEL_METADATA_OK", profile, len(expected_columns), sqlalchemy.__version__)
"""


def run_model_probe(
    profile: ModelProfile, *, legacy: bool = False,
) -> subprocess.CompletedProcess[str]:
    environment = {
        name: value for name, value in os.environ.items()
        if name.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR"}
    }
    environment["MYPYPATH"] = str(ROOT)
    with tempfile.TemporaryDirectory(prefix="seo-model-import-") as directory:
        probe = Path(directory) / "model_probe.py"
        probe.write_text(NATIVE_PROBE, encoding="utf-8")
        types = subprocess.run(
            [sys.executable, "-I", "-B", "-X", "utf8", "-m", "mypy", "--strict", "--explicit-package-bases",
             "--cache-dir", str(Path(directory) / "mypy-cache"), str(probe)],
            cwd=directory, env=environment, capture_output=True, text=True,
            encoding="utf-8", check=False, timeout=60,
        )
        if types.returncode != 0:
            return types
        return subprocess.run(
            [sys.executable, "-I", "-B", "-X", "utf8", str(probe), str(ROOT), profile,
             "legacy" if legacy else "pure"],
            cwd=directory, env=environment, capture_output=True, text=True,
            encoding="utf-8", check=False, timeout=30,
        )


class ModelImportContractTests(unittest.TestCase):
    def test_probe_preserves_unicode_failure_diagnostics(self) -> None:
        # Только transport fixture: не подмена ORM и не native metadata evidence.
        marker = "MODEL_DIAGNOSTIC:кириллица"
        with patch(__name__ + ".NATIVE_PROBE", f"raise RuntimeError({marker!r})\n"):
            result = run_model_probe("management")
        self.assertNotEqual(result.returncode, 0)
        self.assertIsInstance(result.stderr, str)
        self.assertIn(marker, result.stderr)
        self.assertNotIn("MODEL_METADATA_OK", result.stdout)

    def test_legacy_paths_are_explicit_single_definition_reexports(self) -> None:
        for profile, (package, exports) in MODEL_EXPORTS.items():
            with self.subTest(profile=profile):
                path = ROOT / package.replace(".", "/") / "db/models.py"
                module = ast.parse(path.read_text(encoding="utf-8"))
                statements = [
                    statement for statement in module.body
                    if not (
                        isinstance(statement, ast.Expr)
                        and isinstance(statement.value, ast.Constant)
                        and isinstance(statement.value.value, str)
                    )
                ]
                self.assertEqual(len(statements), 1, "Compatibility module must not define a second ORM")
                imported = statements[0]
                self.assertIsInstance(imported, ast.ImportFrom)
                if not isinstance(imported, ast.ImportFrom):
                    self.fail("An explicit static reexport is required")
                self.assertEqual(imported.module, package + ".models")
                self.assertEqual(imported.level, 0)
                self.assertCountEqual(
                    [(alias.name, alias.asname) for alias in imported.names],
                    [(name, name) for name in exports],
                )

    def test_model_native_checks_are_mandatory_in_both_backend_profiles(self) -> None:
        for profile in ("management", "client"):
            with self.subTest(profile=profile):
                self.assertIn(f"tests/unit/test_{profile}_model_imports.py", get_profile(profile).tests)


if __name__ == "__main__":
    unittest.main()
