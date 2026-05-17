from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from services.project_integrations.credentials_vault import CredentialsVault
from services.project_integrations.integrations_service import (
    IntegrationValidationError,
    IntegrationsService,
)
from services.api_gateway.routes.integrations_routes import WordpressSecretResponse


def test_credentials_vault_round_trip():
    vault = CredentialsVault(Fernet.generate_key().decode("utf-8"))

    encrypted = vault.encrypt({"public_key": "pub-123", "secret_key": "sec-456"})
    decrypted = vault.decrypt(encrypted)

    assert decrypted == {"public_key": "pub-123", "secret_key": "sec-456"}


def test_resolve_tilda_page_id_prefers_explicit_metadata(monkeypatch):
    service = IntegrationsService(vault=CredentialsVault(Fernet.generate_key().decode("utf-8")))
    monkeypatch.setattr(
        service,
        "get_tilda_credentials",
        lambda db, project_id: {"page_mappings": {"https://example.com/page": "mapped-page"}},
    )

    page_id = service.resolve_tilda_page_id(
        db=None,
        project_id="project-1",
        entity_id="https://example.com/page",
        metadata={"page_id": "explicit-page"},
    )

    assert page_id == "explicit-page"


def test_resolve_tilda_page_id_uses_registered_url_mapping(monkeypatch):
    service = IntegrationsService(vault=CredentialsVault(Fernet.generate_key().decode("utf-8")))
    monkeypatch.setattr(
        service,
        "get_tilda_credentials",
        lambda db, project_id: {"page_mappings": {"https://example.com/page": "mapped-page"}},
    )

    page_id = service.resolve_tilda_page_id(
        db=None,
        project_id="project-1",
        entity_id="https://example.com/page/",
        metadata={},
    )

    assert page_id == "mapped-page"


def test_resolve_tilda_page_id_requires_mapping_for_url(monkeypatch):
    service = IntegrationsService(vault=CredentialsVault(Fernet.generate_key().decode("utf-8")))
    monkeypatch.setattr(service, "get_tilda_credentials", lambda db, project_id: {"page_mappings": {}})

    with pytest.raises(IntegrationValidationError):
        service.resolve_tilda_page_id(
            db=None,
            project_id="project-1",
            entity_id="https://example.com/page",
            metadata={},
        )


def test_serialize_wordpress_integration_exposes_only_hint():
    service = IntegrationsService(vault=CredentialsVault(Fernet.generate_key().decode("utf-8")))
    integration = SimpleNamespace(
        platform="wordpress",
        creds_hint="secret...",
        connected_at=None,
        updated_at=None,
        details={"base_url": "https://example.com", "plugin_health": {"status": "ok"}},
    )

    payload = service.serialize_integration(integration)

    assert payload["platform"] == "wordpress"
    assert payload["hint"] == "secret..."
    assert payload["site_url"] == "https://example.com"
    assert "hmac_secret" not in payload


def test_wordpress_hmac_metadata_exposes_safe_fields_only():
    service = IntegrationsService(vault=CredentialsVault(Fernet.generate_key().decode("utf-8")))
    secret = service._generate_secret()
    metadata = service._build_hmac_key_metadata(secret)
    integration = SimpleNamespace(
        platform="wordpress",
        creds_hint="hint...",
        connected_at=None,
        updated_at=None,
        details={
            "base_url": "https://example.com",
            "status": "secret_generated",
            "hmac_key": metadata,
        },
    )

    payload = service.serialize_integration(integration)

    assert len(secret) >= 32
    assert payload["status"] == "secret_generated"
    assert payload["hmac_key_id"].startswith("wp_")
    assert payload["hmac_secret_fingerprint"] == metadata["fingerprint"]
    assert payload["hmac_secret_expires_at"] == metadata["expires_at"]
    assert "generated_secret" not in payload
    assert "hmac_secret" not in payload


def test_wp_config_line_escapes_generated_secret():
    service = IntegrationsService(vault=CredentialsVault(Fernet.generate_key().decode("utf-8")))

    assert service._build_wp_config_line("abc'def") == "define('SEO_MASTER_HMAC_SECRET', 'abc\\'def');"


def test_wordpress_secret_response_accepts_service_snake_case_payload():
    response = WordpressSecretResponse(
        platform="wordpress",
        connected=True,
        status="secret_generated",
        generated_secret="secret-value",
        wp_config_line="define('SEO_MASTER_HMAC_SECRET', 'secret-value');",
    )

    assert response.generated_secret == "secret-value"
    assert response.model_dump(by_alias=True)["generatedSecret"] == "secret-value"
