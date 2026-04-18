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
        'project_integrations',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('uuid_generate_v4()')),
        sa.Column('project_id', sa.String(36), sa.ForeignKey('audit_schema.projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('platform', sa.String(32), nullable=False),
        sa.Column('encrypted_creds', sa.Text, nullable=False),
        sa.Column('creds_hint', sa.String(32), nullable=False),
        sa.Column('details', postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('connected_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.UniqueConstraint('project_id', 'platform', name='uq_project_integrations_project_platform'),
        schema='audit_schema'
    )
    op.create_index('idx_project_integrations_project_id', 'project_integrations', ['project_id'], schema='audit_schema')
    op.create_index('idx_project_integrations_platform', 'project_integrations', ['platform'], schema='audit_schema')

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
        'crawl_results',
        sa.Column('audit_id', sa.String(64), nullable=False),
        sa.Column('project_id', sa.String(128), nullable=False),
        sa.Column('root_url', sa.String(2048), nullable=False),
        sa.Column('url_hash', sa.String(64), sa.Computed("md5(root_url)", persisted=True), nullable=False),
        sa.Column('mode', sa.String(16), nullable=False),
        sa.Column('site_type_hint', sa.String(64), server_default='unknown', nullable=False),
        sa.Column('platform', sa.String(64), server_default='generic', nullable=False),
        sa.Column('seeds', postgresql.JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('status', sa.String(32), server_default='queued', nullable=False),
        sa.Column('summary', postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('findings', postgresql.JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('pages', postgresql.JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('options', postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('crawled_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.PrimaryKeyConstraint('project_id', 'audit_id', name='pk_crawl_results'),
        schema='audit_schema',
        postgresql_partition_by='HASH (project_id)',
    )
    for remainder in range(8):
        op.execute(
            f"""
            CREATE TABLE IF NOT EXISTS audit_schema.crawl_results_p{remainder}
            PARTITION OF audit_schema.crawl_results
            FOR VALUES WITH (MODULUS 8, REMAINDER {remainder})
            """
        )
    op.create_index('ix_crawl_results_project_crawled_at', 'crawl_results', ['project_id', 'crawled_at'], schema='audit_schema')
    op.create_index('ix_crawl_results_url_hash', 'crawl_results', ['url_hash'], schema='audit_schema')
    op.create_index('ix_crawl_results_mode_status', 'crawl_results', ['mode', 'status'], schema='audit_schema')

    op.create_table(
        'public_audit_results',
        sa.Column('audit_id', sa.String(64), primary_key=True),
        sa.Column('project_id', sa.String(128), nullable=True),
        sa.Column('root_url', sa.String(2048), nullable=False),
        sa.Column('mode', sa.String(16), server_default='public', nullable=False),
        sa.Column('site_type_hint', sa.String(64), server_default='unknown', nullable=False),
        sa.Column('platform', sa.String(64), server_default='generic', nullable=False),
        sa.Column('seeds', postgresql.JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('status', sa.String(32), server_default='queued', nullable=False),
        sa.Column('summary', postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('findings', postgresql.JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('pages', postgresql.JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('options', postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        schema='audit_schema'
    )
    op.create_index('idx_public_audit_created_at', 'public_audit_results', ['created_at'], schema='audit_schema')
    op.create_index('ix_public_audit_results_mode_status', 'public_audit_results', ['mode', 'status'], schema='audit_schema')
    
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
        sa.Column('score_id', sa.String(64), primary_key=True),
        sa.Column('project_id', sa.String(128), nullable=True),
        sa.Column('root_url', sa.String(2048), nullable=False),
        sa.Column('ff_score', sa.Float, nullable=False),
        sa.Column('components', postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('inputs', postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('thresholds', postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('eeat_score_id', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        schema='semantic_schema'
    )
    op.create_index('idx_ff_score_project_id', 'ff_scores', ['project_id'], schema='semantic_schema')
    op.create_index('idx_ff_score_created_at', 'ff_scores', ['created_at'], schema='semantic_schema')
    op.create_index('idx_ff_score_root_url', 'ff_scores', ['root_url'], schema='semantic_schema')
    
    op.create_table(
        'eeat_scores',
        sa.Column('score_id', sa.String(64), primary_key=True),
        sa.Column('project_id', sa.String(128), nullable=True),
        sa.Column('root_url', sa.String(2048), nullable=False),
        sa.Column('score', sa.Float, nullable=False),
        sa.Column('breakdown', postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('signals', postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        schema='semantic_schema'
    )
    op.create_index('idx_eeat_score_project_id', 'eeat_scores', ['project_id'], schema='semantic_schema')
    op.create_index('idx_eeat_score_created_at', 'eeat_scores', ['created_at'], schema='semantic_schema')
    op.create_index('idx_eeat_root_url', 'eeat_scores', ['root_url'], schema='semantic_schema')

    op.create_table(
        'content_drafts',
        sa.Column('draft_id', sa.String(64), primary_key=True),
        sa.Column('project_id', sa.String(128), nullable=True),
        sa.Column('root_url', sa.String(2048), nullable=False),
        sa.Column('drafts', postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        schema='semantic_schema'
    )
    op.create_index('idx_content_draft_project_id', 'content_drafts', ['project_id'], schema='semantic_schema')
    op.create_index('idx_content_draft_created_at', 'content_drafts', ['created_at'], schema='semantic_schema')

    op.create_table(
        'semantic_analysis',
        sa.Column('analysis_id', sa.String(64), primary_key=True),
        sa.Column('project_id', sa.String(128), nullable=True),
        sa.Column('root_url', sa.String(2048), nullable=False),
        sa.Column('content_gap', postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('semantic_distance', postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('keyword_coverage', postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('inputs', postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        schema='semantic_schema'
    )
    op.create_index('idx_semantic_analysis_project_id', 'semantic_analysis', ['project_id'], schema='semantic_schema')
    op.create_index('idx_semantic_analysis_created_at', 'semantic_analysis', ['created_at'], schema='semantic_schema')
    
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
            id VARCHAR(36) NOT NULL DEFAULT uuid_generate_v4()::text,
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
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
            PRIMARY KEY (date, id)
        ) PARTITION BY RANGE (date)
    """)
    op.execute("CREATE INDEX idx_gsc_project_id ON reporting_schema.gsc_data(project_id)")
    op.execute("CREATE INDEX idx_gsc_date ON reporting_schema.gsc_data(date DESC)")
    op.execute("CREATE INDEX idx_gsc_query ON reporting_schema.gsc_data(query)")
    op.execute("CREATE INDEX idx_gsc_page ON reporting_schema.gsc_data(page)")
    op.execute("CREATE TABLE reporting_schema.gsc_data_2024 PARTITION OF reporting_schema.gsc_data FOR VALUES FROM ('2024-01-01') TO ('2025-01-01')")
    op.execute("CREATE TABLE reporting_schema.gsc_data_2025 PARTITION OF reporting_schema.gsc_data FOR VALUES FROM ('2025-01-01') TO ('2026-01-01')")
    op.execute("CREATE TABLE reporting_schema.gsc_data_2026 PARTITION OF reporting_schema.gsc_data FOR VALUES FROM ('2026-01-01') TO ('2027-01-01')")
    op.execute("CREATE TABLE reporting_schema.gsc_data_2027 PARTITION OF reporting_schema.gsc_data FOR VALUES FROM ('2027-01-01') TO ('2028-01-01')")

    op.execute("""
        CREATE TABLE IF NOT EXISTS reporting_schema.ga4_data (
            id VARCHAR(36) NOT NULL DEFAULT uuid_generate_v4()::text,
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
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
            PRIMARY KEY (date, id)
        ) PARTITION BY RANGE (date)
    """)
    op.execute("CREATE INDEX idx_ga4_project_id ON reporting_schema.ga4_data(project_id)")
    op.execute("CREATE INDEX idx_ga4_date ON reporting_schema.ga4_data(date DESC)")
    op.execute("CREATE INDEX idx_ga4_page_path ON reporting_schema.ga4_data(page_path)")
    op.execute("CREATE TABLE reporting_schema.ga4_data_2024 PARTITION OF reporting_schema.ga4_data FOR VALUES FROM ('2024-01-01') TO ('2025-01-01')")
    op.execute("CREATE TABLE reporting_schema.ga4_data_2025 PARTITION OF reporting_schema.ga4_data FOR VALUES FROM ('2025-01-01') TO ('2026-01-01')")
    op.execute("CREATE TABLE reporting_schema.ga4_data_2026 PARTITION OF reporting_schema.ga4_data FOR VALUES FROM ('2026-01-01') TO ('2027-01-01')")
    op.execute("CREATE TABLE reporting_schema.ga4_data_2027 PARTITION OF reporting_schema.ga4_data FOR VALUES FROM ('2027-01-01') TO ('2028-01-01')")

    op.execute("""
        CREATE TABLE IF NOT EXISTS reporting_schema.yandex_webmaster_data (
            id VARCHAR(36) NOT NULL DEFAULT uuid_generate_v4()::text,
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
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
            PRIMARY KEY (date, id)
        ) PARTITION BY RANGE (date)
    """)
    op.execute("CREATE INDEX idx_ym_project_id ON reporting_schema.yandex_webmaster_data(project_id)")
    op.execute("CREATE INDEX idx_ym_date ON reporting_schema.yandex_webmaster_data(date DESC)")
    op.execute("CREATE INDEX idx_ym_query ON reporting_schema.yandex_webmaster_data(query)")
    op.execute("CREATE TABLE reporting_schema.yandex_webmaster_data_2024 PARTITION OF reporting_schema.yandex_webmaster_data FOR VALUES FROM ('2024-01-01') TO ('2025-01-01')")
    op.execute("CREATE TABLE reporting_schema.yandex_webmaster_data_2025 PARTITION OF reporting_schema.yandex_webmaster_data FOR VALUES FROM ('2025-01-01') TO ('2026-01-01')")
    op.execute("CREATE TABLE reporting_schema.yandex_webmaster_data_2026 PARTITION OF reporting_schema.yandex_webmaster_data FOR VALUES FROM ('2026-01-01') TO ('2027-01-01')")
    op.execute("CREATE TABLE reporting_schema.yandex_webmaster_data_2027 PARTITION OF reporting_schema.yandex_webmaster_data FOR VALUES FROM ('2027-01-01') TO ('2028-01-01')")
    
    op.create_table(
        'reports',
        sa.Column('report_id', sa.String(64), primary_key=True),
        sa.Column('project_id', sa.String(128), nullable=True),
        sa.Column('root_url', sa.String(2048), nullable=False),
        sa.Column('data', postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        schema='reporting_schema'
    )
    op.create_index('idx_report_project_id', 'reports', ['project_id'], schema='reporting_schema')
    op.create_index('idx_report_created_at', 'reports', ['created_at'], schema='reporting_schema')

    op.create_table(
        'metrics_history',
        sa.Column('metric_id', sa.String(64), primary_key=True),
        sa.Column('project_id', sa.String(128), nullable=True),
        sa.Column('root_url', sa.String(2048), nullable=False),
        sa.Column('metrics', postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        schema='reporting_schema'
    )
    op.create_index('idx_metrics_history_project_id', 'metrics_history', ['project_id'], schema='reporting_schema')
    op.create_index('idx_metrics_history_created_at', 'metrics_history', ['created_at'], schema='reporting_schema')
    
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

    op.create_table(
        'processed_events',
        sa.Column('id', sa.String(255), primary_key=True),
        sa.Column('event_id', sa.String(128), nullable=False),
        sa.Column('consumer_name', sa.String(128), nullable=False),
        sa.Column('event_name', sa.String(128), nullable=True),
        sa.Column('routing_key', sa.String(255), nullable=True),
        sa.Column('payload', postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('metadata', postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('processed_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.UniqueConstraint('consumer_name', 'event_id', name='uq_processed_events_consumer_event'),
    )
    op.create_index('idx_processed_events_event_id', 'processed_events', ['event_id'])
    op.create_index('idx_processed_events_consumer', 'processed_events', ['consumer_name'])
    op.create_index('idx_processed_events_processed_at', 'processed_events', ['processed_at'])

    op.create_table(
        'failed_events',
        sa.Column('id', sa.String(255), primary_key=True),
        sa.Column('event_id', sa.String(128), nullable=False),
        sa.Column('consumer_name', sa.String(128), nullable=False),
        sa.Column('event_name', sa.String(128), nullable=True),
        sa.Column('routing_key', sa.String(255), nullable=True),
        sa.Column('payload', postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('error', sa.Text, nullable=False),
        sa.Column('attempt', sa.Integer, server_default='1', nullable=False),
        sa.Column('retry_policy', postgresql.JSONB, server_default=sa.text("'[10, 60, 300]'::jsonb"), nullable=False),
        sa.Column('next_retry_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved', sa.Boolean, server_default='false', nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('failed_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('metadata', postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.UniqueConstraint('consumer_name', 'event_id', name='uq_failed_events_consumer_event'),
    )
    op.create_index('idx_failed_events_event_id', 'failed_events', ['event_id'])
    op.create_index('idx_failed_events_consumer', 'failed_events', ['consumer_name'])
    op.create_index('idx_failed_events_resolved', 'failed_events', ['resolved'])
    op.create_index('idx_failed_events_next_retry_at', 'failed_events', ['next_retry_at'])
    op.create_index('idx_failed_events_failed_at', 'failed_events', ['failed_at'])

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
        ('audit_schema', 'project_integrations'),
        ('audit_schema', 'crawls'),
        ('audit_schema', 'pages'),
        ('audit_schema', 'core_web_vitals'),
        ('audit_schema', 'schema_validations'),
        ('audit_schema', 'backlinks'),
        ('audit_schema', 'crawl_results'),
        ('audit_schema', 'public_audit_results'),
        ('audit_schema', 'crawl_events'),
        ('semantic_schema', 'ff_scores'),
        ('semantic_schema', 'eeat_scores'),
        ('semantic_schema', 'content_drafts'),
        ('semantic_schema', 'semantic_analysis'),
        ('semantic_schema', 'content_gaps'),
        ('semantic_schema', 'llm_generations'),
        ('semantic_schema', 'semantic_events'),
        ('reporting_schema', 'gsc_data'),
        ('reporting_schema', 'ga4_data'),
        ('reporting_schema', 'yandex_webmaster_data'),
        ('reporting_schema', 'reports'),
        ('reporting_schema', 'metrics_history'),
        ('reporting_schema', 'cost_efficiency'),
        ('public', 'users'),
        ('public', 'changelog'),
        ('public', 'domain_events'),
        ('public', 'processed_events'),
        ('public', 'failed_events'),
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

    op.execute("""
        CREATE OR REPLACE VIEW reporting_schema.project_performance AS
        WITH latest_ff_scores AS (
            SELECT DISTINCT ON (project_id)
                project_id,
                root_url,
                ff_score,
                components,
                created_at
            FROM semantic_schema.ff_scores
            WHERE project_id IS NOT NULL
            ORDER BY project_id, created_at DESC
        ),
        latest_audits AS (
            SELECT DISTINCT ON (project_id)
                project_id,
                root_url,
                summary,
                crawled_at
            FROM audit_schema.crawl_results
            WHERE project_id IS NOT NULL
                AND status = 'completed'
            ORDER BY project_id, crawled_at DESC
        ),
        gsc_rollup AS (
            SELECT
                project_id,
                SUM(clicks)::BIGINT AS total_clicks,
                SUM(impressions)::BIGINT AS total_impressions,
                AVG(position)::DOUBLE PRECISION AS avg_position,
                MAX(date) AS latest_gsc_date
            FROM reporting_schema.gsc_data
            GROUP BY project_id
        )
        SELECT
            p.id AS project_id,
            p.name AS project_name,
            p.url,
            ff.ff_score,
            NULLIF(ff.components ->> 'freshness', '')::DOUBLE PRECISION AS freshness_score,
            NULLIF(ff.components ->> 'familiarity', '')::DOUBLE PRECISION AS familiarity_score,
            NULLIF(ff.components ->> 'quality', '')::DOUBLE PRECISION AS quality_score,
            COALESCE(NULLIF(a.summary -> 'coverage' ->> 'processed', '')::INTEGER, 0) AS processed_pages,
            COALESCE(NULLIF(a.summary -> 'coverage' ->> 'attempted', '')::INTEGER, 0) AS attempted_pages,
            NULLIF(a.summary ->> 'score', '')::DOUBLE PRECISION AS latest_audit_score,
            NULLIF(a.summary -> 'cwv' ->> 'LCP_grade', '') AS lcp_grade,
            NULLIF(a.summary -> 'cwv' ->> 'FID_grade', '') AS fid_grade,
            NULLIF(a.summary -> 'cwv' ->> 'CLS_grade', '') AS cls_grade,
            a.crawled_at AS latest_audit_created_at,
            gsc.total_clicks,
            gsc.total_impressions,
            gsc.avg_position,
            gsc.latest_gsc_date,
            p.created_at
        FROM audit_schema.projects p
        LEFT JOIN latest_ff_scores ff ON ff.project_id = p.id
        LEFT JOIN latest_audits a ON a.project_id = p.id
        LEFT JOIN gsc_rollup gsc ON gsc.project_id = p.id
        WHERE p.status = 'active'
    """)

    op.execute("""
        CREATE OR REPLACE VIEW semantic_schema.content_recommendations AS
        WITH latest_eeat_scores AS (
            SELECT DISTINCT ON (COALESCE(project_id, ''), root_url)
                project_id,
                root_url,
                score,
                created_at
            FROM semantic_schema.eeat_scores
            ORDER BY COALESCE(project_id, ''), root_url, created_at DESC
        )
        SELECT
            sa.analysis_id AS id,
            sa.project_id,
            NULL::VARCHAR(36) AS page_id,
            sa.root_url AS url,
            'semantic_gap'::VARCHAR(100) AS gap_type,
            COALESCE(sa.keyword_coverage -> 'missing', '[]'::jsonb) AS missing_keywords,
            COALESCE(
                NULLIF(
                    ARRAY_TO_STRING(
                        ARRAY(
                            SELECT jsonb_array_elements_text(COALESCE(sa.content_gap -> 'suggestions', '[]'::jsonb))
                        ),
                        E'\n'
                    ),
                    ''
                ),
                'No recommendations available'
            ) AS recommendations,
            CASE
                WHEN COALESCE(NULLIF(sa.content_gap ->> 'gap', '')::DOUBLE PRECISION, 0.0) >= 30 THEN 'critical'
                WHEN COALESCE(NULLIF(sa.content_gap ->> 'gap', '')::DOUBLE PRECISION, 0.0) >= 20 THEN 'high'
                WHEN COALESCE(NULLIF(sa.content_gap ->> 'gap', '')::DOUBLE PRECISION, 0.0) >= 10 THEN 'medium'
                ELSE 'low'
            END AS priority,
            ee.score AS eeat_score,
            sa.created_at
        FROM semantic_schema.semantic_analysis sa
        LEFT JOIN latest_eeat_scores ee
            ON ee.root_url = sa.root_url
            AND ee.project_id IS NOT DISTINCT FROM sa.project_id
        WHERE COALESCE(NULLIF(sa.content_gap ->> 'gap', '')::DOUBLE PRECISION, 0.0) > 0
        ORDER BY
            CASE
                WHEN COALESCE(NULLIF(sa.content_gap ->> 'gap', '')::DOUBLE PRECISION, 0.0) >= 30 THEN 1
                WHEN COALESCE(NULLIF(sa.content_gap ->> 'gap', '')::DOUBLE PRECISION, 0.0) >= 20 THEN 2
                WHEN COALESCE(NULLIF(sa.content_gap ->> 'gap', '')::DOUBLE PRECISION, 0.0) >= 10 THEN 3
                ELSE 4
            END,
            sa.created_at DESC
    """)

    op.execute("""
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
    """)

    op.execute("""
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
    """)


def downgrade():
    op.execute('DROP SCHEMA IF EXISTS reporting_schema CASCADE')
    op.execute('DROP SCHEMA IF EXISTS semantic_schema CASCADE')
    op.execute('DROP SCHEMA IF EXISTS audit_schema CASCADE')
    
    op.drop_table('failed_events')
    op.drop_table('processed_events')
    op.drop_table('domain_events')
    op.drop_table('changelog')
    op.drop_table('users')
    
    op.execute('DROP FUNCTION IF EXISTS update_updated_at_column CASCADE')
    
    op.execute('DROP EXTENSION IF EXISTS "btree_gist"')
    op.execute('DROP EXTENSION IF EXISTS "btree_gin"')
    op.execute('DROP EXTENSION IF EXISTS "pg_trgm"')
    op.execute('DROP EXTENSION IF EXISTS "uuid-ossp"')
