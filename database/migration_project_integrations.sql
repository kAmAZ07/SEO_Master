CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE SCHEMA IF NOT EXISTS audit_schema;

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TABLE IF NOT EXISTS audit_schema.project_integrations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id VARCHAR(36) NOT NULL REFERENCES audit_schema.projects(id) ON DELETE CASCADE,
    platform VARCHAR(32) NOT NULL,
    encrypted_creds TEXT NOT NULL,
    creds_hint VARCHAR(32) NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    connected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_project_integrations_project_platform UNIQUE (project_id, platform)
);

CREATE INDEX IF NOT EXISTS idx_project_integrations_project_id
    ON audit_schema.project_integrations(project_id);

CREATE INDEX IF NOT EXISTS idx_project_integrations_platform
    ON audit_schema.project_integrations(platform);

DROP TRIGGER IF EXISTS update_project_integrations_updated_at
    ON audit_schema.project_integrations;

CREATE TRIGGER update_project_integrations_updated_at
    BEFORE UPDATE ON audit_schema.project_integrations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
