# services/management_service/schemas/task.py

from typing import Optional, Dict, Any, List, TypedDict
from datetime import datetime
from pydantic import BaseModel, Field, UUID4

from services.management_service.db.models import TaskType, TaskStatus


class TaskMetadata(TypedDict, total=False):
    audit_result_id: str
    semantic_result_id: str
    crawl_id: str
    ffscore_task_id: str
    eeat_task_id: str
    content_generation_id: str
    saga_id: str
    correlation_id: str
    diff_data: Dict[str, Any]
    interlinks: List[Dict[str, Any]]
    changes: Dict[str, Any]
    current_ffscore: float
    expected_ffscore: float
    current_eeat: float
    expected_eeat: float
    impact: float
    urgency: float
    effort: float
    priority_score: float
    average_impact_score: float
    deployment: Dict[str, Any]


class TaskBase(BaseModel):
    project_id: UUID4
    task_type: TaskType
    url: str = Field(..., max_length=2048)
    title: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = None
    impact_score: Optional[float] = Field(0.5, ge=0.0, le=1.0)
    effort_score: Optional[float] = Field(0.5, ge=0.0, le=1.0)
    metadata: TaskMetadata = Field(default_factory=dict)
    
    class Config:
        use_enum_values = True


class TaskCreate(TaskBase):
    pass


class TaskUpdate(BaseModel):
    task_type: Optional[TaskType] = None
    status: Optional[TaskStatus] = None
    url: Optional[str] = Field(None, max_length=2048)
    title: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = None
    impact_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    effort_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    metadata: Optional[TaskMetadata] = None
    assigned_to: Optional[str] = None
    
    class Config:
        use_enum_values = True


class TaskStatusUpdate(BaseModel):
    status: TaskStatus
    metadata: Optional[TaskMetadata] = None
    
    class Config:
        use_enum_values = True


class TaskResponse(BaseModel):
    id: UUID4
    project_id: UUID4
    task_type: TaskType
    status: TaskStatus
    url: str
    title: Optional[str]
    description: Optional[str]
    impact_score: float
    effort_score: float
    priority_score: float
    metadata: TaskMetadata
    assigned_to: Optional[str]
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    deployed_at: Optional[datetime]
    
    class Config:
        from_attributes = True
        use_enum_values = True


class TaskListResponse(BaseModel):
    tasks: List[TaskResponse]
    total: int
    page: int
    page_size: int
    
    class Config:
        from_attributes = True


class TaskPrioritizationRequest(BaseModel):
    project_id: UUID4
    max_tasks: Optional[int] = Field(10, ge=1, le=100)
    task_types: Optional[List[TaskType]] = None
    
    class Config:
        use_enum_values = True


class TaskPrioritizationResponse(BaseModel):
    tasks: List[TaskResponse]
    prioritization_method: str = "Impact x Effort"
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        from_attributes = True


class TaskDeploymentRequest(BaseModel):
    task_id: UUID4
    auto_deploy: bool = True
    correlation_id: Optional[str] = None


class TaskDeploymentResponse(BaseModel):
    task_id: UUID4
    deployment_status: str
    change_id: Optional[str]
    deployed_at: Optional[datetime]
    error: Optional[str]
    
    class Config:
        from_attributes = True
