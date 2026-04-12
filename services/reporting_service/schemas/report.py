from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ReportType(str, Enum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    CHANGELOG = "changelog"
    RAW = "raw"


class ReportSlice(str, Enum):
    REPORT_SNAPSHOT = "report_snapshot"
    RAW_DATA = "raw_data"
    AGGREGATES = "aggregates"
    CHANGELOG = "changelog"


class ReportSource(str, Enum):
    GSC = "gsc"
    GA4 = "ga4"
    YANDEX = "yandex"


class ReportGenerationRequest(BaseModel):
    project_id: str
    root_url: str
    report_type: ReportType = ReportType.WEEKLY
    period_start: date | None = None
    period_end: date | None = None
    include_sources: list[ReportSource] = Field(
        default_factory=lambda: [ReportSource.GSC, ReportSource.GA4, ReportSource.YANDEX]
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_period(self) -> "ReportGenerationRequest":
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("period_start_must_not_be_after_period_end")
        return self


class MetricsCalculationRequest(BaseModel):
    project_id: str | None = None
    root_url: str = ""
    cost: float = 0.0
    revenue: float = 0.0
    hitl_actions: int = 0
    automated_actions: int = 0
    trust_inputs: dict[str, Any] = Field(default_factory=dict)
    metric_id: str | None = None


class Report(BaseModel):
    report_id: str
    project_id: str | None
    root_url: str
    report_type: ReportType | None = None
    created_at: datetime
    data: dict[str, Any]
