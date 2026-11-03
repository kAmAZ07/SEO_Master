"""Проверки Docker-команд с fake subprocess, без daemon, приложений и БД."""

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from scripts import image_checks, project


PROJECT = "seo-local-dev-" + "a" * 24
CONTAINER_ID = "b" * 64


class ImageCheckTests(unittest.TestCase):
    def test_probe_covers_every_distinct_dockerfile_without_infrastructure(self) -> None:
        self.assertEqual({service.value for service in image_checks.ImageService}, {
            "api-gateway", "audit-service", "semantic-service", "reporting-service",
            "client-api-gateway", "tilda-adapter", "management-web", "frontend",
        })
        for service in image_checks.ImageService:
            with self.subTest(service=service.value):
                command = image_checks.build_probe_command(PROJECT, service)
                self.assertEqual(command[:2], ["docker", "create"])
                self.assertIn(f"{PROJECT}-{service.value}:latest", command)
                for required in (
                    "--pull=never", "--read-only", "--network=none", "--cap-drop=ALL",
                    "--security-opt=no-new-privileges", "--no-healthcheck",
                    "--memory=256m", "--cpus=1", "--pids-limit=64",
                ):
                    self.assertIn(required, command)
                for forbidden in (
                    "--user", "--env", "--env-file", "--mount", "--volume",
                    "--publish", "--privileged", "--network=host", "--use-api-socket",
                ):
                    self.assertNotIn(forbidden, command)
                if service is image_checks.ImageService.FRONTEND:
                    self.assertEqual(command[-2:], ["id", "-u"])
                    self.assertIn("--entrypoint=/bin/busybox", command)
                else:
                    self.assertEqual(command[-5:], [
                        "-I", "-S", "-B", "-c", "import os; print(os.geteuid())",
                    ])
                    self.assertIn("--entrypoint=/usr/local/bin/python", command)

    def test_shared_or_malformed_project_is_rejected_before_any_docker_call(self) -> None:
        for candidate in ("seo-master", "../outside", PROJECT + "-extra"):
            with self.subTest(project=candidate), self.assertRaises(ValueError):
                image_checks.build_probe_command(candidate, image_checks.ImageService.API)

    def test_success_checks_real_uid_and_removes_only_its_created_container(self) -> None:
        for service in image_checks.ImageService:
            uid = 101 if service is image_checks.ImageService.FRONTEND else 10001
            with (
                self.subTest(service=service.value),
                patch("scripts.image_checks.subprocess.run", side_effect=(
                    subprocess.CompletedProcess([], 0, CONTAINER_ID + "\n", ""),
                    subprocess.CompletedProcess([], 0, f"{uid}\n", ""),
                    subprocess.CompletedProcess([], 0, CONTAINER_ID + "\n", ""),
                )) as execute,
            ):
                image_checks.check_image(PROJECT, service)
                self.assertEqual(execute.call_args_list[1].args[0], [
                    "docker", "start", "--attach", CONTAINER_ID,
                ])
                self.assertEqual(execute.call_args_list[2].args[0], [
                    "docker", "container", "rm", "--force", "--volumes", CONTAINER_ID,
                ])
                for call in execute.call_args_list:
                    self.assertTrue(call.kwargs["capture_output"])
                    self.assertGreater(call.kwargs["timeout"], 0)
                    self.assertNotIn("DATABASE_URL", call.kwargs["env"])
                    self.assertNotIn("COMPOSE_FILE", call.kwargs["env"])

    def test_root_wrong_uid_and_invalid_output_fail_with_cleanup(self) -> None:
        for output in ("0\n", "101\n", "10001\nextra", "not-a-uid"):
            with (
                self.subTest(output=output),
                patch("scripts.image_checks.subprocess.run", side_effect=(
                    subprocess.CompletedProcess([], 0, CONTAINER_ID, ""),
                    subprocess.CompletedProcess([], 0, output, ""),
                    subprocess.CompletedProcess([], 0, CONTAINER_ID, ""),
                )) as execute,
                self.assertRaisesRegex(image_checks.ImageCheckError, "IMAGE_UID_INVALID"),
            ):
                image_checks.check_image(PROJECT, image_checks.ImageService.API)
            self.assertEqual(execute.call_count, 3)

    def test_probe_error_or_timeout_still_cleans_up_owned_container(self) -> None:
        for result in (
            subprocess.CompletedProcess([], 1, "", "private-diagnostic"),
            subprocess.TimeoutExpired(["docker", "start"], 20),
        ):
            with (
                self.subTest(result=type(result).__name__),
                patch("scripts.image_checks.subprocess.run", side_effect=(
                    subprocess.CompletedProcess([], 0, CONTAINER_ID, ""),
                    result,
                    subprocess.CompletedProcess([], 0, CONTAINER_ID, ""),
                )) as execute,
                self.assertRaises(image_checks.ImageCheckError) as raised,
            ):
                image_checks.check_image(PROJECT, image_checks.ImageService.API)
            self.assertNotIn("private-diagnostic", str(raised.exception))
            self.assertEqual(execute.call_args_list[-1].args[0][-1], CONTAINER_ID)

    def test_failed_create_never_starts_or_deletes_an_unproven_container(self) -> None:
        for result in (
            subprocess.CompletedProcess([], 1, "", "private-diagnostic"),
            subprocess.CompletedProcess([], 0, "unrelated-container", ""),
            subprocess.TimeoutExpired(["docker", "create"], 20),
        ):
            with (
                self.subTest(result=type(result).__name__),
                patch("scripts.image_checks.subprocess.run", side_effect=(result,)) as execute,
                self.assertRaises(image_checks.ImageCheckError),
            ):
                image_checks.check_image(PROJECT, image_checks.ImageService.API)
            self.assertEqual(execute.call_count, 1)

    def test_cleanup_failure_is_a_failed_gate_not_silently_ignored(self) -> None:
        for result in (
            subprocess.CompletedProcess([], 1, "", "private-diagnostic"),
            subprocess.TimeoutExpired(["docker", "container", "rm"], 10),
        ):
            with (
                self.subTest(result=type(result).__name__),
                patch("scripts.image_checks.subprocess.run", side_effect=(
                    subprocess.CompletedProcess([], 0, CONTAINER_ID, ""),
                    subprocess.CompletedProcess([], 0, "10001", ""),
                    result,
                )),
                self.assertRaisesRegex(image_checks.ImageCheckError, "IMAGE_CLEANUP_FAILED"),
            ):
                image_checks.check_image(PROJECT, image_checks.ImageService.API)

    def test_unavailable_daemon_fails_before_loading_local_key_file(self) -> None:
        with (
            patch("scripts.image_checks.project.require_local_docker", side_effect=ValueError),
            patch("scripts.image_checks.project.load_configuration") as load,
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(image_checks.main(), 2)
        load.assert_not_called()

    def test_full_gate_checks_all_images_and_does_not_relay_diagnostics(self) -> None:
        with (
            patch("scripts.image_checks.project.require_local_docker"),
            patch("scripts.image_checks.project.load_configuration", return_value=(
                project.Configuration(Path("unused"), PROJECT)
            )),
            patch("scripts.image_checks.check_image", side_effect=(
                image_checks.ImageCheckError("IMAGE_UID_INVALID"), *([None] * 7),
            )) as check,
            redirect_stdout(io.StringIO()) as output,
            redirect_stderr(io.StringIO()) as errors,
        ):
            self.assertEqual(image_checks.main(), 1)
        self.assertEqual(check.call_count, len(image_checks.ImageService))
        self.assertIn("IMAGE_UID_INVALID", errors.getvalue())
        self.assertIn("not application startup", output.getvalue())


if __name__ == "__main__":
    unittest.main()
