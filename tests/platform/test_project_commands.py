"""Общий runner не читает рабочий dotenv и не разрешает опасные действия."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import backend, project


class ProjectCommandTests(unittest.TestCase):
    def test_windows_bootstrap_removes_foreign_explicit_permissions(self) -> None:
        if os.name != "nt":
            # POSIX has no Windows ACEs; its permission boundary is tested below.
            self.assertNotEqual(os.name, "nt")
            return
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / ".seo-local/dev"
            state.mkdir(parents=True)
            subprocess.run(
                ["icacls.exe", str(state), "/grant", "*S-1-1-0:(OI)(CI)R"],
                check=True, capture_output=True,
            )
            path = project.bootstrap(root, "dev")
            project.validate_private_permissions(state)
            project.validate_private_permissions(path)
            # A direct file ACE must be rejected before reading existing key bytes.
            subprocess.run(
                ["icacls.exe", str(path), "/grant", "*S-1-1-0:R"],
                check=True, capture_output=True,
            )
            with patch.object(Path, "read_text", side_effect=AssertionError("secret read")), self.assertRaises(ValueError):
                project.load_configuration(root, "dev")
            with self.assertRaises(ValueError):
                project.bootstrap(root, "dev")

    def test_powershell_wrapper_fails_when_python_cannot_launch(self) -> None:
        executable = shutil.which("powershell.exe" if os.name == "nt" else "pwsh")
        if executable is None:
            raise RuntimeError("Для проверки PowerShell-оболочки нужен PowerShell")
        wrapper = Path(__file__).resolve().parents[2] / "scripts/project.ps1"
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-File", str(wrapper),
             "-Action", "up", "-Python", "seo-nonexistent-python-for-regression"],
            capture_output=True, check=False, timeout=20,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_powershell_wrapper_preserves_real_runner_exit_codes(self) -> None:
        executable = shutil.which("powershell.exe" if os.name == "nt" else "pwsh")
        if executable is None:
            raise RuntimeError("Для проверки PowerShell-оболочки нужен PowerShell")
        wrapper = Path(__file__).resolve().parents[2] / "scripts/project.ps1"
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / ("python fixture.cmd" if os.name == "nt" else "python-fixture")
            for exit_code in (0, 2, 7):
                fixture.write_text(
                    f"@exit /b {exit_code}\r\n" if os.name == "nt"
                    else f"#!/bin/sh\nexit {exit_code}\n",
                    encoding="ascii",
                )
                fixture.chmod(0o700)
                result = subprocess.run(
                    [executable, "-NoProfile", "-NonInteractive", "-File", str(wrapper),
                     "-Action", "up", "-Python", str(fixture)],
                    capture_output=True, check=False, timeout=20,
                )
                self.assertEqual(result.returncode, exit_code)

    def test_common_gate_covers_every_backend_profile_and_resolves_php_from_path(self) -> None:
        self.assertEqual(set(project.BACKEND_PROFILES), {item.name for item in backend.PROFILES})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = root / "tests/wordpress/run.py"
            runner.parent.mkdir(parents=True)
            runner.write_text("# fixture\n", encoding="utf-8")
            with (
                patch("scripts.project.run_checked", return_value=0) as run,
                patch("scripts.project.check_frontend", return_value=0),
                patch("scripts.project.shutil.which", return_value=sys.executable),
            ):
                self.assertEqual(project.check_quality(root, "test", "php"), 0)
            self.assertEqual(
                run.call_args_list[-1].args[0],
                [sys.executable, "-B", str(runner), "--php", sys.executable],
            )

    def test_remote_or_malformed_docker_transports_are_rejected_before_daemon_call(self) -> None:
        for endpoint in (
            "npipe:////remote-host/pipe/docker_engine",
            "npipe:////./pipe/../docker_engine",
            "unix://remote-host/docker.sock", "unix:///../docker.sock",
            "unix:///var/run/docker.sock?remote=1", "tcp://127.0.0.1:2375",
        ):
            with (
                self.subTest(endpoint=endpoint),
                patch("scripts.project.subprocess.run", return_value=SimpleNamespace(
                    stdout=endpoint, returncode=0,
                )) as run,
                self.assertRaises(ValueError),
            ):
                project.require_local_docker()
            self.assertEqual(run.call_count, 1)
        for endpoint in ("npipe:////./pipe/dockerDesktopLinuxEngine", "unix:///var/run/docker.sock"):
            with patch("scripts.project.subprocess.run", side_effect=(
                SimpleNamespace(stdout=endpoint, returncode=0),
                SimpleNamespace(stdout="linux", returncode=0),
            )):
                project.require_local_docker()

    def test_php_executable_is_canonicalized_before_changing_child_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            executable = workspace / ("php.exe" if os.name == "nt" else "php")
            executable.write_text("# fixture\n", encoding="ascii")
            executable.chmod(0o700)
            relative_executable = os.path.relpath(executable, Path.cwd())
            self.assertIsNotNone(shutil.which(relative_executable))
            root = workspace / "checkout"
            runner = root / "tests/wordpress/run.py"
            runner.parent.mkdir(parents=True)
            runner.write_text("# fixture\n", encoding="ascii")
            with (
                patch("scripts.project.run_checked", return_value=0) as run,
                patch("scripts.project.check_frontend", return_value=0),
            ):
                self.assertEqual(project.check_quality(root, "test", relative_executable), 0)
            self.assertEqual(run.call_args_list[-1].args[0][-1], str(executable.resolve()))

    def test_frontend_executables_are_canonicalized_before_entering_snapshot(self) -> None:
        relative_python = os.path.relpath(sys.executable, Path.cwd())
        with (
            patch("scripts.project.shutil.which", return_value=relative_python),
            patch("scripts.project.subprocess.run", return_value=SimpleNamespace(
                stdout="v24.14.1", returncode=0,
            )) as version,
            patch("scripts.project.copy_frontend"),
            patch("scripts.project.run_checked", return_value=0) as run,
        ):
            self.assertEqual(project.check_frontend(Path.cwd(), "test"), 0)
        expected = str(Path(sys.executable).resolve())
        self.assertEqual(version.call_args.args[0][0], expected)
        for call in run.call_args_list:
            self.assertEqual(call.args[0][0], expected)

    def test_frontend_home_cache_and_npm_configuration_are_owned_by_temporary_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            environment = project.prepare_frontend_environment(workspace)
            for name in (
                "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "NPM_CONFIG_CACHE",
                "NPM_CONFIG_USERCONFIG", "NPM_CONFIG_GLOBALCONFIG",
                "TEMP", "TMP", "TMPDIR",
            ):
                self.assertTrue(Path(environment[name]).resolve().is_relative_to(workspace.resolve()))
            for name in ("NPM_CONFIG_USERCONFIG", "NPM_CONFIG_GLOBALCONFIG"):
                self.assertEqual(Path(environment[name]).read_bytes(), b"")
            self.assertNotEqual(
                environment["NPM_CONFIG_USERCONFIG"], environment["NPM_CONFIG_GLOBALCONFIG"],
            )
            self.assertEqual(environment["NPM_CONFIG_REGISTRY"], "https://registry.npmjs.org/")

    def test_parent_environment_retains_only_os_locations_for_docker_plugin_discovery(self) -> None:
        with patch.dict(os.environ, {"PROGRAMDATA": "os-program-data"}):
            environment = project.build_environment()
        self.assertEqual(environment["PROGRAMDATA"], "os-program-data")
    def test_project_name_cannot_target_an_existing_shared_stack(self) -> None:
        valid = "seo-local-dev-" + "a" * 24
        self.assertEqual(project.validate_project_name(valid, "dev"), valid)
        for candidate in ("seo-master", "seo-master-dev", "../escape", valid + "-more"):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                project.validate_project_name(candidate, "dev")
        with self.assertRaises(ValueError):
            project.validate_project_name(valid, "prod")

    def test_compose_command_is_explicit_and_down_keeps_volumes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = root / ".seo-local/dev/environment.env"
            command = project.build_compose_command(
                root, environment, "seo-local-dev-" + "a" * 24, "dev", "down",
            )
            self.assertIn("--env-file", command)
            self.assertIn(str(environment), command)
            self.assertIn("--project-name", command)
            self.assertEqual(command[-1], "down")
            for forbidden in ("-v", "--volumes", "--remove-orphans", "--rmi"):
                self.assertNotIn(forbidden, command)
            self.assertEqual(command.count("--file"), 2)
            self.assertIn(str(root / "docker-compose.dev.yml"), command)
            with self.assertRaises(ValueError):
                project.build_compose_command(
                    root, environment, "seo-local-dev-" + "a" * 24, "dev", "rm",
                )

    def test_parent_service_and_compose_variables_are_not_inherited(self) -> None:
        with patch.dict(os.environ, {
            "DATABASE_URL": "must-not-be-inherited",
            "COMPOSE_FILE": "unexpected.yml",
            "COMPOSE_PROFILES": "dangerous",
            "COMPOSE_PROJECT_NAME": "shared",
            "DOCKER_HOST": "tcp://example.invalid",
            "NODE_OPTIONS": "--require=unexpected.js",
            "NPM_CONFIG_USERCONFIG": "unexpected",
        }):
            environment = project.build_environment()
        for name in (
            "DATABASE_URL", "COMPOSE_FILE", "COMPOSE_PROFILES",
            "COMPOSE_PROJECT_NAME", "DOCKER_HOST", "NODE_OPTIONS",
            "NPM_CONFIG_USERCONFIG",
        ):
            self.assertNotIn(name, environment)

    def test_bootstrap_is_private_idempotent_and_does_not_read_working_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("do-not-read", encoding="utf-8")
            path = project.bootstrap(root, "dev")
            before = path.read_bytes()
            self.assertEqual(project.bootstrap(root, "dev"), path)
            self.assertEqual(path.read_bytes(), before)
            configuration = project.load_configuration(root, "dev")
            self.assertEqual(configuration.environment_file, path)
            self.assertTrue(configuration.project_name.startswith("seo-local-dev-"))
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                project.bootstrap(root, "prod")

    def test_configuration_rejects_unknown_values_and_linked_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = project.bootstrap(root, "dev")
            path.write_text(path.read_text(encoding="utf-8") + "DATABASE_URL=outside\n")
            with self.assertRaises(ValueError):
                project.load_configuration(root, "dev")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = project.bootstrap(root, "dev")
            os.link(path, root / "linked.env")
            with self.assertRaises(ValueError):
                project.load_configuration(root, "dev")

    def test_up_and_e2e_fail_closed_before_docker_until_migration_gate_exists(self) -> None:
        with patch("scripts.project.subprocess.run") as run:
            for action in ("up", "e2e"):
                with self.subTest(action=action):
                    self.assertNotEqual(project.main([action]), 0)
            run.assert_not_called()

    def test_make_and_powershell_are_thin_wrappers(self) -> None:
        root = Path(__file__).resolve().parents[2]
        makefile = (root / "Makefile").read_text(encoding="utf-8")
        powershell = (root / "scripts/project.ps1").read_text(encoding="utf-8")
        for action in ("bootstrap", "up", "test", "e2e", "lint", "build", "down"):
            self.assertIn(action, makefile)
            self.assertIn(action, powershell)
        self.assertIn("scripts/project.py", makefile)
        self.assertIn("project.py", powershell)
        self.assertNotIn("docker compose", makefile)
        self.assertNotIn("docker compose", powershell)
