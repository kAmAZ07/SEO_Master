from datetime import datetime
from pydantic import BaseModel


class Report(BaseModel):
    report_id: str
    project_id: str | None
    root_url: str
    created_at: datetime
    data: dict