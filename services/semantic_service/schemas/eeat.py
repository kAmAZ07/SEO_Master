from pydantic import AnyUrl, BaseModel, ConfigDict, Field

MAX_TEXT_CHARS = 1_000_000


class EEATRequest(BaseModel):
    """Сигналы E-E-A-T. `None` — «не проверялось»; это не то же самое, что «нет»."""

    project_id: str | None = None
    root_url: AnyUrl
    text: str = Field(default="", max_length=MAX_TEXT_CHARS)

    backlinks_count: int | None = Field(default=None, ge=0, le=100000000)
    brand_mentions: int | None = Field(default=None, ge=0, le=1000000)
    authoritative_outbound_links: int | None = Field(default=None, ge=0, le=1000000)

    has_https: bool | None = None
    has_privacy_policy: bool | None = None
    has_contacts: bool | None = None
    has_author_schema: bool | None = None


class EEATResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score_id: str
    formula_version: str
    status: str  # complete | insufficient_data
    score: float | None  # null при status = insufficient_data
    breakdown: dict
    partial: dict | None
    missing_inputs: dict
    heuristic: bool
    signals: dict
