from datetime import datetime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, DateTime, Float
from sqlalchemy.dialects.postgresql import JSONB


class Base(DeclarativeBase):
    pass


class FFScoreRow(Base):
    __tablename__ = "ff_scores"

    score_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    root_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    ff_score: Mapped[float] = mapped_column(Float, nullable=False)
    components: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    inputs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    thresholds: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    eeat_score_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class EEATScoreRow(Base):
    __tablename__ = "eeat_scores"

    score_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    root_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    score: Mapped[float] = mapped_column(Float, nullable=False)
    breakdown: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    signals: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class ContentDraftRow(Base):
    __tablename__ = "content_drafts"

    draft_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    root_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    drafts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class SemanticAnalysisRow(Base):
    __tablename__ = "semantic_analysis"

    analysis_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    root_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    content_gap: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    semantic_distance: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    keyword_coverage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    inputs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)