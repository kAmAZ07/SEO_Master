"""Миграции на настоящем PostgreSQL: пустая БД → head, унаследованные данные → head, откат (ТЗ §4.4, AC-03).

Запуск требует доступного PostgreSQL с правом создавать БД:

    TEST_PG_ADMIN_URL=postgresql://user:pass@127.0.0.1:5432/postgres pytest tests/integration/test_migrations_postgres.py

Без переменной тесты пропускаются. Каждый тест работает в собственной временной БД.
"""
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

sa = pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg2")
pytest.importorskip("alembic")

ADMIN_URL = os.environ.get("TEST_PG_ADMIN_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_PG_ADMIN_URL is not set")

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "20260414_001_crawl_partitions"
HEAD = "20261109_001_hitl_barrier"
AL4_TABLES = ("workspaces", "workspace_members", "projects", "domain_authorizations", "legacy_project_id_map", "public_audit_results")


ALEMBIC_RUNNER = (
    "import os, sys\n"
    "from alembic import command\n"
    "from alembic.config import Config\n"
    "cfg = Config('alembic.ini')\n"
    "cfg.set_main_option('sqlalchemy.url', os.environ['SEO_TEST_DSN'].replace('%', '%%'))\n"
    "getattr(command, sys.argv[1])(cfg, *sys.argv[2:])\n"
)


class TempDatabase:
    def __init__(self) -> None:
        self.name = "al4_" + uuid.uuid4().hex[:12]
        base = sa.engine.make_url(ADMIN_URL)
        self.url = base.set(database=self.name).render_as_string(hide_password=False)

    def __enter__(self):
        admin = sa.create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(sa.text(f'CREATE DATABASE "{self.name}"'))
        admin.dispose()
        self.engine = sa.create_engine(self.url)
        return self

    def __exit__(self, *exc):
        self.engine.dispose()
        admin = sa.create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{self.name}" WITH (FORCE)'))
        admin.dispose()

    def alembic(self, *args, check=True):
        # После AR-2 env.py не читает DATABASE_URL: DSN передаётся явным sqlalchemy.url (docs/PLATFORM.md).
        env = {**os.environ, "SEO_TEST_DSN": self.url, "PYTHONPATH": str(ROOT)}
        result = subprocess.run(
            [sys.executable, "-c", ALEMBIC_RUNNER, *args], cwd=ROOT / "database", env=env, capture_output=True, text=True, timeout=120
        )
        if check and result.returncode != 0:
            raise AssertionError(f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
        return result

    def scalar(self, sql, **params):
        with self.engine.begin() as conn:
            return conn.execute(sa.text(sql), params).scalar()

    def rows(self, sql, **params):
        with self.engine.begin() as conn:
            return [tuple(r) for r in conn.execute(sa.text(sql), params).fetchall()]

    def run(self, sql, **params):
        with self.engine.begin() as conn:
            conn.execute(sa.text(sql), params)


@pytest.fixture()
def db():
    with TempDatabase() as database:
        yield database


def seed_legacy(db):
    """Данные в схеме до AL-4: глобально уникальный URL, без workspace."""
    db.run(
        """
        INSERT INTO audit_schema.projects (id, name, url, owner_id, created_at) VALUES
        ('p1', 'A1', 'https://Example.com/',         'u1', now() - interval '3 day'),
        ('p2', 'A2', 'https://example.com',          'u1', now() - interval '2 day'),
        ('p3', 'B1', 'https://example.com/',         'u2', now() - interval '1 day'),
        ('p4', 'A3', 'not a url',                    'u1', now()),
        ('p5', 'A4', 'https://blog.site.ru/news/',   'u1', now()),
        ('p6', 'C1', 'https://other.org/',           'u3', now());
        INSERT INTO audit_schema.crawls (id, project_id, status) VALUES ('c1', 'p1', 'completed');
        INSERT INTO audit_schema.public_audit_results (audit_id, root_url, mode, project_id)
            VALUES ('0123abcd-0000-4000-8000-000000000001', 'https://example.com/', 'public', 'p1');
        CREATE TABLE public.projects (id uuid PRIMARY KEY, name varchar(255), domain varchar(255) UNIQUE);
        INSERT INTO public.projects VALUES
            ('11111111-1111-1111-1111-111111111111', 'm1', 'blog.site.ru'),
            ('22222222-2222-2222-2222-222222222222', 'm2', 'example.com'),
            ('33333333-3333-3333-3333-333333333333', 'm3', 'nowhere.io'),
            ('44444444-4444-4444-4444-444444444444', 'm4', 'bad domain!');
        """
    )


# --- пустая БД ------------------------------------------------------------------------------------

def test_empty_database_upgrades_to_head_and_back_to_base(db):
    db.alembic("upgrade", "head")
    assert db.scalar("SELECT version_num FROM public.alembic_version") == HEAD
    for table in ("workspaces", "workspace_members", "domain_authorizations", "legacy_project_id_map"):
        assert db.scalar("SELECT to_regclass(:name) IS NOT NULL", name=f"audit_schema.{table}"), table
    assert db.scalar("SELECT count(*) FROM audit_schema.projects") == 0

    db.alembic("downgrade", "base")
    assert db.scalar("SELECT count(*) FROM public.alembic_version") == 0
    assert not db.scalar("SELECT to_regclass('audit_schema.projects') IS NOT NULL")

    db.alembic("upgrade", "head")  # повторный накат после полного отката
    assert db.scalar("SELECT version_num FROM public.alembic_version") == HEAD


def test_single_head(db):
    heads = db.alembic("heads").stdout
    assert heads.count("(head)") == 1 and HEAD in heads


# --- унаследованные данные -----------------------------------------------------------------------------

def test_legacy_data_survives_the_migration_and_is_assigned_to_personal_workspaces(db):
    db.alembic("upgrade", BASELINE)
    seed_legacy(db)
    db.alembic("upgrade", "head")

    assert db.scalar("SELECT count(*) FROM audit_schema.projects") == 6  # ничего не потеряно
    assert db.scalar("SELECT count(*) FROM audit_schema.crawls WHERE project_id = 'p1'") == 1
    assert db.scalar("SELECT project_id FROM audit_schema.public_audit_results") == "p1"

    owners = db.rows("SELECT owner_id, kind FROM audit_schema.workspaces ORDER BY owner_id")
    assert owners == [("u1", "personal"), ("u2", "personal"), ("u3", "personal")]
    assert db.scalar("SELECT count(*) FROM audit_schema.projects WHERE workspace_id IS NULL") == 0
    members = db.rows("SELECT user_id, role FROM audit_schema.workspace_members ORDER BY user_id")
    assert members == [("u1", "owner"), ("u2", "owner"), ("u3", "owner")]
    # Проект находится в workspace своего владельца и ни в каком другом.
    assert db.scalar(
        "SELECT count(*) FROM audit_schema.projects p JOIN audit_schema.workspaces w ON w.id = p.workspace_id WHERE w.owner_id <> p.owner_id"
    ) == 0


def test_scopes_are_canonical_and_conflicts_are_flagged_not_dropped(db):
    db.alembic("upgrade", BASELINE)
    seed_legacy(db)
    db.alembic("upgrade", "head")

    scopes = {r[0]: r[1:] for r in db.rows("SELECT id, origin, path_prefix, status FROM audit_schema.projects")}
    assert scopes["p1"] == ("https://example.com", "/", "active")
    assert scopes["p2"] == (None, None, "scope_review")  # та же область у того же владельца
    assert scopes["p3"] == ("https://example.com", "/", "active")  # другой пользователь: домен не блокируется
    assert scopes["p4"] == (None, None, "scope_review")  # адрес не разобрать
    assert scopes["p5"] == ("https://blog.site.ru", "/news", "active")


def test_management_projects_are_mapped_without_merging_across_users(db):
    db.alembic("upgrade", BASELINE)
    seed_legacy(db)
    db.alembic("upgrade", "head")

    mapped = {
        r[0]: r[1:]
        for r in db.rows("SELECT legacy_id, status, project_id FROM audit_schema.legacy_project_id_map")
    }
    assert mapped["11111111-1111-1111-1111-111111111111"] == ("matched", "p5")
    assert mapped["22222222-2222-2222-2222-222222222222"] == ("ambiguous", None)  # example.com у двух пользователей
    assert mapped["33333333-3333-3333-3333-333333333333"] == ("unmatched", None)
    assert mapped["44444444-4444-4444-4444-444444444444"] == ("unmatched", None)
    assert db.scalar("SELECT count(*) FROM public.projects") == 4  # таблица management не тронута


def test_migration_without_management_table_still_works(db):
    db.alembic("upgrade", BASELINE)
    db.run("INSERT INTO audit_schema.projects (id, name, url, owner_id) VALUES ('p1', 'A', 'https://a.test/', 'u1')")
    db.alembic("upgrade", "head")
    assert db.scalar("SELECT count(*) FROM audit_schema.legacy_project_id_map") == 0
    assert db.scalar("SELECT count(*) FROM audit_schema.projects") == 1


# --- откат ---------------------------------------------------------------------------------------------

def test_downgrade_restores_global_url_uniqueness_when_possible(db):
    db.alembic("upgrade", BASELINE)
    seed_legacy(db)
    db.alembic("upgrade", "head")
    db.run("DELETE FROM audit_schema.projects WHERE id IN ('p2', 'p3')")  # убрать повторяющиеся адреса

    db.alembic("downgrade", BASELINE)

    assert db.scalar("SELECT version_num FROM public.alembic_version") == BASELINE
    assert db.scalar("SELECT count(*) FROM audit_schema.projects") == 4
    with pytest.raises(sa.exc.IntegrityError):
        db.run("INSERT INTO audit_schema.projects (id, name, url, owner_id) VALUES ('dup', 'x', 'https://other.org/', 'u9')")
    columns = {r[0] for r in db.rows("SELECT column_name FROM information_schema.columns WHERE table_schema = 'audit_schema' AND table_name = 'projects'")}
    assert not columns & {"workspace_id", "origin", "path_prefix", "description"}

    db.alembic("upgrade", "head")  # накат после отката


def test_downgrade_refuses_when_two_projects_share_a_url(db):
    db.alembic("upgrade", "head")
    db.run(
        """
        INSERT INTO audit_schema.workspaces (id, name, kind, owner_id) VALUES ('w1', 'a', 'personal', 'u1'), ('w2', 'b', 'personal', 'u2');
        INSERT INTO audit_schema.projects (id, name, url, owner_id, workspace_id, origin, path_prefix) VALUES
            ('p1', 'A', 'https://same.test/', 'u1', 'w1', 'https://same.test', '/'),
            ('p2', 'B', 'https://same.test/', 'u2', 'w2', 'https://same.test', '/');
        """
    )
    result = db.alembic("downgrade", BASELINE, check=False)
    assert result.returncode != 0 and "Cannot restore global UNIQUE(url)" in result.stderr
    assert db.scalar("SELECT version_num FROM public.alembic_version") == HEAD  # состояние не изменилось
    assert db.scalar("SELECT count(*) FROM audit_schema.projects") == 2
    assert db.scalar("SELECT to_regclass('audit_schema.workspaces') IS NOT NULL")


# --- ограничения целостности в БД -------------------------------------------------------------------------

@pytest.fixture()
def migrated(db):
    db.alembic("upgrade", "head")
    db.run(
        """
        INSERT INTO audit_schema.workspaces (id, name, kind, owner_id) VALUES ('w1', 'a', 'personal', 'u1'), ('w2', 'b', 'personal', 'u2');
        INSERT INTO audit_schema.projects (id, name, url, owner_id, workspace_id, origin, path_prefix)
            VALUES ('p1', 'A', 'https://a.test/', 'u1', 'w1', 'https://a.test', '/');
        """
    )
    return db


def test_same_scope_in_one_workspace_is_unique_but_free_across_workspaces(migrated):
    migrated.run(
        "INSERT INTO audit_schema.projects (id, name, url, owner_id, workspace_id, origin, path_prefix) "
        "VALUES ('p2', 'B', 'https://a.test/', 'u2', 'w2', 'https://a.test', '/')"
    )
    with pytest.raises(sa.exc.IntegrityError):
        migrated.run(
            "INSERT INTO audit_schema.projects (id, name, url, owner_id, workspace_id, origin, path_prefix) "
            "VALUES ('p3', 'C', 'https://a.test/', 'u1', 'w1', 'https://a.test', '/')"
        )


def test_second_personal_workspace_for_one_owner_is_rejected(migrated):
    with pytest.raises(sa.exc.IntegrityError):
        migrated.run("INSERT INTO audit_schema.workspaces (id, name, kind, owner_id) VALUES ('w3', 'again', 'personal', 'u1')")
    migrated.run("INSERT INTO audit_schema.workspaces (id, name, kind, owner_id) VALUES ('w4', 'team', 'team', 'u1')")


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO audit_schema.workspace_members (workspace_id, user_id, role) VALUES ('w1', 'u9', 'root')",
        "INSERT INTO audit_schema.workspaces (id, name, kind, owner_id) VALUES ('w9', 'x', 'galaxy', 'u9')",
        "INSERT INTO audit_schema.domain_authorizations (id, project_id, workspace_id, status, scope_origin, scope_path_prefix, challenge_hash, subject_user_id, issued_at, expires_at) "
        "VALUES ('d1', 'p1', 'w1', 'bogus', 'https://a.test', '/', 'h', 'u1', now(), now())",
        "INSERT INTO audit_schema.domain_authorizations (id, project_id, workspace_id, method, scope_origin, scope_path_prefix, challenge_hash, subject_user_id, issued_at, expires_at) "
        "VALUES ('d1', 'p1', 'w1', 'dns_txt', 'https://a.test', '/', 'h', 'u1', now(), now())",
        "INSERT INTO audit_schema.legacy_project_id_map (id, source, legacy_id, status) VALUES ('l1', 's', '1', 'maybe')",
        "INSERT INTO audit_schema.domain_authorizations (id, project_id, workspace_id, scope_origin, scope_path_prefix, challenge_hash, subject_user_id, issued_at, expires_at) "
        "VALUES ('d1', 'no-such-project', 'w1', 'https://a.test', '/', 'h', 'u1', now(), now())",
    ],
)
def test_invalid_rows_are_rejected_by_the_database(migrated, sql):
    with pytest.raises(sa.exc.IntegrityError):
        migrated.run(sql)


def test_only_one_pending_and_one_verified_authorization_per_project(migrated):
    insert = (
        "INSERT INTO audit_schema.domain_authorizations (id, project_id, workspace_id, status, scope_origin, scope_path_prefix, "
        "challenge_hash, subject_user_id, issued_at, expires_at) VALUES (:id, 'p1', 'w1', :status, 'https://a.test', '/', 'h', 'u1', now(), now())"
    )
    migrated.run(insert, id="d1", status="pending")
    migrated.run(insert, id="d2", status="verified")
    with pytest.raises(sa.exc.IntegrityError):
        migrated.run(insert, id="d3", status="pending")
    with pytest.raises(sa.exc.IntegrityError):
        migrated.run(insert, id="d4", status="verified")
    for index, status in enumerate(("superseded", "superseded", "revoked", "expired")):
        migrated.run(insert, id=f"h{index}", status=status)  # история не ограничена


def test_deleting_a_project_removes_its_authorizations_and_unlinks_the_id_map(migrated):
    migrated.run(
        "INSERT INTO audit_schema.domain_authorizations (id, project_id, workspace_id, scope_origin, scope_path_prefix, challenge_hash, subject_user_id, issued_at, expires_at) "
        "VALUES ('d1', 'p1', 'w1', 'https://a.test', '/', 'h', 'u1', now(), now());"
        "INSERT INTO audit_schema.legacy_project_id_map (id, source, legacy_id, project_id, status) VALUES ('l1', 's', '1', 'p1', 'matched')"
    )
    migrated.run("DELETE FROM audit_schema.projects WHERE id = 'p1'")
    assert migrated.scalar("SELECT count(*) FROM audit_schema.domain_authorizations") == 0
    assert migrated.rows("SELECT project_id, status FROM audit_schema.legacy_project_id_map") == [(None, "matched")]


def test_workspace_with_projects_cannot_be_deleted(migrated):
    with pytest.raises(sa.exc.IntegrityError):
        migrated.run("DELETE FROM audit_schema.workspaces WHERE id = 'w1'")


def test_models_match_the_migrated_schema(migrated):
    """Столбцы ORM-моделей AL-4 совпадают со схемой после миграции."""
    sys.path.insert(0, str(ROOT))
    from config.database_config import Base
    import database.models  # noqa: F401

    for name in AL4_TABLES:
        table = Base.metadata.tables[f"audit_schema.{name}"]
        actual = {r[0] for r in migrated.rows("SELECT column_name FROM information_schema.columns WHERE table_schema = 'audit_schema' AND table_name = :t", t=name)}
        assert {c.name for c in table.columns} <= actual, name


# --- init_schema.sql (свежая установка через docker) ----------------------------------------------------------------

def test_init_schema_sql_matches_the_migrated_schema(db):
    with TempDatabase() as fresh:
        sql = (ROOT / "database" / "init_schema.sql").read_text(encoding="utf-8")
        raw = fresh.engine.raw_connection()
        try:
            with raw.cursor() as cursor:
                cursor.execute(sql)
            raw.commit()
        finally:
            raw.close()
        db.alembic("upgrade", "head")

        columns = (
            "SELECT table_name || '.' || column_name || ':' || data_type || ':' || is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'audit_schema' AND table_name = ANY(:tables) ORDER BY 1"
        )
        assert fresh.rows(columns, tables=list(AL4_TABLES)) == db.rows(columns, tables=list(AL4_TABLES))

        indexes = (
            "SELECT tablename || ':' || regexp_replace(indexdef, ' USING btree', '') FROM pg_indexes "
            "WHERE schemaname = 'audit_schema' AND tablename = ANY(:tables) AND indexname NOT LIKE '%_pkey' ORDER BY 1"
        )
        init_indexes = {r[0].split("ON ", 1)[-1] if False else r[0] for r in fresh.rows(indexes, tables=["workspaces", "workspace_members", "domain_authorizations", "legacy_project_id_map"])}
        migrated_indexes = {r[0] for r in db.rows(indexes, tables=["workspaces", "workspace_members", "domain_authorizations", "legacy_project_id_map"])}
        assert init_indexes == migrated_indexes
