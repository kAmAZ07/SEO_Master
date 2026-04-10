from pydantic import AnyUrl, BaseModel, Field


class SemanticAnalysisRequest(BaseModel):
    project_id: str | None = None
    root_url: AnyUrl
    audit_id: str | None = None
    analysis_id: str | None = None
    mode: str | None = None

    content_text: str = ""
    pages: list[dict] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    serp_top10_texts: list[str] = Field(default_factory=list)


class SemanticAnalysisResponse(BaseModel):
    analysis_id: str
    project_id: str | None = None
    root_url: str
    content_gap: dict
    semantic_distance: dict
    keyword_coverage: dict
    inputs: dict
