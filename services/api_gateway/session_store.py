"""Хранилище состояния сессий: отзыв и защита refresh-токенов от повторного использования.

JWT сам по себе нельзя отозвать, поэтому шлюз хранит минимум состояния:

* отозванные сессии (`sid`), чтобы выход из аккаунта действительно закрывал доступ;
* отметку «токены этого пользователя, выпущенные раньше момента X, недействительны»,
  чтобы смена пароля закрывала все сессии разом;
* использованные идентификаторы refresh-токенов (`jti`), чтобы повторное
  предъявление уже обменянного refresh-токена было обнаружено.

Хранилище закрыто по умолчанию: если оно недоступно, проверка токена завершается
ошибкой 503, а не пропускает запрос.
"""
from __future__ import annotations

import threading
import time
from typing import Optional, Protocol

from fastapi import HTTPException, status

from config.logging_config import get_logger

logger = get_logger(__name__)

_KEY_PREFIX = "auth:"


class SessionStoreUnavailable(Exception):
    """Хранилище состояния сессий недоступно."""


class SessionStore(Protocol):
    def revoke_session(self, session_id: str, ttl_seconds: int) -> None: ...

    def is_session_revoked(self, session_id: str) -> bool: ...

    def revoke_user_before(self, user_id: str, moment: float, ttl_seconds: int) -> None: ...

    def user_revoked_before(self, user_id: str) -> Optional[float]: ...

    def mark_refresh_used(self, jti: str, ttl_seconds: int) -> bool:
        """Атомарно помечает refresh-токен использованным. True, если это первое использование."""
        ...


class InMemorySessionStore:
    """Хранилище в памяти процесса. Для тестов и локальной отладки, не для нескольких воркеров."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._revoked_sessions: dict[str, float] = {}
        self._revoked_before: dict[str, tuple[float, float]] = {}
        self._used_refresh: dict[str, float] = {}

    def _alive(self, expires_at: float) -> bool:
        return expires_at > self._clock()

    def revoke_session(self, session_id: str, ttl_seconds: int) -> None:
        with self._lock:
            self._revoked_sessions[session_id] = self._clock() + ttl_seconds

    def is_session_revoked(self, session_id: str) -> bool:
        with self._lock:
            expires_at = self._revoked_sessions.get(session_id)
            return expires_at is not None and self._alive(expires_at)

    def revoke_user_before(self, user_id: str, moment: float, ttl_seconds: int) -> None:
        with self._lock:
            current = self._revoked_before.get(user_id)
            if current is None or moment > current[0]:
                self._revoked_before[user_id] = (moment, self._clock() + ttl_seconds)

    def user_revoked_before(self, user_id: str) -> Optional[float]:
        with self._lock:
            entry = self._revoked_before.get(user_id)
            if entry is None or not self._alive(entry[1]):
                return None
            return entry[0]

    def mark_refresh_used(self, jti: str, ttl_seconds: int) -> bool:
        with self._lock:
            expires_at = self._used_refresh.get(jti)
            if expires_at is not None and self._alive(expires_at):
                return False
            self._used_refresh[jti] = self._clock() + ttl_seconds
            return True


class RedisSessionStore:
    """Хранилище в Redis. Используются только SET (в том числе NX) и GET."""

    def __init__(self, client):
        self._client = client

    def _call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # redis.RedisError и сетевые ошибки
            logger.error("Session store error", extra={"error_type": type(exc).__name__})
            raise SessionStoreUnavailable(str(exc)) from exc

    def revoke_session(self, session_id: str, ttl_seconds: int) -> None:
        self._call(self._client.set, f"{_KEY_PREFIX}revoked_sid:{session_id}", "1", ex=max(1, int(ttl_seconds)))

    def is_session_revoked(self, session_id: str) -> bool:
        return self._call(self._client.get, f"{_KEY_PREFIX}revoked_sid:{session_id}") is not None

    def revoke_user_before(self, user_id: str, moment: float, ttl_seconds: int) -> None:
        # Хранится только самая поздняя отметка: более ранняя не должна её перезаписывать.
        key = f"{_KEY_PREFIX}revoked_before:{user_id}"
        current = self.user_revoked_before(user_id)
        if current is not None and current >= moment:
            return
        self._call(self._client.set, key, repr(float(moment)), ex=max(1, int(ttl_seconds)))

    def user_revoked_before(self, user_id: str) -> Optional[float]:
        raw = self._call(self._client.get, f"{_KEY_PREFIX}revoked_before:{user_id}")
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        return float(raw)

    def mark_refresh_used(self, jti: str, ttl_seconds: int) -> bool:
        created = self._call(self._client.set, f"{_KEY_PREFIX}used_refresh:{jti}", "1", nx=True, ex=max(1, int(ttl_seconds)))
        return bool(created)


_store: Optional[SessionStore] = None
_store_lock = threading.Lock()


def _build_default_store() -> SessionStore:
    import redis

    from services.api_gateway.config import settings

    client = redis.Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=1,
    )
    return RedisSessionStore(client)


def get_session_store() -> SessionStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = _build_default_store()
    return _store


def set_session_store(store: Optional[SessionStore]) -> None:
    """Подменяет хранилище (тесты). None сбрасывает на хранилище по умолчанию."""
    global _store
    with _store_lock:
        _store = store


def store_unavailable_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Session store is unavailable",
        headers={"Retry-After": "5"},
    )
