from pydantic import BaseModel, Field, EmailStr, validator
from typing import Optional, Dict, Any
from datetime import datetime


class RegisterRequest(BaseModel):
    email: EmailStr = Field(..., description="User email")
    password: str = Field(..., min_length=8, max_length=128, description="User password")
    full_name: str = Field(..., min_length=2, max_length=100, description="User full name")
    
    @validator("password")
    def validate_password(cls, v):
        if not any(char.isdigit() for char in v):
            raise ValueError("Password must contain at least one digit")
        if not any(char.isupper() for char in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(char.islower() for char in v):
            raise ValueError("Password must contain at least one lowercase letter")
        return v
    
    class Config:
        schema_extra = {
            "example": {
                "email": "user@example.com",
                "password": "SecurePass123",
                "full_name": "John Doe"
            }
        }


class LoginRequest(BaseModel):
    email: EmailStr = Field(..., description="User email")
    password: str = Field(..., description="User password")
    
    class Config:
        schema_extra = {
            "example": {
                "email": "user@example.com",
                "password": "SecurePass123"
            }
        }


class TokenPair(BaseModel):
    access_token: str = Field(..., description="JWT access token")
    refresh_token: str = Field(..., description="JWT refresh token")
    token_type: str = Field(default="bearer", description="Token type")
    expires_in: Optional[int] = Field(None, description="Access token expiration in seconds")
    
    class Config:
        schema_extra = {
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "token_type": "bearer",
                "expires_in": 1800
            }
        }


class UserResponse(BaseModel):
    id: str = Field(..., description="User ID")
    email: EmailStr = Field(..., description="User email")
    full_name: str = Field(..., description="User full name")
    is_active: bool = Field(default=True)
    created_at: Optional[str] = Field(None, description="ISO 8601 timestamp")
    
    class Config:
        schema_extra = {
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "email": "user@example.com",
                "full_name": "John Doe",
                "is_active": True,
                "created_at": "2026-01-15T10:30:00Z"
            }
        }


class LoginResponse(BaseModel):
    success: bool = True
    message: Optional[str] = Field(default="Login successful")
    user: UserResponse
    tokens: TokenPair


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(..., description="JWT refresh token")
    
    class Config:
        schema_extra = {
            "example": {
                "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
            }
        }


class ProjectResponse(BaseModel):
    id: str = Field(..., description="Project ID")
    name: str = Field(..., description="Project name")
    url: str = Field(..., description="Project URL")
    ff_score: Optional[float] = Field(None, ge=0, le=100, description="FF-Score (0-100)")
    last_audit: Optional[str] = Field(None, description="ISO 8601 timestamp")
    status: str = Field(..., description="active, paused, archived")
    created_at: Optional[str] = Field(None, description="ISO 8601 timestamp")
    
    class Config:
        schema_extra = {
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "name": "My Website",
                "url": "https://example.com",
                "ff_score": 78.5,
                "last_audit": "2026-02-04T20:00:00Z",
                "status": "active",
                "created_at": "2026-01-01T10:00:00Z"
            }
        }


class HITLTaskResponse(BaseModel):
    id: str = Field(..., description="Task ID")
    task_type: str = Field(..., description="wordpress_meta, wordpress_content, tilda_page, etc")
    entity_id: str = Field(..., description="Post ID / Page ID")
    entity_type: str = Field(..., description="wordpress_post, tilda_page")
    priority: int = Field(..., ge=1, le=10, description="Priority 1-10")
    impact_score: float = Field(..., ge=0, description="Impact score")
    effort_score: float = Field(..., ge=0, description="Effort score")
    changes: Dict[str, Any] = Field(..., description="Proposed changes (before/after)")
    created_at: str = Field(..., description="ISO 8601 timestamp")
    status: str = Field(..., description="pending_approval, approved, rejected, applied")
    project_id: Optional[str] = None
    
    @validator("status")
    def validate_status(cls, v):
        allowed = ["pending_approval", "approved", "rejected", "applied", "failed"]
        if v not in allowed:
            raise ValueError(f"Status must be one of {allowed}")
        return v
    
    class Config:
        schema_extra = {
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "task_type": "wordpress_meta",
                "entity_id": "123",
                "entity_type": "wordpress_post",
                "priority": 8,
                "impact_score": 7.5,
                "effort_score": 2.0,
                "changes": {
                    "before": {"title": "Old Title"},
                    "after": {"title": "Optimized Title with Keywords"}
                },
                "created_at": "2026-02-04T20:00:00Z",
                "status": "pending_approval"
            }
        }


class ApprovalRequest(BaseModel):
    comment: Optional[str] = Field(None, max_length=500, description="Approval/rejection comment")
    
    class Config:
        schema_extra = {
            "example": {
                "comment": "Approved after review"
            }
        }


class DashboardResponse(BaseModel):
    user: UserResponse
    projects_count: int = Field(..., ge=0)
    projects: list[ProjectResponse] = Field(default_factory=list)
    pending_hitl_tasks: Optional[int] = Field(None, ge=0)
    
    class Config:
        schema_extra = {
            "example": {
                "user": {
                    "id": "550e8400-e29b-41d4-a716-446655440000",
                    "email": "user@example.com",
                    "full_name": "John Doe",
                    "is_active": True
                },
                "projects_count": 3,
                "projects": [],
                "pending_hitl_tasks": 5
            }
        }
