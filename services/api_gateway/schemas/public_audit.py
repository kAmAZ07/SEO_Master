from pydantic import BaseModel, Field, validator, EmailStr
from typing import Optional, Dict, Any, List
from datetime import datetime
import re


class QuickAuditRequest(BaseModel):
    url: str = Field(..., description="Website URL to audit", min_length=10, max_length=500)
    email: Optional[EmailStr] = Field(None, description="Email for results notification")
    
    @validator("url")
    def validate_url(cls, v):
        v = v.strip().lower()
        
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        
        blocked_hosts = [
            "localhost",
            "127.0.0.1",
            "0.0.0.0",
            "192.168.",
            "10.",
            "172.16.",
            "172.17.",
            "172.18.",
            "172.19.",
            "172.20.",
            "172.21.",
            "172.22.",
            "172.23.",
            "172.24.",
            "172.25.",
            "172.26.",
            "172.27.",
            "172.28.",
            "172.29.",
            "172.30.",
            "172.31.",
            "[::1]",
            "169.254."
        ]
        
        for blocked in blocked_hosts:
            if blocked in v:
                raise ValueError("Cannot audit localhost or internal network URLs")
        
        url_pattern = re.compile(
            r'^https?://'
            r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'
            r'localhost|'
            r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'
            r'(?::\d+)?'
            r'(?:/?|[/?]\S+)$', re.IGNORECASE
        )
        
        if not url_pattern.match(v):
            raise ValueError("Invalid URL format")
        
        return v
    
    class Config:
        schema_extra = {
            "example": {
                "url": "https://example.com",
                "email": "user@example.com"
            }
        }


class AuditIssue(BaseModel):
    category: str = Field(..., description="Issue category: technical, content, seo, performance")
    severity: str = Field(..., description="critical, high, medium, low")
    title: str = Field(..., description="Issue title")
    description: str = Field(..., description="Issue description")
    affected_pages: List[str] = Field(default_factory=list)
    recommendation: Optional[str] = None


class CoreWebVitals(BaseModel):
    lcp: Optional[float] = Field(None, description="Largest Contentful Paint (seconds)")
    fid: Optional[float] = Field(None, description="First Input Delay (milliseconds)")
    cls: Optional[float] = Field(None, description="Cumulative Layout Shift")
    fcp: Optional[float] = Field(None, description="First Contentful Paint (seconds)")
    ttfb: Optional[float] = Field(None, description="Time to First Byte (milliseconds)")
    performance_score: Optional[int] = Field(None, ge=0, le=100)


class AuditResults(BaseModel):
    pages_crawled: int = Field(..., ge=0)
    issues_found: int = Field(..., ge=0)
    critical_issues: int = Field(default=0, ge=0)
    high_issues: int = Field(default=0, ge=0)
    medium_issues: int = Field(default=0, ge=0)
    low_issues: int = Field(default=0, ge=0)
    issues: List[AuditIssue] = Field(default_factory=list)
    core_web_vitals: Optional[CoreWebVitals] = None
    seo_score: Optional[int] = Field(None, ge=0, le=100)
    recommendations_count: int = Field(default=0, ge=0)


class AuditStatusResponse(BaseModel):
    uid: str = Field(..., description="Unique audit identifier")
    status: str = Field(..., description="pending, in_progress, completed, failed")
    progress: int = Field(..., ge=0, le=100, description="Progress percentage")
    message: str = Field(..., description="Status message")
    url: Optional[str] = None
    results: Optional[AuditResults] = None
    created_at: str = Field(..., description="ISO 8601 timestamp")
    completed_at: Optional[str] = Field(None, description="ISO 8601 timestamp")
    error: Optional[str] = None
    
    @validator("status")
    def validate_status(cls, v):
        allowed_statuses = ["pending", "in_progress", "completed", "failed", "expired"]
        if v not in allowed_statuses:
            raise ValueError(f"Status must be one of {allowed_statuses}")
        return v
    
    class Config:
        schema_extra = {
            "example": {
                "uid": "550e8400-e29b-41d4-a716-446655440000",
                "status": "completed",
                "progress": 100,
                "message": "Audit completed successfully",
                "url": "https://example.com",
                "results": {
                    "pages_crawled": 10,
                    "issues_found": 15,
                    "critical_issues": 2,
                    "high_issues": 5,
                    "medium_issues": 6,
                    "low_issues": 2
                },
                "created_at": "2026-02-04T21:00:00Z",
                "completed_at": "2026-02-04T21:01:30Z"
            }
        }


class RateLimitInfo(BaseModel):
    limit: int = Field(..., description="Maximum requests allowed")
    remaining: int = Field(..., ge=0, description="Remaining requests")
    reset_in_seconds: int = Field(..., ge=0, description="Seconds until limit reset")
    window_seconds: int = Field(..., description="Rate limit window in seconds")
    
    class Config:
        schema_extra = {
            "example": {
                "limit": 5,
                "remaining": 3,
                "reset_in_seconds": 2400,
                "window_seconds": 3600
            }
        }


class QuickAuditResponse(BaseModel):
    success: bool = True
    uid: str = Field(..., description="Audit unique identifier")
    status: str = Field(default="pending")
    message: str = Field(..., description="Response message")
    estimated_time_seconds: int = Field(default=60, ge=0)
    
    class Config:
        schema_extra = {
            "example": {
                "success": True,
                "uid": "550e8400-e29b-41d4-a716-446655440000",
                "status": "pending",
                "message": "Audit started. Check status using the provided UID.",
                "estimated_time_seconds": 60
            }
        }
