"""Подтверждение прав на сайт: HTTP-challenge, свежесть 30 дней, негативы AC-03."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

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
from services.api_gateway.projects.verification import (
    MAX_ISSUES_PER_DAY,
    DomainVerificationService,
    VerificationConfig,
    scope_of,
)
from services.api_gateway.projects.workspaces import ensure_personal_workspace
from shared.netpolicy import FetchResult, NetPolicyError
from shared.projects import challenge as core
from shared.projects.authorization_state import AuthStatus, InvalidTransition
from tests.fixtures.project_db import make_engine, make_sessionmaker

UTC = timezone.utc
T0 = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
ALICE = "00000000-0000-4000-8000-00000000000a"


def run(coro):
    return asyncio.run(coro)


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


class FakeSite:
    """Подменяет сетевой слой: что сайт отвечает на запрос файла challenge."""

    def __init__(self):
        self.requests = []
        self.status = 200
        self.body = b""
        self.headers = {"content-type": "text/plain"}
        self.error = None
        self.redirects = []
        self.final_suffix = ""

    def serve_token(self, token):
        self.body = core.expected_body(token).encode()

    async def __call__(self, url):
        self.requests.append(url)
        if self.error:
            raise self.error
        return FetchResult(
            url=url, final_url=url + self.final_suffix, status=self.status, headers=dict(self.headers),
            body=self.body, redirects=list(self.redirects), peer_ip="93.184.216.34", elapsed=0.01,
        )


@pytest.fixture()
def env(tmp_path):
    maker = make_sessionmaker(make_engine(str(tmp_path)))
    return maker


def make_project(db, url_prefix="/", owner=ALICE, origin="https://example.com"):
    workspace = ensure_personal_workspace(db, owner)
    project = Project(
        id=str(uuid.uuid4()), name="p", url=origin + url_prefix, owner_id=owner, workspace_id=workspace.id,
        origin=origin, path_prefix=url_prefix, status="active",
    )
    db.add(project)
    db.commit()
    return project


def service(db, site, clock, **config):
    return DomainVerificationService(db, site, VerificationConfig(**config), clock)


def issue_and_serve(svc, db, project, site):
    issued = svc.issue_challenge(project, ALICE)
    site.serve_token(issued.token)
    return issued


# --- выдача --------------------------------------------------------------------------------------

def test_issue_returns_file_location_and_stores_only_the_hash(env):
    with env() as db:
        project = make_project(db, "/blog")
        svc = service(db, FakeSite(), Clock())
        issued = svc.issue_challenge(project, ALICE)

        assert issued.file_url == "https://example.com/blog/seo-master-challenge.txt"
        assert issued.file_body == f"seo-master-site-verification={issued.token}"
        row = issued.authorization
        assert row.status == "pending" and row.attempts == 0
        assert row.scope_origin == "https://example.com" and row.scope_path_prefix == "/blog"
        assert row.expires_at - row.issued_at == timedelta(hours=24)
        assert row.subject_user_id == ALICE
        assert issued.token not in row.challenge_hash
        dumped = " ".join(str(getattr(row, c.name)) for c in DomainAuthorization.__table__.columns)
        assert issued.token not in dumped


def test_origin_wide_challenge_lives_at_the_root(env):
    with env() as db:
        project = make_project(db, "/")
        issued = service(db, FakeSite(), Clock()).issue_challenge(project, ALICE)
        assert issued.file_url == "https://example.com/seo-master-challenge.txt"


def test_new_challenge_supersedes_the_previous_pending_one_and_old_token_stops_working(env):
    with env() as db:
        project, site, clock = make_project(db), FakeSite(), Clock()
        svc = service(db, site, clock)
        first = svc.issue_challenge(project, ALICE)
        first_id = first.authorization.id
        second = svc.issue_challenge(project, ALICE)

        db.expire_all()
        assert db.get(DomainAuthorization, first_id).status == "superseded"
        site.serve_token(first.token)
        clock.advance(seconds=10)
        outcome = run(svc.check(project))
        assert not outcome.verified and outcome.error_code == "token_mismatch"
        assert outcome.authorization.id == second.authorization.id


def test_issue_for_project_without_scope_is_rejected(env):
    with env() as db:
        project = make_project(db)
        project.origin = project.path_prefix = None
        db.commit()
        with pytest.raises(ScopeInvalid) as caught:
            service(db, FakeSite(), Clock()).issue_challenge(project, ALICE)
        assert caught.value.code == "project_scope_unresolved"
        assert db.scalar(select(DomainAuthorization.id)) is None


def test_issue_is_limited_per_project_per_day(env):
    with env() as db:
        project, clock = make_project(db), Clock()
        svc = service(db, FakeSite(), clock)
        for _ in range(MAX_ISSUES_PER_DAY):
            svc.issue_challenge(project, ALICE)
        with pytest.raises(RateLimited) as caught:
            svc.issue_challenge(project, ALICE)
        assert caught.value.code == "challenge_issue_limit" and caught.value.retry_after >= 1
        clock.advance(days=1, seconds=1)
        svc.issue_challenge(project, ALICE)  # окно прошло


def test_old_unverified_challenges_are_pruned_but_verified_evidence_is_kept(env):
    with env() as db:
        project, site, clock = make_project(db), FakeSite(), Clock()
        svc = service(db, site, clock)
        issue_and_serve(svc, db, project, site)
        clock.advance(seconds=10)
        assert run(svc.check(project)).verified
        svc.issue_challenge(project, ALICE)  # superseded позже, проверенный остаётся
        clock.advance(days=40)
        svc.issue_challenge(project, ALICE)
        statuses = sorted(r.status for r in db.scalars(select(DomainAuthorization)))
        assert "verified" in statuses
        assert statuses.count("superseded") <= 1  # давний неподтверждённый удалён


# --- успешная проверка ----------------------------------------------------------------------------

def test_successful_check_verifies_and_records_evidence(env):
    with env() as db:
        project, site, clock = make_project(db, "/blog"), FakeSite(), Clock()
        svc = service(db, site, clock)
        issue_and_serve(svc, db, project, site)
        clock.advance(seconds=30)

        outcome = run(svc.check(project))

        assert outcome.verified and outcome.error_code is None
        row = outcome.authorization
        assert row.status == "verified" and row.verified_at is not None
        assert row.verified_at.replace(tzinfo=UTC) == clock.now
        assert row.evidence["url"] == "https://example.com/blog/seo-master-challenge.txt"
        assert row.evidence["http_status"] == 200
        assert "token" not in row.evidence
        assert site.requests == ["https://example.com/blog/seo-master-challenge.txt"]
        state = svc.state(project)
        assert state.state == "verified"
        assert state.fresh_until is not None


def test_check_accepts_crlf_and_bom_file(env):
    with env() as db:
        project, site, clock = make_project(db), FakeSite(), Clock()
        svc = service(db, site, clock)
        issued = svc.issue_challenge(project, ALICE)
        site.body = b"\xef\xbb\xbf" + issued.file_body.encode() + b"\r\n"
        clock.advance(seconds=10)
        assert run(svc.check(project)).verified


def test_reverification_supersedes_the_old_confirmation_atomically(env):
    with env() as db:
        project, site, clock = make_project(db), FakeSite(), Clock()
        svc = service(db, site, clock)
        issue_and_serve(svc, db, project, site)
        clock.advance(seconds=10)
        first = run(svc.check(project)).authorization.id

        clock.advance(days=29)
        assert svc.state(project).state == "verified"  # старое ещё действует, пока идёт повторная проверка
        issue_and_serve(svc, db, project, site)
        assert svc.state(project).state == "verified"
        clock.advance(seconds=10)
        second = run(svc.check(project)).authorization.id

        db.expire_all()
        assert db.get(DomainAuthorization, first).status == "superseded"
        assert db.get(DomainAuthorization, second).status == "verified"
        assert len(db.scalars(select(DomainAuthorization).where(DomainAuthorization.status == "verified")).all()) == 1


# --- негативы проверки ------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "configure, code",
    [
        (lambda site, token: setattr(site, "status", 404), "http_404"),
        (lambda site, token: setattr(site, "status", 500), "http_500"),
        (lambda site, token: setattr(site, "status", 403), "http_403"),
        (lambda site, token: setattr(site, "headers", {"content-type": "text/html; charset=utf-8"}), "bad_content_type"),
        (lambda site, token: setattr(site, "body", b"<html>not found</html>"), "malformed_body"),
        (lambda site, token: setattr(site, "body", b""), "malformed_body"),
        (lambda site, token: setattr(site, "body", core.expected_body(core.new_token()).encode()), "token_mismatch"),
        (lambda site, token: setattr(site, "redirects", ["https://example.com/other"]), "redirected"),
        (lambda site, token: setattr(site, "final_suffix", "?moved=1"), "redirected"),
        (lambda site, token: setattr(site, "error", NetPolicyError("redirect_limit", "more than 0")), "redirected"),
        (lambda site, token: setattr(site, "error", NetPolicyError("address_blocked", "10.0.0.1")), "fetch_address_blocked"),
        (lambda site, token: setattr(site, "error", NetPolicyError("dns_failure", "nxdomain")), "fetch_dns_failure"),
        (lambda site, token: setattr(site, "error", NetPolicyError("timeout", "slow")), "fetch_timeout"),
        (lambda site, token: setattr(site, "error", NetPolicyError("tls_error", "bad cert")), "fetch_tls_error"),
        (lambda site, token: setattr(site, "error", NetPolicyError("response_too_large", "big")), "fetch_response_too_large"),
    ],
)
def test_failed_checks_keep_the_challenge_pending_and_report_a_code(env, configure, code):
    with env() as db:
        project, site, clock = make_project(db), FakeSite(), Clock()
        svc = service(db, site, clock)
        issued = issue_and_serve(svc, db, project, site)
        configure(site, issued.token)
        clock.advance(seconds=10)

        outcome = run(svc.check(project))

        assert not outcome.verified and outcome.error_code == code
        assert outcome.authorization.status == "pending"
        assert outcome.authorization.last_error_code == code
        assert outcome.authorization.attempts == 1
        assert svc.state(project).state == "pending"
        with pytest.raises(DomainNotVerified):
            svc.require_fresh(project)


def test_token_issued_for_another_scope_is_rejected(env):
    """Токен /shop не подтверждает /blog, даже если оба проекта одного владельца."""
    with env() as db:
        site, clock = FakeSite(), Clock()
        blog, shop = make_project(db, "/blog"), make_project(db, "/shop")
        svc = service(db, site, clock)
        shop_issue = svc.issue_challenge(shop, ALICE)
        svc.issue_challenge(blog, ALICE)
        site.serve_token(shop_issue.token)
        clock.advance(seconds=10)
        outcome = run(svc.check(blog))
        assert not outcome.verified and outcome.error_code == "token_mismatch"


def test_subpath_confirmation_does_not_cover_the_whole_host(env):
    with env() as db:
        site, clock = FakeSite(), Clock()
        blog = make_project(db, "/blog")
        svc = service(db, site, clock)
        issue_and_serve(svc, db, blog, site)
        clock.advance(seconds=10)
        assert run(svc.check(blog)).verified
        svc.require_fresh(blog, "https://example.com/blog/post")
        for outside in ("https://example.com/", "https://example.com/admin", "https://example.com/blogger",
                        "https://example.com/blog/../admin", "https://www.example.com/blog"):
            with pytest.raises(UrlOutOfScope):
                svc.require_fresh(blog, outside)


def test_check_without_pending_challenge(env):
    with env() as db:
        with pytest.raises(NoPendingChallenge):
            run(service(db, FakeSite(), Clock()).check(make_project(db)))


def test_expired_challenge_is_marked_expired_and_cannot_be_verified(env):
    with env() as db:
        project, site, clock = make_project(db), FakeSite(), Clock()
        svc = service(db, site, clock)
        issue_and_serve(svc, db, project, site)
        clock.advance(hours=24)  # ровно срок: уже не действует

        outcome = run(svc.check(project))
        assert not outcome.verified and outcome.error_code == "challenge_expired"
        assert outcome.authorization.status == "expired"
        assert site.requests == []  # сеть не трогали
        with pytest.raises(NoPendingChallenge):
            run(svc.check(project))
        assert svc.state(project).state == "unverified"
        assert svc.state(project).last_status == "expired"


def test_challenge_one_second_before_expiry_still_works(env):
    with env() as db:
        project, site, clock = make_project(db), FakeSite(), Clock()
        svc = service(db, site, clock)
        issue_and_serve(svc, db, project, site)
        clock.advance(hours=23, minutes=59, seconds=59)
        assert run(svc.check(project)).verified


# --- частота и лимит попыток ------------------------------------------------------------------------------

def test_checks_are_rate_limited_without_touching_the_network(env):
    with env() as db:
        project, site, clock = make_project(db), FakeSite(), Clock()
        svc = service(db, site, clock, min_check_interval=timedelta(seconds=5))
        issue_and_serve(svc, db, project, site)
        site.status = 404
        clock.advance(seconds=1)
        run(svc.check(project))
        calls = len(site.requests)

        clock.advance(seconds=2)  # с прошлой проверки 2 с из 5
        with pytest.raises(RateLimited) as caught:
            run(svc.check(project))
        assert caught.value.retry_after == 3 and caught.value.code == "check_too_frequent"
        assert len(site.requests) == calls  # сеть не тронута
        assert svc._latest(project.id, AuthStatus.PENDING).attempts == 1  # отказ попытку не расходует

        clock.advance(seconds=2)  # 4 с: ещё рано, остаётся 1 с
        with pytest.raises(RateLimited) as caught:
            run(svc.check(project))
        assert caught.value.retry_after == 1

        clock.advance(seconds=1)  # ровно 5 с с прошлой проверки: разрешено
        run(svc.check(project))
        assert len(site.requests) == calls + 1


def test_attempt_limit_fails_the_challenge(env):
    with env() as db:
        project, site, clock = make_project(db), FakeSite(), Clock()
        svc = service(db, site, clock, max_attempts=3, min_check_interval=timedelta(seconds=0))
        issue_and_serve(svc, db, project, site)
        site.status = 404
        statuses, last = [], None
        for _ in range(3):
            clock.advance(seconds=1)
            last = run(svc.check(project))
            statuses.append(last.authorization.status)  # объект общий для всех попыток: читаем сразу
        assert statuses == ["pending", "pending", "failed"]
        assert last.authorization.last_error_code == "attempts_exhausted"
        with pytest.raises(NoPendingChallenge):
            run(svc.check(project))


def test_attempts_already_over_the_limit_fail_without_a_request(env):
    with env() as db:
        project, site, clock = make_project(db), FakeSite(), Clock()
        svc = service(db, site, clock, max_attempts=5, min_check_interval=timedelta(seconds=0))
        issue_and_serve(svc, db, project, site)
        db.execute(DomainAuthorization.__table__.update().values(attempts=5))
        db.commit()
        clock.advance(seconds=1)
        outcome = run(svc.check(project))
        assert outcome.error_code == "attempts_exhausted" and outcome.authorization.status == "failed"
        assert site.requests == []


# --- свежесть и зависимые операции --------------------------------------------------------------------------

def test_require_fresh_honours_the_30_day_window_to_the_second(env):
    with env() as db:
        project, site, clock = make_project(db), FakeSite(), Clock()
        svc = service(db, site, clock, fresh_days=30)
        issue_and_serve(svc, db, project, site)
        clock.advance(seconds=10)
        run(svc.check(project))
        verified_at = clock.now

        clock.now = verified_at + timedelta(days=30) - timedelta(seconds=1)
        svc.require_fresh(project)
        assert svc.state(project).state == "verified"

        clock.now = verified_at + timedelta(days=30)
        with pytest.raises(DomainNotVerified) as caught:
            svc.require_fresh(project)
        assert caught.value.reason == "stale"
        assert svc.state(project).state == "stale"


def test_never_verified_project_is_blocked(env):
    with env() as db:
        project = make_project(db)
        with pytest.raises(DomainNotVerified) as caught:
            service(db, FakeSite(), Clock()).require_fresh(project)
        assert caught.value.reason == "not_verified"
        assert caught.value.http_status == 403


def test_freshness_period_is_configurable_and_reported(env):
    with env() as db:
        project, site, clock = make_project(db), FakeSite(), Clock()
        svc = service(db, site, clock, fresh_days=7)
        issue_and_serve(svc, db, project, site)
        clock.advance(seconds=10)
        run(svc.check(project))
        state = svc.state(project)
        assert state.fresh_days == 7
        assert state.fresh_until - state.verified_at.replace(tzinfo=UTC) == timedelta(days=7)
        clock.advance(days=8)
        with pytest.raises(DomainNotVerified):
            svc.require_fresh(project)


def test_scope_change_invalidates_confirmation(env):
    with env() as db:
        project, site, clock = make_project(db, "/blog"), FakeSite(), Clock()
        svc = service(db, site, clock)
        issue_and_serve(svc, db, project, site)
        clock.advance(seconds=10)
        run(svc.check(project))

        project.origin = "https://other.example"  # смена домена
        db.commit()
        with pytest.raises(DomainNotVerified) as caught:
            svc.require_fresh(project)
        assert caught.value.reason == "scope_changed"
        assert svc.state(project).state == "unverified"


def test_project_without_scope_cannot_pass_the_gate(env):
    with env() as db:
        project = make_project(db)
        project.origin = project.path_prefix = None
        db.commit()
        with pytest.raises(ScopeInvalid):
            service(db, FakeSite(), Clock()).require_fresh(project)
        state = service(db, FakeSite(), Clock()).state(project)
        assert state.state == "unverified" and state.last_status == "scope_unresolved"
        with pytest.raises(ScopeInvalid):
            scope_of(project)


# --- отзыв ----------------------------------------------------------------------------------------------------------

def test_revoke_blocks_dependent_operations_immediately(env):
    with env() as db:
        project, site, clock = make_project(db), FakeSite(), Clock()
        svc = service(db, site, clock)
        issue_and_serve(svc, db, project, site)
        clock.advance(seconds=10)
        run(svc.check(project))
        svc.require_fresh(project)

        revoked = svc.revoke(project)
        assert revoked.status == "revoked" and revoked.revoked_at is not None
        with pytest.raises(DomainNotVerified):
            svc.require_fresh(project)
        assert svc.state(project).state == "unverified"
        assert svc.state(project).last_status == "revoked"


def test_revoke_cancels_a_pending_challenge_too(env):
    with env() as db:
        project, site, clock = make_project(db), FakeSite(), Clock()
        svc = service(db, site, clock)
        issue_and_serve(svc, db, project, site)
        assert svc.revoke(project).status == "revoked"
        with pytest.raises(NoPendingChallenge):
            run(svc.check(project))


def test_revoke_with_nothing_active(env):
    with env() as db:
        with pytest.raises(NothingToRevoke):
            service(db, FakeSite(), Clock()).revoke(make_project(db))


def test_revoked_confirmation_cannot_be_resurrected_by_a_late_check(env):
    """Проверка файла началась до отзыва и закончилась после: подтверждение не появляется."""
    with env() as db:
        project, clock = make_project(db), Clock()
        holder = {}

        async def revoking_fetch(url):
            svc.revoke(project)  # отзыв, пока идёт сетевой запрос
            return FetchResult(url, url, 200, {"content-type": "text/plain"},
                               core.expected_body(holder["token"]).encode(), [], None, 0.0)

        svc = DomainVerificationService(db, revoking_fetch, VerificationConfig(), clock)
        holder["token"] = svc.issue_challenge(project, ALICE).token
        clock.advance(seconds=10)

        outcome = run(svc.check(project))

        assert not outcome.verified and outcome.error_code == "challenge_no_longer_pending"
        assert outcome.authorization.status == "revoked"
        with pytest.raises(DomainNotVerified):
            svc.require_fresh(project)


# --- параллелизм --------------------------------------------------------------------------------------------------------

def test_confirmation_finished_by_another_session_during_the_fetch_is_not_duplicated(env):
    """Сессия 2 ждёт ответ сайта, пока сессия 1 завершает ту же проверку: подтверждение одно."""
    with env() as db1, env() as db2:
        project1 = make_project(db1)
        project2 = db2.get(Project, project1.id)
        site, clock = FakeSite(), Clock()
        config = dict(min_check_interval=timedelta(seconds=0))
        svc1 = service(db1, site, clock, **config)
        issued = svc1.issue_challenge(project1, ALICE)
        site.serve_token(issued.token)
        clock.advance(seconds=10)

        async def fetch_while_other_session_finishes(url):
            assert (await svc1.check(project1)).verified
            return await site(url)

        svc2 = DomainVerificationService(db2, fetch_while_other_session_finishes, VerificationConfig(**config), clock)
        second = run(svc2.check(project2))

        assert second.verified and second.authorization.status == "verified"
    with env() as db:
        verified = db.scalars(select(DomainAuthorization).where(DomainAuthorization.status == "verified")).all()
        assert len(verified) == 1 and verified[0].id == issued.authorization.id


def test_second_check_after_success_reports_that_nothing_is_pending(env):
    with env() as db:
        project, site, clock = make_project(db), FakeSite(), Clock()
        svc = service(db, site, clock, min_check_interval=timedelta(seconds=0))
        issue_and_serve(svc, db, project, site)
        clock.advance(seconds=10)
        assert run(svc.check(project)).verified
        with pytest.raises(NoPendingChallenge):
            run(svc.check(project))
        assert svc.state(project).state == "verified"


def test_open_authorization_uniqueness_is_enforced_by_the_database(env):
    from sqlalchemy.exc import IntegrityError

    with env() as db:
        project, clock = make_project(db), Clock()
        svc = service(db, FakeSite(), clock)
        issued = svc.issue_challenge(project, ALICE)
        duplicate = DomainAuthorization(
            id=str(uuid.uuid4()), project_id=project.id, workspace_id=project.workspace_id, status="pending",
            scope_origin="https://example.com", scope_path_prefix="/", challenge_hash="0" * 64,
            subject_user_id=ALICE, issued_at=clock.now, expires_at=clock.now + timedelta(hours=1), evidence={},
        )
        db.add(duplicate)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        assert db.get(DomainAuthorization, issued.authorization.id).status == "pending"


def test_concurrent_issue_is_reported_as_conflict_not_corruption(env, monkeypatch):
    with env() as db1, env() as db2:
        project1 = make_project(db1)
        project2 = db2.get(Project, project1.id)
        clock = Clock()
        svc1, svc2 = service(db1, FakeSite(), clock), service(db2, FakeSite(), clock)
        svc1.issue_challenge(project1, ALICE)

        # Второй сервис «не увидел» первый challenge (читал до его появления): вставка упирается в индекс.
        monkeypatch.setattr(svc2, "_expire_stale", lambda *a, **k: None)
        original_execute = db2.execute

        def blind_execute(statement, *args, **kwargs):
            if getattr(statement, "is_update", False):
                return original_execute(select(1))  # supersede ничего не нашёл
            return original_execute(statement, *args, **kwargs)

        monkeypatch.setattr(db2, "execute", blind_execute)
        with pytest.raises(ChallengeConflict):
            svc2.issue_challenge(project2, ALICE)
    with env() as db:
        assert len(db.scalars(select(DomainAuthorization).where(DomainAuthorization.status == "pending")).all()) == 1


# --- защита от выхода из области внутри самой проверки -------------------------------------------------------------------------

def test_probe_requests_only_the_scope_challenge_url(env):
    with env() as db:
        for prefix, expected in (("/", "https://example.com/seo-master-challenge.txt"),
                                 ("/a/b", "https://example.com/a/b/seo-master-challenge.txt")):
            project, site, clock = make_project(db, prefix), FakeSite(), Clock()
            svc = service(db, site, clock)
            issue_and_serve(svc, db, project, site)
            clock.advance(seconds=10)
            run(svc.check(project))
            assert site.requests == [expected]


def test_illegal_status_change_is_refused_before_touching_the_database(env):
    with env() as db:
        project, site, clock = make_project(db), FakeSite(), Clock()
        svc = service(db, site, clock)
        issued = issue_and_serve(svc, db, project, site)
        with pytest.raises(InvalidTransition):
            svc._transition(issued.authorization.id, AuthStatus.VERIFIED, AuthStatus.PENDING)
        assert db.get(DomainAuthorization, issued.authorization.id).status == "pending"


# --- редкие пути ------------------------------------------------------------------------------------------------------------------

def test_scope_with_a_malformed_stored_origin_is_unresolved(env):
    with env() as db:
        project = make_project(db)
        project.origin = "not an origin"
        db.commit()
        with pytest.raises(ScopeInvalid) as caught:
            scope_of(project)
        assert caught.value.code == "project_scope_unresolved"


def test_challenge_revoked_between_lookup_and_slot_claim_reports_no_pending(env):
    with env() as db:
        project, site, clock = make_project(db), FakeSite(), Clock()
        svc = service(db, site, clock)
        issue_and_serve(svc, db, project, site)
        clock.advance(seconds=10)
        real_exec = svc._exec_update
        state = {"done": False}

        def revoke_then_claim(statement):
            if not state["done"]:
                state["done"] = True
                svc.revoke(project)  # параллельный отзыв перед захватом слота
            return real_exec(statement)

        svc._exec_update = revoke_then_claim
        with pytest.raises(NoPendingChallenge):
            run(svc.check(project))
        assert site.requests == []


def test_retry_after_without_a_previous_check_is_one_second(env):
    with env() as db:
        project, site, clock = make_project(db), FakeSite(), Clock()
        svc = service(db, site, clock)
        issued = svc.issue_challenge(project, ALICE)
        assert issued.authorization.last_checked_at is None
        assert svc._retry_after(issued.authorization, clock.now) == 1


def test_integrity_error_on_final_commit_resolves_to_the_current_state(env, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    with env() as db:
        project, site, clock = make_project(db), FakeSite(), Clock()
        svc = service(db, site, clock)
        issue_and_serve(svc, db, project, site)
        clock.advance(seconds=10)

        real_commit = db.commit
        calls = {"n": 0}

        def failing_commit():
            calls["n"] += 1
            if calls["n"] == 2:  # первый commit — захват слота, второй — подтверждение
                raise IntegrityError("stmt", {}, Exception("unique"))
            return real_commit()

        monkeypatch.setattr(db, "commit", failing_commit)
        outcome = run(svc.check(project))
        assert not outcome.verified and outcome.error_code == "verification_conflict"
        assert outcome.authorization.status == "pending"  # транзакция откатилась целиком, старое подтверждение не тронуто

        monkeypatch.setattr(db, "commit", real_commit)
        clock.advance(seconds=10)
        assert run(svc.check(project)).verified  # повтор после сбоя проходит
