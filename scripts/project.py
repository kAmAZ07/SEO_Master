"""Единые локальные команды; запуск стека закрыт до миграционного gate AR-3."""

import argparse
import base64
import json
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from typing import Literal


ROOT = Path(__file__).resolve().parents[1]
Mode = Literal["dev", "prod"]
ComposeAction = Literal["config", "build", "down"]
BACKEND_PROFILES = ("management", "client", "api", "audit", "semantic", "reporting", "tilda")
SECRET_NAMES = (
    "POSTGRES_PASSWORD", "RUNTIME_DATABASE_PASSWORD", "REDIS_PASSWORD", "RABBITMQ_PASSWORD",
    "JWT_SECRET_KEY", "INTERNAL_API_KEY", "MANAGEMENT_EXECUTOR_API_KEY", "TILDA_INTERNAL_API_KEY",
    "TILDA_WEBHOOK_SECRET", "MASTER_ENCRYPTION_KEY",
)
OPTIONAL_CONFIGURATION_NAMES = frozenset(("WORDPRESS_V2_TARGETS_JSON", "SEO_MASTER_HMAC_SECRET", "SEO_MASTER_RECEIPT_SECRET", "DOMAIN_VERIFICATION_FRESH_DAYS", "VERIFICATION_DEADLINE_SECONDS"))
CONFIGURATION_NAMES = frozenset(("COMPOSE_PROJECT_NAME", *SECRET_NAMES))
FRONTEND_ROOT_FILES = frozenset((
    "package.json", "package-lock.json", "index.html", "vite.config.ts",
    "vitest.config.ts", "tsconfig.json", "tsconfig.node.json",
    "tailwind.config.js", "postcss.config.js", ".eslintrc.cjs", "eslint.config.js",
))


@dataclass(frozen=True)
class Configuration:
    environment_file: Path
    project_name: str


def build_environment() -> dict[str, str]:
    allowed = frozenset((
        "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
        "TMPDIR", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
        "PROGRAMDATA", "ALLUSERSPROFILE", "PROGRAMFILES", "PROGRAMFILES(X86)",
        "PROGRAMW6432", "SYSTEMDRIVE",
    ))
    environment = {
        name: value for name, value in os.environ.items() if name.upper() in allowed
    }
    environment.update({
        "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1",
        "CI": "true", "NPM_CONFIG_AUDIT": "false", "NPM_CONFIG_FUND": "false",
        "COMPOSE_DISABLE_ENV_FILE": "1",
    })
    return environment


def validate_mode(value: str) -> Mode:
    if value == "dev":
        return "dev"
    if value == "prod":
        return "prod"
    raise ValueError("Неизвестный профиль")


def validate_project_name(value: str, mode: Mode) -> str:
    if re.fullmatch(rf"seo-local-{mode}-[a-f0-9]{{24}}", value) is None:
        raise ValueError("Недопустимое имя локального Compose project")
    return value


def validate_owned_path(root: Path, candidate: Path) -> None:
    root = root.resolve(strict=True)
    if not candidate.resolve().is_relative_to(root):
        raise ValueError("Путь выходит за пределы разрешённого каталога")
    for part in (candidate, *candidate.parents):
        if not part.is_relative_to(root) or not part.exists():
            continue
        attributes = getattr(part.lstat(), "st_file_attributes", 0)
        if part.is_symlink() or (isinstance(attributes, int) and attributes & 0x400):
            raise ValueError("Ссылки и reparse points не разрешены")
    if candidate.is_file() and candidate.stat().st_nlink != 1:
        raise ValueError("Жёсткие ссылки не разрешены")


def run_windows_acl(path: Path, action: Literal["restrict", "check"]) -> None:
    windows = os.environ.get("SYSTEMROOT")
    if windows is None:
        raise ValueError("Отсутствует системный каталог Windows")
    executable = Path(windows) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(
        [str(executable), "-NoProfile", "-NonInteractive", "-File",
         str(ROOT / "scripts/private_acl.ps1"), "-Action", action, "-Path", str(path)],
        env=build_environment(), capture_output=True, check=False, timeout=15,
    )
    if result.returncode:
        raise ValueError("Проверка закрытого ACL не пройдена")


def restrict_permissions(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o700 if path.is_dir() else 0o600)
        return
    run_windows_acl(path, "restrict")


def validate_private_permissions(path: Path) -> None:
    if sys.platform == "win32":
        run_windows_acl(path, "check")
    elif path.stat().st_mode & 0o077 or path.stat().st_uid != os.getuid():
        raise ValueError("Локальная конфигурация доступна не только владельцу")


def bootstrap(root: Path, mode: Mode) -> Path:
    if mode != "dev":
        raise ValueError("Bootstrap генерирует только локальную dev-конфигурацию")
    root = root.resolve(strict=True)
    directory = root / ".seo-local" / mode
    environment_file = directory / "environment.env"
    validate_owned_path(root, directory)
    validate_owned_path(root, environment_file)
    if environment_file.exists():
        load_configuration(root, mode, _legacy_executor=True)
        previous = environment_file.read_text(encoding="utf-8")
        if not any(line.startswith("MANAGEMENT_EXECUTOR_API_KEY=") for line in previous.splitlines()):
            # Only extend the validated private legacy config; never rotate its existing secrets.
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=directory,
                                             prefix="executor-upgrade-", delete=False) as stream:
                replacement = Path(stream.name)
                restrict_permissions(replacement)
                stream.write(previous.rstrip("\r\n") + "\nMANAGEMENT_EXECUTOR_API_KEY=" + secrets.token_urlsafe(32) + "\n")
            try:
                os.replace(replacement, environment_file)
            finally:
                if replacement.exists():
                    replacement.unlink()
        return load_configuration(root, mode).environment_file
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    restrict_permissions(directory)
    values = {name: secrets.token_urlsafe(32) for name in SECRET_NAMES}
    values["MASTER_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(
        secrets.token_bytes(32),
    ).decode("ascii")
    values["COMPOSE_PROJECT_NAME"] = f"seo-local-{mode}-{secrets.token_hex(12)}"
    descriptor = os.open(environment_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        restrict_permissions(environment_file)
        stream.write("".join(f"{name}={value}\n" for name, value in values.items()))
    return load_configuration(root, mode).environment_file


def load_configuration(root: Path, mode: Mode, *, _legacy_executor: bool = False) -> Configuration:
    environment_file = root.resolve(strict=True) / ".seo-local" / mode / "environment.env"
    validate_owned_path(root, environment_file)
    validate_private_permissions(environment_file.parent)
    validate_private_permissions(environment_file)
    if environment_file.stat().st_size > 8192:
        raise ValueError("Локальная конфигурация слишком велика")
    values: dict[str, str] = {}
    for line in environment_file.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if not separator or name not in CONFIGURATION_NAMES | OPTIONAL_CONFIGURATION_NAMES or name in values:
            raise ValueError("Недопустимая структура локальной конфигурации")
        values[name] = value
    required = CONFIGURATION_NAMES - {"MANAGEMENT_EXECUTOR_API_KEY"} if _legacy_executor else CONFIGURATION_NAMES
    if not required <= values.keys():
        raise ValueError("Отсутствует обязательная локальная настройка")
    for name in SECRET_NAMES:
        if name == "MANAGEMENT_EXECUTOR_API_KEY" and _legacy_executor and name not in values:
            continue
        if re.fullmatch(r"[A-Za-z0-9_-]{43}=?", values[name]) is None:
            raise ValueError("Недопустимый формат локального ключа")
    for name in ("SEO_MASTER_HMAC_SECRET", "SEO_MASTER_RECEIPT_SECRET"):
        if name in values and (re.fullmatch(r"[0-9a-f]{64,128}", values[name]) is None or len(set(values[name])) < 8):
            raise ValueError("Недопустимый формат CMS-ключа")
    if "WORDPRESS_V2_TARGETS_JSON" in values:
        try:
            bindings = json.loads(values["WORDPRESS_V2_TARGETS_JSON"])
        except ValueError:
            raise ValueError("Недопустимый JSON привязок WordPress") from None
        if not isinstance(bindings, dict):
            raise ValueError("Ожидалась карта привязок WordPress")
    if "DOMAIN_VERIFICATION_FRESH_DAYS" in values and re.fullmatch(r"(?:[1-9]|[1-9][0-9]|[12][0-9]{2}|3[0-5][0-9]|36[0-5])", values["DOMAIN_VERIFICATION_FRESH_DAYS"]) is None:
        raise ValueError("Недопустимый срок подтверждения домена")
    if "VERIFICATION_DEADLINE_SECONDS" in values:
        value = values["VERIFICATION_DEADLINE_SECONDS"]
        if re.fullmatch(r"[0-9]{2,4}", value) is None or not 30 <= int(value) <= 3600:
            raise ValueError("Недопустимый срок публичной проверки")
    return Configuration(
        environment_file, validate_project_name(values["COMPOSE_PROJECT_NAME"], mode),
    )


def build_compose_command(
    root: Path, environment_file: Path, project_name: str,
    mode: Mode, action: str,
) -> list[str]:
    validate_project_name(project_name, mode)
    if action not in ("config", "build", "down"):
        raise ValueError("Compose-действие не разрешено")
    command = [
        "docker", "compose", "--project-directory", str(root),
        "--env-file", str(environment_file), "--project-name", project_name,
        "--file", str(root / "docker-compose.yml"),
        "--file", str(root / f"docker-compose.{mode}.yml"),
    ]
    return [*command, action, "--quiet"] if action == "config" else [*command, action]


def run_checked(
    command: list[str], directory: Path, *, quiet: bool = False,
    environment: dict[str, str] | None = None,
) -> int:
    result = subprocess.run(
        command, cwd=directory, env=build_environment() if environment is None else environment,
        check=False,
        capture_output=quiet,
    )
    if result.returncode and quiet:
        # Compose diagnostics may interpolate secrets; never relay raw config/error output.
        print("Команда Docker завершилась ошибкой; вывод с конфигурацией скрыт.", file=sys.stderr)
    return result.returncode


def require_local_docker() -> None:
    result = subprocess.run(
        ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
        env=build_environment(), capture_output=True, text=True, check=True, timeout=10,
    )
    endpoint = result.stdout.strip()
    is_local_pipe = re.fullmatch(r"npipe:////\./pipe/[A-Za-z0-9_.-]{1,128}", endpoint) is not None
    is_local_socket = False
    if endpoint.startswith("unix:///"):
        path = endpoint[len("unix://"):]
        is_local_socket = (
            re.fullmatch(r"/[A-Za-z0-9_./-]+", path) is not None
            and ".." not in PurePosixPath(path).parts and "//" not in path
        )
    if not is_local_pipe and not is_local_socket:
        raise ValueError("Разрешён только локальный Docker context")
    information = subprocess.run(
        ["docker", "info", "--format", "{{.OSType}}"],
        env=build_environment(), capture_output=True, text=True, check=True, timeout=20,
    )
    if information.stdout.strip() != "linux":
        raise ValueError("Требуется локальный Linux Docker daemon")


def copy_frontend(root: Path, destination: Path) -> None:
    listing = subprocess.run(
        ["git", "--no-pager", "ls-files", "-z", "--", "frontend"],
        cwd=root, env=build_environment(), capture_output=True, check=True, timeout=10,
    )
    for filename in listing.stdout.decode("utf-8").rstrip("\0").split("\0"):
        relative = Path(filename)
        if relative.is_absolute() or ".." in relative.parts or relative.parts[0] != "frontend":
            raise ValueError("Недопустимый путь frontend snapshot")
        local = Path(*relative.parts[1:])
        is_source = (
            len(local.parts) > 1 and local.parts[0] in ("src", "public")
            and local.suffix in (".ts", ".tsx", ".js", ".jsx", ".css", ".svg", ".png", ".jpg", ".ico")
        )
        if not is_source and local.as_posix() not in FRONTEND_ROOT_FILES:
            continue
        source = root / relative
        validate_owned_path(root, source)
        target = destination / local
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    for name in ("package.json", "package-lock.json", "tsconfig.json", "index.html"):
        if not (destination / name).is_file():
            raise ValueError("Нужный frontend-исходник не отслеживается Git")


def prepare_frontend_environment(workspace: Path) -> dict[str, str]:
    environment = build_environment()
    for name, relative in (
        ("HOME", "home"), ("USERPROFILE", "home"), ("APPDATA", "home/roaming"),
        ("LOCALAPPDATA", "home/local"), ("NPM_CONFIG_CACHE", "npm-cache"),
        ("TEMP", "temp"), ("TMP", "temp"), ("TMPDIR", "temp"),
    ):
        path = workspace / relative
        path.mkdir(parents=True, exist_ok=True)
        environment[name] = str(path)
    for name, relative in (
        ("NPM_CONFIG_USERCONFIG", "npm-user.conf"),
        ("NPM_CONFIG_GLOBALCONFIG", "npm-global.conf"),
    ):
        path = workspace / relative
        path.write_text("", encoding="utf-8")
        environment[name] = str(path)
    environment["NPM_CONFIG_REGISTRY"] = "https://registry.npmjs.org/"
    return environment


def check_frontend(root: Path, action: Literal["test", "lint"]) -> int:
    environment = build_environment()
    node = shutil.which("node", path=environment.get("PATH", environment.get("Path")))
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm",
                       path=environment.get("PATH", environment.get("Path")))
    if node is None or npm is None:
        raise ValueError("Нужен Node.js 24.14.1 с npm")
    node = str(Path(node).resolve(strict=True))
    npm = str(Path(npm).resolve(strict=True))
    version = subprocess.run(
        [node, "--version"], env=environment, capture_output=True,
        text=True, check=True, timeout=10,
    )
    if version.stdout.strip() != "v24.14.1":
        raise ValueError("Требуется ровно Node.js 24.14.1, как в CI и build image")
    with tempfile.TemporaryDirectory(prefix="seo-frontend-") as directory:
        workspace = Path(directory)
        snapshot = workspace / "source"
        environment = prepare_frontend_environment(workspace)
        copy_frontend(root, snapshot)
        if run_checked([npm, "ci", "--no-audit", "--no-fund"], snapshot, environment=environment):
            return 1
        commands = [
            [node, "node_modules/typescript/bin/tsc", "--noEmit", "-p", "tsconfig.json"],
            [npm, "run", "lint"],
        ] if action == "lint" else [[npm, "test"], [npm, "run", "build"]]
        results = [
            run_checked(command, snapshot, environment=environment) for command in commands
        ]
        return int(any(results))


def check_quality(root: Path, action: Literal["test", "lint"], php: str) -> int:
    if sys.version_info[:2] != (3, 11):
        raise ValueError("Платформенные команды требуют Python 3.11")
    if action == "lint":
        commands = [
            [sys.executable, "-m", "mypy", "--strict", "--explicit-package-bases",
             "scripts", "tests/platform", "tests/wordpress/run.py"],
            [sys.executable, "-m", "ruff", "check", "scripts", "tests/platform",
             "tests/wordpress/run.py"],
        ]
    else:
        commands = [
            [sys.executable, "-m", "unittest", "discover", "-s", "tests/platform", "-v"],
            *[
                [sys.executable, "-B", str(root / "scripts/backend.py"), "check", name]
                for name in BACKEND_PROFILES
            ],
        ]
    results = [run_checked(command, root) for command in commands]
    results.append(check_frontend(root, action))
    runner = root / "tests/wordpress/run.py"
    if not runner.is_file():
        print("WP_GATE_MISSING: отсутствует обязательный PHP/WordPress runner.", file=sys.stderr)
        results.append(1)
    else:
        environment = build_environment()
        executable = shutil.which(php, path=environment.get("PATH", environment.get("Path")))
        if executable is None:
            print("PHP_TOOL_MISSING: укажите PHP executable через --php.", file=sys.stderr)
            results.append(1)
        else:
            executable = str(Path(executable).resolve(strict=True))
            command = [sys.executable, "-B", str(runner), "--php", executable]
            if action == "lint":
                command.append("--lint-only")
            results.append(run_checked(command, root))
    return int(any(results))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=(
        "bootstrap", "config", "up", "test", "e2e", "lint", "build", "down",
    ))
    parser.add_argument("--mode", choices=("dev", "prod"), default="dev")
    parser.add_argument("--php", default="php")
    arguments = parser.parse_args(argv)
    if arguments.action in ("up", "e2e"):
        print(
            "MIGRATION_GATE_BLOCKED: startup DDL ещё не заменён проверенными миграциями "
            "AR-3; запуск и E2E закрыты. Схема и данные не изменены.",
            file=sys.stderr,
        )
        return 2
    try:
        mode = validate_mode(arguments.mode)
        if arguments.action == "bootstrap":
            bootstrap(ROOT, mode)
            print("Локальная dev-конфигурация подготовлена; секреты не выводятся.")
            return 0
        if arguments.action in ("test", "lint"):
            return check_quality(ROOT, arguments.action, arguments.php)
        configuration = load_configuration(ROOT, mode)
        if arguments.action == "config":
            return run_checked(build_compose_command(
                ROOT, configuration.environment_file, configuration.project_name, mode, "config",
            ), ROOT, quiet=True)
        require_local_docker()
        action: ComposeAction = "build" if arguments.action == "build" else "down"
        return run_checked(build_compose_command(
            ROOT, configuration.environment_file, configuration.project_name, mode, action,
        ), ROOT, quiet=True)
    except (ValueError, OSError, subprocess.SubprocessError):
        # Never print exception values: tools and parsers can include secret-bearing input.
        print(
            "PROJECT_PREFLIGHT_FAILED: проверьте инструменты, локальную конфигурацию "
            "и docs/PLATFORM.md; секретные значения и вывод Docker скрыты.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
