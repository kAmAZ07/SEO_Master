"""RecommendationVersion: единое неизменяемое типизированное предложение (ТЗ §9.1, §3.3).

Это единственная схема предложения. Она живёт в коде semantic_service; согласование и исполнение
(AR-4, AR-6) читают её отсюда, свою копию не заводят. JSON Schema доступна как
`recommendation_version_json_schema()` и по `GET /semantic/schemas/recommendation-version`.

Принципы:
  * версия неизменяема (frozen-модели, кортежи) и защищена `content_hash`: хеш от канонического
    JSON всех существенных полей, без идентификаторов, номера версии и времени;
  * «не изменять» (`unchanged`), «удалить» (`remove`) и «установить» (`set`) — разные режимы,
    пустое значение никогда не подставляется вместо отсутствующего diff;
  * `before` и `after` — точные строки; значение JSON-LD хранится канонической JSON-строкой,
    как его принимает WordPress-плагин v2 (`{"op":"set","field":"jsonld","value":"<JSON string>"}`);
  * неизвестные поля отвергаются (`extra="forbid"`), неизвестный тип операции — тоже.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "recommendation_version/1"
GENERATOR_VERSION = "proposal_generator/1"
VALIDATOR_VERSION = "proposal_validator/1"

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

MAX_CHANGES = 20
MAX_EVIDENCE = 50
MAX_PRECONDITIONS = 100
MAX_URL_LENGTH = 2048
MAX_VALUE_LENGTH = 8192


class OperationType(str, Enum):
    TITLE = "title"
    DESCRIPTION = "description"
    H1 = "h1"
    SCHEMA_JSONLD = "schema_jsonld"
    CONTENT_EDIT = "content_edit"
    INTERNAL_LINK = "internal_link"


class ChangeMode(str, Enum):
    SET = "set"  # установить after
    REMOVE = "remove"  # явно удалить значение
    UNCHANGED = "unchanged"  # явно не менять


class ExecutionMode(str, Enum):
    MANAGED = "managed"  # возможна управляемая запись через поддерживаемый CMS-механизм
    MANUAL = "manual"  # только ручная задача: автоматическая запись не заявляется


BeforeState = Literal["missing", "value"]

# Допустимые пути изменений по типу операции. Всё остальное отвергается.
PATHS: dict[OperationType, re.Pattern[str]] = {
    OperationType.TITLE: re.compile(r"^head\.title$"),
    OperationType.DESCRIPTION: re.compile(r"^head\.meta_description$"),
    OperationType.H1: re.compile(r"^body\.h1$"),
    OperationType.SCHEMA_JSONLD: re.compile(r"^head\.jsonld$"),
    OperationType.CONTENT_EDIT: re.compile(r"^body\.content_block:[A-Za-z0-9_-]{1,64}$"),
    OperationType.INTERNAL_LINK: re.compile(r"^body\.internal_link:[0-9]{1,2}$"),
}
# Поле CMS-команды WordPress v2, в которое отображается путь; None — в плагине такого поля нет.
CMS_FIELDS: dict[OperationType, str | None] = {
    OperationType.TITLE: "title",
    OperationType.DESCRIPTION: "description",
    OperationType.H1: None,
    OperationType.SCHEMA_JSONLD: "jsonld",
    OperationType.CONTENT_EDIT: None,
    OperationType.INTERNAL_LINK: None,
}
# Типы, для которых плагин v2 имеет управляемую запись (docs/WORDPRESS.md). H1, контент и внутренние
# ссылки там явно «unsupported_manual»: честный ручной статус, а не фиктивное внедрение.
MANAGED_OPERATIONS = frozenset({OperationType.TITLE, OperationType.DESCRIPTION, OperationType.SCHEMA_JSONLD})


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)


class ExternalEntity(_Frozen):
    kind: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_]*$")
    id: str = Field(min_length=1, max_length=128)


class Target(_Frozen):
    origin: str = Field(min_length=8, max_length=300)
    url: str = Field(min_length=8, max_length=MAX_URL_LENGTH)
    entity: ExternalEntity | None = None


class EvidenceRef(_Frozen):
    evidence_id: str = Field(min_length=1, max_length=128)
    sha256: str

    @field_validator("sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        if not SHA256_RE.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value


class Change(_Frozen):
    path: str = Field(min_length=1, max_length=100)
    mode: ChangeMode
    before_state: BeforeState
    before: str | None = Field(default=None, max_length=MAX_VALUE_LENGTH)
    after: str | None = Field(default=None, max_length=MAX_VALUE_LENGTH)
    cms_field: Literal["title", "description", "jsonld"] | None = None

    @model_validator(mode="after")
    def _shape(self) -> "Change":
        # before_state различает отсутствующее значение и пустую строку.
        if self.before_state == "missing" and self.before is not None:
            raise ValueError("before must be null when before_state is missing")
        if self.before_state == "value" and self.before is None:
            raise ValueError("before is required when before_state is value")
        # after — только у set; remove и unchanged не несут значения.
        if self.mode is ChangeMode.SET and self.after is None:
            raise ValueError("after is required for mode set")
        if self.mode is not ChangeMode.SET and self.after is not None:
            raise ValueError("after must be null unless mode is set")
        return self


class Precondition(_Frozen):
    kind: Literal["before_digest", "evidence_hash", "target_in_project"]
    path: str | None = Field(default=None, max_length=100)
    state: BeforeState | None = None
    sha256: str | None = None
    evidence_id: str | None = Field(default=None, max_length=128)
    project_id: str | None = Field(default=None, max_length=36)
    origin: str | None = Field(default=None, max_length=300)


class ExpectedCheck(_Frozen):
    kind: Literal["field_equals_after", "field_removed", "target_http_ok"]
    path: str | None = Field(default=None, max_length=100)
    sha256: str | None = None
    url: str | None = Field(default=None, max_length=MAX_URL_LENGTH)


class FieldProvenance(_Frozen):
    path: str = Field(max_length=100)
    source: Literal["llm", "cache", "deterministic"]
    provider: str = Field(max_length=40)
    model: str = Field(max_length=100)
    generated_at: str


class RecommendationVersion(_Frozen):
    schema_version: Literal["recommendation_version/1"] = SCHEMA_VERSION
    version_id: str
    recommendation_id: str
    version: int = Field(ge=1, le=10_000)
    previous_version_id: str | None = None

    project_id: str
    task_id: str | None = Field(default=None, max_length=128)
    target: Target
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1, max_length=MAX_EVIDENCE)

    operation_type: OperationType
    execution_mode: ExecutionMode
    manual_reason: str | None = Field(default=None, max_length=200)
    changes: tuple[Change, ...] = Field(min_length=1, max_length=MAX_CHANGES)
    preconditions: tuple[Precondition, ...] = Field(min_length=1, max_length=MAX_PRECONDITIONS)
    expected_checks: tuple[ExpectedCheck, ...] = Field(default=(), max_length=MAX_PRECONDITIONS)
    limitations: tuple[str, ...] = Field(default=(), max_length=20)

    generator_version: str = GENERATOR_VERSION
    validator_version: str = VALIDATOR_VERSION
    prompt_version: str | None = None
    provider: str = Field(max_length=40)
    model: str = Field(max_length=100)
    provenance: tuple[FieldProvenance, ...] = Field(min_length=1, max_length=MAX_CHANGES)

    created_at: str
    content_hash: str

    @field_validator("version_id", "recommendation_id", "project_id")
    @classmethod
    def _uuid(cls, value: str) -> str:
        if not UUID_RE.fullmatch(value):
            raise ValueError("must be a canonical lowercase UUID")
        return value

    @field_validator("previous_version_id")
    @classmethod
    def _previous_uuid(cls, value: str | None) -> str | None:
        if value is not None and not UUID_RE.fullmatch(value):
            raise ValueError("must be a canonical lowercase UUID")
        return value

    @field_validator("content_hash")
    @classmethod
    def _content_hash(cls, value: str) -> str:
        if not SHA256_RE.fullmatch(value):
            raise ValueError("content_hash must be 64 lowercase hex characters")
        return value

    @field_validator("created_at")
    @classmethod
    def _created_at(cls, value: str) -> str:
        parse_utc(value)
        return value

    @model_validator(mode="after")
    def _consistency(self) -> "RecommendationVersion":
        if (self.version == 1) != (self.previous_version_id is None):
            raise ValueError("previous_version_id is required exactly when version > 1")
        if self.previous_version_id == self.version_id:
            raise ValueError("a version cannot precede itself")
        if (self.execution_mode is ExecutionMode.MANUAL) != (self.manual_reason is not None):
            raise ValueError("manual_reason is required exactly for manual execution mode")
        if self.execution_mode is ExecutionMode.MANAGED and self.operation_type not in MANAGED_OPERATIONS:
            raise ValueError("operation type has no managed CMS path")
        return self


def parse_utc(value: str) -> datetime:
    """Строгий разбор времени: только с указанием часового пояса, результат в UTC."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise ValueError("timestamp must be ISO 8601") from None
    if parsed.tzinfo is None:
        raise ValueError("timestamp must carry a UTC offset")
    return parsed.astimezone(timezone.utc)


def utc_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# Поля, которые не входят в content_hash: идентичность записи и время, а не содержание предложения.
_HASH_EXCLUDED = frozenset({"version_id", "recommendation_id", "version", "previous_version_id", "created_at", "content_hash"})


def compute_content_hash(version: RecommendationVersion | dict[str, Any]) -> str:
    """SHA-256 канонического JSON существенных полей. Время генерации по полям (`generated_at`) и способ
    доставки (`source`: llm или cache) исключены, поэтому повторная генерация того же содержимого тем же
    провайдером даёт тот же хеш и не плодит версии."""
    data = version.model_dump(mode="json") if isinstance(version, RecommendationVersion) else dict(version)
    payload = {key: value for key, value in data.items() if key not in _HASH_EXCLUDED}
    payload["provenance"] = [
        {k: v for k, v in item.items() if k not in ("generated_at", "source")} for item in payload.get("provenance", [])
    ]
    return sha256_text("SEO-MASTER-RECOMMENDATION-V1\n" + canonical_json(payload))


def recommendation_version_json_schema() -> dict[str, Any]:
    """Опубликованная JSON Schema. Источник истины — модель выше, отдельного файла схемы нет."""
    return RecommendationVersion.model_json_schema()
