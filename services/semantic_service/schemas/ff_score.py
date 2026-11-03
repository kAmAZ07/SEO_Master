from pydantic import AnyUrl, BaseModel, ConfigDict, Field

# Допустимые уровни доступности, которые вызывающий может указать явно (см. scoring/readings.py).
MAX_TEXT_CHARS = 1_000_000
AVAILABILITY_VALUES = ("measured", "provided", "derived", "estimated", "proxy", "mock", "sample")


class FFScoreRequest(BaseModel):
    """Вход FF-Score. `None` везде означает «не передано»: подставлять значение по умолчанию нельзя."""

    project_id: str | None = None
    root_url: AnyUrl

    content_text: str = Field(default="", max_length=MAX_TEXT_CHARS)
    freshness_days_since_update: int | None = Field(default=None, ge=0, le=3650)
    serp_shift: float | None = Field(default=None, ge=-50.0, le=50.0, allow_inf_nan=False)
    link_velocity: float | None = Field(default=None, ge=0.0, le=100.0, allow_inf_nan=False)

    semantic_distance: float | None = Field(default=None, ge=0.0, le=100.0, allow_inf_nan=False)
    keyword_coverage: float | None = Field(default=None, ge=0.0, le=100.0, allow_inf_nan=False)

    cwv_grade: str | None = None
    broken_links_count: int | None = Field(default=None, ge=0, le=1000000)
    schema_errors_count: int | None = Field(default=None, ge=0, le=1000000)

    backlinks_count: int | None = Field(default=None, ge=0, le=100000000)
    brand_mentions: int | None = Field(default=None, ge=0, le=1000000)
    authoritative_outbound_links: int | None = Field(default=None, ge=0, le=1000000)

    has_https: bool | None = None
    has_privacy_policy: bool | None = None
    has_contacts: bool | None = None
    has_author_schema: bool | None = None

    audit_summary: dict | None = None
    # None — «находки аудита не переданы»; пустой список — «аудит прошёл, находок нет».
    audit_findings: list[dict] | None = None
    input_sources: dict = Field(default_factory=dict)
    # Явное качество данных по имени входа: measured|provided|derived|estimated|proxy|mock|sample.
    input_availability: dict[str, str] = Field(default_factory=dict)


class FFScoreResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ff_score_id: str
    eeat_score_id: str
    formula_version: str
    status: str  # complete | insufficient_data
    ff_score: float | None  # null при status = insufficient_data
    components: dict
    weights: dict
    thresholds: dict
    mode: dict | None
    coverage: dict
    partial: dict | None
    heuristic: bool
    note: str
    inputs: dict
    eeat: dict
