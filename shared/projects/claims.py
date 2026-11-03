"""Токен присоединения публичного аудита к проекту (AL-4).

Публичный аудит доступен по ссылке всем, у кого есть его идентификатор, поэтому сам
идентификатор не доказывает, что аудит запускал именно этот пользователь. Создатель получает
одноразовый токен в ответе на запуск; в базе остаётся только хэш.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets

CLAIM_TOKEN_BYTES = 24
_AUDIT_ID_PATTERN = re.compile(r"^[0-9a-fA-F-]{8,64}$")
MAX_TOKEN_LENGTH = 128


def new_claim_token() -> str:
    return secrets.token_urlsafe(CLAIM_TOKEN_BYTES)


def hash_claim_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_valid_audit_id(value: object) -> bool:
    return isinstance(value, str) and bool(_AUDIT_ID_PATTERN.match(value))


def token_matches(stored_hash: str | None, token: object) -> bool:
    """Сравнение за постоянное время; пустой хэш или токен никогда не совпадают."""
    if not stored_hash or not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
        return False
    return hmac.compare_digest(stored_hash, hash_claim_token(token))
