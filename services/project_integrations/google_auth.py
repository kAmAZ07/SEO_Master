from __future__ import annotations

from typing import Any, Dict, Sequence

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials

from services.project_integrations.google_settings import (
    GOOGLE_TOKEN_ENDPOINT,
    oauth_client_config,
    validate_google_token_settings,
)


def build_google_credentials(
    credentials_payload: Dict[str, Any],
    token_payload: Dict[str, Any] | None = None,
    *,
    scopes: Sequence[str],
) -> Credentials | service_account.Credentials:
    validate_google_token_settings(credentials_payload, token_payload)
    credential_type = str(credentials_payload.get("type") or "").strip().lower()
    if credential_type == "service_account":
        return service_account.Credentials.from_service_account_info(
            {**credentials_payload, "token_uri": GOOGLE_TOKEN_ENDPOINT}, scopes=list(scopes),
        )

    if credential_type == "authorized_user":
        return Credentials.from_authorized_user_info(credentials_payload, scopes=list(scopes))

    oauth_client = oauth_client_config(credentials_payload)

    if token_payload is None:
        raise ValueError("google_oauth_token_missing")

    client_id = oauth_client.get("client_id")
    client_secret = oauth_client.get("client_secret")
    if not client_id or not client_secret:
        raise ValueError("google_oauth_client_invalid")

    return Credentials(
        token=token_payload.get("token"),
        refresh_token=token_payload.get("refresh_token"),
        token_uri=GOOGLE_TOKEN_ENDPOINT,
        client_id=client_id,
        client_secret=client_secret,
        scopes=list(scopes),
    )
