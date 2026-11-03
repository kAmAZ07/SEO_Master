"""Native ORM contract для services.client_api_gateway с его собственным lock."""

from tests.platform.test_model_imports import run_model_probe


def test_client_models_import_without_runtime_dependencies() -> None:
    result = run_model_probe("client")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MODEL_METADATA_OK client 2 2.0.25" in result.stdout
