"""Реальный YAML parser проверяет workflow без Actions, Node, приложений и сети."""

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
INSTALL_COMMAND = (
    "python -m pip install --require-hashes --only-binary=:all: -r locks/tooling.txt"
)


class WorkflowYamlTests(unittest.TestCase):
    def test_every_workflow_parses_and_run_commands_remain_strings(self) -> None:
        paths = sorted((ROOT / ".github/workflows").glob("*.yml"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(workflow=path.name):
                # SafeLoader returns untyped values; narrow them at each boundary.
                workflow: object = yaml.safe_load(path.read_text(encoding="utf-8"))
                if not isinstance(workflow, dict):
                    self.fail("Workflow must be a mapping")
                jobs: object = workflow.get("jobs")
                if not isinstance(jobs, dict) or not jobs:
                    self.fail("Workflow must declare jobs")
                for job_name, job in jobs.items():
                    if not isinstance(job, dict):
                        self.fail(f"Job must be a mapping: {job_name}")
                    steps: object = job.get("steps")
                    if not isinstance(steps, list):
                        self.fail(f"Job must declare steps: {job_name}")
                    for step in steps:
                        if not isinstance(step, dict):
                            self.fail(f"Step must be a mapping: {job_name}")
                        if "run" in step:
                            self.assertIsInstance(step["run"], str)

    def test_unquoted_colon_space_is_rejected_by_the_real_parser(self) -> None:
        with self.assertRaises(yaml.YAMLError):
            yaml.safe_load("run: " + INSTALL_COMMAND + "\n")

    def test_block_scalar_preserves_the_exact_install_command(self) -> None:
        parsed: object = yaml.safe_load("run: >-\n  " + INSTALL_COMMAND + "\n")
        self.assertEqual(parsed, {"run": INSTALL_COMMAND})

    def test_unsafe_python_tags_are_rejected_without_construction(self) -> None:
        with self.assertRaises(yaml.YAMLError):
            yaml.safe_load("!!python/object:builtins.object {}")


if __name__ == "__main__":
    unittest.main()
