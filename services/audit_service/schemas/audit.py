from datetime import datetime
from pydantic import BaseModel, AnyUrl, Field


class AuditOptions(BaseModel):
    max_pages: int = Field(default=10, ge=1, le=100000)
    max_depth: int = Field(default=2, ge=0, le=10)
    js_render: bool = False
    respect_robots: bool = True
    concurrency: int = Field(default=5, ge=1, le=20)
    timeout: float = Field(default=10.0, ge=1.0, le=300.0)


class BaseAuditRequest(BaseModel):
    root_url: AnyUrl
    project_id: str | None = Field(default=None)
    site_type_hint: str | None = Field(default="unknown")
    platform: str | None = Field(default="generic")
    seeds: list[str] = Field(default_factory=list)
    options: AuditOptions = Field(default_factory=AuditOptions)


def _default_full_audit_options() -> AuditOptions:
    return AuditOptions(max_pages=1000, max_depth=4, js_render=True, timeout=30.0)


class PublicAuditRequest(BaseAuditRequest):
    project_id: str | None = Field(default=None)


class FullAuditRequest(BaseAuditRequest):
    project_id: str = Field(..., min_length=1)
    options: AuditOptions = Field(default_factory=_default_full_audit_options)


class AuditQueuedResponse(BaseModel):
    audit_id: str
    status: str
    mode: str
    project_id: str | None = None


class PublicAuditResponse(AuditQueuedResponse):
    pass


class FullAuditResponse(AuditQueuedResponse):
    pass


class AuditStatusResponse(BaseModel):
    audit_id: str
    project_id: str | None = None
    root_url: str
    mode: str
    status: str
    summary: dict
    findings: list
    pages: list
    created_at: datetime
    updated_at: datetime
