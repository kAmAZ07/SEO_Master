"""Подтверждение прав на область проекта HTTP-challenge (ТЗ REQ-SECURITY §5.2, AC-03).

Поток:
  1. `issue_challenge` — выдаёт одноразовый токен (в базе только хэш) и описывает файл,
     который владелец сайта должен разместить;
  2. `check` — запрашивает файл через SafeHttpClient без редиректов и сравнивает токен;
  3. `require_fresh` — единая проверка для операций, зависящих от подтверждения.

Все изменения состояния — условные UPDATE (`WHERE status = ...`), поэтому параллельные запросы
не создают двух подтверждений и не перезаписывают друг друга. Время и сеть внедряются.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.models import DomainAuthorization, Project
from services.api_gateway.projects.errors import (
    ChallengeConflict,
    DomainNotVerified,
    NoPendingChallenge,
    NothingToRevoke,
    RateLimited,
    ScopeInvalid,
    UrlOutOfScope,
)
from shared.netpolicy import FetchResult, NetPolicyError
from shared.projects import challenge as challenge_core
from shared.projects.authorization_state import (
    AuthStatus,
    as_utc,
    ensure_transition,
    fresh_until,
    is_fresh,
)
from shared.projects.scope import ProjectScope, ScopeError, parse_scope

Clock = Callable[[], datetime]
Fetch = Callable[[str], Awaitable[FetchResult]]

# Не больше такого числа выданных challenge на проект за сутки: таблица не растёт без границ.
MAX_ISSUES_PER_DAY = 20
PRUNE_AFTER = timedelta(days=30)
MAX_EVIDENCE_URL = 2048


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class VerificationConfig:
    challenge_ttl: timedelta = timedelta(hours=24)
    fresh_days: int = 30
    min_check_interval: timedelta = timedelta(seconds=5)
    max_attempts: int = 20

    @classmethod
    def from_settings(cls, settings: Any) -> "VerificationConfig":
        return cls(
            challenge_ttl=timedelta(hours=settings.DOMAIN_CHALLENGE_TTL_HOURS),
            fresh_days=settings.DOMAIN_VERIFICATION_FRESH_DAYS,
            min_check_interval=timedelta(seconds=settings.DOMAIN_CHECK_MIN_INTERVAL_SECONDS),
            max_attempts=settings.DOMAIN_CHECK_MAX_ATTEMPTS,
        )


@dataclass(frozen=True)
class IssuedChallenge:
    authorization: DomainAuthorization
    token: str
    file_url: str
    file_body: str


@dataclass(frozen=True)
class CheckOutcome:
    authorization: DomainAuthorization
    verified: bool
    error_code: Optional[str] = None


@dataclass(frozen=True)
class VerificationState:
    """Сводка для API/UI. `state`: unverified | pending | verified | stale."""
    state: str
    scope_origin: Optional[str]
    scope_path_prefix: Optional[str]
    fresh_days: int
    verified_at: Optional[datetime] = None
    fresh_until: Optional[datetime] = None
    challenge_expires_at: Optional[datetime] = None
    attempts: int = 0
    last_error_code: Optional[str] = None
    last_status: Optional[str] = None


def scope_of(project: Project) -> ProjectScope:
    """Область проекта. У проекта без области (scope_review) операции, зависящие от неё, недоступны."""
    if not project.origin or not project.path_prefix:
        raise ScopeInvalid("project has no valid scope", code="project_scope_unresolved")
    try:
        return parse_scope(project.origin + ("" if project.path_prefix == "/" else project.path_prefix))
    except ScopeError as exc:
        raise ScopeInvalid(str(exc), code="project_scope_unresolved") from exc


class DomainVerificationService:
    def __init__(self, db: Session, fetch: Fetch, config: VerificationConfig, clock: Clock = utcnow):
        self._db = db
        self._fetch = fetch
        self._config = config
        self._clock = clock

    # --- выдача challenge --------------------------------------------------

    def issue_challenge(self, project: Project, actor_id: str) -> IssuedChallenge:
        now = self._clock()
        scope = scope_of(project)

        issued_today = self._db.execute(
            select(func.count()).select_from(DomainAuthorization).where(
                DomainAuthorization.project_id == project.id,
                DomainAuthorization.issued_at > now - timedelta(days=1),
            )
        ).scalar_one()
        if issued_today >= MAX_ISSUES_PER_DAY:
            raise RateLimited(3600, "too many verification challenges issued for this project", code="challenge_issue_limit")

        self._expire_stale(project.id, now)
        # Новый challenge отменяет невыполненный прежний: его токен перестаёт работать.
        self._exec_update(
            update(DomainAuthorization)
            .where(DomainAuthorization.project_id == project.id, DomainAuthorization.status == AuthStatus.PENDING.value)
            .values(status=AuthStatus.SUPERSEDED.value)
        )

        token = challenge_core.new_token()
        authorization = DomainAuthorization(
            id=str(uuid.uuid4()),
            project_id=project.id,
            workspace_id=project.workspace_id,
            method="http_challenge",
            status=AuthStatus.PENDING.value,
            scope_origin=scope.origin,
            scope_path_prefix=scope.path_prefix,
            challenge_hash=challenge_core.challenge_hash(project.workspace_id, scope, token),
            subject_user_id=actor_id,
            issued_at=now,
            expires_at=now + self._config.challenge_ttl,
            attempts=0,
            evidence={},
        )
        self._db.add(authorization)
        try:
            self._db.commit()
        except IntegrityError as exc:
            self._db.rollback()
            raise ChallengeConflict("another challenge was issued concurrently, try again") from exc
        self._prune(project.id, now)
        return IssuedChallenge(
            authorization=authorization,
            token=token,
            file_url=scope.challenge_url(challenge_core.CHALLENGE_FILENAME),
            file_body=challenge_core.expected_body(token),
        )

    # --- проверка -----------------------------------------------------------

    async def check(self, project: Project) -> CheckOutcome:
        now = self._clock()
        scope = scope_of(project)

        pending = self._latest(project.id, AuthStatus.PENDING)
        if pending is None:
            raise NoPendingChallenge("no pending challenge: issue a new one first")

        if as_utc(pending.expires_at) <= now:
            self._transition(pending.id, AuthStatus.PENDING, AuthStatus.EXPIRED, last_error_code="challenge_expired")
            return CheckOutcome(self._reload(pending.id), False, "challenge_expired")

        # Займ «слота проверки» атомарен: параллельные запросы не обходят интервал и лимит попыток.
        floor = now - self._config.min_check_interval
        claimed = self._exec_update(
            update(DomainAuthorization)
            .where(
                DomainAuthorization.id == pending.id,
                DomainAuthorization.status == AuthStatus.PENDING.value,
                or_(DomainAuthorization.last_checked_at.is_(None), DomainAuthorization.last_checked_at <= floor),
            )
            .values(last_checked_at=now, attempts=DomainAuthorization.attempts + 1)
        )
        if claimed.rowcount != 1:
            self._db.rollback()
            latest = self._reload(pending.id)
            if latest.status != AuthStatus.PENDING.value:  # отозван, заменён или подтверждён параллельно
                raise NoPendingChallenge("the challenge is no longer pending")
            raise RateLimited(self._retry_after(latest, now), "verification checks are too frequent", code="check_too_frequent")
        self._db.commit()
        current = self._reload(pending.id)

        if current.attempts > self._config.max_attempts:
            self._transition(current.id, AuthStatus.PENDING, AuthStatus.FAILED, last_error_code="attempts_exhausted")
            return CheckOutcome(self._reload(current.id), False, "attempts_exhausted")

        code, evidence = await self._probe(scope, current, now)
        if code is not None:
            self._record_failure(current, code)
            return CheckOutcome(self._reload(current.id), False, code)
        return self._finalize_verified(project, current, evidence, now)

    async def _probe(self, scope: ProjectScope, authorization: DomainAuthorization, now: datetime):
        url = scope.challenge_url(challenge_core.CHALLENGE_FILENAME)
        try:
            result = await self._fetch(url)
        except NetPolicyError as exc:
            # Редирект не расширяет область (ТЗ §5.2): он считается отказом, а не следуется.
            return ("redirected" if exc.code in ("redirect_limit", "redirect_loop", "redirect_invalid") else f"fetch_{exc.code}"), {}

        if result.redirects or result.final_url != url:
            return "redirected", {}
        if result.status != 200:
            return f"http_{result.status}", {}
        content_type = (result.headers.get("content-type") or "").lower()
        if "html" in content_type:
            return "bad_content_type", {}
        token = challenge_core.extract_token(result.body)
        if token is None:
            return "malformed_body", {}
        workspace_id = authorization.workspace_id
        if not challenge_core.matches(authorization.challenge_hash, workspace_id, scope, token):
            return "token_mismatch", {}
        evidence = {
            "method": "http_challenge",
            "url": url[:MAX_EVIDENCE_URL],
            "http_status": result.status,
            "peer_ip": result.peer_ip,
            "checked_at": now.isoformat(),
        }
        return None, evidence

    def _finalize_verified(self, project: Project, current: DomainAuthorization, evidence: Dict[str, Any], now: datetime) -> CheckOutcome:
        # Объект `current` мог устареть за время сетевого запроса; допустимость перехода
        # проверяем для ожидаемого исхода, а фактическое состояние решает условный UPDATE ниже.
        ensure_transition(AuthStatus.PENDING.value, AuthStatus.VERIFIED)
        self._exec_update(
            update(DomainAuthorization)
            .where(DomainAuthorization.project_id == project.id, DomainAuthorization.status == AuthStatus.VERIFIED.value)
            .values(status=AuthStatus.SUPERSEDED.value)
        )
        promoted = self._exec_update(
            update(DomainAuthorization)
            .where(DomainAuthorization.id == current.id, DomainAuthorization.status == AuthStatus.PENDING.value)
            .values(status=AuthStatus.VERIFIED.value, verified_at=now, last_error_code=None, evidence=evidence)
        )
        if promoted.rowcount != 1:
            # Пока файл проверялся, challenge отозвали, заменили или подтвердили параллельно.
            self._db.rollback()
            return self._conflict_outcome(current.id)
        try:
            self._db.commit()
        except IntegrityError:
            self._db.rollback()
            return self._conflict_outcome(current.id)
        return CheckOutcome(self._reload(current.id), True, None)

    def _conflict_outcome(self, authorization_id: str) -> CheckOutcome:
        """Исход проверки, проигравшей гонку: итоговое состояние берётся из БД."""
        latest = self._reload(authorization_id)
        if latest.status == AuthStatus.VERIFIED.value:
            return CheckOutcome(latest, True, None)
        code = "verification_conflict" if latest.status == AuthStatus.PENDING.value else "challenge_no_longer_pending"
        return CheckOutcome(latest, False, code)

    def _record_failure(self, authorization: DomainAuthorization, code: str) -> None:
        self._exec_update(
            update(DomainAuthorization)
            .where(DomainAuthorization.id == authorization.id, DomainAuthorization.status == AuthStatus.PENDING.value)
            .values(last_error_code=code)
        )
        self._db.commit()
        if self._reload(authorization.id).attempts >= self._config.max_attempts:
            self._transition(authorization.id, AuthStatus.PENDING, AuthStatus.FAILED, last_error_code="attempts_exhausted")

    # --- отзыв и состояние --------------------------------------------------

    def revoke(self, project: Project) -> DomainAuthorization:
        now = self._clock()
        revoked: List[str] = []
        for status in (AuthStatus.PENDING, AuthStatus.VERIFIED):
            row = self._latest(project.id, status)
            if row is not None and self._transition(row.id, status, AuthStatus.REVOKED, revoked_at=now):
                revoked.append(row.id)
        if not revoked:
            raise NothingToRevoke("there is no active confirmation or pending challenge")
        return self._reload(revoked[-1])

    def state(self, project: Project) -> VerificationState:
        now = self._clock()
        try:
            scope = scope_of(project)
        except ScopeInvalid:
            return VerificationState("unverified", None, None, self._config.fresh_days, last_status="scope_unresolved")

        verified = self._latest(project.id, AuthStatus.VERIFIED)
        pending = self._latest(project.id, AuthStatus.PENDING)
        pending_alive = pending is not None and as_utc(pending.expires_at) > now

        if verified is not None and self._matches_scope(verified, scope):
            fresh = is_fresh(verified.status, verified.verified_at, self._config.fresh_days, now)
            return VerificationState(
                "verified" if fresh else "stale",
                scope.origin,
                scope.path_prefix,
                self._config.fresh_days,
                verified_at=verified.verified_at,
                fresh_until=fresh_until(verified.verified_at, self._config.fresh_days),
                challenge_expires_at=pending.expires_at if pending_alive else None,
                attempts=pending.attempts if pending_alive else 0,
                last_error_code=pending.last_error_code if pending_alive else None,
            )
        if pending_alive:
            return VerificationState(
                "pending", scope.origin, scope.path_prefix, self._config.fresh_days,
                challenge_expires_at=pending.expires_at, attempts=pending.attempts, last_error_code=pending.last_error_code,
            )
        last = self._db.execute(
            select(DomainAuthorization.status, DomainAuthorization.last_error_code)
            .where(DomainAuthorization.project_id == project.id)
            .order_by(DomainAuthorization.issued_at.desc(), DomainAuthorization.id)
            .limit(1)
        ).first()
        return VerificationState(
            "unverified", scope.origin, scope.path_prefix, self._config.fresh_days,
            last_status=("expired" if pending is not None else (last[0] if last else None)),
            last_error_code=last[1] if last else None,
        )

    def require_fresh(self, project: Project, url: Optional[str] = None) -> DomainAuthorization:
        """Единая проверка для полного обхода, расписания и публикации.

        Поднимает DomainNotVerified (нет, устарело, область изменилась) или UrlOutOfScope.
        """
        now = self._clock()
        scope = scope_of(project)
        verified = self._latest(project.id, AuthStatus.VERIFIED)
        if verified is None:
            raise DomainNotVerified("not_verified")
        if not self._matches_scope(verified, scope):
            raise DomainNotVerified("scope_changed")
        if not is_fresh(verified.status, verified.verified_at, self._config.fresh_days, now):
            raise DomainNotVerified("stale")
        if url is not None and not scope.contains(url):
            raise UrlOutOfScope("url is outside the confirmed project scope")
        return verified

    # --- внутреннее ----------------------------------------------------------

    @staticmethod
    def _matches_scope(authorization: DomainAuthorization, scope: ProjectScope) -> bool:
        return authorization.scope_origin == scope.origin and authorization.scope_path_prefix == scope.path_prefix

    def _latest(self, project_id: str, status: AuthStatus) -> Optional[DomainAuthorization]:
        return self._db.execute(
            select(DomainAuthorization)
            .where(DomainAuthorization.project_id == project_id, DomainAuthorization.status == status.value)
            .order_by(DomainAuthorization.issued_at.desc(), DomainAuthorization.id)
            .limit(1)
        ).scalar_one_or_none()

    def _exec_update(self, statement: Any):
        """Условный UPDATE без синхронизации объектов сессии: состояние читается из БД, а не из кэша."""
        return self._db.execute(statement.execution_options(synchronize_session=False))

    def _reload(self, authorization_id: str) -> DomainAuthorization:
        self._db.expire_all()
        return self._db.execute(select(DomainAuthorization).where(DomainAuthorization.id == authorization_id)).scalar_one()

    def _transition(self, authorization_id: str, current: AuthStatus, target: AuthStatus, **values: Any) -> bool:
        ensure_transition(current.value, target)
        result = self._exec_update(
            update(DomainAuthorization)
            .where(DomainAuthorization.id == authorization_id, DomainAuthorization.status == current.value)
            .values(status=target.value, **values)
        )
        self._db.commit()
        return result.rowcount == 1

    def _expire_stale(self, project_id: str, now: datetime) -> None:
        self._exec_update(
            update(DomainAuthorization)
            .where(
                DomainAuthorization.project_id == project_id,
                DomainAuthorization.status == AuthStatus.PENDING.value,
                DomainAuthorization.expires_at <= now,
            )
            .values(status=AuthStatus.EXPIRED.value, last_error_code="challenge_expired")
        )

    def _prune(self, project_id: str, now: datetime) -> None:
        """Удаляет давние неподтверждённые challenge. Подтверждавшиеся хранятся как доказательства."""
        self._db.execute(
            DomainAuthorization.__table__.delete().where(
                DomainAuthorization.project_id == project_id,
                DomainAuthorization.verified_at.is_(None),
                DomainAuthorization.status.notin_((AuthStatus.PENDING.value, AuthStatus.VERIFIED.value)),
                DomainAuthorization.issued_at < now - PRUNE_AFTER,
            )
        )
        self._db.commit()

    def _retry_after(self, pending: DomainAuthorization, now: datetime) -> int:
        if pending.last_checked_at is None:
            return 1
        remaining = (as_utc(pending.last_checked_at) + self._config.min_check_interval - now).total_seconds()
        return max(1, math.ceil(remaining))
