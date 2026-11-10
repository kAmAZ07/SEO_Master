"""legacy_project_id_map: совпадение по host не доказывает владельца (AL-4, исправление)

Revision ID: 20261110_001_legacy_map_unproven
Revises: 20261109_001_hitl_barrier
Create Date: 2026-11-10 00:00:00.000000

Проблема. Миграция `20261103_001_workspaces` помечала проект management `matched` и записывала
каноничный `project_id`, если домен совпал ровно с одним проектом Core. В таблице management
`public.projects` нет владельца, поэтому по одному домену нельзя сказать, чей это проект: два
пользователя вправе иметь проект на один сайт (`contracts/projects.md`), а совпадение с единственным
каноническим проектом — лишь то, что второй пока не создан. Читатель карты мог принять такую запись за
подтверждённое соответствие и перенести задачи и approvals к чужому проекту.

Что делает.
  * `matched` → `candidate`: записи сохраняют предложенный проект в `candidate_project_id`, а
    `project_id` остаётся NULL. Колонка `project_id` зарезервирована для подтверждённого соответствия;
    статуса подтверждения пока нет и автоматически он не выставляется;
  * статусы: `candidate`, `unmatched`, `ambiguous`;
  * CHECK `ck_legacy_project_id_map_unproven`: `project_id` пуст, кандидат допустим только у `candidate`.

Данные не удаляются. Откат возвращает прежнее состояние точно: `candidate` → `matched`, кандидат снова
записывается в `project_id`. Таблицу в рабочем режиме никто не пишет, кроме миграций.
"""
import sqlalchemy as sa
from alembic import op

revision = "20261110_001_legacy_map_unproven"
down_revision = "20261109_001_hitl_barrier"
branch_labels = None
depends_on = None

SCHEMA = "audit_schema"
TABLE = "legacy_project_id_map"
STATUS_CHECK = "ck_legacy_project_id_map_status"
UNPROVEN_CHECK = "ck_legacy_project_id_map_unproven"
CANDIDATE_FK = "fk_legacy_project_id_map_candidate"
CANDIDATE_INDEX = "idx_legacy_project_id_map_candidate"
CANDIDATE_REASON = "single canonical project with the same host; owner not proven"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("candidate_project_id", sa.String(36), nullable=True), schema=SCHEMA)
    op.drop_constraint(STATUS_CHECK, TABLE, schema=SCHEMA, type_="check")
    op.execute(
        f"UPDATE {SCHEMA}.{TABLE} SET candidate_project_id = project_id, project_id = NULL, "
        f"status = 'candidate', reason = '{CANDIDATE_REASON}' "
        "WHERE status = 'matched' OR project_id IS NOT NULL"
    )
    op.create_foreign_key(
        CANDIDATE_FK, TABLE, "projects", ["candidate_project_id"], ["id"],
        source_schema=SCHEMA, referent_schema=SCHEMA, ondelete="SET NULL",
    )
    op.create_index(CANDIDATE_INDEX, TABLE, ["candidate_project_id"], schema=SCHEMA)
    op.create_check_constraint(
        STATUS_CHECK, TABLE, "status IN ('candidate', 'unmatched', 'ambiguous')", schema=SCHEMA
    )
    op.create_check_constraint(
        UNPROVEN_CHECK, TABLE,
        "project_id IS NULL AND (candidate_project_id IS NULL OR status = 'candidate')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(UNPROVEN_CHECK, TABLE, schema=SCHEMA, type_="check")
    op.drop_constraint(STATUS_CHECK, TABLE, schema=SCHEMA, type_="check")
    op.execute(
        f"UPDATE {SCHEMA}.{TABLE} SET project_id = candidate_project_id, status = 'matched', "
        "reason = 'single canonical project with the same host' WHERE status = 'candidate'"
    )
    op.drop_index(CANDIDATE_INDEX, table_name=TABLE, schema=SCHEMA)
    op.drop_constraint(CANDIDATE_FK, TABLE, schema=SCHEMA, type_="foreignkey")
    op.drop_column(TABLE, "candidate_project_id", schema=SCHEMA)
    op.create_check_constraint(
        STATUS_CHECK, TABLE, "status IN ('matched', 'unmatched', 'ambiguous')", schema=SCHEMA
    )
