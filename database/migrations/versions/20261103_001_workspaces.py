"""workspaces, project scope, domain authorizations (AL-4)

Revision ID: 20261103_001_workspaces
Revises: 20260414_001_crawl_partitions
Create Date: 2026-11-03 00:00:00.000000

Что делает:
  * вводит личные workspace и членство (роль owner у создателя);
  * переносит каждый существующий проект в личное workspace его владельца;
  * считает каноническую область проекта (origin + path_prefix) и снимает глобальную
    уникальность URL: уникальна область внутри workspace;
  * добавляет DomainAuthorization и таблицу соответствий идентификаторов проектов других сервисов;
  * добавляет claim_token_hash в публичные аудиты.

Унаследованные данные не удаляются:
  * проект с неразбираемым URL или с областью, уже занятой более ранним проектом того же
    владельца, остаётся с пустой областью и статусом `scope_review`;
  * проекты management (`public.projects`, если таблица есть) попадают в таблицу соответствий.
    Автоматически сопоставляется только домен, у которого ровно один канонический проект;
    остальное помечается `unmatched`/`ambiguous` для ручного разбора.

Откат возвращает схему, но не возвращает данные в прежнюю форму: workspace и подтверждения
удаляются. Глобальная уникальность URL восстанавливается, только если дубликатов нет.
"""
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from shared.projects.scope import ScopeError, parse_scope

revision = "20261103_001_workspaces"
down_revision = "20260414_001_crawl_partitions"
branch_labels = None
depends_on = None

LEGACY_SOURCE_MANAGEMENT = "management.projects"


def _table_exists(bind, schema: str, table: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_name = :table)"
            ),
            {"schema": schema, "table": table},
        ).scalar()
    )


def _columns(bind, schema: str, table: str) -> set:
    rows = bind.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :table"
        ),
        {"schema": schema, "table": table},
    )
    return {row[0] for row in rows}


def _create_schema_objects(bind) -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="personal"),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint("kind IN ('personal', 'team')", name="ck_workspaces_kind"),
        schema="audit_schema",
    )
    op.create_index(
        "uq_workspaces_one_personal_per_owner",
        "workspaces",
        ["owner_id"],
        unique=True,
        schema="audit_schema",
        postgresql_where=sa.text("kind = 'personal'"),
    )
    op.create_table(
        "workspace_members",
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("audit_schema.workspaces.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.String(36), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'admin', 'reviewer', 'deployer', 'viewer')", name="ck_workspace_members_role"),
        schema="audit_schema",
    )
    op.create_index("idx_workspace_members_user_id", "workspace_members", ["user_id"], schema="audit_schema")

    # Новые столбцы проекта. workspace_id станет NOT NULL после переноса данных.
    op.execute("ALTER TABLE audit_schema.projects ADD COLUMN IF NOT EXISTS description TEXT")
    op.execute("ALTER TABLE audit_schema.projects ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(36)")
    op.execute("ALTER TABLE audit_schema.projects ADD COLUMN IF NOT EXISTS origin VARCHAR(2048)")
    op.execute("ALTER TABLE audit_schema.projects ADD COLUMN IF NOT EXISTS path_prefix VARCHAR(1024)")

    op.create_table(
        "domain_authorizations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("audit_schema.projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("audit_schema.workspaces.id"), nullable=False),
        sa.Column("method", sa.String(32), nullable=False, server_default="http_challenge"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("scope_origin", sa.String(2048), nullable=False),
        sa.Column("scope_path_prefix", sa.String(1024), nullable=False),
        sa.Column("challenge_hash", sa.String(64), nullable=False),
        sa.Column("subject_user_id", sa.String(36), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("evidence", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint("method IN ('http_challenge')", name="ck_domain_authorizations_method"),
        sa.CheckConstraint(
            "status IN ('pending', 'verified', 'superseded', 'expired', 'failed', 'revoked')",
            name="ck_domain_authorizations_status",
        ),
        schema="audit_schema",
    )
    op.create_index("idx_domain_authorizations_project", "domain_authorizations", ["project_id"], schema="audit_schema")
    op.create_index(
        "uq_domain_authorizations_open",
        "domain_authorizations",
        ["project_id", "status"],
        unique=True,
        schema="audit_schema",
        postgresql_where=sa.text("status IN ('pending', 'verified')"),
    )

    op.create_table(
        "legacy_project_id_map",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("legacy_id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("audit_schema.projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("source", "legacy_id", name="uq_legacy_project_id_map_source_id"),
        sa.CheckConstraint("status IN ('matched', 'unmatched', 'ambiguous')", name="ck_legacy_project_id_map_status"),
        schema="audit_schema",
    )
    op.create_index("idx_legacy_project_id_map_project", "legacy_project_id_map", ["project_id"], schema="audit_schema")

    op.execute("ALTER TABLE audit_schema.public_audit_results ADD COLUMN IF NOT EXISTS claim_token_hash VARCHAR(64)")


def _backfill_workspaces_and_scopes(bind) -> dict:
    stats = {"workspaces": 0, "projects": 0, "scope_review": 0}
    projects = bind.execute(
        sa.text("SELECT id, url, owner_id FROM audit_schema.projects ORDER BY created_at, id")
    ).fetchall()

    workspace_by_owner: dict = {}
    taken_scopes: set = set()
    for project_id, url, owner_id in projects:
        workspace_id = workspace_by_owner.get(owner_id)
        if workspace_id is None:
            workspace_id = str(uuid.uuid4())
            workspace_by_owner[owner_id] = workspace_id
            bind.execute(
                sa.text(
                    "INSERT INTO audit_schema.workspaces (id, name, kind, owner_id) "
                    "VALUES (:id, 'Личное пространство', 'personal', :owner)"
                ),
                {"id": workspace_id, "owner": owner_id},
            )
            bind.execute(
                sa.text(
                    "INSERT INTO audit_schema.workspace_members (workspace_id, user_id, role) "
                    "VALUES (:ws, :owner, 'owner')"
                ),
                {"ws": workspace_id, "owner": owner_id},
            )
            stats["workspaces"] += 1

        origin = path_prefix = None
        status_override = None
        try:
            scope = parse_scope(url)
            key = (workspace_id, scope.origin, scope.path_prefix)
            if key in taken_scopes:
                status_override = "scope_review"
            else:
                taken_scopes.add(key)
                origin, path_prefix = scope.origin, scope.path_prefix
        except ScopeError:
            status_override = "scope_review"

        if status_override:
            stats["scope_review"] += 1
        bind.execute(
            sa.text(
                "UPDATE audit_schema.projects SET workspace_id = :ws, origin = :origin, "
                "path_prefix = :prefix, status = COALESCE(:status, status) WHERE id = :id"
            ),
            {"ws": workspace_id, "origin": origin, "prefix": path_prefix, "status": status_override, "id": project_id},
        )
        stats["projects"] += 1
    return stats


def _map_management_projects(bind) -> dict:
    stats = {"matched": 0, "unmatched": 0, "ambiguous": 0}
    if not _table_exists(bind, "public", "projects"):
        return stats
    columns = _columns(bind, "public", "projects")
    if not {"id", "domain"} <= columns:
        return stats

    hosts: dict = {}
    for project_id, origin in bind.execute(
        sa.text("SELECT id, origin FROM audit_schema.projects WHERE origin IS NOT NULL")
    ):
        try:
            host = parse_scope(origin).host
        except ScopeError:
            continue
        hosts.setdefault(host, []).append(project_id)

    for legacy_id, domain in bind.execute(sa.text("SELECT id::text, domain FROM public.projects")).fetchall():
        try:
            host = parse_scope(f"https://{domain}/").host
        except ScopeError:
            host = None
        candidates = hosts.get(host, []) if host else []
        if len(candidates) == 1:
            status, project_id, reason = "matched", candidates[0], "single canonical project with the same host"
        elif not candidates:
            status, project_id, reason = "unmatched", None, "no canonical project with this host"
        else:
            status, project_id, reason = "ambiguous", None, f"{len(candidates)} canonical projects share this host"
        bind.execute(
            sa.text(
                "INSERT INTO audit_schema.legacy_project_id_map (id, source, legacy_id, project_id, status, reason) "
                "VALUES (:id, :source, :legacy_id, :project_id, :status, :reason) "
                "ON CONFLICT (source, legacy_id) DO NOTHING"
            ),
            {
                "id": str(uuid.uuid4()),
                "source": LEGACY_SOURCE_MANAGEMENT,
                "legacy_id": legacy_id,
                "project_id": project_id,
                "status": status,
                "reason": reason,
            },
        )
        stats[status] += 1
    return stats


def upgrade():
    bind = op.get_bind()
    _create_schema_objects(bind)

    backfill = _backfill_workspaces_and_scopes(bind)
    mapping = _map_management_projects(bind)
    print(f"[AL-4 migration] backfill={backfill} management_map={mapping}")

    op.execute("ALTER TABLE audit_schema.projects ALTER COLUMN workspace_id SET NOT NULL")
    op.execute(
        "ALTER TABLE audit_schema.projects ADD CONSTRAINT fk_projects_workspace "
        "FOREIGN KEY (workspace_id) REFERENCES audit_schema.workspaces (id)"
    )
    op.create_index("idx_projects_workspace_id", "projects", ["workspace_id"], schema="audit_schema")
    op.execute(
        "ALTER TABLE audit_schema.projects ADD CONSTRAINT uq_projects_workspace_scope "
        "UNIQUE (workspace_id, origin, path_prefix)"
    )
    # Домен больше не глобальная блокировка (ТЗ §3.2): уникальность URL заменена областью внутри workspace.
    op.execute("ALTER TABLE audit_schema.projects DROP CONSTRAINT IF EXISTS projects_url_key")


def downgrade():
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text("SELECT COUNT(*) FROM (SELECT url FROM audit_schema.projects GROUP BY url HAVING COUNT(*) > 1) d")
    ).scalar()
    if duplicates:
        raise RuntimeError(
            f"Cannot restore global UNIQUE(url): {duplicates} URL(s) are used by several projects. "
            "Resolve the duplicates before downgrading."
        )

    op.execute("ALTER TABLE audit_schema.projects ADD CONSTRAINT projects_url_key UNIQUE (url)")
    op.execute("ALTER TABLE audit_schema.projects DROP CONSTRAINT IF EXISTS uq_projects_workspace_scope")
    op.drop_index("idx_projects_workspace_id", table_name="projects", schema="audit_schema")
    op.execute("ALTER TABLE audit_schema.projects DROP CONSTRAINT IF EXISTS fk_projects_workspace")
    op.execute("ALTER TABLE audit_schema.public_audit_results DROP COLUMN IF EXISTS claim_token_hash")

    op.drop_index("idx_legacy_project_id_map_project", table_name="legacy_project_id_map", schema="audit_schema")
    op.drop_table("legacy_project_id_map", schema="audit_schema")
    op.drop_index("uq_domain_authorizations_open", table_name="domain_authorizations", schema="audit_schema")
    op.drop_index("idx_domain_authorizations_project", table_name="domain_authorizations", schema="audit_schema")
    op.drop_table("domain_authorizations", schema="audit_schema")

    for column in ("path_prefix", "origin", "workspace_id", "description"):
        op.execute(f"ALTER TABLE audit_schema.projects DROP COLUMN IF EXISTS {column}")

    op.drop_index("idx_workspace_members_user_id", table_name="workspace_members", schema="audit_schema")
    op.drop_table("workspace_members", schema="audit_schema")
    op.drop_index("uq_workspaces_one_personal_per_owner", table_name="workspaces", schema="audit_schema")
    op.drop_table("workspaces", schema="audit_schema")
