import os
import sys
from datetime import datetime
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, Depends, status, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import and_

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.database_config import get_db
from config.logging_config import get_logger
from services.client_api_gateway.config import settings
from services.client_api_gateway.db.models import DeploymentLog, Changelog, ClientKey
from services.client_api_gateway.auth.signature_validator import validate_hmac_signature
from services.client_api_gateway.logging.changelog_logger import log_change

logger = get_logger(__name__)

app = FastAPI(
    title="Client API Gateway",
    description="Gateway for deploying approved changes to client sites (WordPress/Tilda)",
    version="1.0.0"
)


class DeployChangeRequest(BaseModel):
    project_id: str = Field(..., description="Project ID")
    task_id: str = Field(..., description="Approved task ID from Management Service")
    change_type: str = Field(..., description="Type: wordpress_meta, wordpress_content, tilda_page, etc")
    entity_id: str = Field(..., description="Post ID / Page ID")
    entity_type: str = Field(..., description="wordpress_post, tilda_page")
    changes: Dict[str, Any] = Field(..., description="Changes data (before/after)")
    priority: int = Field(default=5, description="Priority 1-10")
    metadata: Optional[Dict[str, Any]] = Field(default=None)


class ConfirmChangeRequest(BaseModel):
    change_id: str = Field(..., description="Change ID")
    status: str = Field(..., description="applied or failed")
    error_message: Optional[str] = Field(default=None)
    applied_at: datetime = Field(..., description="Timestamp when change was applied")


class PendingChange(BaseModel):
    change_id: str
    task_id: str
    change_type: str
    entity_id: str
    entity_type: str
    changes: Dict[str, Any]
    priority: int
    created_at: datetime
    metadata: Optional[Dict[str, Any]]


async def verify_management_service(request: Request):
    internal_key = os.getenv("INTERNAL_API_KEY", "internal-secret-key-change-in-production")
    x_api_key = request.headers.get("X-API-Key")
    
    if not x_api_key or x_api_key != internal_key:
        logger.warning(f"Unauthorized internal request from {request.client.host}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing internal API key"
        )
    return True


async def verify_client_signature(
    request: Request,
    x_signature: str = Header(..., description="HMAC-SHA256 signature"),
    x_timestamp: str = Header(..., description="Unix timestamp"),
    x_project_id: str = Header(..., description="Project ID"),
    db: Session = Depends(get_db)
):
    if not settings.ENABLE_SIGNATURE_VALIDATION:
        return x_project_id
    
    client_key = db.query(ClientKey).filter(
        and_(
            ClientKey.project_id == x_project_id,
            ClientKey.is_active == True
        )
    ).first()
    
    if not client_key:
        logger.warning(f"Client key not found for project {x_project_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid project ID or inactive key"
        )
    
    body = await request.body()
    
    is_valid = validate_hmac_signature(
        signature=x_signature,
        timestamp=x_timestamp,
        method=request.method,
        url=str(request.url),
        body=body,
        secret_key=client_key.hmac_key,
        max_age_seconds=settings.HMAC_SIGNATURE_MAX_AGE_SECONDS
    )
    
    if not is_valid:
        logger.warning(f"Invalid HMAC signature for project {x_project_id} from {request.client.host}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid HMAC signature or timestamp expired"
        )
    
    return x_project_id


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "client-api-gateway",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.post("/internal/deploy", dependencies=[Depends(verify_management_service)])
async def deploy_change(
    request: DeployChangeRequest,
    db: Session = Depends(get_db)
):
    try:
        client_key = db.query(ClientKey).filter(
            ClientKey.project_id == request.project_id
        ).first()
        
        if not client_key:
            raise HTTPException(
                status_code=404,
                detail=f"Project {request.project_id} not found"
            )
        
        deployment = DeploymentLog(
            project_id=request.project_id,
            task_id=request.task_id,
            change_type=request.change_type,
            entity_id=request.entity_id,
            entity_type=request.entity_type,
            changes=request.changes,
            priority=request.priority,
            status="pending",
            metadata=request.metadata
        )
        db.add(deployment)
        db.flush()
        
        changelog_entry = Changelog(
            project_id=request.project_id,
            entity_id=request.entity_id,
            entity_type=request.entity_type,
            change_type=request.change_type,
            before_value=request.changes.get("before"),
            after_value=request.changes.get("after"),
            applied=False,
            metadata=request.metadata
        )
        db.add(changelog_entry)
        
        db.commit()
        
        logger.info(
            f"Change deployed for project {request.project_id}, "
            f"task {request.task_id}, deployment_id {deployment.id}"
        )
        
        return {
            "success": True,
            "change_id": str(deployment.id),
            "project_id": request.project_id,
            "task_id": request.task_id,
            "status": "pending",
            "message": "Change queued for deployment. Plugin should fetch it."
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deploying change: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/changes/pending/{project_id}", response_model=List[PendingChange])
async def get_pending_changes(
    project_id: str = Depends(verify_client_signature),
    limit: int = 50,
    db: Session = Depends(get_db)
):
    try:
        pending_changes = db.query(DeploymentLog).filter(
            and_(
                DeploymentLog.project_id == project_id,
                DeploymentLog.status == "pending"
            )
        ).order_by(
            DeploymentLog.priority.desc(),
            DeploymentLog.created_at.asc()
        ).limit(limit).all()
        
        result = []
        for change in pending_changes:
            result.append(PendingChange(
                change_id=str(change.id),
                task_id=change.task_id,
                change_type=change.change_type,
                entity_id=change.entity_id,
                entity_type=change.entity_type,
                changes=change.changes,
                priority=change.priority,
                created_at=change.created_at,
                metadata=change.metadata
            ))
        
        logger.info(f"Project {project_id} fetched {len(result)} pending changes")
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching pending changes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/changes/confirm/{change_id}")
async def confirm_change(
    change_id: str,
    request: ConfirmChangeRequest,
    project_id: str = Depends(verify_client_signature),
    db: Session = Depends(get_db)
):
    try:
        deployment = db.query(DeploymentLog).filter(
            and_(
                DeploymentLog.id == change_id,
                DeploymentLog.project_id == project_id
            )
        ).first()
        
        if not deployment:
            raise HTTPException(status_code=404, detail="Change not found")
        
        if deployment.status != "pending":
            raise HTTPException(
                status_code=400,
                detail=f"Change already {deployment.status}"
            )
        
        deployment.status = request.status
        deployment.error_message = request.error_message
        deployment.applied_at = request.applied_at
        
        changelog = db.query(Changelog).filter(
            and_(
                Changelog.project_id == project_id,
                Changelog.entity_id == deployment.entity_id,
                Changelog.change_type == deployment.change_type
            )
        ).order_by(Changelog.created_at.desc()).first()
        
        if changelog:
            changelog.applied = (request.status == "applied")
            changelog.applied_at = request.applied_at if request.status == "applied" else None
        
        db.commit()
        
        log_change(
            db=db,
            project_id=project_id,
            entity_id=deployment.entity_id,
            entity_type=deployment.entity_type,
            change_type=deployment.change_type,
            before_value=deployment.changes.get("before"),
            after_value=deployment.changes.get("after"),
            applied=(request.status == "applied"),
            metadata={
                "change_id": change_id,
                "task_id": deployment.task_id,
                "error_message": request.error_message
            }
        )
        
        logger.info(
            f"Change {change_id} confirmed as {request.status} "
            f"for project {project_id}"
        )
        
        return {
            "success": True,
            "change_id": change_id,
            "status": request.status,
            "message": f"Change {request.status}"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error confirming change: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/changes/history/{project_id}")
async def get_change_history(
    project_id: str = Depends(verify_client_signature),
    limit: int = 100,
    entity_id: Optional[str] = None,
    db: Session = Depends(get_db)
):
    try:
        query = db.query(Changelog).filter(Changelog.project_id == project_id)
        
        if entity_id:
            query = query.filter(Changelog.entity_id == entity_id)
        
        history = query.order_by(
            Changelog.created_at.desc()
        ).limit(limit).all()
        
        result = []
        for entry in history:
            result.append({
                "id": str(entry.id),
                "entity_id": entry.entity_id,
                "entity_type": entry.entity_type,
                "change_type": entry.change_type,
                "before_value": entry.before_value,
                "after_value": entry.after_value,
                "applied": entry.applied,
                "created_at": entry.created_at.isoformat(),
                "applied_at": entry.applied_at.isoformat() if entry.applied_at else None,
                "metadata": entry.metadata
            })
        
        return {
            "success": True,
            "project_id": project_id,
            "total": len(result),
            "history": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching change history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.SERVICE_PORT,
        reload=settings.is_development(),
        log_level=settings.LOG_LEVEL.lower()
    )
