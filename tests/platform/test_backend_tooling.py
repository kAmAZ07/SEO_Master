"""Регрессии платформы; приложения и рабочая конфигурация не импортируются."""

import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from scripts import backend


ROOT = Path(__file__).resolve().parents[2]


class BackendToolingTests(unittest.TestCase):
    def test_profiles_keep_incompatible_runtime_requirements_separate(self) -> None:
        management = backend.get_profile("management")
        client = backend.get_profile("client")
        self.assertNotEqual(management.runtime_input, client.runtime_input)
        self.assertEqual(len(management.tests), 3)
        self.assertEqual(len(client.tests), 1)
        with self.assertRaises(ValueError):
            backend.get_profile("../management")

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


if __name__ == "__main__":
    unittest.main()
