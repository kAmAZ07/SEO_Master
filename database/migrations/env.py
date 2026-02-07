import os
import sys
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool, text
from alembic import context

sys.path.insert(0, os.path.realpath(os.path.join(os.path.dirname(__file__), '..')))

from database_config import Base, DATABASE_URL
from secrets_manager import get_api_credentials

from models import (
    Project, Crawl, Page, CoreWebVitals, SchemaValidation,
    Backlink, PublicAuditResult, CrawlEvent,
    FFScore, EEATScore, ContentGap, LLMGeneration, SemanticEvent,
    GSCData, GA4Data, YandexWebmasterData, Report, CostEfficiency,
    Changelog, DomainEvent, User
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def get_url():
    credentials = get_api_credentials()
    return credentials.get_database_url()

def create_schemas(connection):
    connection.execute(text("CREATE SCHEMA IF NOT EXISTS audit_schema"))
    connection.execute(text("CREATE SCHEMA IF NOT EXISTS semantic_schema"))
    connection.execute(text("CREATE SCHEMA IF NOT EXISTS reporting_schema"))
    connection.commit()

def create_extensions(connection):
    connection.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
    connection.execute(text('CREATE EXTENSION IF NOT EXISTS "pg_trgm"'))
    connection.execute(text('CREATE EXTENSION IF NOT EXISTS "btree_gin"'))
    connection.execute(text('CREATE EXTENSION IF NOT EXISTS "btree_gist"'))
    connection.commit()

def create_partitions(connection):
    partitions = [
        ("reporting_schema.gsc_data", [
            ("gsc_data_2024", "2024-01-01", "2025-01-01"),
            ("gsc_data_2025", "2025-01-01", "2026-01-01"),
            ("gsc_data_2026", "2026-01-01", "2027-01-01"),
            ("gsc_data_2027", "2027-01-01", "2028-01-01"),
        ]),
        ("reporting_schema.ga4_data", [
            ("ga4_data_2024", "2024-01-01", "2025-01-01"),
            ("ga4_data_2025", "2025-01-01", "2026-01-01"),
            ("ga4_data_2026", "2026-01-01", "2027-01-01"),
            ("ga4_data_2027", "2027-01-01", "2028-01-01"),
        ]),
        ("reporting_schema.yandex_webmaster_data", [
            ("yandex_webmaster_data_2024", "2024-01-01", "2025-01-01"),
            ("yandex_webmaster_data_2025", "2025-01-01", "2026-01-01"),
            ("yandex_webmaster_data_2026", "2026-01-01", "2027-01-01"),
            ("yandex_webmaster_data_2027", "2027-01-01", "2028-01-01"),
        ]),
    ]
    
    for parent_table, partition_configs in partitions:
        for partition_name, start_date, end_date in partition_configs:
            check_sql = text(f"""
                SELECT EXISTS (
                    SELECT 1 FROM pg_tables 
                    WHERE schemaname = 'reporting_schema' 
                    AND tablename = '{partition_name}'
                )
            """)
            
            result = connection.execute(check_sql).scalar()
            
            if not result:
                create_sql = text(f"""
                    CREATE TABLE IF NOT EXISTS reporting_schema.{partition_name}
                    PARTITION OF {parent_table}
                    FOR VALUES FROM ('{start_date}') TO ('{end_date}')
                """)
                connection.execute(create_sql)
    
    connection.commit()

def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url") or get_url()
    
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema="public",
        include_schemas=True,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = get_url()
    
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        create_extensions(connection)
        create_schemas(connection)
        
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema="public",
            include_schemas=True,
            compare_type=True,
            compare_server_default=True,
            include_object=include_object_filter,
        )

        with context.begin_transaction():
            context.run_migrations()
        
        create_partitions(connection)

def include_object_filter(object, name, type_, reflected, compare_to):
    if type_ == "table":
        if name.endswith(('_2024', '_2025', '_2026', '_2027')):
            return False
    
    return True

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
