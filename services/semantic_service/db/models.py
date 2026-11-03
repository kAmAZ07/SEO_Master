from datetime import datetime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import CheckConstraint, DateTime, Float, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB


class Base(DeclarativeBase):
    pass


class FFScoreRow(Base):
    __tablename__ = "ff_scores"
    __table_args__ = (
        Index("idx_ff_score_project_id", "project_id"),
        Index("idx_ff_score_created_at", "created_at"),
        Index("idx_ff_score_root_url", "root_url"),
        # NULL-балл допустим только вместе со статусом insufficient_data; NaN и Infinity не проходят диапазон.
        CheckConstraint("(status = 'insufficient_data') = (ff_score IS NULL)", name="ck_ff_scores_null_iff_insufficient"),
        CheckConstraint("ff_score IS NULL OR (ff_score >= 0 AND ff_score <= 100)", name="ck_ff_scores_range"),
        CheckConstraint("status IN ('complete', 'insufficient_data', 'legacy_estimated')", name="ck_ff_scores_status"),
        {"schema": "semantic_schema"},
    )

    score_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    root_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))

    ff_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    formula_version: Mapped[str] = mapped_column(String(32), nullable=False)
    components: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    inputs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    thresholds: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    eeat_score_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class EEATScoreRow(Base):
    __tablename__ = "eeat_scores"
    __table_args__ = (
        Index("idx_eeat_score_project_id", "project_id"),
        Index("idx_eeat_score_created_at", "created_at"),
        Index("idx_eeat_root_url", "root_url"),
        CheckConstraint("(status = 'insufficient_data') = (score IS NULL)", name="ck_eeat_scores_null_iff_insufficient"),
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= 100)", name="ck_eeat_scores_range"),
        CheckConstraint("status IN ('complete', 'insufficient_data', 'legacy_estimated')", name="ck_eeat_scores_status"),
        {"schema": "semantic_schema"},
    )

    score_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    root_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))

    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    formula_version: Mapped[str] = mapped_column(String(32), nullable=False)
    breakdown: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    signals: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class ContentDraftRow(Base):
    __tablename__ = "content_drafts"
    __table_args__ = (
        Index("idx_content_draft_project_id", "project_id"),
        Index("idx_content_draft_created_at", "created_at"),
        {"schema": "semantic_schema"},
    )

    draft_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    root_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))

    drafts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class SemanticAnalysisRow(Base):
    __tablename__ = "semantic_analysis"
    __table_args__ = (
        Index("idx_semantic_analysis_project_id", "project_id"),
        Index("idx_semantic_analysis_created_at", "created_at"),
        {"schema": "semantic_schema"},
    )

    analysis_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    root_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))

    content_gap: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    semantic_distance: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    keyword_coverage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    inputs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)



class RecommendationVersionRow(Base):
    """Неизменяемая версия предложения (RecommendationVersion). Только добавление: UPDATE запрещён триггером БД,
    пара (recommendation_id, version) уникальна, поэтому параллельная регенерация не создаёт дубль версии."""

    __tablename__ = "recommendation_versions"
    __table_args__ = (
        UniqueConstraint("recommendation_id", "version", name="uq_recommendation_versions_version"),
        CheckConstraint("version >= 1", name="ck_recommendation_versions_version"),
        CheckConstraint("(version = 1) = (previous_version_id IS NULL)", name="ck_recommendation_versions_lineage"),
        CheckConstraint(
            "operation_type IN ('title', 'description', 'h1', 'schema_jsonld', 'content_edit', 'internal_link')",
            name="ck_recommendation_versions_operation",
        ),
        CheckConstraint("execution_mode IN ('managed', 'manual')", name="ck_recommendation_versions_mode"),
        CheckConstraint("length(content_hash) = 64", name="ck_recommendation_versions_hash"),
        Index("idx_recommendation_versions_latest", "project_id", "target_url", "operation_type", "version"),
        Index("idx_recommendation_versions_project_created", "project_id", "created_at"),
        Index("idx_recommendation_versions_hash", "content_hash"),
        Index(
            "uq_recommendation_versions_first", "project_id", "target_url", "operation_type",
            unique=True, postgresql_where=text("version = 1"),
        ),
        {"schema": "semantic_schema"},
    )

    version_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    recommendation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    target_origin: Mapped[str] = mapped_column(String(300), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
