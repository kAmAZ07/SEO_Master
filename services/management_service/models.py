# services/management_service/models.py

import enum
import uuid

from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    DateTime,
    Boolean,
    Text,
    ForeignKey,
    Enum as SQLEnum,
    Index,
    CheckConstraint,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

Base = declarative_base()


class TaskType(enum.Enum):
    UPDATE_META = "UPDATE_META"
    UPDATE_CONTENT = "UPDATE_CONTENT"
    ADD_INTERNAL_LINKS = "ADD_INTERNAL_LINKS"
    UPDATE_SCHEMA = "UPDATE_SCHEMA"
    FIX_404 = "FIX_404"
    UPDATE_TILDA_PAGE = "UPDATE_TILDA_PAGE"
    OPTIMIZE_IMAGES = "OPTIMIZE_IMAGES"
    FIX_BROKEN_LINKS = "FIX_BROKEN_LINKS"


class TaskStatus(enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DEPLOYED = "DEPLOYED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class HITLStatus(enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    domain = Column(String(255), nullable=False, unique=True)
    platform = Column(String(50), nullable=False, default="wordpress")
    is_active = Column(Boolean, default=True, nullable=False)

    settings = Column(JSONB, default={})
    meta = Column("metadata", JSONB, default={})

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    tasks = relationship("Task", back_populates="project", cascade="all, delete-orphan")
    hitl_approvals = relationship("HITLApproval", back_populates="project", cascade="all, delete-orphan")
    changelogs = relationship("Changelog", back_populates="project", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_projects_domain", "domain"),
        Index("idx_projects_is_active", "is_active"),
    )


class Task(Base):
    __tablename__ = "tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)

    task_type = Column(SQLEnum(TaskType), nullable=False)
    status = Column(SQLEnum(TaskStatus), default=TaskStatus.PENDING, nullable=False)

    url = Column(String(2048), nullable=False)
    title = Column(String(500))
    description = Column(Text)

    impact_score = Column(Float, default=0.5)
    effort_score = Column(Float, default=0.5)
    priority_score = Column(Float, default=0.5)

    meta = Column("metadata", JSONB, default={})

    assigned_to = Column(String(255))

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    deployed_at = Column(DateTime(timezone=True))

    project = relationship("Project", back_populates="tasks")
    hitl_approval = relationship("HITLApproval", back_populates="task", uselist=False, cascade="all, delete-orphan")
    changelogs = relationship("Changelog", back_populates="task", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_tasks_project_id", "project_id"),
        Index("idx_tasks_status", "status"),
        Index("idx_tasks_task_type", "task_type"),
        Index("idx_tasks_created_at", "created_at"),
        Index("idx_tasks_priority_score", "priority_score"),
        Index("idx_tasks_project_status", "project_id", "status"),
    )

    def calculate_priority(self):
        self.priority_score = self.impact_score * (2 - self.effort_score)
        return self.priority_score


class HITLApproval(Base):
    __tablename__ = "hitl_approvals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, unique=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)

    status = Column(SQLEnum(HITLStatus), default=HITLStatus.PENDING, nullable=False)

    diff_data = Column(JSONB, nullable=False)

    impact_score = Column(Float)
    recommendation = Column(Text)

    approved_by = Column(String(255))
    approved_at = Column(DateTime(timezone=True))
    rejected_by = Column(String(255))
    rejected_at = Column(DateTime(timezone=True))
    rejection_reason = Column(Text)

    meta = Column("metadata", JSONB, default={})

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    task = relationship("Task", back_populates="hitl_approval")
    project = relationship("Project", back_populates="hitl_approvals")

    __table_args__ = (
        Index("idx_hitl_approvals_task_id", "task_id"),
        Index("idx_hitl_approvals_project_id", "project_id"),
        Index("idx_hitl_approvals_status", "status"),
        Index("idx_hitl_approvals_created_at", "created_at"),
    )


class Changelog(Base):
    __tablename__ = "management_changelog"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)

    entity_id = Column(String(2048), nullable=False)
    entity_type = Column(String(100), nullable=False)

    change_type = Column(String(100), nullable=False)

    before_value = Column(JSONB)
    after_value = Column(JSONB)

    applied = Column(Boolean, default=False, nullable=False)
    applied_at = Column(DateTime(timezone=True))

    source = Column(String(50), default="auto", nullable=False)

    meta = Column("metadata", JSONB, default={})

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    project = relationship("Project", back_populates="changelogs")
    task = relationship("Task", back_populates="changelogs")

    __table_args__ = (
        Index("idx_management_changelog_project_id", "project_id"),
        Index("idx_management_changelog_task_id", "task_id"),
        Index("idx_management_changelog_entity_id", "entity_id"),
        Index("idx_management_changelog_applied", "applied"),
        Index("idx_management_changelog_created_at", "created_at"),
        Index("idx_management_changelog_project_entity", "project_id", "entity_id"),
    )


class HITLHistory(Base):
    """Append-only snapshots; migrations additionally protect UPDATE/DELETE."""
    __tablename__ = "hitl_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    approval_id = Column(UUID(as_uuid=True), ForeignKey("hitl_approvals.id", ondelete="RESTRICT"), nullable=False)
    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False)
    project_id = Column(UUID(as_uuid=True), nullable=False)
    version_id = Column(String(36), nullable=False)
    entry_type = Column(String(16), nullable=False)
    content_hash = Column(String(64), nullable=False)
    entry_hash = Column(String(64), nullable=False)
    meta = Column("metadata", JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint("approval_id", "version_id", "entry_type", name="uq_hitl_history_entry"),
        CheckConstraint("entry_type IN ('prepared','approve','reject','revoke')", name="ck_hitl_history_type"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$' AND entry_hash ~ '^[0-9a-f]{64}$'", name="ck_hitl_history_hash"),
        Index("idx_hitl_history_task_created", "task_id", "created_at", "id"),
    )


class ManagementOutbox(Base):
    """Immutable event envelope with independently mutable delivery state."""
    __tablename__ = "management_outbox"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    history_id = Column(UUID(as_uuid=True), ForeignKey("hitl_history.id", ondelete="RESTRICT"), nullable=False)
    event_type = Column(String(100), nullable=False)
    routing_key = Column(String(255), nullable=False)
    status = Column(String(16), nullable=False, default="pending", server_default="pending")
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    available_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    lease_token = Column(UUID(as_uuid=True))
    lease_expires_at = Column(DateTime(timezone=True))
    published_at = Column(DateTime(timezone=True))
    error_code = Column(String(64))
    meta = Column("metadata", JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint("history_id", name="uq_management_outbox_history"),
        CheckConstraint("status IN ('pending','leased','published')", name="ck_management_outbox_status"),
        CheckConstraint("attempts >= 0", name="ck_management_outbox_attempts"),
        CheckConstraint("(status = 'published') = (published_at IS NOT NULL)", name="ck_management_outbox_published"),
        CheckConstraint("(status = 'leased' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
                        "(status != 'leased' AND lease_token IS NULL AND lease_expires_at IS NULL)",
                        name="ck_management_outbox_lease"),
        Index("idx_management_outbox_delivery", "status", "available_at", "lease_expires_at"),
    )
