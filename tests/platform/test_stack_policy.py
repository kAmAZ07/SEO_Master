"""Регрессии исходной конфигурации; не запускают Docker или приложения."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DOCKERFILES = {
    "services/api_gateway/Dockerfile": "api",
    "services/audit_service/Dockerfile": "audit",
    "services/semantic_service/Dockerfile": "semantic",
    "services/reporting_service/Dockerfile": "reporting",
    "services/management_service/Dockerfile": "management",
    "services/client_api_gateway/Dockerfile": "client",
    "adapters/tilda-adapter/Dockerfile": "tilda",
}


class StackPolicyTests(unittest.TestCase):
    def test_runtime_images_are_pinned_hashed_and_non_root(self) -> None:
        for filename, profile in RUNTIME_DOCKERFILES.items():
            with self.subTest(filename=filename):
                content = (ROOT / filename).read_text(encoding="utf-8")
                first_from = next(
                    line for line in content.splitlines() if line.startswith("FROM ")
                )
                self.assertRegex(first_from, r"@sha256:[0-9a-f]{64}(?:\s|$)")
                self.assertIn(f"COPY locks/{profile}.txt ", content)
                self.assertIn("--require-hashes --only-binary=:all:", content)
                self.assertRegex(content, r"(?m)^USER (?:10001:10001|appuser|pwuser)$")
                self.assertNotIn("chmod 777", content)
                self.assertNotIn("build-essential", content)
                self.assertNotIn("COPY . .", content)

    def test_compose_profiles_cannot_mount_checkout_or_seed_schema(self) -> None:
        for filename in (
            "docker-compose.yml", "docker-compose.dev.yml", "docker-compose.prod.yml",
        ):
            with self.subTest(filename=filename):
                content = (ROOT / filename).read_text(encoding="utf-8")
                self.assertNotIn("container_name:", content)
                self.assertNotRegex(content, r"(?m)^\s*name:")
                self.assertNotIn("env_file:", content)
                self.assertNotIn(".env.production", content)
                self.assertNotIn("docker-entrypoint-initdb.d", content)
                self.assertNotIn("init_schema.sql", content)
                self.assertNotRegex(content, r"(?m)^\s*-\s*\./")
                self.assertNotRegex(content, r'(?m)^\s*-\s*"?[0-9]+:[0-9]+')
                self.assertNotIn(":-change-me", content)
                self.assertNotIn(":-seo_pass", content)

    def test_canonical_topology_preserves_real_services_and_management_queues(self) -> None:
        content = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        for name in (
            "postgres", "redis", "rabbitmq", "api-gateway", "audit-service",
            "semantic-service", "reporting-service", "client-api-gateway",
            "management-web", "management-worker", "management-beat",
            "tilda-adapter", "frontend",
        ):
            with self.subTest(service=name):
                self.assertIn(f"\n  {name}:\n", content)
        self.assertIn("services.management_service.tasks.celery_app:celery_app", content)
        self.assertIn("--queues=management,management_periodic", content)
        for name in ("AUDIT", "SEMANTIC", "REPORTING"):
            for setting in ("DATABASE_URL", "REDIS_URL", "RABBITMQ_URL"):
                self.assertIn(f"{name}_{setting}:", content)
        self.assertIn("CELERY_BROKER_URL:", content)
        self.assertIn("CELERY_RESULT_BACKEND:", content)
        self.assertIn("CORS_ORIGINS:", content)
        self.assertNotIn("CORS_ORIGINS: '*'", content)
        self.assertIn("REDISCLI_AUTH", content)
        self.assertNotIn('"redis-cli", "-a"', content)

    def test_root_dockerfile_is_a_management_compatibility_alias(self) -> None:
        root = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        service = (ROOT / "services/management_service/Dockerfile").read_text(
            encoding="utf-8",
        )
        self.assertEqual(root, service)

    def test_external_images_have_immutable_digests(self) -> None:
        content = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        images = re.findall(r"(?m)^\s+image:\s+(\S+)", content)
        self.assertGreaterEqual(len(images), 3)
        for image in images:
            with self.subTest(image=image):
                self.assertRegex(image, r"^[a-z0-9/_.:-]+@sha256:[0-9a-f]{64}$")

    def test_every_runtime_dsn_uses_only_runtime_database_credential(self) -> None:
        content = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        runtime_lines = [line for line in content.splitlines() if "://seo_runtime:" in line]
        self.assertEqual(len(runtime_lines), 6)
        for line in runtime_lines:
            with self.subTest(setting=line.partition(":")[0].strip()):
                self.assertIn("${RUNTIME_DATABASE_PASSWORD:?bootstrap required}", line)
                self.assertNotIn("${POSTGRES_PASSWORD", line)


if __name__ == "__main__":
    unittest.main()
