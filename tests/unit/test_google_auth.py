from types import SimpleNamespace
from unittest.mock import Mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import pytest

from services.project_integrations.google_auth import build_google_credentials, service_account

SCOPE = ("https://www.googleapis.com/auth/webmasters.readonly",)
OFFICIAL_ENDPOINT = "https://oauth2.googleapis.com/token"


@pytest.mark.parametrize(("credentials", "token"), [
    ({"type": "service_account", "token_uri": "https://fixture.test/token"}, None),
    ({"type": "authorized_user", "token_uri": "https://fixture.test/token"}, None),
    ({"web": {"client_id": "fixture", "client_secret": "fixture", "token_uri": "https://fixture.test/token"}}, {}),
    ({"client_id": "fixture", "client_secret": "fixture"}, {"token_uri": "https://fixture.test/token"}),
])
def test_credentials_never_use_a_configured_untrusted_token_endpoint(credentials, token):
    with pytest.raises(ValueError, match="^google_token_endpoint_unsupported$"):
        build_google_credentials(credentials, token, scopes=SCOPE)


def assert_official_refresh(credentials):
    transport = Mock(return_value=SimpleNamespace(
        status=200, data=b'{"access_token":"fixture","token_type":"Bearer","expires_in":3600}',
    ))
    credentials.refresh(transport)
    assert transport.call_count == 1
    assert transport.call_args.kwargs["url"] == OFFICIAL_ENDPOINT
    assert credentials.scopes == list(SCOPE)


def test_service_account_uses_official_refresh_without_mutating_input():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()).decode("ascii")
    legacy = {"type": "service_account", "token_uri": "https://accounts.google.com/o/oauth2/token",
              "client_email": "fixture@example.test", "private_key": pem}

    assert_official_refresh(build_google_credentials(legacy, scopes=SCOPE))
    assert legacy["token_uri"] == "https://accounts.google.com/o/oauth2/token"


def test_service_account_rejects_a_different_universe(monkeypatch):
    initialize = Mock()
    monkeypatch.setattr(service_account.Credentials, "from_service_account_info", initialize)
    with pytest.raises(ValueError, match="^google_universe_unsupported$"):
        build_google_credentials({"type": "service_account", "universe_domain": "fixture.test"}, scopes=SCOPE)
    initialize.assert_not_called()


@pytest.mark.parametrize("kind", ["web", "authorized_user"])
def test_user_credentials_use_fixed_endpoint_and_requested_readonly_scope(kind):
    client = {"client_id": "fixture", "client_secret": "fixture"}
    token = {"refresh_token": "fixture", "token_uri": OFFICIAL_ENDPOINT}
    credentials = {"type": kind, **client, **token} if kind == "authorized_user" else {"web": client}
    result = build_google_credentials(credentials, token if kind == "web" else None, scopes=SCOPE)
    assert result.token_uri == OFFICIAL_ENDPOINT
    assert_official_refresh(result)
