"""AL-5 на настоящем PostgreSQL: честные баллы, неизменяемые версии, откат миграции, параллельная запись.

Запуск (нужен PostgreSQL с правом создавать БД и пакеты psycopg2, asyncpg, alembic):

    TEST_PG_ADMIN_URL=postgresql://user:pass@127.0.0.1:5432/postgres pytest tests/integration/test_semantic_postgres.py

Без переменной тесты пропускаются. Каждый тест работает в собственной временной БД.
"""
import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

sa = pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg2")
pytest.importorskip("alembic")
pytest.importorskip("asyncpg")

ADMIN_URL = os.environ.get("TEST_PG_ADMIN_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_PG_ADMIN_URL is not set")

ROOT = Path(__file__).resolve().parents[2]
PREVIOUS = "20261103_001_workspaces"
HEAD = "20261110_001_legacy_map_unproven"
PROJECT = "11111111-1111-4111-8111-111111111111"
OTHER_PROJECT = "22222222-2222-4222-8222-222222222222"


ALEMBIC_RUNNER = (
    "import os, sys\n"
    "from alembic import command\n"
    "from alembic.config import Config\n"
    "cfg = Config('alembic.ini')\n"
    "cfg.set_main_option('sqlalchemy.url', os.environ['SEO_TEST_DSN'].replace('%', '%%'))\n"
    "getattr(command, sys.argv[1])(cfg, *sys.argv[2:])\n"
)


class TempDatabase:
    def __init__(self):
        self.name = "al5_" + uuid.uuid4().hex[:12]
        base = sa.engine.make_url(ADMIN_URL)
        self.url = base.set(database=self.name).render_as_string(hide_password=False)
        self.async_url = base.set(drivername="postgresql+asyncpg", database=self.name).render_as_string(hide_password=False)

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


@pytest.fixture()
def migrated(db):
    db.alembic("upgrade", "head")
    return db


def insert_ff(db, score_id, score, status):
    db.run(
        "INSERT INTO semantic_schema.ff_scores (score_id, root_url, ff_score, status, formula_version) VALUES (:i, 'https://a.test/', :s, :st, 'ff_score_v1')",
        i=score_id, s=score, st=status,
    )


def insert_eeat(db, score_id, score, status):
    db.run(
        "INSERT INTO semantic_schema.eeat_scores (score_id, root_url, score, status, formula_version) VALUES (:i, 'https://a.test/', :s, :st, 'eeat_v1')",
        i=score_id, s=score, st=status,
    )


# --- баллы -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("insert", [insert_ff, insert_eeat])
def test_complete_score_is_stored_and_null_requires_insufficient_status(migrated, insert):
    insert(migrated, "ok", 81.5, "complete")
    insert(migrated, "boundary-low", 0, "complete")
    insert(migrated, "boundary-high", 100, "complete")
    insert(migrated, "honest-null", None, "insufficient_data")


@pytest.mark.parametrize("insert", [insert_ff, insert_eeat])
@pytest.mark.parametrize(
    "score,status",
    [
        (None, "complete"),               # пустой балл без статуса «недостаточно данных»
        (None, "legacy_estimated"),
        (50.0, "insufficient_data"),      # число при статусе «недостаточно данных»
        (-0.01, "complete"),
        (100.01, "complete"),
        (float("nan"), "complete"),       # NaN не проходит диапазон
        (float("inf"), "complete"),
        (10.0, "unknown_status"),
    ],
)
def test_invalid_scores_are_rejected_by_the_database(migrated, insert, score, status):
    with pytest.raises(sa.exc.IntegrityError):
        insert(migrated, "bad", score, status)


# --- миграция унаследованных данных ----------------------------------------------------------------------------------

def test_legacy_rows_are_marked_as_estimated_not_complete(db):
    db.alembic("upgrade", PREVIOUS)
    db.run(
        "INSERT INTO semantic_schema.ff_scores (score_id, root_url, ff_score) VALUES ('old-ff', 'https://a.test/', 67.5);"
        "INSERT INTO semantic_schema.eeat_scores (score_id, root_url, score) VALUES ('old-eeat', 'https://a.test/', 40.97)"
    )
    db.alembic("upgrade", "head")
    assert db.rows("SELECT ff_score, status, formula_version FROM semantic_schema.ff_scores") == [(67.5, "legacy_estimated", "legacy")]
    assert db.rows("SELECT score, status, formula_version FROM semantic_schema.eeat_scores") == [(40.97, "legacy_estimated", "legacy")]


def test_downgrade_restores_the_previous_schema_and_keeps_numbers(db):
    db.alembic("upgrade", PREVIOUS)
    db.run("INSERT INTO semantic_schema.ff_scores (score_id, root_url, ff_score) VALUES ('old-ff', 'https://a.test/', 67.5)")
    db.alembic("upgrade", "head")
    db.alembic("downgrade", PREVIOUS)
    assert db.rows("SELECT ff_score FROM semantic_schema.ff_scores") == [(67.5,)]
    assert db.scalar("SELECT to_regclass('semantic_schema.recommendation_versions') IS NULL")
    assert db.scalar("SELECT is_nullable FROM information_schema.columns WHERE table_schema='semantic_schema' AND table_name='ff_scores' AND column_name='ff_score'") == "NO"
    assert db.scalar("SELECT count(*) FROM information_schema.columns WHERE table_schema='semantic_schema' AND table_name='ff_scores' AND column_name IN ('status','formula_version')") == 0
    assert db.scalar("SELECT to_regprocedure('semantic_schema.forbid_recommendation_version_update()') IS NULL")
    db.alembic("upgrade", "head")  # повторный накат после отката


@pytest.mark.parametrize("insert,table", [(insert_ff, "ff_scores"), (insert_eeat, "eeat_scores")])
def test_downgrade_refuses_when_honest_null_scores_exist(migrated, insert, table):
    insert(migrated, "honest-null", None, "insufficient_data")
    result = migrated.alembic("downgrade", PREVIOUS, check=False)
    assert result.returncode != 0 and "NULL" in (result.stderr + result.stdout)
    # отказ не оставил схему наполовину изменённой
    assert migrated.scalar("SELECT version_num FROM public.alembic_version") == HEAD
    assert migrated.scalar(f"SELECT count(*) FROM semantic_schema.{table} WHERE status = 'insufficient_data'") == 1
    assert migrated.scalar("SELECT to_regclass('semantic_schema.recommendation_versions') IS NOT NULL")


def test_downgrade_refuses_while_versions_are_stored(migrated):
    insert_version(migrated, "a1", "b1", 1, None)
    result = migrated.alembic("downgrade", PREVIOUS, check=False)
    assert result.returncode != 0 and "stored versions" in (result.stderr + result.stdout)
    assert migrated.scalar("SELECT count(*) FROM semantic_schema.recommendation_versions") == 1


# --- версии предложений ---------------------------------------------------------------------------------------------

def insert_version(db, version_id, recommendation_id, version, previous, *, project=PROJECT, url="https://a.test/", operation="title", mode="managed", content_hash="a" * 64):
    db.run(
        """
        INSERT INTO semantic_schema.recommendation_versions
            (version_id, recommendation_id, version, previous_version_id, project_id, target_url, target_origin,
             operation_type, execution_mode, content_hash, schema_version, payload)
        VALUES (:vid, :rid, :v, :prev, :p, :u, 'https://a.test', :op, :mode, :h, 'recommendation_version/1', CAST('{}' AS jsonb))
        """,
        vid=version_id, rid=recommendation_id, v=version, prev=previous, p=project, u=url, op=operation, mode=mode, h=content_hash,
    )


def test_versions_are_append_only(migrated):
    insert_version(migrated, "v1", "r1", 1, None)
    with pytest.raises(sa.exc.DBAPIError) as failure:
        migrated.run("UPDATE semantic_schema.recommendation_versions SET operation_type = 'h1' WHERE version_id = 'v1'")
    assert "immutable" in str(failure.value)
    with pytest.raises(sa.exc.DBAPIError):
        migrated.run("UPDATE semantic_schema.recommendation_versions SET payload = CAST('{\"x\": 1}' AS jsonb)")
    assert migrated.scalar("SELECT operation_type FROM semantic_schema.recommendation_versions WHERE version_id = 'v1'") == "title"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"version": 0, "previous": None},
        {"version": 1, "previous": "v0"},      # первая версия не может иметь предшественника
        {"version": 2, "previous": None},      # следующая обязана иметь предшественника
        {"version": 1, "previous": None, "operation": "rewrite_everything"},
        {"version": 1, "previous": None, "mode": "auto"},
        {"version": 1, "previous": None, "content_hash": "short"},
    ],
)
def test_malformed_versions_are_rejected(migrated, kwargs):
    version = kwargs.pop("version")
    previous = kwargs.pop("previous")
    with pytest.raises(sa.exc.IntegrityError):
        insert_version(migrated, "bad", "r-bad", version, previous, **kwargs)


def test_version_number_and_first_version_are_unique(migrated):
    insert_version(migrated, "v1", "r1", 1, None)
    insert_version(migrated, "v2", "r1", 2, "v1")
    with pytest.raises(sa.exc.IntegrityError):
        insert_version(migrated, "v2-dup", "r1", 2, "v1")           # та же пара (recommendation_id, version)
    with pytest.raises(sa.exc.IntegrityError):
        insert_version(migrated, "v1-race", "r-other", 1, None)     # вторая «первая» версия для той же цели
    # другая цель, другой тип операции и другой проект не мешают
    insert_version(migrated, "w1", "r2", 1, None, url="https://a.test/other")
    insert_version(migrated, "w2", "r3", 1, None, operation="description")
    insert_version(migrated, "w3", "r4", 1, None, project=OTHER_PROJECT)


# --- модели соответствуют схеме ------------------------------------------------------------------------------------

def test_both_orm_models_match_the_migrated_schema(migrated):
    sys.path.insert(0, str(ROOT))
    from config.database_config import Base
    import database.models  # noqa: F401
    from services.semantic_service.db.models import Base as SemanticBase

    for metadata in (Base.metadata, SemanticBase.metadata):
        for name in ("ff_scores", "eeat_scores", "recommendation_versions"):
            table = metadata.tables[f"semantic_schema.{name}"]
            actual = {
                r[0]: r[1]
                for r in migrated.rows(
                    "SELECT column_name, is_nullable FROM information_schema.columns WHERE table_schema = 'semantic_schema' AND table_name = :t", t=name
                )
            }
            assert {c.name for c in table.columns} == set(actual), name
            for column in table.columns:
                assert (actual[column.name] == "YES") == bool(column.nullable), f"{name}.{column.name}"
            constraints = {c.name for c in table.constraints if getattr(c, "name", None)}
            existing = {r[0] for r in migrated.rows("SELECT conname FROM pg_constraint WHERE conrelid = CAST(:t AS regclass)", t=f"semantic_schema.{name}")}
            assert constraints <= existing, (name, constraints - existing)
            indexes = {i.name for i in table.indexes}
            present = {r[0] for r in migrated.rows("SELECT indexname FROM pg_indexes WHERE schemaname = 'semantic_schema' AND tablename = :t", t=name)}
            assert indexes <= present, (name, indexes - present)


def test_init_schema_sql_matches_the_migrated_semantic_schema(db):
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
            "SELECT table_name || '.' || column_name || ':' || data_type || ':' || is_nullable || ':' || coalesce(character_maximum_length::text, '') "
            "FROM information_schema.columns WHERE table_schema = 'semantic_schema' AND table_name IN ('ff_scores', 'eeat_scores', 'recommendation_versions') ORDER BY 1"
        )
        assert fresh.rows(columns) == db.rows(columns)
        constraints = (
            "SELECT conrelid::regclass || ':' || conname || ':' || pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid IN ('semantic_schema.ff_scores'::regclass, 'semantic_schema.eeat_scores'::regclass, 'semantic_schema.recommendation_versions'::regclass) ORDER BY 1"
        )
        assert fresh.rows(constraints) == db.rows(constraints)
        indexes = (
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'semantic_schema' "
            "AND tablename = 'recommendation_versions' ORDER BY 1"
        )
        # Индексы ff_scores/eeat_scores в init_schema.sql (created_at DESC) расходятся с миграциями с самого начала
        # и AL-5 их не менял, поэтому сравниваются только индексы новой таблицы.
        assert fresh.rows(indexes) == db.rows(indexes)
        # триггер неизменяемости есть и при свежей установке
        fresh.run("INSERT INTO semantic_schema.ff_scores (score_id, root_url, ff_score, status, formula_version) VALUES ('x', 'https://a.test/', NULL, 'insufficient_data', 'ff_score_v1')")
        insert_version(fresh, "v1", "r1", 1, None)
        with pytest.raises(sa.exc.DBAPIError):
            fresh.run("UPDATE semantic_schema.recommendation_versions SET task_id = 't'")


# --- SqlProposalStore -------------------------------------------------------------------------------------------------

def build_version(*, recommendation_id, version, previous, after="Купить окна в Москве недорого", version_id=None):
    from services.semantic_service.proposals.model import (
        Change, ChangeMode, EvidenceRef, ExecutionMode, FieldProvenance, OperationType,
        RecommendationVersion, Target, compute_content_hash,
    )
    from services.semantic_service.proposals.derive import derive_expected_checks, derive_preconditions

    changes = (Change(path="head.title", mode=ChangeMode.SET, before_state="missing", after=after, cms_field="title"),)
    evidence = (EvidenceRef(evidence_id="audit-1", sha256="c" * 64),)
    target = Target(origin="https://a.test", url="https://a.test/")
    base = dict(
        version_id=version_id or str(uuid.uuid4()), recommendation_id=recommendation_id, version=version, previous_version_id=previous,
        project_id=PROJECT, target=target, evidence=evidence, operation_type=OperationType.TITLE, execution_mode=ExecutionMode.MANAGED,
        changes=changes, preconditions=derive_preconditions(PROJECT, target, evidence, changes),
        expected_checks=derive_expected_checks(target, changes), provider="deterministic", model="facts_v1",
        provenance=(FieldProvenance(path="head.title", source="deterministic", provider="deterministic", model="facts_v1", generated_at="2026-11-04T10:00:00Z"),),
        created_at="2026-11-04T10:00:00Z", content_hash="0" * 64,
    )
    draft = RecommendationVersion.model_construct(**base)
    return RecommendationVersion(**{**base, "content_hash": compute_content_hash(draft.model_dump(mode="json"))})


@pytest.fixture()
def store(migrated):
    sys.path.insert(0, str(ROOT))
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from services.semantic_service.proposals.repository import SqlProposalStore

    engine = create_async_engine(migrated.async_url)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    yield SqlProposalStore(maker)
    asyncio.run(engine.dispose())


def test_store_roundtrip_latest_history_and_listing(store):
    from services.semantic_service.proposals.model import OperationType

    first = build_version(recommendation_id=str(uuid.uuid4()), version=1, previous=None)
    second = build_version(recommendation_id=first.recommendation_id, version=2, previous=first.version_id, after="Пластиковые окна под ключ")

    async def scenario():
        assert await store.latest(PROJECT, "https://a.test/", OperationType.TITLE) is None
        await store.append(first)
        await store.append(second)
        assert await store.get(first.version_id) == first
        assert await store.get(str(uuid.uuid4())) is None
        assert await store.latest(PROJECT, "https://a.test/", OperationType.TITLE) == second
        assert await store.latest(OTHER_PROJECT, "https://a.test/", OperationType.TITLE) is None
        assert await store.history(first.recommendation_id) == [first, second]
        assert {v.version_id for v in await store.list_for_project(PROJECT)} == {first.version_id, second.version_id}
        assert await store.list_for_project(OTHER_PROJECT) == []

    asyncio.run(scenario())


def test_store_reports_conflicts_for_duplicate_number_and_second_first_version(store):
    from services.semantic_service.proposals.store import VersionConflict

    first = build_version(recommendation_id=str(uuid.uuid4()), version=1, previous=None)
    same_number = build_version(recommendation_id=first.recommendation_id, version=1, previous=None, after="Другой заголовок страницы")
    other_recommendation = build_version(recommendation_id=str(uuid.uuid4()), version=1, previous=None, after="Ещё один заголовок")

    async def scenario():
        await store.append(first)
        with pytest.raises(VersionConflict):
            await store.append(first)           # тот же version_id
        with pytest.raises(VersionConflict):
            await store.append(same_number)     # та же пара (recommendation_id, version)
        with pytest.raises(VersionConflict):
            await store.append(other_recommendation)  # вторая первая версия той же цели

    asyncio.run(scenario())


def test_store_does_not_hide_other_integrity_errors(store):
    from sqlalchemy.exc import IntegrityError

    broken = build_version(recommendation_id=str(uuid.uuid4()), version=1, previous=None)
    object.__setattr__(broken, "version", 2)  # версия 2 без предшественника нарушает CHECK, это не гонка

    async def scenario():
        with pytest.raises(IntegrityError):
            await store.append(broken)

    asyncio.run(scenario())


def test_store_refuses_a_row_whose_payload_was_tampered_with(store, migrated):
    from services.semantic_service.proposals.repository import CorruptedVersion

    version = build_version(recommendation_id=str(uuid.uuid4()), version=1, previous=None)

    async def scenario():
        await store.append(version)
        # триггер запрещает UPDATE, поэтому «порчу» вносит администратор, отключив его
        migrated.run("ALTER TABLE semantic_schema.recommendation_versions DISABLE TRIGGER trg_recommendation_versions_immutable")
        migrated.run(
            "UPDATE semantic_schema.recommendation_versions SET payload = jsonb_set(payload, '{changes,0,after}', '\"Подмена\"') WHERE version_id = :v",
            v=version.version_id,
        )
        with pytest.raises(CorruptedVersion):
            await store.get(version.version_id)
        migrated.run("UPDATE semantic_schema.recommendation_versions SET payload = '{}'::jsonb WHERE version_id = :v", v=version.version_id)
        with pytest.raises(CorruptedVersion):
            await store.latest(PROJECT, "https://a.test/", version.operation_type)

    asyncio.run(scenario())


def test_parallel_generation_creates_exactly_one_version_per_content(store):
    """Параллельные запросы с одним содержимым не создают дублей и не теряют записей."""
    from services.semantic_service.proposals.model import OperationType

    versions = [build_version(recommendation_id=str(uuid.uuid4()), version=1, previous=None, after=f"Заголовок страницы номер {i}") for i in range(8)]

    async def scenario():
        from services.semantic_service.proposals.store import VersionConflict

        results = await asyncio.gather(*(store.append(v) for v in versions), return_exceptions=True)
        winners = [r for r in results if r is None]
        losers = [r for r in results if isinstance(r, VersionConflict)]
        assert len(winners) == 1 and len(losers) == 7
        assert await store.latest(PROJECT, "https://a.test/", OperationType.TITLE) is not None

    asyncio.run(scenario())
