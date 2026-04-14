"""partition crawl results by project

Revision ID: 20260414_001_crawl_results_partitioning
Revises: 001_initial_migration
Create Date: 2026-04-14 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "20260414_001_crawl_results_partitioning"
down_revision = "001_initial_migration"
branch_labels = None
depends_on = None


def _table_exists(bind, schema_name: str, table_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = :schema_name
                        AND table_name = :table_name
                )
                """
            ),
            {"schema_name": schema_name, "table_name": table_name},
        ).scalar()
    )


def _is_partitioned(bind, schema_name: str, table_name: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_partitioned_table pt
                    JOIN pg_class c ON c.oid = pt.partrelid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = :schema_name
                        AND c.relname = :table_name
                )
                """
            ),
            {"schema_name": schema_name, "table_name": table_name},
        ).scalar()
    )


def _create_partitioned_crawl_results() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_schema.crawl_results (
            audit_id VARCHAR(64) NOT NULL,
            project_id VARCHAR(128) NOT NULL,
            root_url VARCHAR(2048) NOT NULL,
            url_hash VARCHAR(64) GENERATED ALWAYS AS (md5(root_url)) STORED,
            mode VARCHAR(16) NOT NULL,
            site_type_hint VARCHAR(64) NOT NULL DEFAULT 'unknown',
            platform VARCHAR(64) NOT NULL DEFAULT 'generic',
            seeds JSONB NOT NULL DEFAULT '[]'::jsonb,
            status VARCHAR(32) NOT NULL DEFAULT 'queued',
            summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            findings JSONB NOT NULL DEFAULT '[]'::jsonb,
            pages JSONB NOT NULL DEFAULT '[]'::jsonb,
            options JSONB NOT NULL DEFAULT '{}'::jsonb,
            crawled_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
            CONSTRAINT pk_crawl_results PRIMARY KEY (project_id, audit_id)
        ) PARTITION BY HASH (project_id)
        """
    )
    for remainder in range(8):
        op.execute(
            f"""
            CREATE TABLE IF NOT EXISTS audit_schema.crawl_results_p{remainder}
            PARTITION OF audit_schema.crawl_results
            FOR VALUES WITH (MODULUS 8, REMAINDER {remainder})
            """
        )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_crawl_results_project_crawled_at
        ON audit_schema.crawl_results(project_id, crawled_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_crawl_results_url_hash
        ON audit_schema.crawl_results(url_hash)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_crawl_results_mode_status
        ON audit_schema.crawl_results(mode, status)
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS update_crawl_results_updated_at ON audit_schema.crawl_results
        """
    )
    op.execute(
        """
        CREATE TRIGGER update_crawl_results_updated_at
        BEFORE UPDATE ON audit_schema.crawl_results
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()
        """
    )


def upgrade():
    bind = op.get_bind()
    op.execute("CREATE SCHEMA IF NOT EXISTS audit_schema")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ language 'plpgsql'
        """
    )

    if not _table_exists(bind, "audit_schema", "crawl_results"):
        _create_partitioned_crawl_results()
    elif not _is_partitioned(bind, "audit_schema", "crawl_results"):
        op.execute("ALTER TABLE audit_schema.crawl_results ADD COLUMN IF NOT EXISTS crawled_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL")
        op.execute("UPDATE audit_schema.crawl_results SET project_id = 'legacy' WHERE project_id IS NULL OR project_id = ''")
        op.execute("ALTER TABLE audit_schema.crawl_results ALTER COLUMN project_id SET NOT NULL")
        op.execute("ALTER TABLE audit_schema.crawl_results RENAME TO crawl_results_legacy")
        op.execute("DROP INDEX IF EXISTS audit_schema.ix_crawl_results_project_created_at")
        op.execute("DROP INDEX IF EXISTS audit_schema.ix_crawl_results_project_crawled_at")
        op.execute("DROP INDEX IF EXISTS audit_schema.ix_crawl_results_url_hash")
        op.execute("DROP INDEX IF EXISTS audit_schema.ix_crawl_results_mode_status")
        _create_partitioned_crawl_results()
        op.execute(
            """
            INSERT INTO audit_schema.crawl_results (
                audit_id,
                project_id,
                root_url,
                mode,
                site_type_hint,
                platform,
                seeds,
                status,
                summary,
                findings,
                pages,
                options,
                crawled_at,
                created_at,
                updated_at
            )
            SELECT
                audit_id,
                project_id,
                root_url,
                mode,
                site_type_hint,
                platform,
                seeds,
                status,
                summary,
                findings,
                pages,
                options,
                COALESCE(crawled_at, created_at, NOW()),
                created_at,
                updated_at
            FROM audit_schema.crawl_results_legacy
            """
        )
        op.execute("DROP TABLE audit_schema.crawl_results_legacy CASCADE")
    else:
        _create_partitioned_crawl_results()

    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_schema.cleanup_old_crawl_data(
            retention_days INTEGER DEFAULT 30
        )
        RETURNS INTEGER AS $$
        DECLARE
            purged_count INTEGER;
        BEGIN
            UPDATE audit_schema.crawl_results
            SET
                pages = '[]'::jsonb,
                summary = jsonb_set(
                    COALESCE(summary, '{}'::jsonb),
                    '{raw_retention}',
                    jsonb_build_object(
                        'raw_purged_at', NOW(),
                        'raw_retention_days', retention_days
                    ),
                    true
                ),
                updated_at = NOW()
            WHERE crawled_at < NOW() - (retention_days || ' days')::INTERVAL
                AND status = 'completed'
                AND jsonb_array_length(COALESCE(pages, '[]'::jsonb)) > 0;

            GET DIAGNOSTICS purged_count = ROW_COUNT;
            RETURN purged_count;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_schema.cleanup_old_crawl_aggregates(
            retention_days INTEGER DEFAULT 365
        )
        RETURNS INTEGER AS $$
        DECLARE
            deleted_count INTEGER;
        BEGIN
            DELETE FROM audit_schema.crawl_results
            WHERE crawled_at < NOW() - (retention_days || ' days')::INTERVAL
                AND status = 'completed';

            GET DIAGNOSTICS deleted_count = ROW_COUNT;
            RETURN deleted_count;
        END;
        $$ LANGUAGE plpgsql
        """
    )


def downgrade():
    pass
