# services/management_service/schemas/task.py

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, UUID4
from typing_extensions import TypedDict

from services.management_service.db.models import TaskStatus, TaskType


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
    model_config = ConfigDict(use_enum_values=True)

    project_id: UUID4
    task_type: TaskType
    url: str = Field(..., max_length=2048)
    title: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = None
    impact_score: Optional[float] = Field(0.5, ge=0.0, le=1.0)
    effort_score: Optional[float] = Field(0.5, ge=0.0, le=1.0)
    metadata: TaskMetadata = Field(default_factory=dict)


class TaskCreate(TaskBase):
    pass


class TaskUpdate(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    task_type: Optional[TaskType] = None
    status: Optional[TaskStatus] = None
    url: Optional[str] = Field(None, max_length=2048)
    title: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = None
    impact_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    effort_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    metadata: Optional[TaskMetadata] = None
    assigned_to: Optional[str] = None


class TaskStatusUpdate(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    status: TaskStatus
    metadata: Optional[TaskMetadata] = None


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

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


class TaskListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tasks: List[TaskResponse]
    total: int
    page: int
    page_size: int


class TaskPrioritizationRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    project_id: UUID4
    max_tasks: Optional[int] = Field(10, ge=1, le=100)
    task_types: Optional[List[TaskType]] = None


class TaskPrioritizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tasks: List[TaskResponse]
    prioritization_method: str = 'Impact x Effort'
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class TaskDeploymentRequest(BaseModel):
    task_id: UUID4
    auto_deploy: bool = True
    correlation_id: Optional[str] = None


class TaskDeploymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    task_id: UUID4
    deployment_status: str
    change_id: Optional[str]
    deployed_at: Optional[datetime]
    error: Optional[str]
