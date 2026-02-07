"""initial migration

Revision ID: 001_initial_migration
Revises: 
Create Date: 2026-02-01 21:52:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '001_initial_migration'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "btree_gin"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "btree_gist"')
    
    op.execute('CREATE SCHEMA IF NOT EXISTS audit_schema')
    op.execute('CREATE SCHEMA IF NOT EXISTS semantic_schema')
    op.execute('CREATE SCHEMA IF NOT EXISTS reporting_schema')
    
    op.create_table(
        'projects',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('url', sa.String(2048), nullable=False, unique=True),
        sa.Column('status', sa.String(50), server_default='active'),
        sa.Column('owner_id', sa.String(36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        schema='audit_schema'
    )
    op.create_index('idx_projects_owner_id', 'projects', ['owner_id'], schema='audit_schema')
    op.create_index('idx_projects_status', 'projects', ['status'], schema='audit_schema')
    op.create_index('idx_projects_url', 'projects', ['url'], schema='audit_schema')
    
    op.create_table(
        'crawls',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('project_id', sa.String(36), sa.ForeignKey('audit_schema.projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(50), server_default='pending'),
        sa.Column('pages_crawled', sa.Integer, server_default='0'),
        sa.Column('total_pages', sa.Integer, nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        schema='audit_schema'
    )
    op.create_index('idx_crawl_project_id', 'crawls', ['project_id'], schema='audit_schema')
    op.create_index('idx_crawl_status', 'crawls', ['status'], schema='audit_schema')
    op.create_index('idx_crawl_created_at', 'crawls', ['created_at'], schema='audit_schema')
    
    op.create_table(
        'pages',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('crawl_id', sa.String(36), sa.ForeignKey('audit_schema.crawls.id', ondelete='CASCADE'), nullable=False),
        sa.Column('url', sa.String(2048), nullable=False),
        sa.Column('status_code', sa.Integer, nullable=True),
        sa.Column('title', sa.String(1024), nullable=True),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('h1', sa.String(1024), nullable=True),
        sa.Column('content_length', sa.BigInteger, nullable=True),
        sa.Column('load_time', sa.Float, nullable=True),
        sa.Column('html_content', sa.Text, nullable=True),
        sa.Column('meta_data', postgresql.JSONB, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        schema='audit_schema'
    )
    op.create_index('idx_page_crawl_id', 'pages', ['crawl_id'], schema='audit_schema')
    op.create_index('idx_page_url', 'pages', ['url'], schema='audit_schema')
    op.create_index('idx_page_status_code', 'pages', ['status_code'], schema='audit_schema')
    op.execute('CREATE INDEX idx_page_title_trgm ON audit_schema.pages USING gin(title gin_trgm_ops)')
    op.create_index('idx_page_meta_data', 'pages', ['meta_data'], postgresql_using='gin', schema='audit_schema')
    
    op.create_table(
        'core_web_vitals',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('page_id', sa.String(36), sa.ForeignKey('audit_schema.pages.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('lcp', sa.Float, nullable=True),
        sa.Column('fid', sa.Float, nullable=True),
        sa.Column('cls', sa.Float, nullable=True),
        sa.Column('ttfb', sa.Float, nullable=True),
        sa.Column('fcp', sa.Float, nullable=True),
        sa.Column('overall_score', sa.Float, nullable=True),
        sa.Column('is_good', sa.Boolean, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        schema='audit_schema'
    )
    op.create_index('idx_cwv_page_id', 'core_web_vitals', ['page_id'], schema='audit_schema')
    op.create_index('idx_cwv_is_good', 'core_web_vitals', ['is_good'], schema='audit_schema')
    op.create_index('idx_cwv_overall_score', 'core_web_vitals', ['overall_score'], schema='audit_schema')
    
    op.create_table(
        'schema_validations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('page_id', sa.String(36), sa.ForeignKey('audit_schema.pages.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('has_schema', sa.Boolean, server_default='false'),
        sa.Column('schema_types', postgresql.JSONB, nullable=True),
        sa.Column('validation_errors', postgresql.JSONB, nullable=True),
        sa.Column('is_valid', sa.Boolean, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        schema='audit_schema'
    )
    op.create_index('idx_schema_page_id', 'schema_validations', ['page_id'], schema='audit_schema')
    op.create_index('idx_schema_has_schema', 'schema_validations', ['has_schema'], schema='audit_schema')
    op.create_index('idx_schema_is_valid', 'schema_validations', ['is_valid'], schema='audit_schema')
    
    op.create_table(
        'backlinks',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('page_id', sa.String(36), sa.ForeignKey('audit_schema.pages.id', ondelete='CASCADE'), nullable=False),
        sa.Column('source_url', sa.String(2048), nullable=False),
        sa.Column('anchor_text', sa.Text, nullable=True),
        sa.Column('link_type', sa.String(50), nullable=True),
        sa.Column('discovered_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        schema='audit_schema'
    )
    op.create_index('idx_backlink_page_id', 'backlinks', ['page_id'], schema='audit_schema')
    op.create_index('idx_backlink_source_url', 'backlinks', ['source_url'], schema='audit_schema')
    op.create_index('idx_backlink_discovered_at', 'backlinks', ['discovered_at'], schema='audit_schema')
    
    op.create_table(
        'public_audit_results',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('url', sa.String(2048), nullable=False),
        sa.Column('ip_address', sa.String(45), nullable=False),
        sa.Column('results', postgresql.JSONB, nullable=False),
        sa.Column('status', sa.String(50), server_default='completed'),
        sa.Column('is_deleted', sa.Boolean, server_default='false', nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        schema='audit_schema'
    )
    op.create_index('idx_public_audit_created_at', 'public_audit_results', ['created_at'], schema='audit_schema')
    op.create_index('idx_public_audit_deleted', 'public_audit_results', ['is_deleted'], schema='audit_schema')
    op.create_index('idx_public_audit_ip', 'public_audit_results', ['ip_address'], schema='audit_schema')
    
    op.create_table(
        'crawl_events',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('crawl_id', sa.String(36), sa.ForeignKey('audit_schema.crawls.id', ondelete='CASCADE'), nullable=False),
        sa.Column('event_type', sa.String(50), nullable=False),
        sa.Column('event_data', postgresql.JSONB, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        schema='audit_schema'
    )
    op.create_index('idx_crawl_event_crawl_id', 'crawl_events', ['crawl_id'], schema='audit_schema')
    op.create_index('idx_crawl_event_type', 'crawl_events', ['event_type'], schema='audit_schema')
    op.create_index('idx_crawl_event_created_at', 'crawl_events', ['created_at'], schema='audit_schema')
    
    op.create_table(
        'ff_scores',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('project_id', sa.String(36), nullable=False),
        sa.Column('page_id', sa.String(36), nullable=True),
        sa.Column('total_score', sa.Float, nullable=False),
        sa.Column('freshness_score', sa.Float, nullable=False),
        sa.Column('familiarity_score', sa.Float, nullable=False),
        sa.Column('quality_score', sa.Float, nullable=False),
        sa.Column('calculated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()')),
        sa.Column('metadata', postgresql.JSONB, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        schema='semantic_schema'
    )
    op.create_index('idx_ff_score_project_id', 'ff_scores', ['project_id'], schema='semantic_schema')
    op.create_index('idx_ff_score_page_id', 'ff_scores', ['page_id'], schema='semantic_schema')
    op.create_index('idx_ff_score_calculated_at', 'ff_scores', ['calculated_at'], schema='semantic_schema')
    op.create_index('idx_ff_score_total', 'ff_scores', ['total_score'], schema='semantic_schema')
    
    op.create_table(
        'eeat_scores',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('page_id', sa.String(36), nullable=False, unique=True),
        sa.Column('total_score', sa.Float, nullable=False),
        sa.Column('experience_score', sa.Float, nullable=False),
        sa.Column('expertise_score', sa.Float, nullable=False),
        sa.Column('authoritativeness_score', sa.Float, nullable=False),
        sa.Column('trustworthiness_score', sa.Float, nullable=False),
        sa.Column('signals', postgresql.JSONB, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        schema='semantic_schema'
    )
    op.create_index('idx_eeat_score_page_id', 'eeat_scores', ['page_id'], schema='semantic_schema')
    op.create_index('idx_eeat_total_score', 'eeat_scores', ['total_score'], schema='semantic_schema')
    
    op.create_table(
        'content_gaps',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('project_id', sa.String(36), nullable=False),
        sa.Column('page_id', sa.String(36), nullable=True),
        sa.Column('gap_type', sa.String(100), nullable=False),
        sa.Column('missing_keywords', postgresql.JSONB, nullable=True),
        sa.Column('recommendations', sa.Text, nullable=True),
        sa.Column('priority', sa.String(20), server_default='medium'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        schema='semantic_schema'
    )
    op.create_index('idx_content_gap_project_id', 'content_gaps', ['project_id'], schema='semantic_schema')
    op.create_index('idx_content_gap_page_id', 'content_gaps', ['page_id'], schema='semantic_schema')
    op.create_index('idx_content_gap_priority', 'content_gaps', ['priority'], schema='semantic_schema')
    
    op.create_table(
        'llm_generations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('page_id', sa.String(36), nullable=False),
        sa.Column('generation_type', sa.String(50), nullable=False),
        sa.Column('prompt', sa.Text, nullable=False),
        sa.Column('generated_content', sa.Text, nullable=False),
        sa.Column('model_name', sa.String(100), nullable=True),
        sa.Column('tokens_used', sa.Integer, nullable=True),
        sa.Column('cache_hit', sa.Boolean, server_default='false'),
        sa.Column('approved', sa.Boolean, server_default='false'),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        schema='semantic_schema'
    )
    op.create_index('idx_llm_generation_page_id', 'llm_generations', ['page_id'], schema='semantic_schema')
    op.create_index('idx_llm_generation_type', 'llm_generations', ['generation_type'], schema='semantic_schema')
    op.create_index('idx_llm_generation_approved', 'llm_generations', ['approved'], schema='semantic_schema')
    op.create_index('idx_llm_generation_cache_hit', 'llm_generations', ['cache_hit'], schema='semantic_schema')
    
    op.create_table(
        'semantic_events',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('event_type', sa.String(50), nullable=False),
        sa.Column('project_id', sa.String(36), nullable=True),
        sa.Column('event_data', postgresql.JSONB, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        schema='semantic_schema'
    )
    op.create_index('idx_semantic_event_type', 'semantic_events', ['event_type'], schema='semantic_schema')
    op.create_index('idx_semantic_event_project_id', 'semantic_events', ['project_id'], schema='semantic_schema')
    op.create_index('idx_semantic_event_created_at', 'semantic_events', ['created_at'], schema='semantic_schema')
    
    op.execute("""
        CREATE TABLE IF NOT EXISTS reporting_schema.gsc_data (
            id VARCHAR(36) PRIMARY KEY,
            project_id VARCHAR(36) NOT NULL,
            date DATE NOT NULL,
            query VARCHAR(512),
            page VARCHAR(2048),
            clicks INTEGER DEFAULT 0,
            impressions INTEGER DEFAULT 0,
            ctr DOUBLE PRECISION DEFAULT 0.0,
            position DOUBLE PRECISION,
            raw_data JSONB,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
        ) PARTITION BY RANGE (date)
    """)
    op.create_index('idx_gsc_project_id', 'gsc_data', ['project_id'], schema='reporting_schema')
    op.create_index('idx_gsc_date', 'gsc_data', ['date'], schema='reporting_schema')
    op.create_index('idx_gsc_query', 'gsc_data', ['query'], schema='reporting_schema')
    op.create_index('idx_gsc_page', 'gsc_data', ['page'], schema='reporting_schema')
    
    op.execute("""
        CREATE TABLE IF NOT EXISTS reporting_schema.ga4_data (
            id VARCHAR(36) PRIMARY KEY,
            project_id VARCHAR(36) NOT NULL,
            date DATE NOT NULL,
            page_path VARCHAR(2048),
            sessions INTEGER DEFAULT 0,
            users INTEGER DEFAULT 0,
            pageviews INTEGER DEFAULT 0,
            avg_session_duration DOUBLE PRECISION DEFAULT 0.0,
            bounce_rate DOUBLE PRECISION DEFAULT 0.0,
            conversions INTEGER DEFAULT 0,
            revenue DOUBLE PRECISION DEFAULT 0.0,
            raw_data JSONB,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
        ) PARTITION BY RANGE (date)
    """)
    op.create_index('idx_ga4_project_id', 'ga4_data', ['project_id'], schema='reporting_schema')
    op.create_index('idx_ga4_date', 'ga4_data', ['date'], schema='reporting_schema')
    op.create_index('idx_ga4_page_path', 'ga4_data', ['page_path'], schema='reporting_schema')
    
    op.execute("""
        CREATE TABLE IF NOT EXISTS reporting_schema.yandex_webmaster_data (
            id VARCHAR(36) PRIMARY KEY,
            project_id VARCHAR(36) NOT NULL,
            date DATE NOT NULL,
            query VARCHAR(512),
            url VARCHAR(2048),
            shows INTEGER DEFAULT 0,
            clicks INTEGER DEFAULT 0,
            ctr DOUBLE PRECISION DEFAULT 0.0,
            position DOUBLE PRECISION,
            raw_data JSONB,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
        ) PARTITION BY RANGE (date)
    """)
    op.create_index('idx_ym_project_id', 'yandex_webmaster_data', ['project_id'], schema='reporting_schema')
    op.create_index('idx_ym_date', 'yandex_webmaster_data', ['date'], schema='reporting_schema')
    op.create_index('idx_ym_query', 'yandex_webmaster_data', ['query'], schema='reporting_schema')
    
    op.create_table(
        'reports',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('project_id', sa.String(36), nullable=False),
        sa.Column('report_type', sa.String(50), nullable=False),
        sa.Column('period_start', sa.Date, nullable=False),
        sa.Column('period_end', sa.Date, nullable=False),
        sa.Column('file_path', sa.String(512), nullable=True),
        sa.Column('metrics', postgresql.JSONB, nullable=True),
        sa.Column('status', sa.String(50), server_default='generated'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        schema='reporting_schema'
    )
    op.create_index('idx_report_project_id', 'reports', ['project_id'], schema='reporting_schema')
    op.create_index('idx_report_type', 'reports', ['report_type'], schema='reporting_schema')
    op.create_index('idx_report_created_at', 'reports', ['created_at'], schema='reporting_schema')
    
    op.create_table(
        'cost_efficiency',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('project_id', sa.String(36), nullable=False),
        sa.Column('period_start', sa.Date, nullable=False),
        sa.Column('period_end', sa.Date, nullable=False),
        sa.Column('total_cost', sa.Float, server_default='0.0'),
        sa.Column('organic_traffic', sa.Integer, server_default='0'),
        sa.Column('conversions', sa.Integer, server_default='0'),
        sa.Column('revenue', sa.Float, server_default='0.0'),
        sa.Column('cost_per_click', sa.Float, server_default='0.0'),
        sa.Column('roi', sa.Float, server_default='0.0'),
        sa.Column('metrics_data', postgresql.JSONB, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        schema='reporting_schema'
    )
    op.create_index('idx_cost_project_id', 'cost_efficiency', ['project_id'], schema='reporting_schema')
    op.create_index('idx_cost_roi', 'cost_efficiency', ['roi'], schema='reporting_schema')
    
    op.create_table(
        'users',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('email', sa.String(255), nullable=False, unique=True),
        sa.Column('hashed_password', sa.String(255), nullable=False),
        sa.Column('full_name', sa.String(255), nullable=True),
        sa.Column('is_active', sa.Boolean, server_default='true'),
        sa.Column('is_superuser', sa.Boolean, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
    )
    op.create_index('idx_user_email', 'users', ['email'], unique=True)
    op.create_index('idx_user_is_active', 'users', ['is_active'])
    
    op.create_table(
        'changelog',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('entity_id', sa.String(36), nullable=False),
        sa.Column('entity_type', sa.String(100), nullable=False),
        sa.Column('change_type', sa.String(50), nullable=False),
        sa.Column('before_value', postgresql.JSONB, nullable=True),
        sa.Column('after_value', postgresql.JSONB, nullable=True),
        sa.Column('impact_score', sa.Float, nullable=True),
        sa.Column('approved_by', sa.String(36), nullable=True),
        sa.Column('applied', sa.Boolean, server_default='false'),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
    )
    op.create_index('idx_changelog_entity_id', 'changelog', ['entity_id'])
    op.create_index('idx_changelog_type', 'changelog', ['change_type'])
    op.create_index('idx_changelog_applied', 'changelog', ['applied'])
    op.create_index('idx_changelog_created_at', 'changelog', ['created_at'])
    
    op.create_table(
        'domain_events',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('event_type', sa.String(100), nullable=False),
        sa.Column('aggregate_id', sa.String(36), nullable=False),
        sa.Column('event_data', postgresql.JSONB, nullable=False),
        sa.Column('processed', sa.Boolean, server_default='false'),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
    )
    op.create_index('idx_event_type', 'domain_events', ['event_type'])
    op.create_index('idx_event_processed', 'domain_events', ['processed'])
    op.create_index('idx_event_aggregate_id', 'domain_events', ['aggregate_id'])
    op.create_index('idx_event_created_at', 'domain_events', ['created_at'])
    
    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ language 'plpgsql'
    """)
    
    tables = [
        ('audit_schema', 'projects'),
        ('audit_schema', 'crawls'),
        ('audit_schema', 'pages'),
        ('audit_schema', 'core_web_vitals'),
        ('audit_schema', 'schema_validations'),
        ('audit_schema', 'backlinks'),
        ('audit_schema', 'public_audit_results'),
        ('audit_schema', 'crawl_events'),
        ('semantic_schema', 'ff_scores'),
        ('semantic_schema', 'eeat_scores'),
        ('semantic_schema', 'content_gaps'),
        ('semantic_schema', 'llm_generations'),
        ('semantic_schema', 'semantic_events'),
        ('reporting_schema', 'reports'),
        ('reporting_schema', 'cost_efficiency'),
        ('public', 'users'),
        ('public', 'changelog'),
        ('public', 'domain_events'),
    ]
    
    for schema, table in tables:
        trigger_name = f'update_{table}_updated_at'
        if schema != 'public':
            op.execute(f"""
                CREATE TRIGGER {trigger_name}
                BEFORE UPDATE ON {schema}.{table}
                FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()
            """)
        else:
            op.execute(f"""
                CREATE TRIGGER {trigger_name}
                BEFORE UPDATE ON {table}
                FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()
            """)


def downgrade():
    op.execute('DROP SCHEMA IF EXISTS reporting_schema CASCADE')
    op.execute('DROP SCHEMA IF EXISTS semantic_schema CASCADE')
    op.execute('DROP SCHEMA IF EXISTS audit_schema CASCADE')
    
    op.drop_table('domain_events')
    op.drop_table('changelog')
    op.drop_table('users')
    
    op.execute('DROP FUNCTION IF EXISTS update_updated_at_column CASCADE')
    
    op.execute('DROP EXTENSION IF EXISTS "btree_gist"')
    op.execute('DROP EXTENSION IF EXISTS "btree_gin"')
    op.execute('DROP EXTENSION IF EXISTS "pg_trgm"')
    op.execute('DROP EXTENSION IF EXISTS "uuid-ossp"')
