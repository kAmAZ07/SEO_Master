import json
from datetime import datetime, timezone
from pydantic import BaseModel


class CrawlCompletedEvent(BaseModel):
    event_name: str = "CrawlCompleted"
    audit_id: str
    root_url: str
    produced_at: str
    summary: dict

    @classmethod
    def build(cls, audit_id: str, root_url: str, summary: dict) -> "CrawlCompletedEvent":
        return cls(audit_id=audit_id, root_url=root_url, produced_at=datetime.now(timezone.utc).isoformat(), summary=summary)

    def to_bytes(self) -> bytes:
        return json.dumps(self.model_dump(), ensure_ascii=False).encode("utf-8")