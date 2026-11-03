"""Статические регрессии build context (AR-1/AR-2, AC-12).

Эти проверки не подменяют сборку Docker и проверку содержимого образа.
Они не импортируют приложения, не читают .env и не подключаются к сервисам.
"""

import subprocess
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_EXCLUSIONS = (
    "**/.env",
    "**/.env.*",
    "**/.git",
    "**/.venv",
    "**/venv",
    "**/node_modules",
    "**/__pycache__",
    "**/*.py[cod]",
    "**/logs",
    "**/*.log",
    "**/reports",
    "**/exports",
    "**/*.key",
    "**/*.pem",
    "**/secrets",
    "**/credentials",
    "**/*credentials*.json",
    "**/*service-account*.json",
    "**/*service_account*.json",
    "**/*.db",
    "**/*.sqlite*",
    "**/db_data",
    "**/pgdata",
    "**/postgres_data",
    "**/redis_data",
    "**/rabbitmq_data",
    "**/dump.rdb",
    "**/.pytest_cache",
    "**/.coverage*",
    "**/htmlcov",
    "**/crawl_data",
    "**/crawled_pages",
    "**/llm_cache",
)


class DockerContextTests(unittest.TestCase):
    def test_root_policy_exists_and_excludes_sensitive_material(self) -> None:
        ignore_file = REPOSITORY_ROOT / ".dockerignore"
        self.assertTrue(ignore_file.is_file(), "Отсутствует корневой .dockerignore")
        rules = [
            line.strip()
            for line in ignore_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        for exclusion in REQUIRED_EXCLUSIONS:
            with self.subTest(exclusion=exclusion):
                self.assertIn(exclusion, rules)
        self.assertFalse(
            any(rule.startswith("!") for rule in rules),
            "Allow-исключения требуют отдельного анализа: они могут вернуть секреты в context",
        )

    def test_root_policy_is_not_hidden_from_version_control(self) -> None:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", ".dockerignore"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(result.returncode, 1, ".dockerignore не должен быть в .gitignore")

    def test_dockerfile_specific_policies_do_not_override_root_policy(self) -> None:
        dockerfiles = [
            REPOSITORY_ROOT / "Dockerfile",
            *sorted((REPOSITORY_ROOT / "services").glob("*/Dockerfile")),
            *sorted((REPOSITORY_ROOT / "adapters").glob("*/Dockerfile")),
            *sorted((REPOSITORY_ROOT / "tests" / "e2e").glob("**/Dockerfile")),
        ]
        for dockerfile in dockerfiles:
            with self.subTest(dockerfile=str(dockerfile.relative_to(REPOSITORY_ROOT))):
                override = dockerfile.with_name(dockerfile.name + ".dockerignore")
                self.assertFalse(
                    override.exists(),
                    "Dockerfile-specific ignore заменяет корневую защиту; нужна отдельная проверка",
                )

    def test_management_image_copies_only_declared_application_sources(self) -> None:
        dockerfile = REPOSITORY_ROOT / "services" / "management_service" / "Dockerfile"
        copy_instructions = [
            line.strip()
            for line in dockerfile.read_text(encoding="utf-8").splitlines()
            if line.lstrip().upper().startswith(("COPY ", "ADD "))
        ]
        allowed_sources = {
            "services/management_service/requirements.txt",
            "config/",
            "database/",
            "shared/",
            "services/management_service/",
            "services/project_integrations/",
            "services/event_resilience.py",
        }
        application_sources: set[str] = set()
        for instruction in copy_instructions:
            if instruction.startswith("COPY --from=dependencies "):
                continue
            parts = instruction.split()
            self.assertEqual(parts[0], "COPY", "ADD и неявная распаковка не разрешены")
            self.assertEqual(len(parts), 3, "Ожидается явный COPY source destination")
            self.assertIn(parts[1], allowed_sources, f"Неограниченный COPY: {instruction}")
            application_sources.add(parts[1])
        self.assertEqual(application_sources, allowed_sources)

    def test_wordpress_release_archive_is_not_excluded_by_blanket_dist_rule(self) -> None:
        """API Gateway пока копирует существующий ZIP; запрет всех dist сломает сборку."""
        ignore_file = REPOSITORY_ROOT / ".dockerignore"
        self.assertTrue(ignore_file.is_file())
        rules = ignore_file.read_text(encoding="utf-8").splitlines()
        self.assertNotIn("**/dist", rules)
        self.assertNotIn("*.zip", rules)
        self.assertNotIn("**/*.zip", rules)
