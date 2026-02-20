from pydantic import BaseModel, AnyUrl, Field


class EEATRequest(BaseModel):
    project_id: str | None = None
    root_url: AnyUrl
    text: str = ""

    backlinks_count: int = Field(default=0, ge=0, le=100000000)
    brand_mentions: int = Field(default=0, ge=0, le=1000000)
    authoritative_outbound_links: int = Field(default=0, ge=0, le=1000000)

    has_https: bool = True
    has_privacy_policy: bool = False
    has_contacts: bool = False
    has_author_schema: bool = False


class EEATResponse(BaseModel):
    score_id: str
    score: float
    breakdown: dict
    signals: dict