"""CI-проверка UID готовых локальных образов; не application startup и не E2E."""

from enum import Enum
import re
import subprocess
import sys
from typing import Literal

from scripts import project


class ImageService(str, Enum):
    API = "api-gateway"
    AUDIT = "audit-service"
    SEMANTIC = "semantic-service"
    REPORTING = "reporting-service"
    CLIENT = "client-api-gateway"
    TILDA = "tilda-adapter"
    MANAGEMENT = "management-web"
    FRONTEND = "frontend"


ImageFailure = Literal[
    "IMAGE_CREATE_FAILED", "IMAGE_PROBE_FAILED", "IMAGE_UID_INVALID",
    "IMAGE_CLEANUP_FAILED",
]


class ImageCheckError(RuntimeError):
    def __init__(self, code: ImageFailure) -> None:
        super().__init__(code)


def build_probe_command(project_name: str, service: ImageService) -> list[str]:
    project.validate_project_name(project_name, "dev")
    if service is ImageService.FRONTEND:
        entrypoint = "/bin/busybox"
        arguments = ["id", "-u"]
    else:
        entrypoint = "/usr/local/bin/python"
        # No sitecustomize, user packages or application imports.
        arguments = ["-I", "-S", "-B", "-c", "import os; print(os.geteuid())"]
    return [
        "docker", "create", "--pull=never", "--read-only", "--network=none",
        "--cap-drop=ALL", "--security-opt=no-new-privileges", "--no-healthcheck",
        "--memory=256m", "--cpus=1", "--pids-limit=64",
        f"--entrypoint={entrypoint}", f"{project_name}-{service.value}:latest",
        *arguments,
    ]


def execute(command: list[str], failure: ImageFailure) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command, env=project.build_environment(), capture_output=True,
            encoding="utf-8", errors="replace", check=False, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        # Docker diagnostics may contain local configuration; never relay them.
        raise ImageCheckError(failure) from None


def check_image(project_name: str, service: ImageService) -> None:
    created = execute(build_probe_command(project_name, service), "IMAGE_CREATE_FAILED")
    container_id = created.stdout.strip()
    if created.returncode or re.fullmatch(r"[a-f0-9]{64}", container_id) is None:
        # No valid newly-created ID: never guess a name or delete other resources.
        raise ImageCheckError("IMAGE_CREATE_FAILED")
    try:
        result = execute(["docker", "start", "--attach", container_id], "IMAGE_PROBE_FAILED")
        expected_uid = "101" if service is ImageService.FRONTEND else "10001"
        if result.returncode:
            raise ImageCheckError("IMAGE_PROBE_FAILED")
        if result.stdout.strip() != expected_uid:
            raise ImageCheckError("IMAGE_UID_INVALID")
    finally:
        # Only the ID returned by this create, with no supplied mounts/volumes.
        # --volumes removes its new anonymous image volumes, never named volumes.
        removed = execute(
            ["docker", "container", "rm", "--force", "--volumes", container_id],
            "IMAGE_CLEANUP_FAILED",
        )
        if removed.returncode:
            raise ImageCheckError("IMAGE_CLEANUP_FAILED")


def main() -> int:
    try:
        project.require_local_docker()
        configuration = project.load_configuration(project.ROOT, "dev")
    except (ValueError, OSError, subprocess.SubprocessError):
        print("IMAGE_PREFLIGHT_FAILED: требуется локальный Linux Docker и dev bootstrap.", file=sys.stderr)
        return 2
    has_failures = False
    for service in ImageService:
        try:
            check_image(configuration.project_name, service)
        except ImageCheckError as error:
            has_failures = True
            print(f"{error}: {service.value}", file=sys.stderr)
    print("IMAGE_UID_SCOPE_ONLY: not application startup or E2E.")
    return int(has_failures)


if __name__ == "__main__":
    raise SystemExit(main())
