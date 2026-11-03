"""Challenge подтверждения прав на сайт (ТЗ REQ-SECURITY §5.2).

Владелец размещает файл `<origin><prefix>/seo-master-challenge.txt` с содержимым
`seo-master-site-verification=<token>`. В базе хранится только SHA-256 от строки, связывающей
workspace, область и токен: утечка таблицы не даёт подделать файл, а токен, выданный для одной
области или workspace, не подходит для другой.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from typing import Optional

from shared.projects.scope import ProjectScope

CHALLENGE_FILENAME = "seo-master-challenge.txt"
BODY_PREFIX = "seo-master-site-verification="
TOKEN_BYTES = 24
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{32}")
MAX_BODY_BYTES = 512


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)  # 24 байта дают ровно 32 символа base64url


def challenge_hash(workspace_id: str, scope: ProjectScope, token: str) -> str:
    material = "\n".join(("v1", workspace_id, scope.origin, scope.path_prefix, token))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def expected_body(token: str) -> str:
    return f"{BODY_PREFIX}{token}"


def extract_token(body: bytes) -> Optional[str]:
    """Токен из тела файла. Тело обязано целиком состоять из одной строки challenge."""
    if len(body) > MAX_BODY_BYTES:
        return None
    try:
        # utf-8-sig: Блокнот Windows сохраняет файл с BOM, это не должно ломать проверку.
        text = body.decode("utf-8-sig").strip()
    except UnicodeDecodeError:
        return None
    if not text.startswith(BODY_PREFIX):
        return None
    token = text[len(BODY_PREFIX):]
    return token if _TOKEN_PATTERN.fullmatch(token) else None


def matches(stored_hash: str, workspace_id: str, scope: ProjectScope, token: str) -> bool:
    return hmac.compare_digest(stored_hash, challenge_hash(workspace_id, scope, token))
