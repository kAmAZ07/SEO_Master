from typing import Any


GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_ACCEPTED_TOKEN_ENDPOINTS = (GOOGLE_TOKEN_ENDPOINT, "https://accounts.google.com/o/oauth2/token")


class GoogleConfigurationError(ValueError):
    pass


def oauth_client_config(credentials: dict[str, Any]) -> dict[str, Any]:
    for key in ("installed", "web"):
        client = credentials.get(key)
        if isinstance(client, dict):
            return client
    return credentials


def validate_google_token_settings(credentials: dict[str, Any], token: dict[str, Any] | None) -> None:
    for config in (credentials, oauth_client_config(credentials), token or {}):
        endpoint = config.get("token_uri")
        if endpoint is not None and endpoint not in _ACCEPTED_TOKEN_ENDPOINTS:
            raise GoogleConfigurationError("google_token_endpoint_unsupported")
        if config.get("universe_domain", "googleapis.com") != "googleapis.com":
            raise GoogleConfigurationError("google_universe_unsupported")
