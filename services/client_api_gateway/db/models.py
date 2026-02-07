import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    String,
    DateTime,
    Boolean,
    Text,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()


class ClientKey(Base):
    __tablename__ = "client_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(String(255), nullable=False)
    key_id = Column(String(100), nullable=False, unique=True)
    secret = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    rotated_at = Column(DateTime(timezone=True))
    grace_until = Column(DateTime(timezone=True))
    last_used_at = Column(DateTime(timezone=True))

    metadata = Column(JSONB, default={})

    __table_args__ = (
        Index("idx_client_keys_project", "project_id"),
        Index("idx_client_keys_active", "project_id", "is_active"),
    )


class DeploymentLog(Base):
    __tablename__ = "deployment_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(String(255), nullable=False)
    task_id = Column(String(255))
    change_type = Column(String(50), nullable=False)
    entity_id = Column(String(2048), nullable=False)
    entity_type = Column(String(100), nullable=False)
    status = Column(String(50), default="received", nullable=False)
    error_message = Column(Text)

    changes = Column(JSONB, nullable=False)
    metadata = Column(JSONB, default={})

    source_ip = Column(String(45))
    user_agent = Column(String(512))
    correlation_id = Column(String(255))

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    applied_at = Column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_deploy_log_project", "project_id"),
        Index("idx_deploy_log_status", "status"),
        Index("idx_deploy_log_change_type", "change_type"),
        Index("idx_deploy_log_created_at", "created_at"),
    )
