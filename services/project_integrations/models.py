import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()


class ProjectIntegration(Base):
    __tablename__ = "project_integrations"
    __table_args__ = (
        UniqueConstraint("project_id", "platform", name="uq_project_integrations_project_platform"),
        Index("idx_project_integrations_project_id", "project_id"),
        Index("idx_project_integrations_platform", "platform"),
        {"schema": "audit_schema"},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        String(36),
        ForeignKey("audit_schema.projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    platform = Column(String(32), nullable=False)
    encrypted_creds = Column(Text, nullable=False)
    creds_hint = Column(String(32), nullable=False)
    details = Column(JSONB, nullable=False, default=dict)

    connected_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
