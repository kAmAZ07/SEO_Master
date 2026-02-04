CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "btree_gin";
CREATE EXTENSION IF NOT EXISTS "btree_gist";

CREATE SCHEMA IF NOT EXISTS audit_schema;
CREATE SCHEMA IF NOT EXISTS semantic_schema;
CREATE SCHEMA IF NOT EXISTS reporting_schema;

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TABLE IF NOT EXISTS audit_schema.projects (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    name VARCHAR(255) NOT NULL,
    url VARCHAR(2048) NOT NULL UNIQUE,
    status VARCHAR(50) DEFAULT 'active',
    owner_id VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_projects_owner_id ON audit_schema.projects(owner_id);
CREATE INDEX idx_projects_status ON audit_schema.projects(status);
CREATE INDEX idx_projects_url ON audit_schema.projects(url);

CREATE TRIGGER update_projects_updated_at BEFORE UPDATE ON audit_schema.projects
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS audit_schema.crawls (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    project_id VARCHAR(36) NOT NULL REFERENCES audit_schema.projects(id) ON DELETE CASCADE,
    status VARCHAR(50) DEFAULT 'pending',
    pages_crawled INTEGER DEFAULT 0,
    total_pages INTEGER,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_crawl_project_id ON audit_schema.crawls(project_id);
CREATE INDEX idx_crawl_status ON audit_schema.crawls(status);
CREATE INDEX idx_crawl_created_at ON audit_schema.crawls(created_at DESC);
CREATE INDEX idx_crawl_completed_at ON audit_schema.crawls(completed_at DESC) WHERE completed_at IS NOT NULL;

CREATE TRIGGER update_crawls_updated_at BEFORE UPDATE ON audit_schema.crawls
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS audit_schema.pages (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    crawl_id VARCHAR(36) NOT NULL REFERENCES audit_schema.crawls(id) ON DELETE CASCADE,
    url VARCHAR(2048) NOT NULL,
    status_code INTEGER,
    title VARCHAR(1024),
    description TEXT,
    h1 VARCHAR(1024),
    content_length BIGINT,
    load_time DOUBLE PRECISION,
    html_content TEXT,
    meta_data JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_page_crawl_id ON audit_schema.pages(crawl_id);
CREATE INDEX idx_page_url ON audit_schema.pages(url);
CREATE INDEX idx_page_status_code ON audit_schema.pages(status_code);
CREATE INDEX idx_page_title_trgm ON audit_schema.pages USING gin(title gin_trgm_ops);
CREATE INDEX idx_page_meta_data ON audit_schema.pages USING gin(meta_data);

CREATE TRIGGER update_pages_updated_at BEFORE UPDATE ON audit_schema.pages
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS audit_schema.core_web_vitals (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    page_id VARCHAR(36) NOT NULL UNIQUE REFERENCES audit_schema.pages(id) ON DELETE CASCADE,
    lcp DOUBLE PRECISION,
    fid DOUBLE PRECISION,
    cls DOUBLE PRECISION,
    ttfb DOUBLE PRECISION,
    fcp DOUBLE PRECISION,
    overall_score DOUBLE PRECISION,
    is_good BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_cwv_page_id ON audit_schema.core_web_vitals(page_id);
CREATE INDEX idx_cwv_is_good ON audit_schema.core_web_vitals(is_good);
CREATE INDEX idx_cwv_overall_score ON audit_schema.core_web_vitals(overall_score DESC);

CREATE TRIGGER update_core_web_vitals_updated_at BEFORE UPDATE ON audit_schema.core_web_vitals
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS audit_schema.schema_validations (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    page_id VARCHAR(36) NOT NULL UNIQUE REFERENCES audit_schema.pages(id) ON DELETE CASCADE,
    has_schema BOOLEAN DEFAULT FALSE,
    schema_types JSONB,
    validation_errors JSONB,
    is_valid BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_schema_page_id ON audit_schema.schema_validations(page_id);
CREATE INDEX idx_schema_has_schema ON audit_schema.schema_validations(has_schema);
CREATE INDEX idx_schema_is_valid ON audit_schema.schema_validations(is_valid);

CREATE TRIGGER update_schema_validations_updated_at BEFORE UPDATE ON audit_schema.schema_validations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS audit_schema.backlinks (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    page_id VARCHAR(36) NOT NULL REFERENCES audit_schema.pages(id) ON DELETE CASCADE,
    source_url VARCHAR(2048) NOT NULL,
    anchor_text TEXT,
    link_type VARCHAR(50),
    discovered_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_backlink_page_id ON audit_schema.backlinks(page_id);
CREATE INDEX idx_backlink_source_url ON audit_schema.backlinks(source_url);
CREATE INDEX idx_backlink_discovered_at ON audit_schema.backlinks(discovered_at DESC);

CREATE TRIGGER update_backlinks_updated_at BEFORE UPDATE ON audit_schema.backlinks
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS audit_schema.public_audit_results (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    url VARCHAR(2048) NOT NULL,
    ip_address VARCHAR(45) NOT NULL,
    results JSONB NOT NULL,
    status VARCHAR(50) DEFAULT 'completed',
    is_deleted BOOLEAN DEFAULT FALSE NOT NULL,
    deleted_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_public_audit_created_at ON audit_schema.public_audit_results(created_at DESC);
CREATE INDEX idx_public_audit_deleted ON audit_schema.public_audit_results(is_deleted);
CREATE INDEX idx_public_audit_ip ON audit_schema.public_audit_results(ip_address);

CREATE TRIGGER update_public_audit_results_updated_at BEFORE UPDATE ON audit_schema.public_audit_results
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS audit_schema.crawl_events (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    crawl_id VARCHAR(36) NOT NULL REFERENCES audit_schema.crawls(id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL,
    event_data JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_crawl_event_crawl_id ON audit_schema.crawl_events(crawl_id);
CREATE INDEX idx_crawl_event_type ON audit_schema.crawl_events(event_type);
CREATE INDEX idx_crawl_event_created_at ON audit_schema.crawl_events(created_at DESC);

CREATE TRIGGER update_crawl_events_updated_at BEFORE UPDATE ON audit_schema.crawl_events
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS semantic_schema.ff_scores (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    project_id VARCHAR(36) NOT NULL,
    page_id VARCHAR(36),
    total_score DOUBLE PRECISION NOT NULL,
    freshness_score DOUBLE PRECISION NOT NULL,
    familiarity_score DOUBLE PRECISION NOT NULL,
    quality_score DOUBLE PRECISION NOT NULL,
    calculated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_ff_score_project_id ON semantic_schema.ff_scores(project_id);
CREATE INDEX idx_ff_score_page_id ON semantic_schema.ff_scores(page_id);
CREATE INDEX idx_ff_score_calculated_at ON semantic_schema.ff_scores(calculated_at DESC);
CREATE INDEX idx_ff_score_total ON semantic_schema.ff_scores(total_score DESC);

CREATE TRIGGER update_ff_scores_updated_at BEFORE UPDATE ON semantic_schema.ff_scores
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS semantic_schema.eeat_scores (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    page_id VARCHAR(36) NOT NULL UNIQUE,
    total_score DOUBLE PRECISION NOT NULL,
    experience_score DOUBLE PRECISION NOT NULL,
    expertise_score DOUBLE PRECISION NOT NULL,
    authoritativeness_score DOUBLE PRECISION NOT NULL,
    trustworthiness_score DOUBLE PRECISION NOT NULL,
    signals JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_eeat_score_page_id ON semantic_schema.eeat_scores(page_id);
CREATE INDEX idx_eeat_total_score ON semantic_schema.eeat_scores(total_score DESC);

CREATE TRIGGER update_eeat_scores_updated_at BEFORE UPDATE ON semantic_schema.eeat_scores
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS semantic_schema.content_gaps (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    project_id VARCHAR(36) NOT NULL,
    page_id VARCHAR(36),
    gap_type VARCHAR(100) NOT NULL,
    missing_keywords JSONB,
    recommendations TEXT,
    priority VARCHAR(20) DEFAULT 'medium',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_content_gap_project_id ON semantic_schema.content_gaps(project_id);
CREATE INDEX idx_content_gap_page_id ON semantic_schema.content_gaps(page_id);
CREATE INDEX idx_content_gap_priority ON semantic_schema.content_gaps(priority);

CREATE TRIGGER update_content_gaps_updated_at BEFORE UPDATE ON semantic_schema.content_gaps
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS semantic_schema.llm_generations (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    page_id VARCHAR(36) NOT NULL,
    generation_type VARCHAR(50) NOT NULL,
    prompt TEXT NOT NULL,
    generated_content TEXT NOT NULL,
    model_name VARCHAR(100),
    tokens_used INTEGER,
    cache_hit BOOLEAN DEFAULT FALSE,
    approved BOOLEAN DEFAULT FALSE,
    approved_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_llm_generation_page_id ON semantic_schema.llm_generations(page_id);
CREATE INDEX idx_llm_generation_type ON semantic_schema.llm_generations(generation_type);
CREATE INDEX idx_llm_generation_approved ON semantic_schema.llm_generations(approved);
CREATE INDEX idx_llm_generation_cache_hit ON semantic_schema.llm_generations(cache_hit);

CREATE TRIGGER update_llm_generations_updated_at BEFORE UPDATE ON semantic_schema.llm_generations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS semantic_schema.semantic_events (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    event_type VARCHAR(50) NOT NULL,
    project_id VARCHAR(36),
    event_data JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_semantic_event_type ON semantic_schema.semantic_events(event_type);
CREATE INDEX idx_semantic_event_project_id ON semantic_schema.semantic_events(project_id);
CREATE INDEX idx_semantic_event_created_at ON semantic_schema.semantic_events(created_at DESC);

CREATE TRIGGER update_semantic_events_updated_at BEFORE UPDATE ON semantic_schema.semantic_events
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS reporting_schema.gsc_data (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
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
) PARTITION BY RANGE (date);

CREATE INDEX idx_gsc_project_id ON reporting_schema.gsc_data(project_id);
CREATE INDEX idx_gsc_date ON reporting_schema.gsc_data(date DESC);
CREATE INDEX idx_gsc_query ON reporting_schema.gsc_data(query);
CREATE INDEX idx_gsc_page ON reporting_schema.gsc_data(page);

CREATE TABLE reporting_schema.gsc_data_2024 PARTITION OF reporting_schema.gsc_data
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');

CREATE TABLE reporting_schema.gsc_data_2025 PARTITION OF reporting_schema.gsc_data
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');

CREATE TABLE reporting_schema.gsc_data_2026 PARTITION OF reporting_schema.gsc_data
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');

CREATE TABLE reporting_schema.gsc_data_2027 PARTITION OF reporting_schema.gsc_data
    FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');

CREATE TRIGGER update_gsc_data_updated_at BEFORE UPDATE ON reporting_schema.gsc_data
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS reporting_schema.ga4_data (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
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
) PARTITION BY RANGE (date);

CREATE INDEX idx_ga4_project_id ON reporting_schema.ga4_data(project_id);
CREATE INDEX idx_ga4_date ON reporting_schema.ga4_data(date DESC);
CREATE INDEX idx_ga4_page_path ON reporting_schema.ga4_data(page_path);

CREATE TABLE reporting_schema.ga4_data_2024 PARTITION OF reporting_schema.ga4_data
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');

CREATE TABLE reporting_schema.ga4_data_2025 PARTITION OF reporting_schema.ga4_data
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');

CREATE TABLE reporting_schema.ga4_data_2026 PARTITION OF reporting_schema.ga4_data
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');

CREATE TABLE reporting_schema.ga4_data_2027 PARTITION OF reporting_schema.ga4_data
    FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');

CREATE TRIGGER update_ga4_data_updated_at BEFORE UPDATE ON reporting_schema.ga4_data
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS reporting_schema.yandex_webmaster_data (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
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
) PARTITION BY RANGE (date);

CREATE INDEX idx_ym_project_id ON reporting_schema.yandex_webmaster_data(project_id);
CREATE INDEX idx_ym_date ON reporting_schema.yandex_webmaster_data(date DESC);
CREATE INDEX idx_ym_query ON reporting_schema.yandex_webmaster_data(query);

CREATE TABLE reporting_schema.yandex_webmaster_data_2024 PARTITION OF reporting_schema.yandex_webmaster_data
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');

CREATE TABLE reporting_schema.yandex_webmaster_data_2025 PARTITION OF reporting_schema.yandex_webmaster_data
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');

CREATE TABLE reporting_schema.yandex_webmaster_data_2026 PARTITION OF reporting_schema.yandex_webmaster_data
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');

CREATE TABLE reporting_schema.yandex_webmaster_data_2027 PARTITION OF reporting_schema.yandex_webmaster_data
    FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');

CREATE TRIGGER update_yandex_webmaster_data_updated_at BEFORE UPDATE ON reporting_schema.yandex_webmaster_data
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS reporting_schema.reports (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    project_id VARCHAR(36) NOT NULL,
    report_type VARCHAR(50) NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    file_path VARCHAR(512),
    metrics JSONB,
    status VARCHAR(50) DEFAULT 'generated',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_report_project_id ON reporting_schema.reports(project_id);
CREATE INDEX idx_report_type ON reporting_schema.reports(report_type);
CREATE INDEX idx_report_created_at ON reporting_schema.reports(created_at DESC);

CREATE TRIGGER update_reports_updated_at BEFORE UPDATE ON reporting_schema.reports
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS reporting_schema.cost_efficiency (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    project_id VARCHAR(36) NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    total_cost DOUBLE PRECISION DEFAULT 0.0,
    organic_traffic INTEGER DEFAULT 0,
    conversions INTEGER DEFAULT 0,
    revenue DOUBLE PRECISION DEFAULT 0.0,
    cost_per_click DOUBLE PRECISION DEFAULT 0.0,
    roi DOUBLE PRECISION DEFAULT 0.0,
    metrics_data JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_cost_project_id ON reporting_schema.cost_efficiency(project_id);
CREATE INDEX idx_cost_period ON reporting_schema.cost_efficiency(period_start, period_end);
CREATE INDEX idx_cost_roi ON reporting_schema.cost_efficiency(roi DESC);

CREATE TRIGGER update_cost_efficiency_updated_at BEFORE UPDATE ON reporting_schema.cost_efficiency
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS public.users (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    email VARCHAR(255) NOT NULL UNIQUE,
    hashed_password VARCHAR(255) NOT NULL,
    full_name VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE,
    is_superuser BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

CREATE UNIQUE INDEX idx_user_email ON public.users(email);
CREATE INDEX idx_user_is_active ON public.users(is_active);

CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON public.users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS public.changelog (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    entity_id VARCHAR(36) NOT NULL,
    entity_type VARCHAR(100) NOT NULL,
    change_type VARCHAR(50) NOT NULL,
    before_value JSONB,
    after_value JSONB,
    impact_score DOUBLE PRECISION,
    approved_by VARCHAR(36),
    applied BOOLEAN DEFAULT FALSE,
    applied_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_changelog_entity_id ON public.changelog(entity_id);
CREATE INDEX idx_changelog_type ON public.changelog(change_type);
CREATE INDEX idx_changelog_applied ON public.changelog(applied);
CREATE INDEX idx_changelog_created_at ON public.changelog(created_at DESC);

CREATE TRIGGER update_changelog_updated_at BEFORE UPDATE ON public.changelog
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS public.domain_events (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    event_type VARCHAR(100) NOT NULL,
    aggregate_id VARCHAR(36) NOT NULL,
    event_data JSONB NOT NULL,
    processed BOOLEAN DEFAULT FALSE,
    processed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_event_type ON public.domain_events(event_type);
CREATE INDEX idx_event_processed ON public.domain_events(processed);
CREATE INDEX idx_event_aggregate_id ON public.domain_events(aggregate_id);
CREATE INDEX idx_event_created_at ON public.domain_events(created_at DESC);

CREATE TRIGGER update_domain_events_updated_at BEFORE UPDATE ON public.domain_events
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE OR REPLACE VIEW reporting_schema.project_performance AS
SELECT 
    p.id AS project_id,
    p.name AS project_name,
    p.url,
    f.total_score AS ff_score,
    f.freshness_score,
    f.familiarity_score,
    f.quality_score,
    COUNT(DISTINCT c.id) AS total_crawls,
    COUNT(DISTINCT pg.id) AS total_pages,
    AVG(cwv.overall_score) AS avg_cwv_score,
    SUM(gsc.clicks) AS total_clicks,
    SUM(gsc.impressions) AS total_impressions,
    AVG(gsc.position) AS avg_position,
    p.created_at
FROM audit_schema.projects p
LEFT JOIN semantic_schema.ff_scores f ON f.project_id = p.id
LEFT JOIN audit_schema.crawls c ON c.project_id = p.id
LEFT JOIN audit_schema.pages pg ON pg.crawl_id = c.id
LEFT JOIN audit_schema.core_web_vitals cwv ON cwv.page_id = pg.id
LEFT JOIN reporting_schema.gsc_data gsc ON gsc.project_id = p.id
WHERE p.status = 'active'
GROUP BY p.id, p.name, p.url, f.total_score, f.freshness_score, f.familiarity_score, f.quality_score, p.created_at;

CREATE OR REPLACE VIEW semantic_schema.content_recommendations AS
SELECT 
    cg.id,
    cg.project_id,
    cg.page_id,
    pg.url,
    cg.gap_type,
    cg.missing_keywords,
    cg.recommendations,
    cg.priority,
    e.total_score AS eeat_score,
    cg.created_at
FROM semantic_schema.content_gaps cg
LEFT JOIN audit_schema.pages pg ON pg.id = cg.page_id
LEFT JOIN semantic_schema.eeat_scores e ON e.page_id = cg.page_id
WHERE cg.priority IN ('high', 'critical')
ORDER BY 
    CASE cg.priority 
        WHEN 'critical' THEN 1
        WHEN 'high' THEN 2
        ELSE 3
    END,
    cg.created_at DESC;

CREATE OR REPLACE FUNCTION reporting_schema.calculate_monthly_roi(
    p_project_id VARCHAR,
    p_year INTEGER,
    p_month INTEGER
)
RETURNS TABLE (
    total_cost DOUBLE PRECISION,
    organic_traffic BIGINT,
    conversions BIGINT,
    revenue DOUBLE PRECISION,
    roi DOUBLE PRECISION
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        COALESCE(SUM(ce.total_cost), 0) AS total_cost,
        COALESCE(SUM(ga.sessions), 0) AS organic_traffic,
        COALESCE(SUM(ga.conversions), 0) AS conversions,
        COALESCE(SUM(ga.revenue), 0) AS revenue,
        CASE 
            WHEN SUM(ce.total_cost) > 0 THEN 
                ((SUM(ga.revenue) - SUM(ce.total_cost)) / SUM(ce.total_cost)) * 100
            ELSE 0
        END AS roi
    FROM reporting_schema.cost_efficiency ce
    LEFT JOIN reporting_schema.ga4_data ga ON ga.project_id = ce.project_id
    WHERE ce.project_id = p_project_id
        AND EXTRACT(YEAR FROM ce.period_start) = p_year
        AND EXTRACT(MONTH FROM ce.period_start) = p_month
        AND EXTRACT(YEAR FROM ga.date) = p_year
        AND EXTRACT(MONTH FROM ga.date) = p_month;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION audit_schema.cleanup_old_crawl_data(
    retention_days INTEGER DEFAULT 90
)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM audit_schema.crawls
    WHERE completed_at < NOW() - (retention_days || ' days')::INTERVAL
        AND status = 'completed';
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION audit_schema.cleanup_public_audits(
    retention_days INTEGER DEFAULT 7
)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    UPDATE audit_schema.public_audit_results
    SET is_deleted = TRUE,
        deleted_at = NOW()
    WHERE created_at < NOW() - (retention_days || ' days')::INTERVAL
        AND is_deleted = FALSE;
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

INSERT INTO public.users (email, hashed_password, full_name, is_active, is_superuser)
VALUES ('admin@seo-platform.com', '$2b$12$dummyhash', 'System Administrator', TRUE, TRUE)
ON CONFLICT (email) DO NOTHING;

GRANT USAGE ON SCHEMA audit_schema TO PUBLIC;
GRANT USAGE ON SCHEMA semantic_schema TO PUBLIC;
GRANT USAGE ON SCHEMA reporting_schema TO PUBLIC;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA audit_schema TO PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA semantic_schema TO PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA reporting_schema TO PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO PUBLIC;

ALTER DEFAULT PRIVILEGES IN SCHEMA audit_schema GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA semantic_schema GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA reporting_schema GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO PUBLIC;
