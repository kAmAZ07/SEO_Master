"""honest FF-Score / E-E-A-T statuses and immutable recommendation versions (AL-5)

Revision ID: 20261104_001_semantic_al5
Revises: 20261103_001_workspaces
Create Date: 2026-11-04 00:00:00.000000

Что делает:
  * `ff_scores.ff_score` и `eeat_scores.score` становятся nullable: полный балл при нехватке данных
    равен NULL со статусом `insufficient_data` (ТЗ §9.4), а не подставленным числом;
  * добавляет `status` и `formula_version` обеим таблицам. Уже сохранённые строки получают статус
    `legacy_estimated` и версию `legacy`: они считались с подстановкой оценок (50, 30 дней, 0) вместо
    недостающих данных и не должны выдаваться за полные баллы;
  * CHECK-ограничения: NULL только вместе с `insufficient_data`, диапазон 0…100 (NaN и Infinity не проходят);
  * создаёт `semantic_schema.recommendation_versions` — неизменяемые версии предложений. Триггер
    запрещает UPDATE; пара (recommendation_id, version) уникальна.

Данные не удаляются.

Откат возвращает схему. Он отказывает до любых изменений, если есть строки с NULL-баллом
(NOT NULL восстановить нельзя) или таблица предложений не пуста (откат уничтожил бы историю версий).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261104_001_semantic_al5"
down_revision = "20261103_001_workspaces"
branch_labels = None
depends_on = None

SCHEMA = "semantic_schema"
OPERATIONS = "'title', 'description', 'h1', 'schema_jsonld', 'content_edit', 'internal_link'"


def _score_checks(table: str, column: str) -> list[tuple[str, str]]:
    prefix = "ck_ff_scores" if table == "ff_scores" else "ck_eeat_scores"
    return [
        (f"{prefix}_null_iff_insufficient", f"(status = 'insufficient_data') = ({column} IS NULL)"),
        (f"{prefix}_range", f"{column} IS NULL OR ({column} >= 0 AND {column} <= 100)"),
        (f"{prefix}_status", "status IN ('complete', 'insufficient_data', 'legacy_estimated')"),
    ]


def _upgrade_score_table(table: str, column: str) -> None:
    op.alter_column(table, column, existing_type=sa.Float(), nullable=True, schema=SCHEMA)
    op.add_column(table, sa.Column("status", sa.String(32), nullable=True), schema=SCHEMA)
    op.add_column(table, sa.Column("formula_version", sa.String(32), nullable=True), schema=SCHEMA)
    op.execute(f"UPDATE {SCHEMA}.{table} SET status = 'legacy_estimated', formula_version = 'legacy'")
    op.alter_column(table, "status", existing_type=sa.String(32), nullable=False, schema=SCHEMA)
    op.alter_column(table, "formula_version", existing_type=sa.String(32), nullable=False, schema=SCHEMA)
    for name, condition in _score_checks(table, column):
        op.create_check_constraint(name, table, condition, schema=SCHEMA)


def _downgrade_score_table(table: str, column: str) -> None:
    for name, _ in reversed(_score_checks(table, column)):
        op.drop_constraint(name, table, schema=SCHEMA, type_="check")
    op.drop_column(table, "formula_version", schema=SCHEMA)
    op.drop_column(table, "status", schema=SCHEMA)
    op.alter_column(table, column, existing_type=sa.Float(), nullable=False, schema=SCHEMA)


def upgrade() -> None:
    _upgrade_score_table("ff_scores", "ff_score")
    _upgrade_score_table("eeat_scores", "score")

    op.create_table(
        "recommendation_versions",
        sa.Column("version_id", sa.String(36), primary_key=True),
        sa.Column("recommendation_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("previous_version_id", sa.String(36), nullable=True),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(128), nullable=True),
        sa.Column("target_url", sa.String(2048), nullable=False),
        sa.Column("target_origin", sa.String(300), nullable=False),
        sa.Column("operation_type", sa.String(32), nullable=False),
        sa.Column("execution_mode", sa.String(16), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(40), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("recommendation_id", "version", name="uq_recommendation_versions_version"),
        sa.CheckConstraint("version >= 1", name="ck_recommendation_versions_version"),
        sa.CheckConstraint("(version = 1) = (previous_version_id IS NULL)", name="ck_recommendation_versions_lineage"),
        sa.CheckConstraint(f"operation_type IN ({OPERATIONS})", name="ck_recommendation_versions_operation"),
        sa.CheckConstraint("execution_mode IN ('managed', 'manual')", name="ck_recommendation_versions_mode"),
        sa.CheckConstraint("length(content_hash) = 64", name="ck_recommendation_versions_hash"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_recommendation_versions_latest", "recommendation_versions",
        ["project_id", "target_url", "operation_type", "version"], schema=SCHEMA,
    )
    op.create_index(
        "idx_recommendation_versions_project_created", "recommendation_versions",
        ["project_id", "created_at"], schema=SCHEMA,
    )
    op.create_index("idx_recommendation_versions_hash", "recommendation_versions", ["content_hash"], schema=SCHEMA)
    # Одновременные первые версии для одной цели не создают две «рекомендации»: проигравший получает конфликт.
    op.create_index(
        "uq_recommendation_versions_first", "recommendation_versions",
        ["project_id", "target_url", "operation_type"], unique=True, schema=SCHEMA,
        postgresql_where=sa.text("version = 1"),
    )

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.forbid_recommendation_version_update() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'recommendation_versions rows are immutable' USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_recommendation_versions_immutable BEFORE UPDATE ON {SCHEMA}.recommendation_versions
            FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.forbid_recommendation_version_update()
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    versions = bind.execute(sa.text(f"SELECT count(*) FROM {SCHEMA}.recommendation_versions")).scalar()
    if versions:
        raise RuntimeError(
            f"Cannot drop recommendation_versions: {versions} stored versions would be lost. "
            "Export or remove them explicitly before downgrading."
        )
    for table, column in (("ff_scores", "ff_score"), ("eeat_scores", "score")):
        nulls = bind.execute(sa.text(f"SELECT count(*) FROM {SCHEMA}.{table} WHERE {column} IS NULL")).scalar()
        if nulls:
            raise RuntimeError(
                f"Cannot restore NOT NULL on {SCHEMA}.{table}.{column}: {nulls} rows hold an honest NULL score "
                "(insufficient_data). Remove or recompute them before downgrading."
            )

    op.execute(f"DROP TRIGGER IF EXISTS trg_recommendation_versions_immutable ON {SCHEMA}.recommendation_versions")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.forbid_recommendation_version_update()")
    op.drop_index("uq_recommendation_versions_first", table_name="recommendation_versions", schema=SCHEMA)
    op.drop_index("idx_recommendation_versions_hash", table_name="recommendation_versions", schema=SCHEMA)
    op.drop_index("idx_recommendation_versions_project_created", table_name="recommendation_versions", schema=SCHEMA)
    op.drop_index("idx_recommendation_versions_latest", table_name="recommendation_versions", schema=SCHEMA)
    op.drop_table("recommendation_versions", schema=SCHEMA)

    _downgrade_score_table("eeat_scores", "score")
    _downgrade_score_table("ff_scores", "ff_score")
