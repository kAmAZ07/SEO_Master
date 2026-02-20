from pydantic import BaseModel, AnyUrl, Field


class FFScoreRequest(BaseModel):
    project_id: str | None = None
    root_url: AnyUrl

    content_text: str = ""
    freshness_days_since_update: int = Field(default=30, ge=0, le=3650)
    serp_shift: float = Field(default=0.0, ge=-50.0, le=50.0)
    link_velocity: float = Field(default=0.0, ge=0.0, le=100.0)

    semantic_distance: float = Field(default=50.0, ge=0.0, le=100.0)
    keyword_coverage: float = Field(default=50.0, ge=0.0, le=100.0)

    cwv_grade: str = "unknown"
    broken_links_count: int = Field(default=0, ge=0, le=1000000)
    schema_errors_count: int = Field(default=0, ge=0, le=1000000)

    backlinks_count: int = Field(default=0, ge=0, le=100000000)
    brand_mentions: int = Field(default=0, ge=0, le=1000000)
    authoritative_outbound_links: int = Field(default=0, ge=0, le=1000000)

    has_https: bool = True
    has_privacy_policy: bool = False
    has_contacts: bool = False
    has_author_schema: bool = False


class FFScoreResponse(BaseModel):
    ff_score_id: str
    eeat_score_id: str
    ff_score: float
    components: dict
    inputs: dict
    eeat: dict