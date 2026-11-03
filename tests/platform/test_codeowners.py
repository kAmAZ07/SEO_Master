"""Проверки назначения владельцев для поддерживаемого подмножества CODEOWNERS."""

import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OwnerRule = tuple[str, str]


def read_owner_rules() -> list[OwnerRule]:
    """Поддерживаются только *, абсолютные пути и абсолютные каталоги."""
    rules: list[OwnerRule] = []
    for line in (REPOSITORY_ROOT / ".github" / "CODEOWNERS").read_text(
        encoding="utf-8",
    ).splitlines():
        stripped_line = line.strip()
        if not stripped_line or stripped_line.startswith("#"):
            continue
        fields = stripped_line.split()
        if len(fields) != 2:
            raise ValueError("У каждого правила должен быть ровно один владелец")
        pattern, owner = fields
        if pattern != "*" and (
            not pattern.startswith("/")
            or any(character in pattern for character in "*?![]\\")
        ):
            raise ValueError("Проверка поддерживает только явные пути и каталоги")
        if owner not in {"@kAmAZ07", "@sshpv"}:
            raise ValueError("Владелец не подтверждён участниками")
        rules.append((pattern, owner))
    return rules


def resolve_owner(path: str, rules: list[OwnerRule]) -> str | None:
    """Для простых правил последнее совпадение важнее предыдущего."""
    matched_owner: str | None = None
    for pattern, owner in rules:
        relative_pattern = pattern.removeprefix("/")
        if (
            pattern == "*"
            or path == relative_pattern
            or (pattern.endswith("/") and path.startswith(relative_pattern))
        ):
            matched_owner = owner
    return matched_owner


class CodeownersTests(unittest.TestCase):
    def test_alexandra_owns_upstream_and_hot_files(self) -> None:
        rules = read_owner_rules()
        for path in (
            "services/api_gateway/auth.py",
            "services/api_gateway/routes/protected/projects_routes.py",
            "services/api_gateway/routes/protected_routes.py",
            "services/audit_service/main.py",
            "services/semantic_service/llm/llm_client.py",
            "services/project_integrations/models.py",
            "services/reporting_service/main.py",
            "shared/netpolicy.py",
            "shared/projects/scope.py",
            "shared/projects/authorization_state.py",
            "services/event_resilience.py",
            "database/models.py",
            "database/migrations/versions/20260414_001_crawl_results_partitioning.py",
            "frontend/src/App.tsx",
            "frontend/src/store/store.ts",
            "frontend/src/pages/Audit.tsx",
            "frontend/src/pages/Projects.tsx",
            "frontend/src/components/projects/ProjectAuditTab.tsx",
            "contracts/netpolicy.md",
            "contracts/public_audit.md",
            "contracts/projects.md",
            "tests/unit/test_domain_verification.py",
            "tests/unit/test_domain_verification_network.py",
            "tests/unit/test_project_access.py",
            "tests/unit/test_project_core.py",
            "tests/unit/test_project_scope.py",
            "tests/unit/test_projects_gateway.py",
            "tests/unit/test_public_audit_claim_token.py",
            "tests/integration/test_migrations_postgres.py",
            "README.md",
        ):
            with self.subTest(path=path):
                self.assertEqual(resolve_owner(path, rules), "@sshpv")

    def test_arseniy_owns_downstream_platform_and_hitl_exceptions(self) -> None:
        rules = read_owner_rules()
        for path in (
            ".github/CODEOWNERS",
            ".github/workflows/ci.yml",
            ".dockerignore",
            ".env.example",
            "Dockerfile",
            "docker-compose.yml",
            "docker-compose.dev.yml",
            "docker-compose.prod.yml",
            "Makefile",
            "scripts/seed_demo_data.py",
            "config/database_config.py",
            "docs/DECISIONS.md",
            "database/migrations/env.py",
            "services/management_service/prioritizer.py",
            "services/client_api_gateway/main.py",
            "adapters/wordpress-plugin/includes/api-handler.php",
            "adapters/tilda-adapter/main.py",
            "services/api_gateway/routes/protected/hitl_routes.py",
            "services/api_gateway/events/hitl_approved.py",
            "services/audit_service/Dockerfile",
            "services/api_gateway/Dockerfile",
            "services/semantic_service/Dockerfile",
            "services/reporting_service/Dockerfile",
            "frontend/package-lock.json",
            "frontend/src/pages/HITL.tsx",
            "frontend/src/pages/HITL.test.tsx",
            "frontend/src/store/slices/hitlSlice.ts",
            "frontend/src/api/hitlAPI.ts",
            "frontend/src/api/hitlAPI.test.ts",
            "frontend/src/types/hitl.ts",
            "frontend/src/components/hitl/DiffViewer.tsx",
            "tests/e2e/test_extended_stack.py",
            "tests/platform/test_codeowners.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(resolve_owner(path, rules), "@kAmAZ07")

    def test_directory_rule_does_not_capture_similarly_named_sibling(self) -> None:
        rules = read_owner_rules()
        self.assertEqual(resolve_owner("frontend-other/example.ts", rules), "@kAmAZ07")
        self.assertEqual(
            resolve_owner("services/api_gateway_extra/example.py", rules), "@kAmAZ07",
        )

    def test_last_matching_rule_wins_not_the_most_specific(self) -> None:
        rules = [
            ("*", "@kAmAZ07"),
            ("/frontend/src/pages/HITL.tsx", "@kAmAZ07"),
            ("/frontend/", "@sshpv"),
        ]
        self.assertEqual(resolve_owner("frontend/src/pages/HITL.tsx", rules), "@sshpv")
