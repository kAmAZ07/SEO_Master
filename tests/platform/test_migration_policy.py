"""Контракт владения таблицами; ORM и рабочая конфигурация не импортируются."""

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
import unittest

from scripts import migration_policy as policy


class MigrationPolicyTests(unittest.TestCase):
    def test_manifests_are_exact_immutable_and_disjoint(self) -> None:
        self.assertEqual(len(policy.owned_tables("core")), 32)
        self.assertEqual(policy.owned_tables("management"), frozenset(
            policy.TableIdentity("public", name)
            for name in ("projects", "tasks", "hitl_approvals", "management_changelog")
        ))
        self.assertEqual(policy.owned_tables("client"), frozenset(
            policy.TableIdentity(None, name) for name in ("client_keys", "deployment_log")
        ))
        for left, right in (("core", "management"), ("core", "client"), ("management", "client")):
            self.assertFalse(policy.owned_tables(left) & policy.owned_tables(right))
        table = policy.TableIdentity(None, "projects")
        self.assertEqual(table, policy.TableIdentity("public", "projects"))
        with self.assertRaises(FrozenInstanceError):
            setattr(table, "name", "changed")
        with self.assertRaisesRegex(policy.MigrationPolicyError, "MIGRATION_SCOPE_INVALID"):
            policy.owned_tables("unknown")

    def test_invalid_identifiers_fail_without_echoing_input(self) -> None:
        for schema, name in (("", "x"), ("public", "../x"), ("x\0", "x"), ("public", "x" * 64)):
            with self.subTest(schema=schema, name=name):
                with self.assertRaises(policy.MigrationPolicyError) as failure:
                    policy.TableIdentity(schema, name)
                self.assertEqual(str(failure.exception), "MIGRATION_IDENTITY_INVALID")

    def test_metadata_requires_every_owned_table_before_comparison(self) -> None:
        expected = policy.owned_tables("core")
        missing = frozenset((
            policy.TableIdentity("audit_schema", "project_integrations"),
            policy.TableIdentity("public", "processed_events"),
            policy.TableIdentity("public", "failed_events"),
        ))
        policy.validate_metadata("core", expected)
        self.assertEqual(len(expected - missing), 29)
        with self.assertRaises(policy.MigrationPolicyError) as failure:
            policy.validate_metadata("core", expected - missing)
        self.assertEqual(failure.exception.code, "MIGRATION_METADATA_INCOMPLETE")
        self.assertEqual(failure.exception.tables, missing)

    def test_duplicate_canonical_and_foreign_metadata_are_rejected(self) -> None:
        expected = list(policy.owned_tables("client"))
        cases = (
            (expected + [policy.TableIdentity(None, "client_keys")], "MIGRATION_METADATA_DUPLICATE"),
            (expected + [policy.TableIdentity("audit_schema", "projects")], "MIGRATION_METADATA_FOREIGN"),
        )
        for supplied, code in cases:
            with self.subTest(code=code), self.assertRaises(policy.MigrationPolicyError) as failure:
                policy.validate_metadata("client", supplied)
            self.assertEqual(failure.exception.code, code)

    def test_include_name_rejects_foreign_tables_before_reflection(self) -> None:
        core = policy.OwnershipFilters("core")
        management = policy.OwnershipFilters("management")
        for schema, name, expected_core, expected_management in (
            ("audit_schema", "projects", True, False),
            (None, "projects", False, True),
            ("public", "projects", False, True),
            ("public", "processed_events", True, False),
            ("audit_schema", "project_integrations", True, False),
            ("public", "alembic_version", False, False),
            ("public", "management_alembic_version", False, False),
            ("audit_schema", "crawl_results_p999", False, False),
            ("reporting_schema", "gsc_data_2028", False, False),
            ("public", "foreign_table", False, False),
        ):
            with self.subTest(schema=schema, name=name):
                parents = {"schema_name": schema}
                self.assertEqual(core.include_name(name, "table", parents), expected_core)
                self.assertEqual(management.include_name(name, "table", parents), expected_management)
        self.assertTrue(core.include_name(None, "schema", {}))
        self.assertFalse(management.include_name("audit_schema", "schema", {}))
        self.assertFalse(core.include_name("projects", "table", {}))
        self.assertFalse(core.include_name("x", "unknown_type", {}))

    def test_children_and_compare_objects_cannot_escape_parent_ownership(self) -> None:
        filters = policy.OwnershipFilters("management")
        owned = SimpleNamespace(schema=None, name="projects")
        foreign = SimpleNamespace(schema="audit_schema", name="projects")
        for type_ in ("column", "index", "unique_constraint", "foreign_key_constraint"):
            with self.subTest(type_=type_):
                self.assertTrue(filters.include_name("x", type_, {"schema_name": None, "table_name": "projects"}))
                self.assertFalse(filters.include_name("x", type_, {"schema_name": "audit_schema", "table_name": "projects"}))
                self.assertFalse(filters.include_name("x", type_, {"schema_name": None}))
                item = SimpleNamespace(name="x", table=owned)
                self.assertTrue(filters.include_object(item, "x", type_, False, None))
                self.assertFalse(filters.include_object(SimpleNamespace(table=foreign), "x", type_, False, None))
                self.assertFalse(filters.include_object(item, "x", type_, False, SimpleNamespace(table=foreign)))
                self.assertFalse(filters.include_object(
                    SimpleNamespace(table=SimpleNamespace(schema=None, name="unknown")),
                    "x", type_, True, None,
                ))
        self.assertTrue(filters.include_object(owned, "projects", "table", False, None))
        self.assertFalse(filters.include_object(foreign, "projects", "table", False, None))
        self.assertFalse(filters.include_object(owned, "different", "table", False, None))
        with self.assertRaisesRegex(policy.MigrationPolicyError, "MIGRATION_REFLECTED_TABLE_UNMODELED"):
            filters.include_object(owned, "projects", "table", True, None)
