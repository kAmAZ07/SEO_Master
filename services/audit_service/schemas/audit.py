from datetime import datetime
from pydantic import BaseModel, AnyUrl, Field


class AuditOptions(BaseModel):
    max_pages: int = Field(default=10, ge=1, le=100)
    max_depth: int = Field(default=2, ge=0, le=10)
    js_render: bool = False
    respect_robots: bool = True
    concurrency: int = Field(default=5, ge=1, le=20)
    timeout: float = Field(default=10.0, ge=1.0, le=60.0)


class PublicAuditRequest(BaseModel):
    root_url: AnyUrl
    site_type_hint: str | None = Field(default="unknown")
    platform: str | None = Field(default="generic")
    seeds: list[str] = Field(default_factory=list)
    options: AuditOptions = Field(default_factory=AuditOptions)


class PublicAuditResponse(BaseModel):
    audit_id: str
    status: str


class AuditStatusResponse(BaseModel):
    audit_id: str
    root_url: str
    status: str
    summary: dict
    findings: list
    pages: list
    created_at: datetime
    updated_at: datetime