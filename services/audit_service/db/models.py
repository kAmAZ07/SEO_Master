from datetime import datetime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, DateTime
from sqlalchemy.dialects.postgresql import JSONB


class Base(DeclarativeBase):
    pass


class CrawlResult(Base):
    __tablename__ = "crawl_results"

    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    root_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    findings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    pages: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    options: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class PublicAuditResult(Base):
    __tablename__ = "public_audit_results"

    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    root_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    findings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    pages: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    options: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)