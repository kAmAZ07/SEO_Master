"""Жизненный цикл DomainAuthorization: таблица переходов и свежесть (ТЗ §5.2).

Состояния:
  pending    — challenge выдан, файл ещё не подтверждён;
  verified   — файл найден; действует `fresh_days` дней от `verified_at`;
  superseded — заменён более новым подтверждением той же области;
  expired    — challenge не подтвердили до `expires_at`;
  failed     — исчерпан лимит попыток проверки;
  revoked    — отозван вручную (смена домена, отзыв прав).
Терминальные состояния не меняются. «Просроченное подтверждение» (verified, но не свежее)
не отдельное состояние: оно вычисляется от времени и блокирует зависимые операции.
"""
from __future__ import annotations

import enum
from datetime import datetime, timedelta, timezone


class AuthStatus(str, enum.Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    FAILED = "failed"
    REVOKED = "revoked"


TRANSITIONS: dict[AuthStatus, frozenset[AuthStatus]] = {
    AuthStatus.PENDING: frozenset({AuthStatus.VERIFIED, AuthStatus.EXPIRED, AuthStatus.FAILED, AuthStatus.REVOKED, AuthStatus.SUPERSEDED}),
    AuthStatus.VERIFIED: frozenset({AuthStatus.SUPERSEDED, AuthStatus.REVOKED}),
    AuthStatus.SUPERSEDED: frozenset(),
    AuthStatus.EXPIRED: frozenset(),
    AuthStatus.FAILED: frozenset(),
    AuthStatus.REVOKED: frozenset(),
}
OPEN_STATUSES = (AuthStatus.PENDING.value, AuthStatus.VERIFIED.value)


class InvalidTransition(Exception):
    code = "invalid_transition"

    def __init__(self, current: str, target: str):
        super().__init__(f"{current} -> {target}")
        self.current = current
        self.target = target


def ensure_transition(current: str, target: AuthStatus) -> None:
    try:
        allowed = TRANSITIONS[AuthStatus(current)]
    except ValueError as exc:
        raise InvalidTransition(current, target.value) from exc
    if target not in allowed:
        raise InvalidTransition(current, target.value)


def as_utc(value: datetime) -> datetime:
    """Время из БД может прийти без tzinfo (sqlite): трактуем как UTC. Наивное время не сравниваем."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def fresh_until(verified_at: datetime, fresh_days: int) -> datetime:
    return as_utc(verified_at) + timedelta(days=fresh_days)


def is_fresh(status: str, verified_at: datetime | None, fresh_days: int, now: datetime) -> bool:
    """Подтверждение действует только в состоянии verified и пока не истёк срок актуальности."""
    if status != AuthStatus.VERIFIED.value or verified_at is None:
        return False
    return as_utc(now) < fresh_until(verified_at, fresh_days)
