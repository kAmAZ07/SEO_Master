"""Регрессии платформы; приложения и рабочая конфигурация не импортируются."""

import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch
import yaml
from scripts import backend


ROOT = Path(__file__).resolve().parents[2]


class BackendToolingTests(unittest.TestCase):
    def test_job_env_cannot_reference_runtime_job_context(self) -> None:
        # GitHub evaluates job.env before a running job/services context exists.
        # This narrow regression supplements YAML parsing; it is not actionlint.
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
            for job_name, job in workflow.get("jobs", {}).items():
                for value in job.get("env", {}).values():
                    if isinstance(value, str):
                        with self.subTest(workflow=path.name, job=job_name):
                            self.assertNotRegex(value, r"\$\{\{[^}]*\bjob\.")

    def test_profiles_keep_incompatible_runtime_requirements_separate(self) -> None:
        management = backend.get_profile("management")
        client = backend.get_profile("client")
        self.assertNotEqual(management.runtime_input, client.runtime_input)
        self.assertEqual(management.tests, (
            "tests/unit/test_hitl_handler.py",
            "tests/unit/test_orchestrator.py",
            "tests/unit/test_prioritizer.py",
            "tests/unit/test_management_model_imports.py",
            "tests/unit/test_management_scores.py",
            "tests/unit/test_management_proposals.py",
            "tests/unit/test_management_approvals.py",
            "tests/unit/test_management_recovery.py",
            "tests/unit/test_verification_policy.py",
        ))
        self.assertEqual(client.tests, (
            "tests/unit/test_client_startup.py",
            "tests/unit/test_client_recovery.py",
            "tests/unit/test_client_preparation.py",
            "tests/unit/test_public_verifier.py",
            "tests/unit/test_client_gateway_rollback.py",
            "tests/unit/test_client_model_imports.py",
        ))
        with self.assertRaises(ValueError):
            backend.get_profile("../management")

    def test_all_service_profiles_cover_existing_unit_inventory(self) -> None:
        self.assertEqual(
            {profile.name for profile in backend.PROFILES},
            {"management", "client", "api", "audit", "semantic", "reporting", "tilda"},
        )
        covered: set[str] = set()
        for profile in backend.PROFILES:
            backend.validate_test_inventory(ROOT, profile)
            covered.update(profile.tests)
        self.assertEqual(covered, {
            candidate.relative_to(ROOT).as_posix()
            for candidate in (ROOT / "tests/unit").glob("test_*.py")
        })

    def test_new_unowned_unit_file_cannot_silently_escape_all_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests/unit").mkdir(parents=True)
            (root / "tests/unit/test_unowned.py").write_text(
                "def test_new():\n    assert True\n", encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "профил"):
                backend.validate_test_inventory(root, backend.get_profile("management"))

    def test_adapter_python_is_copied_without_adapter_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            destination = root / "snapshot"
            adapter = source / "adapters/tilda-adapter"
            adapter.mkdir(parents=True)
            (adapter / "meta_injector.py").write_text("VALUE = 1\n", encoding="utf-8")
            (adapter / ".env").write_text("not-for-the-snapshot\n", encoding="utf-8")
            backend.copy_sources(source, destination, (
                "adapters/tilda-adapter/meta_injector.py", "adapters/tilda-adapter/.env",
            ))
            self.assertTrue((destination / "adapters/tilda-adapter/meta_injector.py").is_file())
            self.assertFalse((destination / "adapters/tilda-adapter/.env").exists())

    def test_child_environment_does_not_inherit_application_or_tool_settings(self) -> None:
        with patch.dict(os.environ, {
            "DATABASE_URL": "must-not-be-inherited",
            "INTERNAL_API_KEY": "must-not-be-inherited",
            "PYTHONPATH": "must-not-be-inherited",
            "PYTEST_ADDOPTS": "--collect-only",
            "UV_INDEX": "must-not-be-inherited",
            "PIP_EXTRA_INDEX_URL": "must-not-be-inherited",
        }):
            environment = backend.build_tool_environment()
        for name in (
            "DATABASE_URL", "INTERNAL_API_KEY", "PYTHONPATH", "PYTEST_ADDOPTS",
            "UV_INDEX", "PIP_EXTRA_INDEX_URL",
        ):
            self.assertNotIn(name, environment)

    def test_runtime_home_cache_and_temporary_files_stay_inside_disposable_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            snapshot = workspace / "source"
            snapshot.mkdir()
            environment = backend.build_test_environment(snapshot, workspace)
            for name in (
                "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR",
            ):
                path = Path(environment[name])
                self.assertTrue(path.resolve().is_relative_to(workspace.resolve()))
                self.assertTrue(path.is_dir())
            for prefix in ("AUDIT", "SEMANTIC", "REPORTING"):
                self.assertEqual(
                    environment[f"{prefix}_DATABASE_URL"],
                    "postgresql+asyncpg://127.0.0.1:1/backend_unit",
                )
            self.assertEqual(environment["PYTHONPATH"], str(snapshot))

    def test_source_snapshot_never_copies_dotenv_or_untracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            destination = root / "snapshot"
            (source / "config").mkdir(parents=True)
            (source / "config/settings.py").write_text("SETTING = 1\n", encoding="utf-8")
            (source / ".env").write_text("not-for-the-snapshot\n", encoding="utf-8")
            (source / "config/.env").write_text("not-for-the-snapshot\n", encoding="utf-8")
            (source / "config/untracked.py").write_text("not tracked\n", encoding="utf-8")
            backend.copy_sources(
                source, destination, ("config/settings.py", ".env", "config/.env"),
            )
            self.assertEqual(
                [path.relative_to(destination).as_posix()
                 for path in destination.rglob("*") if path.is_file()],
                ["config/settings.py"],
            )

    def test_snapshot_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                backend.copy_sources(root, root / "snapshot", ("config/../../escape.py",))

    def test_snapshot_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            candidate = root / "config/settings.py"
            candidate.write_text("SETTING = 1\n", encoding="utf-8")
            with patch.object(Path, "is_symlink", return_value=True):
                with self.assertRaises(ValueError):
                    backend.copy_sources(root, root / "snapshot", ("config/settings.py",))

    def test_snapshot_rejects_absolute_paths_and_internal_parent_segments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            candidate = root / "config/settings.py"
            candidate.write_text("SETTING = 1\n", encoding="utf-8")
            for path in (str(candidate), "config/../config/settings.py"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    backend.copy_sources(root, root / "snapshot", (path,))

    def test_snapshot_rejects_hardlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            original = root / "original.py"
            original.write_text("SETTING = 1\n", encoding="utf-8")
            os.link(original, root / "config/settings.py")
            with self.assertRaises(ValueError):
                backend.copy_sources(root, root / "snapshot", ("config/settings.py",))

    def test_windows_reparse_attribute_is_rejected_without_symlink_flag(self) -> None:
        with patch.object(Path, "is_symlink", return_value=False):
            for attributes, is_reparse in ((0, False), (1024, True), (1040, True)):
                with self.subTest(attributes=attributes), patch.object(
                    Path, "lstat", return_value=SimpleNamespace(st_file_attributes=attributes),
                ):
                    self.assertEqual(
                        backend.is_link_or_reparse_point(Path("source.py")), is_reparse,
                    )

    def test_new_service_test_file_cannot_silently_escape_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests/unit").mkdir(parents=True)
            for name in backend.get_profile("management").tests:
                (root / name).write_text(
                    "from services.management_service import config\n", encoding="utf-8",
                )
            backend.validate_test_inventory(root, backend.get_profile("management"))
            (root / "tests/unit/test_new_management.py").write_text(
                "from services.management_service import config\n", encoding="utf-8",
            )
            with self.assertRaises(RuntimeError):
                backend.validate_test_inventory(root, backend.get_profile("management"))

    def test_dockerfiles_install_runtime_locks_with_hash_validation(self) -> None:
        for service in ("management_service", "client_api_gateway"):
            with self.subTest(service=service):
                dockerfile = (ROOT / "services" / service / "Dockerfile").read_text(
                    encoding="utf-8",
                )
                self.assertIn("--require-hashes", dockerfile)
                self.assertNotIn("-r requirements.txt", dockerfile)

    def test_required_test_plugin_rejects_skip_xfail_and_empty_collection(self) -> None:
        plugin = ROOT / "scripts/pytest_required.py"
        self.assertTrue(plugin.is_file())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for body, expected_success in (
                ("def test_pass():\n    assert True\n", True),
                ("import pytest\npytest.skip('missing dependency', allow_module_level=True)\n", False),
                ("import pytest\n@pytest.mark.xfail(reason='known failure')\n"
                 "def test_fail():\n    assert False\n", False),
                ("import pytest\n@pytest.mark.xfail(reason='unexpected pass')\n"
                 "def test_pass():\n    assert True\n", False),
                ("import socket\nsocket.getaddrinfo('127.0.0.1', 1)\n"
                 "def test_pass():\n    assert True\n", False),
                ("", False),
            ):
                (root / "test_sample.py").write_text(body, encoding="utf-8")
                environment = os.environ.copy()
                environment.pop("PYTEST_ADDOPTS", None)
                environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
                environment["PYTHONPATH"] = str(ROOT)
                result = subprocess.run(
                    [sys.executable, "-m", "pytest", "-p", "scripts.pytest_required",
                     "-q", "--rootdir", str(root), str(root / "test_sample.py")],
                    cwd=root, env=environment, capture_output=True, text=True, check=False,
                )
                self.assertEqual(
                    result.returncode == 0, expected_success,
                    msg=result.stdout + result.stderr,
                )

    def test_network_guard_precedes_initial_conftest_import(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "conftest.py").write_text(
                "import socket\nimport pytest\n"
                "for name in ('getaddrinfo', 'gethostbyname', 'gethostbyname_ex',\n"
                "             'gethostbyaddr', 'getnameinfo'):\n"
                "    resolver = getattr(socket, name)\n"
                "    assert resolver.__module__ == 'scripts.pytest_required', name\n"
                "    with pytest.raises(RuntimeError):\n"
                "        resolver('127.0.0.1', 1)\n",
                encoding="utf-8",
            )
            (root / "test_sample.py").write_text(
                "def test_pass():\n    assert True\n", encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.pop("PYTEST_ADDOPTS", None)
            environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
            environment["PYTHONPATH"] = str(ROOT)
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-p", "scripts.pytest_required",
                 "-q", "--rootdir", str(root), str(root / "test_sample.py")],
                cwd=root, env=environment, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

    def test_native_database_drivers_are_guarded_before_collection(self) -> None:
        self.run_policy_regression(
            "import sys\n"
            "import types\n"
            "from unittest.mock import Mock, patch\n"
            "import pytest\n"
            "from scripts.pytest_required import RequiredTests\n"
            "\n"
            "def test_dbapi_entry_points():\n"
            "    drivers = {}\n"
            "    calls = []\n"
            "    for name in ('psycopg2', 'psycopg', 'asyncpg'):\n"
            "        driver = types.ModuleType(name)\n"
            "        driver.connect = Mock()\n"
            "        driver.create_pool = Mock()\n"
            "        calls.extend((driver.connect, driver.create_pool))\n"
            "        drivers[name] = driver\n"
            "    with patch.dict(sys.modules, drivers):\n"
            "        policy = RequiredTests()\n"
            "        policy.block_network()\n"
            "        try:\n"
            "            for driver in drivers.values():\n"
            "                with pytest.raises(RuntimeError):\n"
            "                    driver.connect('postgresql://127.0.0.1:1/test')\n"
            "            with pytest.raises(RuntimeError):\n"
            "                drivers['asyncpg'].create_pool()\n"
            "            for call in calls:\n"
            "                call.assert_not_called()\n"
            "        finally:\n"
            "            policy.network_patch.undo()\n"
        )

    def test_network_policy_allows_only_own_ephemeral_loopback_listeners(self) -> None:
        self.run_policy_regression(
            "import asyncio\n"
            "import errno\n"
            "import socket\n"
            "import pytest\n"
            "from scripts.pytest_required import RequiredTests\n"
            "from unittest.mock import patch\n"
            "\n"
            "def test_owned_ipv4_listener():\n"
            "    with socket.socket() as listener:\n"
            "        listener.bind(('127.0.0.1', 0))\n"
            "        address = listener.getsockname()\n"
            "        listener.listen()\n"
            "        with socket.socket(type=socket.SOCK_DGRAM) as datagram:\n"
            "            with pytest.raises(RuntimeError):\n"
            "                datagram.connect(address)\n"
            "            with pytest.raises(RuntimeError):\n"
            "                datagram.connect_ex(address)\n"
            "        with socket.socket() as client:\n"
            "            client.connect(address)\n"
            "        with socket.socket() as client:\n"
            "            assert client.connect_ex(address) == 0\n"
            "    with socket.socket() as client:\n"
            "        with pytest.raises(ConnectionRefusedError) as exc:\n"
            "            client.connect(address)\n"
            "        assert exc.value.errno == errno.ECONNREFUSED\n"
            "        assert client.connect_ex(address) == errno.ECONNREFUSED\n"
            "\n"
            "def test_bound_but_not_listening_port_is_not_contacted():\n"
            "    with socket.socket() as bound:\n"
            "        bound.bind(('127.0.0.1', 0))\n"
            "        with socket.socket() as client:\n"
            "            with pytest.raises(ConnectionRefusedError):\n"
            "                client.connect(bound.getsockname())\n"
            "\n"
            "def test_preexisting_listener_is_not_implicitly_allowed():\n"
            "    with socket.socket() as foreign:\n"
            "        foreign.bind(('127.0.0.1', 0))\n"
            "        foreign.listen()\n"
            "        address = foreign.getsockname()\n"
            "        policy = RequiredTests()\n"
            "        policy.block_network()\n"
            "        try:\n"
            "            with socket.socket() as client:\n"
            "                with pytest.raises(RuntimeError):\n"
            "                    client.connect(address)\n"
            "        finally:\n"
            "            policy.network_patch.undo()\n"
            "\n"
            "def test_internal_socketpair_and_asyncio_still_work():\n"
            "    first, second = socket.socketpair()\n"
            "    with first, second:\n"
            "        first.sendall(b'fixture')\n"
            "        assert second.recv(7) == b'fixture'\n"
            "    async def scenario():\n"
            "        await asyncio.sleep(0)\n"
            "        return 'completed'\n"
            "    assert asyncio.run(scenario()) == 'completed'\n"
            "\n"
            "def test_unowned_loopback_dns_and_udp_stay_blocked():\n"
            "    for address in (('127.0.0.1', 1), ('127.0.0.1', 5432),\n"
            "                    ('::1', 1, 0, 0), ('8.8.8.8', 80)):\n"
            "        with socket.socket() as client:\n"
            "            with pytest.raises(RuntimeError):\n"
            "                client.connect(address)\n"
            "            with pytest.raises(RuntimeError):\n"
            "                client.connect_ex(address)\n"
            "    with pytest.raises(RuntimeError):\n"
            "        socket.getaddrinfo('localhost', 80)\n"
            "    with socket.socket(type=socket.SOCK_DGRAM) as client:\n"
            "        with pytest.raises(RuntimeError):\n"
            "            client.sendto(b'no', ('127.0.0.1', 1))\n"
            "\n"
            "def test_forbidden_binds_are_rejected_before_os_call():\n"
            "    with patch.object(socket.socket, 'bind') as original:\n"
            "        policy = RequiredTests()\n"
            "        policy.block_network()\n"
            "        try:\n"
            "            for address in (('0.0.0.0', 0), ('127.0.0.1', 5432),\n"
            "                            ('8.8.8.8', 0), ('::', 0), ('::1', 0, 0, 1)):\n"
            "                with socket.socket() as listener:\n"
            "                    with pytest.raises(RuntimeError):\n"
            "                        listener.bind(address)\n"
            "            original.assert_not_called()\n"
            "        finally:\n"
            "            policy.network_patch.undo()\n"
            "\n"
            "def test_ipv6_owned_listener_uses_exact_address():\n"
            "    if not socket.has_ipv6:\n"
            "        with socket.socket() as client:\n"
            "            with pytest.raises(RuntimeError):\n"
            "                client.connect(('::1', 12345, 0, 0))\n"
            "        return\n"
            "    with patch.object(socket.socket, 'connect') as original:\n"
            "        policy = RequiredTests()\n"
            "        policy.block_network()\n"
            "        try:\n"
            "            with socket.socket(socket.AF_INET6) as listener:\n"
            "                listener.bind(('::1', 0))\n"
            "                listener.listen()\n"
            "                with socket.socket(socket.AF_INET6) as client:\n"
            "                    client.connect(listener.getsockname())\n"
            "                    original.assert_called_once()\n"
            "                    address = ('::1', listener.getsockname()[1], 0, 1)\n"
            "                    with pytest.raises(RuntimeError):\n"
            "                        client.connect(address)\n"
            "                    original.assert_called_once()\n"
            "        finally:\n"
            "            policy.network_patch.undo()\n"
        )

    def test_stream_protocol_guard_rejects_non_tcp_before_native_calls(self) -> None:
        self.run_policy_regression(textwrap.dedent("""
            from contextlib import ExitStack
            import socket
            from unittest.mock import patch
            import pytest
            from scripts.pytest_required import RequiredTests

            class SocketStub:
                def __init__(self, family=socket.AF_INET, proto=0):
                    self.family = family
                    self.type = socket.SOCK_STREAM
                    self.proto = proto
                def getsockname(self):
                    return ('::1', 49152, 0, 0) if self.family == socket.AF_INET6 else ('127.0.0.1', 49152)
                def fileno(self):
                    return 42
                def getsockopt(self, *arguments):
                    return 1

            @pytest.fixture
            def guarded_calls():
                with ExitStack() as stack:
                    originals = {
                        name: stack.enter_context(patch.object(socket.socket, name))
                        for name in ('bind', 'listen', 'connect', 'connect_ex')
                    }
                    originals['connect_ex'].return_value = 0
                    policy = RequiredTests()
                    policy.block_network()
                    try:
                        yield originals
                    finally:
                        policy.network_patch.undo()

            def own_listener(family=socket.AF_INET, proto=0):
                listener = SocketStub(family, proto)
                address = ('::1', 0, 0, 0) if family == socket.AF_INET6 else ('127.0.0.1', 0)
                socket.socket.bind(listener, address)
                socket.socket.listen(listener)
                return listener

            @pytest.mark.parametrize('proto', [17, 132, 255])
            @pytest.mark.parametrize('family', [socket.AF_INET, socket.AF_INET6])
            def test_non_tcp_bind_is_rejected(guarded_calls, family, proto):
                connection = SocketStub(family, proto)
                address = ('::1', 0, 0, 0) if family == socket.AF_INET6 else ('127.0.0.1', 0)
                with pytest.raises(RuntimeError):
                    socket.socket.bind(connection, address)
                guarded_calls['bind'].assert_not_called()

            @pytest.mark.parametrize('proto', [17, 132, 255])
            @pytest.mark.parametrize('operation', ['connect', 'connect_ex'])
            def test_non_tcp_client_is_rejected(guarded_calls, operation, proto):
                listener = own_listener()
                with pytest.raises(RuntimeError):
                    getattr(socket.socket, operation)(SocketStub(proto=proto), listener.getsockname())
                guarded_calls[operation].assert_not_called()

            @pytest.mark.parametrize('operation', ['connect', 'connect_ex'])
            def test_listener_protocol_is_rechecked(guarded_calls, operation):
                listener = own_listener()
                listener.proto = 132
                with pytest.raises(RuntimeError):
                    getattr(socket.socket, operation)(SocketStub(), listener.getsockname())
                guarded_calls[operation].assert_not_called()

            def test_non_tcp_listener_cannot_start_listening(guarded_calls):
                listener = SocketStub()
                socket.socket.bind(listener, ('127.0.0.1', 0))
                listener.proto = 132
                with pytest.raises(RuntimeError):
                    socket.socket.listen(listener)
                guarded_calls['listen'].assert_not_called()

            @pytest.mark.parametrize('family', [socket.AF_INET, socket.AF_INET6])
            @pytest.mark.parametrize('listener_proto', [0, socket.IPPROTO_TCP])
            @pytest.mark.parametrize('client_proto', [0, socket.IPPROTO_TCP])
            def test_default_and_explicit_tcp_still_work(guarded_calls, family, listener_proto, client_proto):
                listener = own_listener(family, listener_proto)
                client = SocketStub(family, client_proto)
                socket.socket.connect(client, listener.getsockname())
                assert socket.socket.connect_ex(client, listener.getsockname()) == 0
                guarded_calls['bind'].assert_called_once()
                guarded_calls['listen'].assert_called_once()
                guarded_calls['connect'].assert_called_once()
                guarded_calls['connect_ex'].assert_called_once()
        """))

    def test_optional_sendmsg_is_rejected_before_native_call(self) -> None:
        self.run_policy_regression(textwrap.dedent("""
            import socket
            from unittest.mock import patch
            import pytest
            from scripts.pytest_required import RequiredTests

            def test_sendmsg_never_reaches_the_native_socket():
                # create=True models POSIX sendmsg on Windows without an OS packet.
                with patch.object(socket.socket, 'sendmsg', create=True) as original:
                    policy = RequiredTests()
                    policy.block_network()
                    try:
                        with pytest.raises(RuntimeError):
                            socket.socket.sendmsg(object(), [b'fixture'], [], 0, ('127.0.0.1', 53))
                        original.assert_not_called()
                    finally:
                        policy.network_patch.undo()
        """))

    def run_policy_regression(self, body: str) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test_policy.py").write_text(body, encoding="utf-8")
            environment = backend.build_tool_environment()
            environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
            environment["PYTHONPATH"] = str(ROOT)
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-p", "scripts.pytest_required",
                 "-p", "no:cacheprovider", "-q", str(root / "test_policy.py")],
                cwd=root, env=environment, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
