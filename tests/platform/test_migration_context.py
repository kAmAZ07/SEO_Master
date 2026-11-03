"""Исполняет настоящий env.py с безопасными фейками; ни приложения, ни БД."""

from collections.abc import Iterator
from configparser import ConfigParser
from contextlib import contextmanager
import importlib.abc
import importlib.machinery
import os
from pathlib import Path
import runpy
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from typing import Callable, Mapping, cast
import unittest
from unittest.mock import patch
from scripts.migration_policy import MigrationPolicyError


ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENT = ROOT / "database/migrations/env.py"
FIXTURE_URL = "postgresql://127.0.0.1:1/migration_fixture"


class ForbiddenApplicationImports(importlib.abc.MetaPathFinder):
    def find_spec(
        self, fullname: str, path: object = None, target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname.split(".")[0] in {"config", "database", "services", "shared"}:
            raise AssertionError("APPLICATION_IMPORT_FORBIDDEN")
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.executions = 0
        self.commits = 0

    def execute(self, statement: object, *parameters: object) -> None:
        self.executions += 1
        raise AssertionError("OUTSIDE_REVISION_SQL_FORBIDDEN")

    def commit(self) -> None:
        self.commits += 1
        raise AssertionError("OUTSIDE_TRANSACTION_COMMIT_FORBIDDEN")


class FakeConfiguration:
    def __init__(self, url: str | None, connection: FakeConnection | None) -> None:
        self.url = url
        self.attributes: dict[str, object] = {}
        if connection is not None:
            self.attributes["connection"] = connection
        self.config_file_name: str | None = None
        self.config_ini_section = "alembic"
        self.cmd_opts = None

    def get_main_option(self, name: str) -> str | None:
        return self.url if name == "sqlalchemy.url" else None


class FakeEnvironment:
    def __init__(
        self, url: str | None = FIXTURE_URL, *, offline: bool = False,
        injected: FakeConnection | None = None, revision_context: object = None,
        fail_revision: bool = False,
    ) -> None:
        self.config = FakeConfiguration(url, injected)
        self.offline = offline
        self.options: dict[str, object] = {}
        if revision_context is not None:
            self.options["revision_context"] = revision_context
        self.configurations: list[dict[str, object]] = []
        self.runs = 0
        self.transactions = 0
        self.fail_revision = fail_revision
        self.connection = injected if injected is not None else FakeConnection()
        self.engine_urls: list[str] = []
        self.closed = 0
        self.disposed = 0
        self.fail_engine = False
        self.fail_connect = False

    def is_offline_mode(self) -> bool:
        return self.offline

    def configure(self, **options: object) -> None:
        self.options.update(options)
        self.configurations.append(options)

    def get_context(self) -> SimpleNamespace:
        return SimpleNamespace(opts=self.options)

    @contextmanager
    def begin_transaction(self) -> Iterator[None]:
        self.transactions += 1
        yield

    def run_migrations(self) -> None:
        self.runs += 1
        if self.fail_revision:
            raise RuntimeError("VERSION_CALLBACK_FAILED")

    def create_engine(self, url: str, **options: object) -> "FakeEnvironment":
        self.engine_urls.append(url)
        if self.fail_engine:
            raise ValueError(f"Driver setup failed for {url}")
        return self

    @contextmanager
    def connect(self) -> Iterator[FakeConnection]:
        if self.fail_connect:
            raise ValueError("Connection setup failed")
        try:
            yield self.connection
        finally:
            self.closed += 1

    def dispose(self) -> None:
        self.disposed += 1


def execute_environment(environment: FakeEnvironment) -> None:
    alembic = ModuleType("alembic")
    setattr(alembic, "context", environment)
    sqlalchemy = ModuleType("sqlalchemy")
    setattr(sqlalchemy, "create_engine", environment.create_engine)
    setattr(sqlalchemy, "engine_from_config", environment.create_engine)
    setattr(sqlalchemy, "pool", SimpleNamespace(NullPool=object()))
    setattr(sqlalchemy, "text", str)
    with (
        patch.dict(sys.modules, {"alembic": alembic, "sqlalchemy": sqlalchemy}),
        patch.object(sys, "meta_path", [ForbiddenApplicationImports(), *sys.meta_path]),
    ):
        runpy.run_path(str(ENVIRONMENT), run_name="migration_env_regression")


class MigrationContextTests(unittest.TestCase):
    def test_configuration_url_lookup_errors_are_redacted_before_setup(self) -> None:
        for offline in (False, True):
            with self.subTest(offline=offline):
                parser = ConfigParser()
                parser.read_string(
                    "[alembic]\nsqlalchemy.url = "
                    "postgresql://127.0.0.1:1/fixture%41PUBLIC_REVIEW_MARKER\n"
                )
                environment = FakeEnvironment(offline=offline)
                with (
                    patch.object(
                        environment.config, "get_main_option",
                        side_effect=lambda name: parser.get("alembic", name),
                    ),
                    self.assertRaises(MigrationPolicyError) as failure,
                ):
                    execute_environment(environment)
                self.assertEqual(failure.exception.code, "MIGRATION_DSN_INVALID")
                self.assertEqual(str(failure.exception), "MIGRATION_DSN_INVALID")
                self.assertIsNone(failure.exception.__cause__)
                self.assertTrue(failure.exception.__suppress_context__)
                self.assertEqual(environment.configurations, [])
                self.assertEqual(environment.engine_urls, [])
                self.assertEqual(environment.runs, 0)

    def test_offline_configuration_errors_are_redacted_before_revision(self) -> None:
        environment = FakeEnvironment(offline=True)
        with (
            patch.object(
                environment, "configure",
                side_effect=[None, ValueError("PUBLIC_REVIEW_MARKER")],
            ),
            self.assertRaises(MigrationPolicyError) as failure,
        ):
            execute_environment(environment)
        self.assertEqual(failure.exception.code, "MIGRATION_DSN_INVALID")
        self.assertEqual(str(failure.exception), "MIGRATION_DSN_INVALID")
        self.assertIsNone(failure.exception.__cause__)
        self.assertTrue(failure.exception.__suppress_context__)
        self.assertEqual(environment.engine_urls, [])
        self.assertEqual((environment.runs, environment.transactions), (0, 0))

    def test_non_string_url_is_rejected_with_a_stable_error(self) -> None:
        for invalid in (123, False, object()):
            environment = FakeEnvironment()
            with (
                self.subTest(type=type(invalid).__name__),
                patch.object(environment.config, "get_main_option", return_value=invalid),
                self.assertRaisesRegex(ValueError, "MIGRATION_DSN_INVALID"),
            ):
                execute_environment(environment)
            self.assertEqual(environment.configurations, [])
            self.assertEqual(environment.engine_urls, [])

    def test_invalid_url_and_offline_connection_fail_before_any_factory(self) -> None:
        for url in (" " + FIXTURE_URL, FIXTURE_URL + "\0", "x" * 8193):
            environment = FakeEnvironment(url)
            with self.subTest(url_length=len(url)), self.assertRaisesRegex(ValueError, "MIGRATION_DSN_INVALID"):
                execute_environment(environment)
            self.assertEqual(environment.configurations, [])
            self.assertEqual(environment.engine_urls, [])
        environment = FakeEnvironment(None, offline=True, injected=FakeConnection())
        with self.assertRaisesRegex(ValueError, "MIGRATION_OFFLINE_CONNECTION_UNSUPPORTED"):
            execute_environment(environment)
        self.assertEqual(environment.configurations, [])

    def test_setup_failure_is_redacted_and_owned_engine_is_disposed(self) -> None:
        for fail_engine in (True, False):
            environment = FakeEnvironment()
            environment.fail_engine = fail_engine
            environment.fail_connect = not fail_engine
            with self.subTest(fail_engine=fail_engine), self.assertRaises(ValueError) as failure:
                execute_environment(environment)
            self.assertEqual(str(failure.exception), "MIGRATION_CONNECTION_FAILED")
            self.assertIsNone(failure.exception.__cause__)
            self.assertTrue(failure.exception.__suppress_context__)
            self.assertEqual(environment.disposed, 0 if fail_engine else 1)
            self.assertEqual(environment.runs, 0)

    def test_custom_autogenerate_flag_also_fails_closed(self) -> None:
        environment = FakeEnvironment()
        environment.options["autogenerate"] = True
        with self.assertRaisesRegex(ValueError, "MIGRATION_AUTOGENERATE_BLOCKED"):
            execute_environment(environment)
        self.assertEqual(environment.engine_urls, [])

    def test_explicit_online_url_is_used_without_helpers_or_hidden_provider(self) -> None:
        environment = FakeEnvironment()
        with patch.dict(os.environ, {"DATABASE_URL": "must-not-be-used", "SECRETS_BACKEND": "aws"}):
            execute_environment(environment)
        self.assertEqual(environment.engine_urls, [FIXTURE_URL])
        self.assertEqual((environment.runs, environment.transactions), (1, 1))
        self.assertEqual((environment.connection.executions, environment.connection.commits), (0, 0))
        self.assertEqual((environment.closed, environment.disposed), (1, 1))

    def test_injected_connection_needs_no_url_and_is_not_closed_or_disposed(self) -> None:
        connection = FakeConnection()
        environment = FakeEnvironment(None, injected=connection)
        execute_environment(environment)
        self.assertIs(environment.options["connection"], connection)
        self.assertEqual(environment.engine_urls, [])
        self.assertEqual((environment.closed, environment.disposed), (0, 0))
        self.assertEqual((connection.executions, connection.commits), (0, 0))

    def test_missing_url_fails_before_configuration_engine_or_models(self) -> None:
        for offline in (False, True):
            for url in (None, "", "   "):
                environment = FakeEnvironment(url, offline=offline)
                with self.subTest(offline=offline, url=url), self.assertRaisesRegex(ValueError, "MIGRATION_DSN_MISSING"):
                    execute_environment(environment)
                self.assertEqual(environment.configurations, [])
                self.assertEqual(environment.engine_urls, [])
                self.assertEqual(environment.runs, 0)

    def test_programmatic_check_and_revision_context_block_before_connect(self) -> None:
        for offline in (False, True):
            environment = FakeEnvironment(offline=offline, revision_context=object())
            with self.subTest(offline=offline), self.assertRaisesRegex(ValueError, "MIGRATION_AUTOGENERATE_BLOCKED"):
                execute_environment(environment)
            self.assertEqual(environment.engine_urls, [])
            self.assertEqual(environment.runs, 0)
            self.assertNotIn("connection", environment.options)

    def test_offline_uses_only_explicit_url_and_alembic_transaction(self) -> None:
        environment = FakeEnvironment(offline=True)
        execute_environment(environment)
        self.assertEqual(environment.engine_urls, [])
        self.assertEqual(environment.options["url"], FIXTURE_URL)
        self.assertIs(environment.options["target_metadata"], None)
        self.assertEqual((environment.runs, environment.transactions), (1, 1))
        self.assertEqual(environment.connection.executions, 0)

    def test_owned_filters_are_really_forwarded_to_alembic(self) -> None:
        environment = FakeEnvironment(injected=FakeConnection())
        execute_environment(environment)
        include_name = cast(
            Callable[[str | None, str, Mapping[str, str | None]], bool],
            environment.options["include_name"],
        )
        self.assertTrue(include_name("projects", "table", {"schema_name": "audit_schema"}))
        self.assertFalse(include_name("projects", "table", {"schema_name": None}))
        include_object = cast(
            Callable[[object, str | None, str, bool, object | None], bool],
            environment.options["include_object"],
        )
        foreign = SimpleNamespace(schema=None, name="projects")
        self.assertFalse(include_object(foreign, "projects", "table", True, None))

    def test_failed_revision_still_closes_owned_engine_without_manual_commit(self) -> None:
        environment = FakeEnvironment(fail_revision=True)
        with self.assertRaisesRegex(RuntimeError, "VERSION_CALLBACK_FAILED"):
            execute_environment(environment)
        self.assertEqual((environment.closed, environment.disposed), (1, 1))
        self.assertEqual((environment.connection.executions, environment.connection.commits), (0, 0))

    def test_policy_context_imports_are_stdlib_only_without_envfile_or_network(self) -> None:
        source = r"""
import importlib.abc
import pathlib
import sys
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'config','database','services','shared','sqlalchemy','alembic'}:
            raise AssertionError('FORBIDDEN_IMPORT')
def audit(event, args):
    if event.startswith('socket.'):
        raise AssertionError('NETWORK_FORBIDDEN')
    if event == 'open' and isinstance(args[0], (str, bytes)):
        if pathlib.Path(args[0]).name.startswith('.env'):
            raise AssertionError('DOTENV_FORBIDDEN')
sys.meta_path.insert(0, Deny())
sys.addaudithook(audit)
sys.path.insert(0, sys.argv[1])
from scripts import migration_policy, migration_context
assert migration_policy.owned_tables('core')
print('CLEAN_IMPORT_OK')
"""
        environment = {
            key: value for key, value in os.environ.items()
            if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
        }
        environment.update({"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        result = subprocess.run(
            [sys.executable, "-B", "-c", source, str(ROOT)], env=environment,
            check=False, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CLEAN_IMPORT_OK", result.stdout)


# Explicit separate command, NOT a silently skipped unittest. Requires the existing
# management-test lock; it must fail if that pinned environment is unavailable.
PINNED_ALEMBIC_PROBE = r"""
from importlib.metadata import version
import importlib.abc
import io
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from scripts.pytest_required import RequiredTests
guard = RequiredTests()
guard.block_network()
class DenyApplication(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'config','database','services','shared'}:
            raise AssertionError('APPLICATION_IMPORT_FORBIDDEN')
def audit(event, args):
    if event == 'open' and isinstance(args[0], str) and Path(args[0]).name.startswith('.env'):
        raise AssertionError('DOTENV_FORBIDDEN')
sys.meta_path.insert(0, DenyApplication())
sys.addaudithook(audit)
assert version('alembic') == '1.13.3'
from alembic import command
from alembic.config import Config
from alembic.runtime.environment import EnvironmentContext
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.dialects.postgresql import dialect
from scripts.migration_policy import MigrationPolicyError
root = Path(sys.argv[1])
checked = 0
with tempfile.TemporaryDirectory(prefix='seo-alembic-api-') as directory:
    location = Path(directory)
    (location / 'versions').mkdir()
    shutil.copyfile(root / 'database/migrations/env.py', location / 'env.py')
    def configuration(with_url=True):
        result = Config(stdout=io.StringIO())
        result.set_main_option('script_location', str(location))
        if with_url:
            result.set_main_option('sqlalchemy.url', 'postgresql://127.0.0.1:1/no_contact')
        return result
    with patch('sqlalchemy.create_engine', side_effect=AssertionError('ENGINE_BEFORE_PREFLIGHT')) as engine_factory:
        for cli_options in (None, SimpleNamespace(autogenerate=True)):
            for operation in ('check', 'autogenerate', 'revision_environment'):
                configured = configuration()
                configured.cmd_opts = cli_options
                try:
                    if operation == 'check':
                        command.check(configured)
                    elif operation == 'autogenerate':
                        command.revision(configured, autogenerate=True)
                    else:
                        configured.set_main_option('revision_environment', 'true')
                        command.revision(configured, autogenerate=False)
                except MigrationPolicyError as error:
                    assert error.code == 'MIGRATION_AUTOGENERATE_BLOCKED', error.code
                else:
                    raise AssertionError('UNSAFE_GENERATION_WAS_NOT_BLOCKED')
                checked += 1
        try:
            command.current(configuration(with_url=False))
        except MigrationPolicyError as error:
            assert error.code == 'MIGRATION_DSN_MISSING', error.code
        else:
            raise AssertionError('MISSING_DSN_WAS_NOT_BLOCKED')
        checked += 1
        assert not engine_factory.called, 'ENGINE_FACTORY_WAS_CALLED'
        invalid_configurations = []
        ini = location / 'interpolation.ini'
        ini.write_text(
            '[alembic]\nscript_location = ' + str(location) +
            '\nsqlalchemy.url = postgresql://127.0.0.1:1/fixture%41PUBLIC_REVIEW_MARKER\n',
            encoding='utf-8',
        )
        invalid_configurations.append(('configuration_interpolation', Config(str(ini)), False))
        for invalid_url in (
            'not-a-dsn',
            'postgresql://127.0.0.1:not-a-port/PUBLIC_REVIEW_MARKER',
        ):
            configured = configuration(with_url=False)
            configured.set_main_option('sqlalchemy.url', invalid_url)
            invalid_configurations.append(('offline_url', configured, True))
        failures = []
        for label, configured, offline in invalid_configurations:
            try:
                with (
                    patch.object(MigrationContext, '_ensure_version_table', side_effect=AssertionError('VERSION_DDL')) as version_ddl,
                    patch.object(MigrationContext, 'run_migrations', side_effect=AssertionError('REVISION_CALLBACK')) as revision_callback,
                ):
                    if offline:
                        script = ScriptDirectory.from_config(configured)
                        with EnvironmentContext(configured, script, as_sql=True, starting_rev='base'):
                            script.run_env()
                    else:
                        command.current(configured)
            except MigrationPolicyError as error:
                if (
                    error.code != 'MIGRATION_DSN_INVALID' or str(error) != 'MIGRATION_DSN_INVALID'
                    or error.__cause__ is not None or not error.__suppress_context__
                ):
                    failures.append(label + ':unstable_or_unredacted_error')
            except Exception as error:
                failures.append(label + ':' + type(error).__name__)
            else:
                failures.append(label + ':configuration_was_accepted')
            if version_ddl.called or revision_callback.called:
                failures.append(label + ':forbidden_migration_or_version_ddl')
            checked += 1
        assert not failures, 'INVALID_CONFIGURATION_REDACTION_FAILED:' + ','.join(failures)
        class BorrowedConnection:
            dialect = dialect()
            def in_transaction(self):
                return True
            def execute(self, *args, **kwargs):
                raise AssertionError('SQL_WAS_EXECUTED')
            def commit(self):
                raise AssertionError('BORROWED_CONNECTION_COMMITTED')
            def close(self):
                raise AssertionError('BORROWED_CONNECTION_CLOSED')
        configured = configuration(with_url=False)
        configured.attributes['connection'] = BorrowedConnection()
        with (
            patch.object(MigrationContext, 'get_current_heads', return_value=()) as heads,
            patch.object(MigrationContext, '_ensure_version_table', side_effect=AssertionError('VERSION_DDL')),
        ):
            command.current(configured)
            assert heads.called
        checked += 1
        assert not engine_factory.called, 'ENGINE_FACTORY_WAS_CALLED'
assert not any(name.split('.')[0] in {'config','database','services','shared'} for name in sys.modules)
guard.network_patch.undo()
print('PINNED_ALEMBIC_NO_DATABASE_OK', checked)
"""


if __name__ == "__main__":
    if sys.argv[1:] == ["--native-alembic"]:
        subprocess.run(
            [sys.executable, "-B", "-c", PINNED_ALEMBIC_PROBE, str(ROOT)],
            check=True, timeout=30,
        )
    else:
        unittest.main()
