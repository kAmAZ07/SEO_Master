from typing import Optional, Dict, Any, List, TypedDict
from datetime import datetime

from pydantic import BaseModel, Field, UUID4

from services.management_service.db.models import HITLStatus


class HITLDiffData(TypedDict, total=False):
    before: Dict[str, Any]
    after: Dict[str, Any]


class HITLApprovalBase(BaseModel):
    task_id: UUID4
    project_id: Optional[UUID4] = None
    diff_data: HITLDiffData
    impact_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    recommendation: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        use_enum_values = True


class HITLApprovalCreate(HITLApprovalBase):
    pass


class HITLApprovalResponse(BaseModel):
    id: UUID4
    task_id: UUID4
    project_id: UUID4
    status: HITLStatus
    diff_data: HITLDiffData
    impact_score: Optional[float]
    recommendation: Optional[str]
    approved_by: Optional[str]
    approved_at: Optional[datetime]
    rejected_by: Optional[str]
    rejected_at: Optional[datetime]
    rejection_reason: Optional[str]
    metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        use_enum_values = True


class HITLDecision(BaseModel):
    auto_deploy: bool = True
    notes: Optional[str] = Field(None, max_length=1000)
    rejection_reason: Optional[str] = Field(None, max_length=1000)


class HITLApprovalListResponse(BaseModel):
    approvals: List[HITLApprovalResponse]
    total: int
    page: int
    page_size: int

    class Config:
        from_attributes = True


class HITLBatchApproveRequest(BaseModel):
    task_ids: List[UUID4]
    approved_by: str
    auto_deploy: bool = True
    correlation_id: Optional[str] = None


class HITLBatchApproveItemResult(BaseModel):
    task_id: UUID4
    success: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class HITLBatchApproveResponse(BaseModel):
    total: int
    approved: int
    failed: int
    results: List[HITLBatchApproveItemResult]
