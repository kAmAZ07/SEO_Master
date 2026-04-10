from datetime import datetime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, DateTime, Index, text
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
