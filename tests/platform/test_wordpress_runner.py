"""Регрессии discovery: только source gate, без PHP/WordPress/БД и проверки ZIP."""

from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.wordpress import run as wordpress_runner


REQUIRED_SOURCES = (
    "adapters/wordpress-plugin/seo-master-plugin.php",
    "adapters/wordpress-plugin/uninstall.php",
    "adapters/wordpress-plugin/admin/settings.php",
    "adapters/wordpress-plugin/includes/api-handler.php",
    "adapters/wordpress-plugin/includes/compatibility-checker.php",
    "adapters/wordpress-plugin/includes/hmac-validator.php",
    "adapters/wordpress-plugin/includes/meta-filter.php",
    "adapters/wordpress-plugin/includes/signature-verifier.php",
    "adapters/wordpress-plugin/includes/v2-protocol.php",
    "adapters/wordpress-plugin/includes/v2-service.php",
    "adapters/wordpress-plugin/includes/v2-sql.php",
    "adapters/wordpress-plugin/includes/v2-store.php",
    "tests/wordpress/legacy-regression.php",
    "tests/wordpress/store-contract.php",
    "tests/wordpress/v2-contract.php",
    "tests/wordpress/vectors.php",
    "tests/wordpress/wp-adapter.php",
)
SOURCE_DIRECTORIES = (
    "adapters/wordpress-plugin",
    "adapters/wordpress-plugin/admin",
    "adapters/wordpress-plugin/includes",
    "tests/wordpress",
)


class WordPressRunnerTests(unittest.TestCase):
    def create_fixture(self, root: Path) -> None:
        for name in REQUIRED_SOURCES:
            source = root / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"<?php\n")
        (root / "tests/wordpress/run.py").write_text(
            "# Fixture only; actual runner is imported from the repository.\n",
            encoding="utf-8",
        )
        (root / "php-cli-fixture").touch()

    def lint_fixture(self, root: Path) -> list[str]:
        with (
            patch.object(wordpress_runner, "__file__", str(root / "tests/wordpress/run.py")),
            patch.object(sys, "argv", [
                "run.py", "--php", str(root / "php-cli-fixture"), "--lint-only",
            ]),
            patch.object(wordpress_runner, "run_php", return_value="") as execute_php,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(wordpress_runner.main(), 0)
        sources: list[str] = []
        for call in execute_php.call_args_list:
            self.assertEqual(call.args[3][0], "-l")
            sources.append(Path(call.args[3][1]).relative_to(root).as_posix())
        return sources

    def test_every_required_source_is_linted_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.create_fixture(root)
            self.assertCountEqual(self.lint_fixture(root), REQUIRED_SOURCES)

    def test_additional_canonical_php_is_not_silently_omitted(self) -> None:
        additions = (
            "adapters/wordpress-plugin/additional.php",
            "adapters/wordpress-plugin/admin/additional.php",
            "adapters/wordpress-plugin/includes/additional.php",
            "adapters/wordpress-plugin/includes/nested/additional.php",
            "tests/wordpress/additional.php",
            "tests/wordpress/nested/additional.php",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.create_fixture(root)
            for name in additions:
                source = root / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(b"<?php\n")
            self.assertCountEqual(self.lint_fixture(root), (*REQUIRED_SOURCES, *additions))

    def test_packaging_vendor_and_generated_php_are_not_sources_or_executed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.create_fixture(root)
            for parent in SOURCE_DIRECTORIES:
                for artifact in ("dist", "generated", "vendor"):
                    source = root / parent / artifact / "nested/not-canonical.php"
                    source.parent.mkdir(parents=True, exist_ok=True)
                    source.write_bytes(b"\xef\xbb\xbfnot a canonical PHP source\n")
            self.assertCountEqual(self.lint_fixture(root), REQUIRED_SOURCES)

    def test_missing_any_required_source_fails_even_when_collection_is_large(self) -> None:
        for missing in REQUIRED_SOURCES:
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.create_fixture(root)
                (root / missing).unlink()
                with self.assertRaisesRegex(RuntimeError, "required PHP source"):
                    self.lint_fixture(root)

    def test_directory_cannot_replace_required_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.create_fixture(root)
            source = root / "adapters/wordpress-plugin/includes/v2-store.php"
            source.unlink()
            source.mkdir()
            with self.assertRaisesRegex(RuntimeError, "required PHP source"):
                self.lint_fixture(root)

    def test_bom_in_any_required_source_still_fails(self) -> None:
        for name in REQUIRED_SOURCES:
            with self.subTest(source=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.create_fixture(root)
                (root / name).write_bytes(b"\xef\xbb\xbf<?php\n")
                with self.assertRaisesRegex(RuntimeError, "bytes before opening tag"):
                    self.lint_fixture(root)


if __name__ == "__main__":
    unittest.main()
