import json
import os
from typing import Any, Dict

from cryptography.fernet import Fernet, InvalidToken


class CredentialsVault:
    def __init__(self, master_key: str | None = None) -> None:
        resolved_key = (master_key or os.getenv("MASTER_ENCRYPTION_KEY") or "").strip()
        if not resolved_key:
            raise ValueError("MASTER_ENCRYPTION_KEY is required for project integrations")

        try:
            self._fernet = Fernet(resolved_key.encode("utf-8"))
        except Exception as exc:
            raise ValueError("MASTER_ENCRYPTION_KEY must be a valid Fernet key") from exc

    def encrypt(self, credentials: Dict[str, Any]) -> str:
        payload = json.dumps(credentials, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self._fernet.encrypt(payload).decode("utf-8")

    def decrypt(self, encrypted_credentials: str) -> Dict[str, Any]:
        try:
            decrypted = self._fernet.decrypt(encrypted_credentials.encode("utf-8"))
        except InvalidToken as exc:
            raise ValueError("Failed to decrypt integration credentials") from exc

        data = json.loads(decrypted.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Decrypted integration payload must be an object")
        return data
