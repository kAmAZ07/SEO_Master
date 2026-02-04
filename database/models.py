from sqlalchemy import Column, Integer, String, Text, JSON, Float, Date, ForeignKey, Index, BigInteger, Boolean, DateTime
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from database_config import Base
from pydantic import BaseModel, Field, validator, EmailStr
from typing import Optional, List, Dict, Any
from datetime import datetime, date
import uuid


class TimestampMixin:
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class SoftDeleteMixin:
    is_deleted = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)


class UUIDMixin:
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))


class Project(Base, TimestampMixin, UUIDMixin):
    __tablename__ = "projects"
    __table_args__ = {"schema": "audit_schema"}
    
    name = Column(String(255), nullable=False)
    url = Column(String(2048), nullable=False, unique=True)
    status = Column(String(50), default="active")
    owner_id = Column(String(36), nullable=False)
    
    crawls = relationship("Crawl", back_populates="project", cascade="all, delete-orphan")


class Crawl(Base, TimestampMixin, UUIDMixin):
    __tablename__ = "crawls"
    __table_args__ = (
        Index("idx_crawl_project_id", "project_id"),
        Index("idx_crawl_status", "status"),
        Index("idx_crawl_created_at", "created_at"),
        {"schema": "audit_schema"}
    )
    
    project_id = Column(String(36), ForeignKey("audit_schema.projects.id"), nullable=False)
    status = Column(String(50), default="pending")
    pages_crawled = Column(Integer, default=0)
    total_pages = Column(Integer, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    
    project = relationship("Project", back_populates="crawls")
    pages = relationship("Page", back_populates="crawl", cascade="all, delete-orphan")
    events = relationship("CrawlEvent", back_populates="crawl", cascade="all, delete-orphan")


class Page(Base, TimestampMixin, UUIDMixin):
    __tablename__ = "pages"
    __table_args__ = (
        Index("idx_page_crawl_id", "crawl_id"),
        Index("idx_page_url", "url"),
        Index("idx_page_status_code", "status_code"),
        {"schema": "audit_schema"}
    )
    
    crawl_id = Column(String(36), ForeignKey("audit_schema.crawls.id"), nullable=False)
    url = Column(String(2048), nullable=False)
    status_code = Column(Integer, nullable=True)
    title = Column(String(1024), nullable=True)
    description = Column(Text, nullable=True)
    h1 = Column(String(1024), nullable=True)
    content_length = Column(BigInteger, nullable=True)
    load_time = Column(Float, nullable=True)
    html_content = Column(Text, nullable=True)
    meta_data = Column(JSON, nullable=True)
    
    crawl = relationship("Crawl", back_populates="pages")
    cwv_metrics = relationship("CoreWebVitals", back_populates="page", uselist=False)
    schema_validation = relationship("SchemaValidation", back_populates="page", uselist=False)
    backlinks = relationship("Backlink", back_populates="page")


class CoreWebVitals(Base, TimestampMixin, UUIDMixin):
    __tablename__ = "core_web_vitals"
    __table_args__ = (
        Index("idx_cwv_page_id", "page_id"),
        {"schema": "audit_schema"}
    )
    
    page_id = Column(String(36), ForeignKey("audit_schema.pages.id"), nullable=False, unique=True)
    lcp = Column(Float, nullable=True)
    fid = Column(Float, nullable=True)
    cls = Column(Float, nullable=True)
    ttfb = Column(Float, nullable=True)
    fcp = Column(Float, nullable=True)
    overall_score = Column(Float, nullable=True)
    is_good = Column(Boolean, default=False)
    
    page = relationship("Page", back_populates="cwv_metrics")


class SchemaValidation(Base, TimestampMixin, UUIDMixin):
    __tablename__ = "schema_validations"
    __table_args__ = (
        Index("idx_schema_page_id", "page_id"),
        {"schema": "audit_schema"}
    )
    
    page_id = Column(String(36), ForeignKey("audit_schema.pages.id"), nullable=False, unique=True)
    has_schema = Column(Boolean, default=False)
    schema_types = Column(JSON, nullable=True)
    validation_errors = Column(JSON, nullable=True)
    is_valid = Column(Boolean, default=False)
    
    page = relationship("Page", back_populates="schema_validation")


class Backlink(Base, TimestampMixin, UUIDMixin):
    __tablename__ = "backlinks"
    __table_args__ = (
        Index("idx_backlink_page_id", "page_id"),
        Index("idx_backlink_source_url", "source_url"),
        {"schema": "audit_schema"}
    )
    
    page_id = Column(String(36), ForeignKey("audit_schema.pages.id"), nullable=False)
    source_url = Column(String(2048), nullable=False)
    anchor_text = Column(Text, nullable=True)
    link_type = Column(String(50), nullable=True)
    discovered_at = Column(DateTime(timezone=True), server_default=func.now())
    
    page = relationship("Page", back_populates="backlinks")


class PublicAuditResult(Base, TimestampMixin, SoftDeleteMixin, UUIDMixin):
    __tablename__ = "public_audit_results"
    __table_args__ = (
        Index("idx_public_audit_created_at", "created_at"),
        Index("idx_public_audit_deleted", "is_deleted"),
        {"schema": "audit_schema"}
    )
    
    url = Column(String(2048), nullable=False)
    ip_address = Column(String(45), nullable=False)
    results = Column(JSON, nullable=False)
    status = Column(String(50), default="completed")


class CrawlEvent(Base, TimestampMixin, UUIDMixin):
    __tablename__ = "crawl_events"
    __table_args__ = (
        Index("idx_crawl_event_crawl_id", "crawl_id"),
        Index("idx_crawl_event_type", "event_type"),
        {"schema": "audit_schema"}
    )
    
    crawl_id = Column(String(36), ForeignKey("audit_schema.crawls.id"), nullable=False)
    event_type = Column(String(50), nullable=False)
    event_data = Column(JSON, nullable=True)
    
    crawl = relationship("Crawl", back_populates="events")


class FFScore(Base, TimestampMixin, UUIDMixin):
    __tablename__ = "ff_scores"
    __table_args__ = (
        Index("idx_ff_score_project_id", "project_id"),
        Index("idx_ff_score_calculated_at", "calculated_at"),
        {"schema": "semantic_schema"}
    )
    
    project_id = Column(String(36), nullable=False)
    page_id = Column(String(36), nullable=True)
    total_score = Column(Float, nullable=False)
    freshness_score = Column(Float, nullable=False)
    familiarity_score = Column(Float, nullable=False)
    quality_score = Column(Float, nullable=False)
    calculated_at = Column(DateTime(timezone=True), server_default=func.now())
    metadata = Column(JSON, nullable=True)


class EEATScore(Base, TimestampMixin, UUIDMixin):
    __tablename__ = "eeat_scores"
    __table_args__ = (
        Index("idx_eeat_score_page_id", "page_id"),
        {"schema": "semantic_schema"}
    )
    
    page_id = Column(String(36), nullable=False, unique=True)
    total_score = Column(Float, nullable=False)
    experience_score = Column(Float, nullable=False)
    expertise_score = Column(Float, nullable=False)
    authoritativeness_score = Column(Float, nullable=False)
    trustworthiness_score = Column(Float, nullable=False)
    signals = Column(JSON, nullable=True)


class ContentGap(Base, TimestampMixin, UUIDMixin):
    __tablename__ = "content_gaps"
    __table_args__ = (
        Index("idx_content_gap_project_id", "project_id"),
        {"schema": "semantic_schema"}
    )
    
    project_id = Column(String(36), nullable=False)
    page_id = Column(String(36), nullable=True)
    gap_type = Column(String(100), nullable=False)
    missing_keywords = Column(JSON, nullable=True)
    recommendations = Column(Text, nullable=True)
    priority = Column(String(20), default="medium")


class LLMGeneration(Base, TimestampMixin, UUIDMixin):
    __tablename__ = "llm_generations"
    __table_args__ = (
        Index("idx_llm_generation_page_id", "page_id"),
        Index("idx_llm_generation_type", "generation_type"),
        {"schema": "semantic_schema"}
    )
    
    page_id = Column(String(36), nullable=False)
    generation_type = Column(String(50), nullable=False)
    prompt = Column(Text, nullable=False)
    generated_content = Column(Text, nullable=False)
    model_name = Column(String(100), nullable=True)
    tokens_used = Column(Integer, nullable=True)
    cache_hit = Column(Boolean, default=False)
    approved = Column(Boolean, default=False)
    approved_at = Column(DateTime(timezone=True), nullable=True)


class SemanticEvent(Base, TimestampMixin, UUIDMixin):
    __tablename__ = "semantic_events"
    __table_args__ = (
        Index("idx_semantic_event_type", "event_type"),
        {"schema": "semantic_schema"}
    )
    
    event_type = Column(String(50), nullable=False)
    project_id = Column(String(36), nullable=True)
    event_data = Column(JSON, nullable=False)


class GSCData(Base, TimestampMixin, UUIDMixin):
    __tablename__ = "gsc_data"
    __table_args__ = (
        Index("idx_gsc_project_id", "project_id"),
        Index("idx_gsc_date", "date"),
        {"schema": "reporting_schema"}
    )
    
    project_id = Column(String(36), nullable=False)
    date = Column(Date, nullable=False)
    query = Column(String(512), nullable=True)
    page = Column(String(2048), nullable=True)
    clicks = Column(Integer, default=0)
    impressions = Column(Integer, default=0)
    ctr = Column(Float, default=0.0)
    position = Column(Float, nullable=True)
    raw_data = Column(JSON, nullable=True)


class GA4Data(Base, TimestampMixin, UUIDMixin):
    __tablename__ = "ga4_data"
    __table_args__ = (
        Index("idx_ga4_project_id", "project_id"),
        Index("idx_ga4_date", "date"),
        {"schema": "reporting_schema"}
    )
    
    project_id = Column(String(36), nullable=False)
    date = Column(Date, nullable=False)
    page_path = Column(String(2048), nullable=True)
    sessions = Column(Integer, default=0)
    users = Column(Integer, default=0)
    pageviews = Column(Integer, default=0)
    avg_session_duration = Column(Float, default=0.0)
    bounce_rate = Column(Float, default=0.0)
    conversions = Column(Integer, default=0)
    revenue = Column(Float, default=0.0)
    raw_data = Column(JSON, nullable=True)


class YandexWebmasterData(Base, TimestampMixin, UUIDMixin):
    __tablename__ = "yandex_webmaster_data"
    __table_args__ = (
        Index("idx_ym_project_id", "project_id"),
        Index("idx_ym_date", "date"),
        {"schema": "reporting_schema"}
    )
    
    project_id = Column(String(36), nullable=False)
    date = Column(Date, nullable=False)
    query = Column(String(512), nullable=True)
    url = Column(String(2048), nullable=True)
    shows = Column(Integer, default=0)
    clicks = Column(Integer, default=0)
    ctr = Column(Float, default=0.0)
    position = Column(Float, nullable=True)
    raw_data = Column(JSON, nullable=True)


class Report(Base, TimestampMixin, UUIDMixin):
    __tablename__ = "reports"
    __table_args__ = (
        Index("idx_report_project_id", "project_id"),
        Index("idx_report_type", "report_type"),
        {"schema": "reporting_schema"}
    )
    
    project_id = Column(String(36), nullable=False)
    report_type = Column(String(50), nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    file_path = Column(String(512), nullable=True)
    metrics = Column(JSON, nullable=True)
    status = Column(String(50), default="generated")


class CostEfficiency(Base, TimestampMixin, UUIDMixin):
    __tablename__ = "cost_efficiency"
    __table_args__ = (
        Index("idx_cost_project_id", "project_id"),
        {"schema": "reporting_schema"}
    )
    
    project_id = Column(String(36), nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    total_cost = Column(Float, default=0.0)
    organic_traffic = Column(Integer, default=0)
    conversions = Column(Integer, default=0)
    revenue = Column(Float, default=0.0)
    cost_per_click = Column(Float, default=0.0)
    roi = Column(Float, default=0.0)
    metrics_data = Column(JSON, nullable=True)


class Changelog(Base, TimestampMixin, UUIDMixin):
    __tablename__ = "changelog"
    __table_args__ = (
        Index("idx_changelog_entity_id", "entity_id"),
        Index("idx_changelog_type", "change_type"),
    )
    
    entity_id = Column(String(36), nullable=False)
    entity_type = Column(String(100), nullable=False)
    change_type = Column(String(50), nullable=False)
    before_value = Column(JSON, nullable=True)
    after_value = Column(JSON, nullable=True)
    impact_score = Column(Float, nullable=True)
    approved_by = Column(String(36), nullable=True)
    applied = Column(Boolean, default=False)
    applied_at = Column(DateTime(timezone=True), nullable=True)


class DomainEvent(Base, TimestampMixin, UUIDMixin):
    __tablename__ = "domain_events"
    __table_args__ = (
        Index("idx_event_type", "event_type"),
        Index("idx_event_processed", "processed"),
    )
    
    event_type = Column(String(100), nullable=False)
    aggregate_id = Column(String(36), nullable=False)
    event_data = Column(JSON, nullable=False)
    processed = Column(Boolean, default=False)
    processed_at = Column(DateTime(timezone=True), nullable=True)


class User(Base, TimestampMixin, UUIDMixin):
    __tablename__ = "users"
    __table_args__ = (
        Index("idx_user_email", "email", unique=True),
    )
    
    email = Column(String(255), nullable=False, unique=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    is_superuser = Column(Boolean, default=False)


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    url: str = Field(..., min_length=1, max_length=2048)
    owner_id: str
    
    @validator('url')
    def validate_url(cls, v):
        if not v.startswith(('http://', 'https://')):
            raise ValueError('URL must start with http:// or https://')
        return v


class ProjectResponse(BaseModel):
    id: str
    name: str
    url: str
    status: str
    owner_id: str
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class CrawlCreate(BaseModel):
    project_id: str


class CrawlResponse(BaseModel):
    id: str
    project_id: str
    status: str
    pages_crawled: int
    total_pages: Optional[int]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    error_message: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True


class PageResponse(BaseModel):
    id: str
    crawl_id: str
    url: str
    status_code: Optional[int]
    title: Optional[str]
    description: Optional[str]
    h1: Optional[str]
    content_length: Optional[int]
    load_time: Optional[float]
    created_at: datetime
    
    class Config:
        from_attributes = True


class CoreWebVitalsResponse(BaseModel):
    id: str
    page_id: str
    lcp: Optional[float]
    fid: Optional[float]
    cls: Optional[float]
    ttfb: Optional[float]
    fcp: Optional[float]
    overall_score: Optional[float]
    is_good: bool
    
    class Config:
        from_attributes = True


class PublicAuditRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048)
    
    @validator('url')
    def validate_url(cls, v):
        if not v.startswith(('http://', 'https://')):
            raise ValueError('URL must start with http:// or https://')
        return v


class PublicAuditResponse(BaseModel):
    id: str
    url: str
    results: Dict[str, Any]
    status: str
    created_at: datetime
    
    class Config:
        from_attributes = True


class FFScoreResponse(BaseModel):
    id: str
    project_id: str
    page_id: Optional[str]
    total_score: float
    freshness_score: float
    familiarity_score: float
    quality_score: float
    calculated_at: datetime
    metadata: Optional[Dict[str, Any]]
    
    class Config:
        from_attributes = True


class EEATScoreResponse(BaseModel):
    id: str
    page_id: str
    total_score: float
    experience_score: float
    expertise_score: float
    authoritativeness_score: float
    trustworthiness_score: float
    signals: Optional[Dict[str, Any]]
    
    class Config:
        from_attributes = True


class LLMGenerationRequest(BaseModel):
    page_id: str
    generation_type: str = Field(..., regex="^(title|description|h1|schema|content_analysis|eeat_analysis)$")
    prompt: str


class LLMGenerationResponse(BaseModel):
    id: str
    page_id: str
    generation_type: str
    generated_content: str
    model_name: Optional[str]
    tokens_used: Optional[int]
    cache_hit: bool
    approved: bool
    created_at: datetime
    
    class Config:
        from_attributes = True


class ContentGapResponse(BaseModel):
    id: str
    project_id: str
    page_id: Optional[str]
    gap_type: str
    missing_keywords: Optional[List[str]]
    recommendations: Optional[str]
    priority: str
    created_at: datetime
    
    class Config:
        from_attributes = True


class GSCDataResponse(BaseModel):
    id: str
    project_id: str
    date: date
    query: Optional[str]
    page: Optional[str]
    clicks: int
    impressions: int
    ctr: float
    position: Optional[float]
    
    class Config:
        from_attributes = True


class GA4DataResponse(BaseModel):
    id: str
    project_id: str
    date: date
    page_path: Optional[str]
    sessions: int
    users: int
    pageviews: int
    avg_session_duration: float
    bounce_rate: float
    conversions: int
    revenue: float
    
    class Config:
        from_attributes = True


class ReportCreate(BaseModel):
    project_id: str
    report_type: str = Field(..., regex="^(gsc|ga4|yandex|combined|cost_efficiency)$")
    period_start: date
    period_end: date


class ReportResponse(BaseModel):
    id: str
    project_id: str
    report_type: str
    period_start: date
    period_end: date
    file_path: Optional[str]
    metrics: Optional[Dict[str, Any]]
    status: str
    created_at: datetime
    
    class Config:
        from_attributes = True


class CostEfficiencyResponse(BaseModel):
    id: str
    project_id: str
    period_start: date
    period_end: date
    total_cost: float
    organic_traffic: int
    conversions: int
    revenue: float
    cost_per_click: float
    roi: float
    
    class Config:
        from_attributes = True


class ChangelogCreate(BaseModel):
    entity_id: str
    entity_type: str
    change_type: str
    before_value: Optional[Dict[str, Any]]
    after_value: Optional[Dict[str, Any]]
    impact_score: Optional[float] = Field(None, ge=0.0, le=1.0)


class ChangelogResponse(BaseModel):
    id: str
    entity_id: str
    entity_type: str
    change_type: str
    before_value: Optional[Dict[str, Any]]
    after_value: Optional[Dict[str, Any]]
    impact_score: Optional[float]
    approved_by: Optional[str]
    applied: bool
    applied_at: Optional[datetime]
    created_at: datetime
    
    class Config:
        from_attributes = True


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: Optional[str]


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: Optional[str]
    is_active: bool
    is_superuser: bool
    created_at: datetime
    
    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ErrorResponse(BaseModel):
    detail: str
    error_code: Optional[str]
