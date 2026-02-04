from fastapi import APIRouter, HTTPException, Depends, status
from pydantic import BaseModel, Field, EmailStr
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from datetime import datetime
import httpx
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from config.database_config import get_db
from config.logging_config import get_logger
from database.models import User, Project
from services.api_gateway.auth import (
    authenticate_user,
    create_user,
    create_token_pair,
    get_current_user,
    verify_refresh_token
)
from services.api_gateway.config import settings

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["Protected"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: str = Field(..., min_length=2)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class ProjectResponse(BaseModel):
    id: str
    name: str
    url: str
    ff_score: Optional[float]
    last_audit: Optional[datetime]
    status: str


class HITLTaskResponse(BaseModel):
    id: str
    task_type: str
    entity_id: str
    entity_type: str
    priority: int
    impact_score: float
    effort_score: float
    changes: Dict[str, Any]
    created_at: datetime
    status: str


class ApprovalRequest(BaseModel):
    comment: Optional[str] = None


@router.post("/auth/register")
async def register(
    request: RegisterRequest,
    db: Session = Depends(get_db)
):
    existing_user = db.query(User).filter(User.email == request.email).first()
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    try:
        user = create_user(
            db=db,
            email=request.email,
            password=request.password,
            full_name=request.full_name
        )
        
        tokens = create_token_pair(user_id=str(user.id), email=user.email)
        
        logger.info(f"New user registered: {user.email}")
        
        return {
            "success": True,
            "message": "User registered successfully",
            "user": {
                "id": str(user.id),
                "email": user.email,
                "full_name": user.full_name
            },
            "tokens": tokens
        }
        
    except Exception as e:
        logger.error(f"Registration error: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/login")
async def login(
    request: LoginRequest,
    db: Session = Depends(get_db)
):
    user = authenticate_user(db, request.email, request.password)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password"
        )
    
    tokens = create_token_pair(user_id=str(user.id), email=user.email)
    
    logger.info(f"User logged in: {user.email}")
    
    return {
        "success": True,
        "user": {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name
        },
        "tokens": tokens
    }


@router.post("/auth/refresh")
async def refresh_token(
    request: RefreshTokenRequest,
    db: Session = Depends(get_db)
):
    token_data = verify_refresh_token(request.refresh_token)
    
    user = db.query(User).filter(User.id == token_data.user_id).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )
    
    tokens = create_token_pair(user_id=str(user.id), email=user.email)
    
    return {
        "success": True,
        "tokens": tokens
    }


@router.get("/me")
async def get_current_user_info(
    current_user: User = Depends(get_current_user)
):
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "full_name": current_user.full_name,
        "is_active": current_user.is_active,
        "created_at": current_user.created_at.isoformat()
    }


@router.get("/dashboard")
async def get_dashboard(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        projects = db.query(Project).filter(Project.user_id == current_user.id).all()
        
        dashboard_data = {
            "user": {
                "email": current_user.email,
                "full_name": current_user.full_name
            },
            "projects_count": len(projects),
            "projects": []
        }
        
        for project in projects:
            dashboard_data["projects"].append({
                "id": str(project.id),
                "name": project.name,
                "url": project.url,
                "ff_score": project.ff_score,
                "last_audit": project.last_audit.isoformat() if project.last_audit else None,
                "status": project.status
            })
        
        return dashboard_data
        
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/hitl/tasks", response_model=List[HITLTaskResponse])
async def get_hitl_tasks(
    current_user: User = Depends(get_current_user),
    status_filter: Optional[str] = "pending",
    limit: int = 50
):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.MANAGEMENT_SERVICE_URL}/api/hitl/tasks",
                params={
                    "user_id": str(current_user.id),
                    "status": status_filter,
                    "limit": limit
                }
            )
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail="Failed to fetch HITL tasks"
                )
            
            return response.json()
            
    except httpx.RequestError as e:
        logger.error(f"Failed to connect to Management Service: {e}")
        raise HTTPException(
            status_code=503,
            detail="Management service temporarily unavailable"
        )


@router.post("/hitl/approve/{task_id}")
async def approve_task(
    task_id: str,
    request: ApprovalRequest,
    current_user: User = Depends(get_current_user)
):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{settings.MANAGEMENT_SERVICE_URL}/api/hitl/approve/{task_id}",
                json={
                    "user_id": str(current_user.id),
                    "comment": request.comment
                }
            )
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail="Failed to approve task"
                )
            
            logger.info(f"Task {task_id} approved by {current_user.email}")
            
            return response.json()
            
    except httpx.RequestError as e:
        logger.error(f"Failed to connect to Management Service: {e}")
        raise HTTPException(
            status_code=503,
            detail="Management service temporarily unavailable"
        )


@router.post("/hitl/reject/{task_id}")
async def reject_task(
    task_id: str,
    request: ApprovalRequest,
    current_user: User = Depends(get_current_user)
):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{settings.MANAGEMENT_SERVICE_URL}/api/hitl/reject/{task_id}",
                json={
                    "user_id": str(current_user.id),
                    "comment": request.comment
                }
            )
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail="Failed to reject task"
                )
            
            logger.info(f"Task {task_id} rejected by {current_user.email}")
            
            return response.json()
            
    except httpx.RequestError as e:
        logger.error(f"Failed to connect to Management Service: {e}")
        raise HTTPException(
            status_code=503,
            detail="Management service temporarily unavailable"
        )
