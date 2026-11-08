"""Native ORM contract для services.management_service с его собственным lock."""

from tests.platform.test_model_imports import run_model_probe


def test_management_models_import_without_runtime_dependencies() -> None:
    result = run_model_probe("management")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MODEL_METADATA_OK management 6 2.0.55" in result.stdout
