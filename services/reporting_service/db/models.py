import uuid
from datetime import date, datetime

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import Boolean, Date, DateTime, Float, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB


class Base(DeclarativeBase):
    pass


class ReportRow(Base):
    __tablename__ = "reports"
    __table_args__ = (
        Index("idx_report_project_id", "project_id"),
        Index("idx_report_created_at", "created_at"),
        {"schema": "reporting_schema"},
    )

    report_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    root_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))

    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class MetricsHistoryRow(Base):
    __tablename__ = "metrics_history"
    __table_args__ = (
        Index("idx_metrics_history_project_id", "project_id"),
        Index("idx_metrics_history_created_at", "created_at"),
        {"schema": "reporting_schema"},
    )

    metric_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    root_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))

    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class GSCDataRow(Base):
    __tablename__ = "gsc_data"
    __table_args__ = (
        Index("idx_gsc_project_id", "project_id"),
        Index("idx_gsc_date", "date"),
        Index("idx_gsc_query", "query"),
        Index("idx_gsc_page", "page"),
        {"schema": "reporting_schema", "postgresql_partition_by": "RANGE (date)"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    date: Mapped[date] = mapped_column(Date, primary_key=True, nullable=False)
    query: Mapped[str | None] = mapped_column(String(512), nullable=True)
    page: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    ctr: Mapped[float] = mapped_column(Float, default=0.0)
    position: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class GA4DataRow(Base):
    __tablename__ = "ga4_data"
    __table_args__ = (
        Index("idx_ga4_project_id", "project_id"),
        Index("idx_ga4_date", "date"),
        Index("idx_ga4_page_path", "page_path"),
        {"schema": "reporting_schema", "postgresql_partition_by": "RANGE (date)"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    date: Mapped[date] = mapped_column(Date, primary_key=True, nullable=False)
    page_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    sessions: Mapped[int] = mapped_column(Integer, default=0)
    users: Mapped[int] = mapped_column(Integer, default=0)
    pageviews: Mapped[int] = mapped_column(Integer, default=0)
    avg_session_duration: Mapped[float] = mapped_column(Float, default=0.0)
    bounce_rate: Mapped[float] = mapped_column(Float, default=0.0)
    conversions: Mapped[int] = mapped_column(Integer, default=0)
    revenue: Mapped[float] = mapped_column(Float, default=0.0)
    raw_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class YandexWebmasterDataRow(Base):
    __tablename__ = "yandex_webmaster_data"
    __table_args__ = (
        Index("idx_ym_project_id", "project_id"),
        Index("idx_ym_date", "date"),
        Index("idx_ym_query", "query"),
        {"schema": "reporting_schema", "postgresql_partition_by": "RANGE (date)"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    date: Mapped[date] = mapped_column(Date, primary_key=True, nullable=False)
    query: Mapped[str | None] = mapped_column(String(512), nullable=True)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    shows: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    ctr: Mapped[float] = mapped_column(Float, default=0.0)
    position: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class ChangelogRow(Base):
    __tablename__ = "changelog"
    __table_args__ = (
        Index("idx_changelog_entity_id", "entity_id"),
        Index("idx_changelog_type", "change_type"),
        Index("idx_changelog_applied", "applied"),
        {"schema": "public"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    change_type: Mapped[str] = mapped_column(String(50), nullable=False)
    before_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    impact_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
