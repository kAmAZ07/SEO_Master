from datetime import datetime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB


class Base(DeclarativeBase):
    pass


class AuditResultFields:
    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    root_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    site_type_hint: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    platform: Mapped[str] = mapped_column(String(64), nullable=False, default="generic")
    seeds: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    findings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    pages: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    options: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class CrawlResult(AuditResultFields, Base):
    __tablename__ = "crawl_results"
    __table_args__ = (
        Index("ix_crawl_results_project_created_at", "project_id", "created_at"),
        Index("ix_crawl_results_mode_status", "mode", "status"),
        {"schema": "audit_schema"},
    )


class PublicAuditResult(AuditResultFields, Base):
    __tablename__ = "public_audit_results"
    __table_args__ = (
        Index("ix_public_audit_results_created_at", "created_at"),
        Index("ix_public_audit_results_mode_status", "mode", "status"),
        {"schema": "audit_schema"},
    )
