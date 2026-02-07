from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from services.client_api_gateway.db.models import DeploymentLog
from config.logging_config import get_logger

logger = get_logger(__name__)


class ChangelogLogger:
    def log_deployment(
        self,
        db: Session,
        project_id: str,
        task_id: Optional[str],
        change_type: str,
        entity_id: str,
        entity_type: str,
        changes: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
        request: Optional[Request] = None,
        correlation_id: Optional[str] = None,
        status: str = "received",
        error_message: Optional[str] = None,
    ) -> DeploymentLog:
        source_ip = None
        user_agent = None
        if request:
            client = request.client
            if client:
                source_ip = client.host
            user_agent = request.headers.get("user-agent")

        log_entry = DeploymentLog(
            project_id=project_id,
            task_id=task_id,
            change_type=change_type,
            entity_id=entity_id,
            entity_type=entity_type,
            status=status,
            error_message=error_message,
            changes=changes,
            metadata=metadata or {},
            source_ip=source_ip,
            user_agent=user_agent,
            correlation_id=correlation_id,
            created_at=datetime.now(timezone.utc),
        )

        db.add(log_entry)
        db.commit()
        db.refresh(log_entry)

        logger.info(
            "Deployment log created",
            extra={
                "project_id": project_id,
                "task_id": task_id,
                "change_type": change_type,
                "status": status,
                "correlation_id": correlation_id,
            },
        )

        return log_entry


def log_deployment(
    db: Session,
    project_id: str,
    task_id: Optional[str],
    change_type: str,
    entity_id: str,
    entity_type: str,
    changes: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
    request: Optional[Request] = None,
    correlation_id: Optional[str] = None,
    status: str = "received",
    error_message: Optional[str] = None,
) -> DeploymentLog:
    return ChangelogLogger().log_deployment(
        db=db,
        project_id=project_id,
        task_id=task_id,
        change_type=change_type,
        entity_id=entity_id,
        entity_type=entity_type,
        changes=changes,
        metadata=metadata,
        request=request,
        correlation_id=correlation_id,
        status=status,
        error_message=error_message,
    )
