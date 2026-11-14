"""Bounded public observations, independent of CMS admin snapshots and receipts."""
from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.hitl_execution import HASH_PATTERN, UUID_PATTERN, canonical, digest

PARSER_VERSION: Literal["public_html_v1"] = "public_html_v1"
CACHE_HEADERS = frozenset({"content-type", "cache-control", "age", "etag", "last-modified", "vary", "expires", "date", "x-cache", "cf-cache-status"})
VerificationError = Literal["fetch_failed", "network_policy", "http_status", "non_html", "invalid_html",
                            "predicate_mismatch", "unsupported_predicate", "baseline_required", "lease_expired"]


def json_value(value: str) -> str:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, entry in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = entry
        return result
    try:
        parsed = json.loads(value, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_json")))
    except RecursionError:
        raise ValueError("jsonld_budget") from None
    if not isinstance(parsed, (dict, list)):
        raise ValueError("jsonld_container_required")
    # Encoding is bounded before parsing; depth/nodes are bounded after decoding.
    count = 0
    def visit(node: Any, depth: int) -> None:
        nonlocal count
        count += 1
        if count > 4096 or depth > 32:
            raise ValueError("jsonld_budget")
        if isinstance(node, dict):
            for item in node.values():
                visit(item, depth + 1)
        elif isinstance(node, list):
            for item in node:
                visit(item, depth + 1)
    visit(parsed, 0)
    return canonical(parsed)


class PublicFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    titles: tuple[str, ...] = Field(max_length=4)
    descriptions: tuple[str | None, ...] = Field(max_length=4)
    jsonld: tuple[str, ...] = Field(max_length=16)
    h1: tuple[str, ...] = Field(max_length=64)
    links: tuple[str, ...] = Field(max_length=128)

    @field_validator("titles", "descriptions", "jsonld", "h1", "links", mode="before")
    @classmethod
    def json_arrays(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if any(len(value.encode("utf-8")) > 8192 or "\x00" in value
               for value in (*self.titles, *(item for item in self.descriptions if item is not None), *self.h1)):
            raise ValueError("public_fact_budget")
        if any(len(value) > 2048 or "\x00" in value for value in self.links):
            raise ValueError("public_link_budget")
        for value in self.jsonld:
            if len(value.encode("utf-8")) > 32768 or json_value(value) != value:
                raise ValueError("public_jsonld_invalid")
        return self


class PublicObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    parser_version: Literal["public_html_v1"] = PARSER_VERSION
    url: str = Field(max_length=2048)
    final_url: str = Field(max_length=2048)
    observed_at: datetime
    status: int = Field(ge=100, le=599)
    headers: dict[str, str]
    body_sha256: str = Field(pattern=HASH_PATTERN)
    facts: PublicFacts | None

    @field_validator("observed_at", mode="before")
    @classmethod
    def iso_date(cls, value: Any) -> Any:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value

    @field_validator("observed_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("verification_utc_required")
        return value

    @model_validator(mode="after")
    def safe_headers(self) -> Self:
        if set(self.headers) - CACHE_HEADERS or any(len(value) > 2048 or "\x00" in value for value in self.headers.values()):
            raise ValueError("verification_header_invalid")
        return self

    def digest(self) -> str:
        return hashlib.sha256(canonical(self.model_dump(mode="json")).encode()).hexdigest()


class FieldCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    field: Literal["http", "title", "description", "jsonld"]
    matches: bool
    expected_hash: str | None = Field(default=None, pattern=HASH_PATTERN)
    actual_hash: str | None = Field(default=None, pattern=HASH_PATTERN)
    reason: Literal["equal", "different", "ambiguous", "baseline_required", "unsupported"]


class PublicBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    operation_id: str = Field(pattern=UUID_PATTERN)
    project_id: str = Field(pattern=UUID_PATTERN)
    recommendation_version_id: str = Field(pattern=UUID_PATTERN)
    content_hash: str = Field(pattern=HASH_PATTERN)
    observation: PublicObservation

    @classmethod
    def load(cls, raw: Any) -> Self:
        return cls.model_validate_json(canonical(raw))


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    observation: PublicObservation | None = None
    checks: tuple[FieldCheck, ...] = Field(default=(), max_length=4)
    error_code: VerificationError | None = None

    @field_validator("checks", mode="before")
    @classmethod
    def json_arrays(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @property
    def matches(self) -> bool:
        return bool(self.error_code is None and self.observation is not None and self.checks and all(check.matches for check in self.checks))

    @classmethod
    def load(cls, raw: Any) -> Self:
        return cls.model_validate_json(canonical(raw))


class VerificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    lease_id: str = Field(pattern=UUID_PATTERN)
    result: VerificationResult


def evaluate_public(record: dict[str, Any], observation: PublicObservation,
                    baseline: PublicObservation | None = None) -> VerificationResult:
    target = record["version"]["target"]["url"]
    if observation.url != target or observation.final_url != target:
        return VerificationResult(observation=observation, error_code="network_policy")
    if observation.status != 200:
        return VerificationResult(observation=observation, error_code="http_status")
    if observation.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "text/html":
        return VerificationResult(observation=observation, error_code="non_html")
    if observation.facts is None:
        return VerificationResult(observation=observation, error_code="invalid_html")
    facts = observation.facts
    checks = [FieldCheck(field="http", matches=True, reason="equal")]
    if len(facts.titles) > 1 or len(facts.descriptions) > 1 or len(set(facts.jsonld)) != len(facts.jsonld):
        return VerificationResult(observation=observation, error_code="predicate_mismatch")
    field_map = {"head.title": "title", "head.description": "description", "head.jsonld": "jsonld"}
    fields = []
    for change in record["version"]["changes"]:
        if change["mode"] == "unchanged":
            continue
        field = change.get("cms_field")
        if field not in {"title", "description", "jsonld"} or field_map.get(change["path"]) != field:
            return VerificationResult(observation=observation, error_code="unsupported_predicate")
        fields.append(field)
    rollback = record.get("operation_type") == "rollback"
    predicates: set[str] = set()
    http_checks = 0
    for predicate in record["version"]["expected_checks"]:
        if predicate["kind"] == "target_http_ok":
            http_checks += 1
            if predicate.get("url") != target:
                return VerificationResult(observation=observation, error_code="unsupported_predicate")
        elif predicate["kind"] not in {"field_equals_after", "field_removed"} or field_map.get(predicate.get("path")) not in fields:
            return VerificationResult(observation=observation, error_code="unsupported_predicate")
        else:
            field = field_map[predicate["path"]]
            if field in predicates:
                return VerificationResult(observation=observation, error_code="unsupported_predicate")
            predicates.add(field)
            desired = record["after"]["fields"][field]
            if not rollback and ((predicate["kind"] == "field_equals_after" and
                    (desired["state"] != "value" or hashlib.sha256(desired["value"].encode()).hexdigest() != predicate.get("sha256")))
                    or (predicate["kind"] == "field_removed" and desired["state"] != "missing")):
                return VerificationResult(observation=observation, error_code="unsupported_predicate")
    if http_checks != 1 or predicates != set(fields) or len(fields) != len(set(fields)):
        return VerificationResult(observation=observation, error_code="unsupported_predicate")
    for field in dict.fromkeys(fields):
        desired = record["after"]["fields"][field]
        actual = facts.titles if field == "title" else facts.descriptions if field == "description" else facts.jsonld
        expected: tuple[str | None, ...]
        if rollback and baseline is not None and baseline.facts is not None:
            expected = baseline.facts.titles if field == "title" else baseline.facts.descriptions if field == "description" else baseline.facts.jsonld
        elif rollback and (desired["state"] == "missing" or desired["value"] == ""):
            return VerificationResult(observation=observation, error_code="baseline_required")
        elif desired["state"] == "value":
            if field == "jsonld":
                try:
                    value = json_value(desired["value"])
                except ValueError:
                    return VerificationResult(observation=observation, error_code="unsupported_predicate")
                expected = (value,)
                if baseline and baseline.facts:
                    previous = record["before"]["fields"]["jsonld"]
                    others = list(baseline.facts.jsonld)
                    if previous["state"] == "value" and previous["value"]:
                        try:
                            old_value = json_value(previous["value"])
                        except ValueError:
                            return VerificationResult(observation=observation, error_code="unsupported_predicate")
                        if old_value in others:
                            others.remove(old_value)
                    expected = (*others, value)
            else:
                expected = (desired["value"],)
        elif rollback:
            return VerificationResult(observation=observation, error_code="baseline_required")
        else:
            expected = ()
            if field == "jsonld" and baseline and baseline.facts:
                others = list(baseline.facts.jsonld)
                previous = record["before"]["fields"]["jsonld"]
                if previous["state"] == "value" and previous["value"]:
                    try:
                        old_value = json_value(previous["value"])
                    except ValueError:
                        return VerificationResult(observation=observation, error_code="unsupported_predicate")
                    if old_value in others:
                        others.remove(old_value)
                expected = tuple(others)
        same = Counter(actual) == Counter(expected) if field == "jsonld" else canonical(actual) == canonical(expected)
        checks.append(FieldCheck(field=field, matches=same, expected_hash=digest(expected), actual_hash=digest(actual),
                                 reason="equal" if same else "different"))
    if not fields:
        return VerificationResult(observation=observation, error_code="unsupported_predicate")
    return VerificationResult(observation=observation, checks=tuple(checks),
                              error_code=None if all(item.matches for item in checks) else "predicate_mismatch")
