"""Воспроизводимые locks и изолированные unit-проверки AR-сервисов."""

import argparse
import base64
from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile


UV_VERSION = "0.11.3"
PYTHON_VERSION = "3.11"
ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORIES = ("config", "database", "shared", "services", "scripts", "tests", "adapters")
WINDOWS_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT из Windows SDK.


@dataclass(frozen=True)
class Profile:
    name: str
    runtime_input: str
    tests: tuple[str, ...]


PROFILES = (
    Profile("management", "services/management_service/requirements.txt", (
        "tests/unit/test_hitl_handler.py",
        "tests/unit/test_orchestrator.py",
        "tests/unit/test_prioritizer.py",
        "tests/unit/test_management_model_imports.py",
    )),
    Profile("client", "services/client_api_gateway/requirements.txt", (
        "tests/unit/test_client_gateway_rollback.py",
        "tests/unit/test_client_model_imports.py",
    )),
    Profile("api", "services/api_gateway/requirements.txt", (
        "tests/unit/test_api_gateway_auth.py",
        "tests/unit/test_api_gateway_error_headers.py",
        "tests/unit/test_api_gateway_protected_routes_layout.py",
        "tests/unit/test_domain_verification.py",
        "tests/unit/test_domain_verification_network.py",
        "tests/unit/test_netpolicy.py",
        "tests/unit/test_project_access.py",
        "tests/unit/test_project_core.py",
        "tests/unit/test_project_integrations_service.py",
        "tests/unit/test_project_scope.py",
        "tests/unit/test_projects_gateway.py",
        "tests/unit/test_public_audit_gateway.py",
        "tests/unit/test_public_audit_rate_limit.py",
    )),
    Profile("audit", "services/audit_service/requirements.txt", (
        "tests/unit/test_audit_task_created_handler.py",
        "tests/unit/test_audit_top_findings.py",
        "tests/unit/test_cwv_analyzer.py",
        "tests/unit/test_meta_checker.py",
        "tests/unit/test_public_audit_claim_token.py",
        "tests/unit/test_public_audit_core.py",
        "tests/unit/test_public_audit_lifecycle.py",
        "tests/unit/test_public_crawler.py",
    )),
    Profile("semantic", "services/semantic_service/requirements.txt", (
        "tests/unit/test_eeat_analyzer.py",
        "tests/unit/test_ff_score_calculator.py",
        "tests/unit/test_llm_policy.py",
        "tests/unit/test_semantic_proposals.py",
        "tests/unit/test_semantic_proposals_extra.py",
        "tests/unit/test_semantic_scoring_api.py",
    )),
    Profile("reporting", "services/reporting_service/requirements.txt", (
        "tests/unit/test_csv_exporter.py",
        "tests/unit/test_metrics_calculator.py",
    )),
    Profile("tilda", "adapters/tilda-adapter/requirements.txt", (
        "tests/unit/test_tilda_meta_injector.py",
    )),
)


def get_profile(name: str) -> Profile:
    for profile in PROFILES:
        if profile.name == name:
            return profile
    raise ValueError("Неизвестный backend-профиль")


def build_tool_environment() -> dict[str, str]:
    allowed_names = (
        "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
        "TMPDIR", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
    )
    environment = {
        name: value for name, value in os.environ.items()
        if name.upper() in allowed_names
    }
    environment.update({
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
    })
    return environment


def copy_sources(source: Path, destination: Path, paths: tuple[str, ...]) -> None:
    source = source.resolve(strict=True)
    destination.mkdir(parents=True, exist_ok=True)
    for relative in paths:
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("Требуется относительный путь без родительских сегментов")
        candidate = source / relative
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(source):
            raise ValueError("Путь исходника выходит за пределы checkout")
        if any(is_link_or_reparse_point(part) for part in (candidate, *candidate.parents)
               if part.is_relative_to(source)):
            raise ValueError("Символические ссылки в snapshot запрещены")
        if relative != "pytest.ini" and (
            candidate.suffix != ".py" or Path(relative).parts[0] not in SOURCE_DIRECTORIES
        ):
            continue
        if resolved.stat().st_nlink != 1:
            raise ValueError("Жёсткие ссылки в snapshot запрещены")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(resolved, target)


def is_link_or_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return isinstance(attributes, int) and bool(attributes & WINDOWS_REPARSE_POINT)


def validate_test_inventory(snapshot: Path, profile: Profile) -> None:
    discovered: set[str] = set()
    for candidate in (snapshot / "tests/unit").glob("test_*.py"):
        source = candidate.read_text(encoding="utf-8-sig")
        owners = {
            item.name for item in PROFILES
            if (
                str(Path(item.runtime_input).parent).replace("\\", "/").replace("/", ".")
                in source
                or str(Path(item.runtime_input).parent).replace("\\", "/") in source
                or (item.name == "tilda" and "'tilda-adapter'" in source)
            )
        }
        if not owners and ("shared.projects" in source or "shared.netpolicy" in source):
            owners.add("api")
        if not owners:
            raise RuntimeError(
                f"Unit-файл не принадлежит ни одному backend-профилю: {candidate.name}",
            )
        if profile.name in owners:
            discovered.add(candidate.relative_to(snapshot).as_posix())
    if discovered != set(profile.tests):
        raise RuntimeError("Список unit-тестов сервиса изменился; обновите backend-профиль")


def require_uv(environment: dict[str, str]) -> str:
    executable = shutil.which("uv", path=environment.get("PATH", environment.get("Path")))
    if executable is None:
        raise RuntimeError(f"Установите uv=={UV_VERSION}")
    result = subprocess.run(
        [executable, "--version"], env=environment, check=True,
        capture_output=True, text=True,
    )
    if result.stdout.split()[:2] != ["uv", UV_VERSION]:
        raise RuntimeError(f"Требуется ровно uv=={UV_VERSION}")
    return executable


def compile_lock(
    executable: str, environment: dict[str, str], source: str,
    destination: Path, constraints: tuple[Path, ...] = (),
) -> None:
    command = [
        executable, "--no-config", "--no-progress", "pip", "compile", source,
        "--python-version", PYTHON_VERSION, "--universal", "--generate-hashes",
        "--no-header", "--no-annotate", "--no-sources", "--no-build",
        "--no-python-downloads", "--default-index", "https://pypi.org/simple",
        "--output-file", str(destination), "--quiet",
    ]
    for constraint in constraints:
        command.extend(("--constraint", str(constraint)))
    subprocess.run(command, cwd=ROOT, env=environment, check=True)


def update_locks(check: bool) -> int:
    environment = build_tool_environment()
    executable = require_uv(environment)
    specifications = [
        ("locks/tooling.in", "tooling", ()),
        *[(profile.runtime_input, profile.name, ()) for profile in PROFILES],
        *[(f"locks/{profile.name}-test.in", f"{profile.name}-test",
           (ROOT / f"locks/{profile.name}.txt",)) for profile in PROFILES],
    ]
    with tempfile.TemporaryDirectory(prefix="seo-lock-check-") as directory:
        for source, name, constraints in specifications:
            checked_in = ROOT / f"locks/{name}.txt"
            output = Path(directory) / f"{name}.txt" if check else checked_in
            if check:
                # Existing versions are constraints, not a floating "latest" resolution.
                # A changed input still changes the package set or fails resolution.
                if not checked_in.is_file():
                    raise RuntimeError(f"Отсутствует lock: {checked_in.name}")
                constraints = (*constraints, checked_in)
            compile_lock(executable, environment, source, output, constraints)
            if check and output.read_text(encoding="utf-8") != checked_in.read_text(
                encoding="utf-8",
            ):
                print(f"Lock не соответствует входам: locks/{name}.txt", file=sys.stderr)
                return 1
            print(f"Lock {'проверен' if check else 'обновлён'}: locks/{name}.txt")
    return 0


def build_test_environment(snapshot: Path, workspace: Path) -> dict[str, str]:
    environment = build_tool_environment()
    for name, relative in (
        ("HOME", "home"), ("USERPROFILE", "home"),
        ("APPDATA", "home/roaming"), ("LOCALAPPDATA", "home/local"),
        ("TEMP", "temp"), ("TMP", "temp"), ("TMPDIR", "temp"),
    ):
        path = workspace / relative
        path.mkdir(parents=True, exist_ok=True)
        environment[name] = str(path)
    environment.update({
        "ENVIRONMENT": "test",
        "DATABASE_URL": "postgresql://127.0.0.1:1/backend_unit",
        "REDIS_URL": "redis://127.0.0.1:1/0",
        "RABBITMQ_HOST": "127.0.0.1",
        "RABBITMQ_PORT": "1",
        "RABBITMQ_USER": "unit",
        "RABBITMQ_PASSWORD": secrets.token_urlsafe(32),
        "INTERNAL_API_KEY": secrets.token_urlsafe(32),
        "JWT_SECRET_KEY": secrets.token_urlsafe(48),
        "MASTER_ENCRYPTION_KEY": base64.urlsafe_b64encode(
            secrets.token_bytes(32),
        ).decode("ascii"),
        "PYTHONPATH": str(snapshot),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "CORS_ORIGINS": "http://localhost",
    })
    for prefix in ("AUDIT", "SEMANTIC", "REPORTING"):
        environment.update({
            f"{prefix}_DATABASE_URL": "postgresql+asyncpg://127.0.0.1:1/backend_unit",
            f"{prefix}_REDIS_URL": "redis://127.0.0.1:1/0",
            f"{prefix}_RABBITMQ_URL": "amqp://127.0.0.1:1/",
        })
    return environment


def run_tests(profile: Profile) -> int:
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("Backend-профили требуют Python 3.11")
    environment = build_tool_environment()
    listing = subprocess.run(
        ["git", "--no-pager", "ls-files", "-z", "--",
         *SOURCE_DIRECTORIES, "pytest.ini"],
        cwd=ROOT, env=environment, check=True, capture_output=True,
    )
    paths = tuple(listing.stdout.decode("utf-8").rstrip("\0").split("\0"))
    with tempfile.TemporaryDirectory(prefix=f"seo-unit-{profile.name}-") as directory:
        snapshot = Path(directory) / "source"
        copy_sources(ROOT, snapshot, paths)
        validate_test_inventory(snapshot, profile)
        for required in (*profile.tests, "scripts/pytest_required.py"):
            if not (snapshot / required).is_file():
                raise RuntimeError(f"Нужный исходник не отслеживается Git: {required}")
        environment = build_test_environment(snapshot, Path(directory))
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "pytest_asyncio.plugin",
             "-p", "scripts.pytest_required", "--strict-config", "--strict-markers",
             "-p", "no:cacheprovider", "-ra", "-q", *profile.tests],
            cwd=snapshot, env=environment, check=False,
        )
        return result.returncode


def check_profile(profile: Profile) -> int:
    environment = build_tool_environment()
    executable = require_uv(environment)
    with tempfile.TemporaryDirectory(prefix=f"seo-venv-{profile.name}-") as directory:
        virtualenv = Path(directory) / "venv"
        subprocess.run(
            [executable, "--no-config", "venv", "--no-python-downloads",
             "--python", sys.executable, str(virtualenv)],
            env=environment, check=True,
        )
        python = virtualenv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run(
            [executable, "--no-config", "pip", "sync", "--no-python-downloads",
             "--python", str(python), "--require-hashes", "--only-binary", ":all:",
             "--default-index", "https://pypi.org/simple",
             str(ROOT / f"locks/{profile.name}-test.txt")],
            env=environment, check=True,
        )
        result = subprocess.run(
            [str(python), "-B", str(Path(__file__).resolve()), "test", profile.name],
            env=environment, check=False,
        )
        return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    lock_parser = subcommands.add_parser("lock")
    lock_parser.add_argument("--check", action="store_true")
    test_parser = subcommands.add_parser("test")
    test_parser.add_argument("profile", choices=tuple(profile.name for profile in PROFILES))
    check_parser = subcommands.add_parser("check")
    check_parser.add_argument("profile", choices=tuple(profile.name for profile in PROFILES))
    arguments = parser.parse_args()
    if arguments.command == "lock":
        return update_locks(arguments.check)
    if arguments.command == "check":
        return check_profile(get_profile(arguments.profile))
    return run_tests(get_profile(arguments.profile))


if __name__ == "__main__":
    raise SystemExit(main())
