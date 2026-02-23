from datetime import datetime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, DateTime
from sqlalchemy.dialects.postgresql import JSONB


class Base(DeclarativeBase):
    pass


class ReportRow(Base):
    __tablename__ = "reports"

    report_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    root_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class MetricsHistoryRow(Base):
    __tablename__ = "metrics_history"

    metric_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    root_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)