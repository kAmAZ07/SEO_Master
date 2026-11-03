"""Токены и сессии API Gateway (ТЗ §5.1, AC-03).

Проверяется не только успешный вход, но и отказные пути: токен не того типа,
подделка, потеря обязательных claims, отзыв сессии, повторное использование
refresh-токена и недоступное хранилище состояния.
"""
import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://user:password@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests-only-0123456789")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key-0123456789")

import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jose import jwt

from services.api_gateway import auth
from services.api_gateway.config import settings
from services.api_gateway.session_store import (
    InMemorySessionStore,
    RedisSessionStore,
    SessionStoreUnavailable,
    set_session_store,
)

USER_ID = str(uuid.uuid4())
EMAIL = "user@example.com"


@pytest.fixture(autouse=True)
def store():
    s = InMemorySessionStore()
    set_session_store(s)
    yield s
    set_session_store(None)


def _raw_token(**overrides):
    """Собирает токен вручную, чтобы проверять нарушения по одному claim."""
    now = time.time()
    claims = {
        "sub": USER_ID,
        "email": EMAIL,
        "type": "access",
        "jti": uuid.uuid4().hex,
        "sid": uuid.uuid4().hex,
        "iat": now,
        "exp": int(now) + 600,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def _pair(session_id=None):
    return auth.create_token_pair(USER_ID, EMAIL, session_id=session_id)


def _is_401(exc_info):
    return exc_info.value.status_code == 401


# --- нормальный путь ---------------------------------------------------------

def test_access_token_roundtrip():
    data = auth.decode_token(_pair().access_token)
    assert data.user_id == USER_ID and data.email == EMAIL
    assert data.token_type == "access" and data.session_id and data.jti


def test_pair_shares_one_session_and_distinct_jti():
    pair = _pair()
    access, refresh = auth.decode_token(pair.access_token), auth.verify_refresh_token(pair.refresh_token)
    assert access.session_id == refresh.session_id
    assert access.jti != refresh.jti


# --- тип токена --------------------------------------------------------------

def test_refresh_token_is_not_accepted_as_access():
    with pytest.raises(HTTPException) as exc:
        auth.decode_token(_pair().refresh_token)
    assert _is_401(exc)


def test_access_token_is_not_accepted_as_refresh():
    with pytest.raises(HTTPException) as exc:
        auth.verify_refresh_token(_pair().access_token)
    assert _is_401(exc)


def test_unknown_token_type_is_rejected():
    with pytest.raises(HTTPException) as exc:
        auth.decode_token(_raw_token(type="service"))
    assert _is_401(exc)


# --- обязательные claims, подпись, срок --------------------------------------

@pytest.mark.parametrize("claim", ["sub", "email", "type", "jti", "sid", "iat", "exp", "iss", "aud"])
def test_missing_required_claim_is_rejected(claim):
    with pytest.raises(HTTPException) as exc:
        auth.decode_token(_raw_token(**{claim: None}))
    assert _is_401(exc)


@pytest.mark.parametrize("claim", ["sub", "email", "sid", "jti"])
def test_empty_string_claim_is_rejected(claim):
    with pytest.raises(HTTPException) as exc:
        auth.decode_token(_raw_token(**{claim: ""}))
    assert _is_401(exc)


def test_wrong_issuer_and_audience_are_rejected():
    for override in ({"iss": "someone-else"}, {"aud": "another-audience"}):
        with pytest.raises(HTTPException) as exc:
            auth.decode_token(_raw_token(**override))
        assert _is_401(exc)


def test_expired_token_is_rejected():
    with pytest.raises(HTTPException) as exc:
        auth.decode_token(_raw_token(iat=time.time() - 1200, exp=int(time.time()) - 600))
    assert _is_401(exc)


def test_token_signed_with_other_key_is_rejected():
    forged = jwt.encode(
        {"sub": USER_ID, "email": EMAIL, "type": "access", "jti": "j", "sid": "s",
         "iat": time.time(), "exp": int(time.time()) + 600,
         "iss": settings.JWT_ISSUER, "aud": settings.JWT_AUDIENCE},
        "another-secret-key-0123456789-abcdef", algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        auth.decode_token(forged)
    assert _is_401(exc)


def test_tampered_payload_is_rejected():
    header, payload, signature = _pair().access_token.split(".")
    tampered = ".".join([header, payload[:-2] + ("AA" if not payload.endswith("AA") else "BB"), signature])
    with pytest.raises(HTTPException) as exc:
        auth.decode_token(tampered)
    assert _is_401(exc)


def test_unsigned_alg_none_token_is_rejected():
    import base64
    import json

    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    now = int(time.time())
    unsigned = ".".join([
        b64({"alg": "none", "typ": "JWT"}),
        b64({"sub": USER_ID, "email": EMAIL, "type": "access", "jti": "j", "sid": "s", "iat": now,
             "exp": now + 600, "iss": settings.JWT_ISSUER, "aud": settings.JWT_AUDIENCE}),
        "",
    ])
    with pytest.raises(HTTPException) as exc:
        auth.decode_token(unsigned)
    assert _is_401(exc)


@pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "Bearer x", "\x00", "a" * 10000])
def test_garbage_tokens_are_rejected(garbage):
    with pytest.raises(HTTPException) as exc:
        auth.decode_token(garbage)
    assert _is_401(exc)


def test_401_carries_www_authenticate_header():
    with pytest.raises(HTTPException) as exc:
        auth.decode_token("abc")
    assert exc.value.headers == {"WWW-Authenticate": "Bearer"}


# --- отзыв сессий ------------------------------------------------------------

def test_revoked_session_stops_working_but_other_session_survives():
    first, second = _pair(), _pair()
    auth.revoke_session(auth.decode_token(first.access_token))
    with pytest.raises(HTTPException) as exc:
        auth.decode_token(first.access_token)
    assert _is_401(exc)
    with pytest.raises(HTTPException):
        auth.verify_refresh_token(first.refresh_token)
    assert auth.decode_token(second.access_token).user_id == USER_ID


def test_revoke_all_sessions_kills_old_tokens_but_not_new_ones():
    old = _pair()
    time.sleep(0.01)
    auth.revoke_all_sessions(USER_ID)
    with pytest.raises(HTTPException):
        auth.decode_token(old.access_token)
    time.sleep(0.01)
    assert auth.decode_token(_pair().access_token).user_id == USER_ID


def test_revoke_all_sessions_does_not_touch_other_users():
    other = auth.create_token_pair(str(uuid.uuid4()), "other@example.com")
    auth.revoke_all_sessions(USER_ID)
    assert auth.decode_token(other.access_token).email == "other@example.com"


# --- ротация refresh-токена --------------------------------------------------

def test_refresh_rotation_keeps_session_and_issues_new_tokens():
    pair = _pair()
    new_pair, old_data = auth.rotate_refresh_token(pair.refresh_token)
    assert new_pair.refresh_token != pair.refresh_token
    assert auth.decode_token(new_pair.access_token).session_id == old_data.session_id


def test_reused_refresh_token_is_rejected_and_revokes_the_whole_session():
    pair = _pair()
    new_pair, _ = auth.rotate_refresh_token(pair.refresh_token)
    with pytest.raises(HTTPException) as exc:
        auth.rotate_refresh_token(pair.refresh_token)
    assert _is_401(exc)
    # украденный токен и легитимная копия закрыты одновременно
    with pytest.raises(HTTPException):
        auth.decode_token(new_pair.access_token)
    with pytest.raises(HTTPException):
        auth.rotate_refresh_token(new_pair.refresh_token)


def test_access_token_cannot_be_rotated():
    with pytest.raises(HTTPException) as exc:
        auth.rotate_refresh_token(_pair().access_token)
    assert _is_401(exc)


# --- недоступное хранилище: закрыто по умолчанию -----------------------------

class _BrokenStore:
    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise SessionStoreUnavailable("redis is down")
        return fail


def test_store_outage_gives_503_not_access():
    token = _pair().access_token
    set_session_store(_BrokenStore())
    with pytest.raises(HTTPException) as exc:
        auth.decode_token(token)
    assert exc.value.status_code == 503
    assert exc.value.headers.get("Retry-After") == "5"


def test_store_outage_blocks_refresh_and_logout():
    pair = _pair()
    data = auth.decode_token(pair.access_token)
    set_session_store(_BrokenStore())
    for call in (lambda: auth.rotate_refresh_token(pair.refresh_token), lambda: auth.revoke_session(data),
                 lambda: auth.revoke_all_sessions(USER_ID)):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 503


# --- Redis-реализация (проверяется на подставном клиенте с тем же контрактом) --

class _FakeRedis:
    def __init__(self):
        self.data, self.ttl = {}, {}
        self.fail = False

    def _check(self):
        if self.fail:
            raise ConnectionError("down")

    def set(self, key, value, ex=None, nx=False):
        self._check()
        if nx and key in self.data:
            return None
        self.data[key], self.ttl[key] = value, ex
        return True

    def get(self, key):
        self._check()
        return self.data.get(key)


def test_redis_store_keys_ttl_and_atomic_refresh_marker():
    client = _FakeRedis()
    store = RedisSessionStore(client)
    store.revoke_session("sid1", 100)
    assert store.is_session_revoked("sid1") and not store.is_session_revoked("sid2")
    assert client.ttl["auth:revoked_sid:sid1"] == 100
    assert store.mark_refresh_used("jti1", 50) is True
    assert store.mark_refresh_used("jti1", 50) is False
    store.revoke_user_before("u1", 200.5, 100)
    store.revoke_user_before("u1", 100.0, 100)  # более ранняя отметка не перезаписывает позднюю
    assert store.user_revoked_before("u1") == 200.5
    assert store.user_revoked_before("u2") is None


def test_redis_store_wraps_client_errors():
    client = _FakeRedis()
    client.fail = True
    with pytest.raises(SessionStoreUnavailable):
        RedisSessionStore(client).is_session_revoked("x")


def test_in_memory_store_honours_ttl():
    now = [1000.0]
    store = InMemorySessionStore(clock=lambda: now[0])
    store.revoke_session("s", 10)
    assert store.is_session_revoked("s")
    now[0] += 11
    assert not store.is_session_revoked("s")
    assert store.mark_refresh_used("j", 5) and not store.mark_refresh_used("j", 5)
    now[0] += 6
    assert store.mark_refresh_used("j", 5)


# --- через HTTP --------------------------------------------------------------

class _FakeQuery:
    def __init__(self, user):
        self.user = user

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.user


class _FakeDb:
    def __init__(self, user):
        self.user = user

    def query(self, *args, **kwargs):
        return _FakeQuery(self.user)

    def add(self, obj):
        pass

    def commit(self):
        pass

    def refresh(self, obj):
        pass


@pytest.fixture()
def http(store):
    from services.api_gateway.main import app
    from config.database_config import get_db

    user = SimpleNamespace(
        id=USER_ID, email=EMAIL, full_name="Test", is_active=True, is_superuser=False,
        created_at=None, updated_at=None, hashed_password=auth.pwd_context.hash("old-password-1"),
    )
    holder = SimpleNamespace(user=user)
    app.dependency_overrides[get_db] = lambda: _FakeDb(holder.user)
    client = TestClient(app, raise_server_exceptions=False)
    client.holder = holder
    yield client
    app.dependency_overrides.clear()


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_http_me_accepts_access_and_rejects_refresh(http):
    pair = _pair()
    assert http.get("/api/me", headers=_bearer(pair.access_token)).status_code == 200
    response = http.get("/api/me", headers=_bearer(pair.refresh_token))
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_http_logout_closes_session(http):
    pair = _pair()
    assert http.post("/api/auth/logout", headers=_bearer(pair.access_token)).status_code == 200
    assert http.get("/api/me", headers=_bearer(pair.access_token)).status_code == 401
    assert http.post("/api/auth/refresh", json={"refresh_token": pair.refresh_token}).status_code == 401


def test_http_refresh_rotates_and_detects_reuse(http):
    pair = _pair()
    first = http.post("/api/auth/refresh", json={"refresh_token": pair.refresh_token})
    assert first.status_code == 200
    new_access = first.json()["token"]
    assert http.get("/api/me", headers=_bearer(new_access)).status_code == 200
    again = http.post("/api/auth/refresh", json={"refresh_token": pair.refresh_token})
    assert again.status_code == 401
    assert http.get("/api/me", headers=_bearer(new_access)).status_code == 401


def test_http_inactive_user_cannot_use_a_valid_token_or_refresh(http):
    pair = _pair()
    http.holder.user.is_active = False
    assert http.get("/api/me", headers=_bearer(pair.access_token)).status_code == 403
    assert http.post("/api/auth/refresh", json={"refresh_token": pair.refresh_token}).status_code == 403


def test_http_change_password_revokes_existing_tokens(http):
    pair = _pair()
    time.sleep(0.01)
    response = http.post(
        "/api/auth/change-password",
        headers=_bearer(pair.access_token),
        json={"currentPassword": "old-password-1", "newPassword": "new-password-2"},
    )
    assert response.status_code == 200 and response.json()["reauth_required"] is True
    assert http.get("/api/me", headers=_bearer(pair.access_token)).status_code == 401
