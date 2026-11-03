"""Challenge, жизненный цикл подтверждения и матрица ролей."""
from datetime import datetime, timedelta, timezone

import pytest

from shared.projects import challenge
from shared.projects.authorization_state import (
    TRANSITIONS,
    AuthStatus,
    InvalidTransition,
    ensure_transition,
    fresh_until,
    is_fresh,
)
from shared.projects.claims import hash_claim_token, is_valid_audit_id, new_claim_token, token_matches
from shared.projects.roles import Permission, Role, has_permission, parse_role
from shared.projects.scope import parse_scope

UTC = timezone.utc
T0 = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
SCOPE = parse_scope("https://example.com/blog")


# --- challenge ------------------------------------------------------------------------

def test_tokens_are_unique_urlsafe_and_32_chars():
    tokens = {challenge.new_token() for _ in range(200)}
    assert len(tokens) == 200
    assert all(len(token) == 32 for token in tokens)


def test_hash_is_bound_to_workspace_scope_and_token():
    token = challenge.new_token()
    base = challenge.challenge_hash("ws-1", SCOPE, token)
    assert base == challenge.challenge_hash("ws-1", SCOPE, token)
    assert base != challenge.challenge_hash("ws-2", SCOPE, token)
    assert base != challenge.challenge_hash("ws-1", parse_scope("https://example.com/shop"), token)
    assert base != challenge.challenge_hash("ws-1", parse_scope("https://example.com/"), token)
    assert base != challenge.challenge_hash("ws-1", parse_scope("http://example.com/blog"), token)
    assert base != challenge.challenge_hash("ws-1", SCOPE, challenge.new_token())
    assert token not in base


def test_matches_uses_all_binding_fields():
    token = challenge.new_token()
    stored = challenge.challenge_hash("ws-1", SCOPE, token)
    assert challenge.matches(stored, "ws-1", SCOPE, token)
    assert not challenge.matches(stored, "ws-2", SCOPE, token)
    assert not challenge.matches(stored, "ws-1", SCOPE, token[:-1] + ("A" if token[-1] != "A" else "B"))


def test_extract_token_accepts_only_the_exact_file_format():
    token = challenge.new_token()
    body = challenge.expected_body(token)
    assert challenge.extract_token(body.encode()) == token
    assert challenge.extract_token(f"  {body}\r\n".encode()) == token
    assert challenge.extract_token(b"\xef\xbb\xbf" + body.encode()) == token  # файл с BOM
    assert challenge.extract_token(body.encode().replace(b"\n", b"\r\n") + b"\r\n") == token


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"<html>seo-master-site-verification=" + b"a" * 32 + b"</html>",
        b"seo-master-site-verification=",
        b"seo-master-site-verification=short",
        b"seo-master-site-verification=" + b"a" * 33,
        b"seo-master-site-verification=" + b"a" * 31 + b"!",
        b"prefix seo-master-site-verification=" + b"a" * 32,
        b"seo-master-site-verification=" + b"a" * 32 + b"\nextra line",
        b"\xff\xfe\x00",
        b"x" * 513,
    ],
)
def test_extract_token_rejects_everything_else(body):
    assert challenge.extract_token(body) is None


# --- жизненный цикл --------------------------------------------------------------------

def test_every_status_has_a_transition_row_and_terminal_states_are_final():
    assert set(TRANSITIONS) == set(AuthStatus)
    for terminal in (AuthStatus.SUPERSEDED, AuthStatus.EXPIRED, AuthStatus.FAILED, AuthStatus.REVOKED):
        assert TRANSITIONS[terminal] == frozenset()


@pytest.mark.parametrize(
    "current, target",
    [(c, t) for c, targets in TRANSITIONS.items() for t in targets],
)
def test_allowed_transitions_pass(current, target):
    ensure_transition(current.value, target)


@pytest.mark.parametrize(
    "current, target",
    [(c, t) for c in AuthStatus for t in AuthStatus if t not in TRANSITIONS[c]],
)
def test_forbidden_transitions_raise(current, target):
    with pytest.raises(InvalidTransition):
        ensure_transition(current.value, target)


def test_unknown_status_raises():
    with pytest.raises(InvalidTransition):
        ensure_transition("bogus", AuthStatus.VERIFIED)


def test_verified_cannot_return_to_pending():
    with pytest.raises(InvalidTransition):
        ensure_transition("verified", AuthStatus.PENDING)


# --- свежесть ---------------------------------------------------------------------------

def test_freshness_boundary_is_exact():
    verified_at = T0
    just_before = verified_at + timedelta(days=30) - timedelta(microseconds=1)
    exactly = verified_at + timedelta(days=30)
    assert is_fresh("verified", verified_at, 30, just_before)
    assert not is_fresh("verified", verified_at, 30, exactly)
    assert not is_fresh("verified", verified_at, 30, exactly + timedelta(seconds=1))
    assert fresh_until(verified_at, 30) == exactly


def test_freshness_requires_verified_status_and_timestamp():
    assert not is_fresh("pending", T0, 30, T0)
    assert not is_fresh("revoked", T0, 30, T0)
    assert not is_fresh("verified", None, 30, T0)


def test_freshness_is_configurable():
    assert not is_fresh("verified", T0, 1, T0 + timedelta(days=1))
    assert is_fresh("verified", T0, 365, T0 + timedelta(days=364))


def test_naive_database_timestamps_are_treated_as_utc():
    naive = datetime(2026, 11, 3, 12, 0)
    assert is_fresh("verified", naive, 30, T0 + timedelta(days=1))
    assert not is_fresh("verified", naive, 30, T0 + timedelta(days=31))


# --- роли -----------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "role, allowed",
    [
        ("owner", {Permission.READ, Permission.MANAGE, Permission.APPROVE, Permission.DEPLOY}),
        ("admin", {Permission.READ, Permission.MANAGE, Permission.APPROVE, Permission.DEPLOY}),
        ("reviewer", {Permission.READ, Permission.APPROVE}),
        ("deployer", {Permission.READ, Permission.DEPLOY}),
        ("viewer", {Permission.READ}),
    ],
)
def test_role_matrix(role, allowed):
    for permission in Permission:
        assert has_permission(role, permission) is (permission in allowed)


def test_unknown_role_grants_nothing():
    for permission in Permission:
        assert has_permission("superuser", permission) is False
        assert has_permission("", permission) is False
    with pytest.raises(ValueError):
        parse_role("superuser")
    assert parse_role("owner") is Role.OWNER


# --- токен присоединения аудита ------------------------------------------------------------------

def test_claim_token_roundtrip_and_hash_only():
    token = new_claim_token()
    stored = hash_claim_token(token)
    assert token_matches(stored, token)
    assert token not in stored and len(stored) == 64
    assert not token_matches(stored, token + "x")
    assert not token_matches(stored, "")
    assert not token_matches(None, token)
    assert not token_matches("", token)
    assert not token_matches(stored, None)
    assert not token_matches(stored, "a" * 129)


def test_audit_id_format():
    assert is_valid_audit_id("0123abcd-0123-4abc-8def-0123456789ab")
    for bad in (None, "", "short", "a" * 65, "../../etc", "zzzzzzzz", 123):
        assert not is_valid_audit_id(bad)
