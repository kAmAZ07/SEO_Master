"""Детерминированные валидаторы предложений (ТЗ §9.1, §9.3): один набор правил на любой источник.

Кэш, LLM, детерминированный fallback и ручное предложение проходят одни и те же функции. Валидатор
ничего не «исправляет» молча: значение либо принимается как есть (после явной нормализации
пробелов в `clean_line`), либо отвергается с кодом. Неизвестная операция отвергается, а не
игнорируется.

Модуль чистый: без сети, БД, часов и случайных чисел. Он не импортирует `shared/` — в образе
semantic этого каталога нет.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import quote, unquote, urlsplit

from services.semantic_service.proposals.derive import derive_expected_checks, derive_preconditions
from services.semantic_service.proposals.model import (
    CMS_FIELDS,
    MANAGED_OPERATIONS,
    PATHS,
    ChangeMode,
    ExecutionMode,
    OperationType,
    RecommendationVersion,
    canonical_json,
    compute_content_hash,
    parse_utc,
)

TEXT_LIMITS = {
    OperationType.TITLE: (10, 70),
    OperationType.DESCRIPTION: (50, 160),
    OperationType.H1: (5, 120),
}
JSONLD_MAX_BYTES = 8192
JSONLD_TYPES = ("WebPage", "Article", "Organization")
JSONLD_NAME_MAX = 200
JSONLD_DESCRIPTION_MAX = 500
CONTENT_EDIT_MAX = 2000
ANCHOR_RANGE = (3, 100)
LINK_POSITIONS = ("intro", "body", "conclusion")
REMOVABLE = frozenset({OperationType.SCHEMA_JSONLD, OperationType.DESCRIPTION, OperationType.INTERNAL_LINK})

# Подстановочные тексты прежнего шаблонного fallback и типичные заглушки. Точные совпадения
# проверяются после удаления адреса сайта и знаков препинания; «ядовитые» фразы — по вхождению.
PLACEHOLDER_EXACT = frozenset({
    "официальный сайт", "официальная страница", "главная", "главная страница", "home", "homepage",
    "untitled", "page", "title", "new page", "заголовок", "описание", "страница", "сайт",
})
PLACEHOLDER_CONTAINS = (
    "заголовок страницы", "краткое описание страницы", "уточните уникальное предложение",
    "lorem ipsum", "your title here", "page title here", "insert description",
)
_PHP_SERIALIZED = re.compile(r"^(?:[aOsibdCN]:\d+[:;{]|N;)")
_EXEC_URL = re.compile(r"(?i)\b(?:javascript|vbscript|data|file)\s*:")
_FORBIDDEN_CHARS = frozenset("<>")
_UNSUPPORTED_CLAIMS = (
    re.compile(r"(?i)\b(?:лучш\w+\s+в\s+\w+|№\s?1|номер\s+один|#1\b|best\s+in\b|number\s+one)"),
    re.compile(r"(?i)гаранти\w*|guarantee\w*|100\s?%"),
    re.compile(r"(?i)официальн\w+\s+дилер|лицензи\w+|сертифицирован\w*|licensed|certified"),
    re.compile(r"(?i)излеч\w+|вылеч\w+|лечени\w+|\bcure[sd]?\b|\btreats?\b"),
    re.compile(r"(?i)бесплатн\w+|\bfree\b|скидк\w+\s+\d+|\bdiscount\b"),
)


@dataclass(frozen=True)
class Issue:
    code: str
    path: str = ""
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "detail": self.detail}


class ProposalRejected(ValueError):
    def __init__(self, issues: list[Issue]):
        super().__init__("; ".join(f"{i.code}@{i.path}" if i.path else i.code for i in issues))
        self.issues = issues


@dataclass(frozen=True)
class TextContext:
    """Контекст проверки текста: адрес сайта (для распознавания заглушек) и факты страницы
    (для проверки, что рискованные утверждения не придуманы)."""

    host: str = ""
    facts_text: str | None = None
    existing: tuple[str, ...] = field(default_factory=tuple)


def clean_line(value: str) -> str:
    """Единственная нормализация: пробелы по краям и повторяющиеся пробелы внутри строки."""
    return re.sub(r"[ \t ]+", " ", value).strip()


def _is_control(ch: str) -> bool:
    category = unicodedata.category(ch)
    return category in ("Cc", "Cs", "Co") or ch in (" ", " ", "﻿", "​", "‮")


def check_text_safety(value: str, path: str) -> list[Issue]:
    issues: list[Issue] = []
    if not isinstance(value, str):
        return [Issue("value_not_text", path)]
    if "\x00" in value:
        issues.append(Issue("nul_character", path))
    if any(_is_control(ch) for ch in value):
        issues.append(Issue("control_character", path))
    if any(ch in _FORBIDDEN_CHARS for ch in value):
        issues.append(Issue("html_not_allowed", path))
    if _PHP_SERIALIZED.match(value.lstrip()):
        issues.append(Issue("serialized_looking_value", path))
    if _EXEC_URL.search(unquote(value)):
        issues.append(Issue("executable_url", path))
    if "&#" in value or re.search(r"&[a-zA-Z]{2,8};", value):
        issues.append(Issue("html_entity_not_allowed", path))
    return issues


def _strip_host(value: str, host: str) -> str:
    text = value.lower()
    if host:
        text = text.replace(host.lower(), " ")
    text = re.sub(r"https?://\S*", " ", text)
    text = re.sub(r"[^\w\s]|_", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def is_placeholder(value: str, host: str = "") -> bool:
    lowered = value.lower()
    if any(marker in lowered for marker in PLACEHOLDER_CONTAINS):
        return True
    stripped = _strip_host(value, host)
    return stripped == "" or stripped in PLACEHOLDER_EXACT


def check_claims(value: str, facts_text: str | None, path: str) -> list[Issue]:
    """Рискованные утверждения (первенство, гарантии, лицензии, лечение, цены) допустимы, только если
    то же слово есть в фактах страницы; иначе это придуманный факт."""
    if facts_text is None:
        return []
    facts = facts_text.lower()
    issues: list[Issue] = []
    for pattern in _UNSUPPORTED_CLAIMS:
        for match in pattern.finditer(value):
            if match.group(0).lower() not in facts:
                issues.append(Issue("ungrounded_claim", path, match.group(0).lower()))
    return issues


def check_text(kind: OperationType, value: str, ctx: TextContext, path: str = "") -> list[Issue]:
    """Проверка значения title / description / H1."""
    if kind not in TEXT_LIMITS:
        return [Issue("operation_unsupported", path, kind.value if isinstance(kind, OperationType) else str(kind))]
    path = path or PATHS[kind].pattern.strip("^$").replace("\\", "")
    if not isinstance(value, str):
        return [Issue("value_not_text", path)]
    issues = check_text_safety(value, path)
    if "\n" in value or "\r" in value:
        issues.append(Issue("multiline_not_allowed", path))
    if value != clean_line(value):
        issues.append(Issue("not_normalised", path))
    low, high = TEXT_LIMITS[kind]
    length = len(value.strip())
    if length == 0:
        issues.append(Issue("empty_value", path))
    elif length < low:
        issues.append(Issue("too_short", path, f"{length}<{low}"))
    elif length > high:
        issues.append(Issue("too_long", path, f"{length}>{high}"))
    if length and is_placeholder(value, ctx.host):
        issues.append(Issue("placeholder_text", path))
    issues.extend(check_claims(value, ctx.facts_text, path))
    return issues


def canonical_jsonld(value: dict) -> str:
    return canonical_json(value)


def check_jsonld(value: str, ctx: TextContext, path: str = "head.jsonld") -> list[Issue]:
    """Базовая JSON-LD из набора, который принимает плагин v2: `@context`, `@type`, `name` и
    необязательное `description`. Лишние ключи отвергаются, а не отбрасываются."""
    if not isinstance(value, str):
        return [Issue("value_not_text", path)]
    if len(value.encode("utf-8")) > JSONLD_MAX_BYTES:
        return [Issue("too_long", path, f">{JSONLD_MAX_BYTES} bytes")]
    issues = check_text_safety(value, path)
    try:
        data = json.loads(value)
    except ValueError:
        return issues + [Issue("invalid_json", path)]
    if not isinstance(data, dict):
        return issues + [Issue("schema_not_object", path)]
    allowed = {"@context", "@type", "name", "description"}
    for key in sorted(set(data) - allowed):
        issues.append(Issue("schema_extra_field", path, key))
    if data.get("@context") != "https://schema.org":
        issues.append(Issue("schema_context_invalid", path))
    schema_type = data.get("@type")
    if not isinstance(schema_type, str) or schema_type not in JSONLD_TYPES:
        issues.append(Issue("schema_type_unsupported", path, str(schema_type)[:40]))
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        issues.append(Issue("schema_name_required", path))
    else:
        issues.extend(_check_schema_text(name, JSONLD_NAME_MAX, ctx, path + ".name"))
    if "description" in data:
        description = data["description"]
        if not isinstance(description, str) or not description.strip():
            issues.append(Issue("schema_description_invalid", path))
        else:
            issues.extend(_check_schema_text(description, JSONLD_DESCRIPTION_MAX, ctx, path + ".description"))
    if not issues and value != canonical_jsonld(data):
        issues.append(Issue("jsonld_not_canonical", path))
    return issues


def _check_schema_text(value: str, limit: int, ctx: TextContext, path: str) -> list[Issue]:
    issues = check_text_safety(value, path)
    if len(value) > limit:
        issues.append(Issue("too_long", path, f"{len(value)}>{limit}"))
    if value != clean_line(value) or "\n" in value:
        issues.append(Issue("not_normalised", path))
    if is_placeholder(value, ctx.host):
        issues.append(Issue("placeholder_text", path))
    issues.extend(check_claims(value, ctx.facts_text, path))
    return issues


def check_internal_link(value: str, ctx: TextContext, path: str) -> list[Issue]:
    """Внутренняя ссылка: якорь, адрес на том же origin, позиция. Self-ссылка и дубль проверяются при составлении
    набора ссылок (AL-14), где известны соседние ссылки."""
    if not isinstance(value, str):
        return [Issue("value_not_text", path)]
    issues = check_text_safety(value, path)
    try:
        data = json.loads(value)
    except ValueError:
        return issues + [Issue("invalid_json", path)]
    if not isinstance(data, dict) or set(data) != {"anchor", "position", "target_url"}:
        return issues + [Issue("link_shape_invalid", path)]
    anchor, position, target = data["anchor"], data["position"], data["target_url"]
    if not isinstance(anchor, str) or not (ANCHOR_RANGE[0] <= len(anchor.strip()) <= ANCHOR_RANGE[1]) or anchor != clean_line(anchor):
        issues.append(Issue("anchor_invalid", path))
    else:
        issues.extend(check_text_safety(anchor, path + ".anchor"))
        if is_placeholder(anchor, ctx.host) or anchor.lower() in ("здесь", "тут", "click here", "here", "читать далее", "read more"):
            issues.append(Issue("anchor_generic", path))
    if position not in LINK_POSITIONS:
        issues.append(Issue("link_position_invalid", path))
    normalised = canonical_url(target) if isinstance(target, str) else None
    if normalised is None or normalised != target:
        issues.append(Issue("link_target_invalid", path))
    elif ctx.host and (urlsplit(target).hostname or "") != ctx.host:
        issues.append(Issue("link_target_foreign_host", path))
    if not issues and value != canonical_json(data):
        issues.append(Issue("link_not_canonical", path))
    return issues


def check_content_edit(before: str | None, after: str, ctx: TextContext, path: str) -> list[Issue]:
    issues = check_text_safety(after, path)
    if not after.strip():
        issues.append(Issue("empty_value", path))
    if len(after) > CONTENT_EDIT_MAX:
        issues.append(Issue("too_long", path, f"{len(after)}>{CONTENT_EDIT_MAX}"))
    if after != after.strip():
        issues.append(Issue("not_normalised", path))
    issues.extend(check_claims(after, ctx.facts_text, path))
    return issues


# --- адреса -------------------------------------------------------------------

def canonical_url(url: str) -> str | None:
    """Строгая каноническая форма http(s)-адреса либо `None`: схема и узел в нижнем регистре, IDNA,
    порты 80/443 без явной записи, без userinfo, query и fragment, без управляющих символов и `\\`."""
    if not isinstance(url, str) or not url or len(url) > 2048:
        return None
    if any(ord(ch) < 0x21 or ord(ch) == 0x7F for ch in url) or "\\" in url:
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname or parts.username is not None or parts.password is not None:
        return None
    if parts.query or parts.fragment:
        return None
    try:
        host = parts.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    default = 443 if scheme == "https" else 80
    if port is not None and port != default:
        return None  # нестандартный порт, а также http на 443 и https на 80 неоднозначны
    if host.endswith("."):
        return None
    if ":" in host:
        host = f"[{host}]"
    path = quote(parts.path or "/", safe="/%-._~!$&'()*+,;=:@")
    return f"{scheme}://{host}{path}"


def canonical_origin(url: str) -> str | None:
    normalised = canonical_url(url)
    if normalised is None:
        return None
    parts = urlsplit(normalised)
    return f"{parts.scheme}://{parts.netloc}"


def host_of(url: str) -> str:
    normalised = canonical_url(url)
    return (urlsplit(normalised).hostname or "") if normalised else ""


# --- проверка готовой версии -----------------------------------------------------

def validate_version(version: RecommendationVersion, facts_text: str | None = None) -> list[Issue]:
    """Полная проверка версии. Не доверяет ни источнику, ни хешу: пересчитывает всё заново."""
    issues: list[Issue] = []
    op = version.operation_type
    if op not in PATHS:
        return [Issue("operation_unsupported", "", str(op))]

    origin = canonical_origin(version.target.url)
    if origin is None:
        issues.append(Issue("target_url_invalid", "target.url"))
    elif origin != version.target.origin or canonical_url(version.target.url) != version.target.url:
        issues.append(Issue("target_origin_mismatch", "target"))
    ctx = TextContext(host=host_of(version.target.url), facts_text=facts_text)

    if len({e.evidence_id for e in version.evidence}) != len(version.evidence):
        issues.append(Issue("evidence_duplicate", "evidence"))

    if len(version.changes) != 1:
        issues.append(Issue("single_change_required", "changes", f"{len(version.changes)}"))
    paths = [c.path for c in version.changes]
    if len(set(paths)) != len(paths):
        issues.append(Issue("duplicate_change_path", "changes"))
    if not any(c.mode is not ChangeMode.UNCHANGED for c in version.changes):
        issues.append(Issue("no_effective_change", "changes"))

    cms_expected = CMS_FIELDS[op]
    for change in version.changes:
        if not PATHS[op].fullmatch(change.path):
            issues.append(Issue("path_not_allowed", change.path))
            continue
        if change.cms_field != cms_expected:
            issues.append(Issue("cms_field_mismatch", change.path))
        if change.mode is ChangeMode.REMOVE and op not in REMOVABLE:
            issues.append(Issue("remove_not_allowed", change.path))
        if change.mode is ChangeMode.REMOVE and change.before_state == "missing":
            issues.append(Issue("nothing_to_remove", change.path))
        if change.mode is ChangeMode.SET:
            assert change.after is not None
            issues.extend(_check_after(op, change.before, change.after, ctx, change.path))
            if change.before_state == "value" and change.before == change.after:
                issues.append(Issue("no_change", change.path))

    issues.extend(_check_derived_sections(version))

    if (op in MANAGED_OPERATIONS) is False and version.execution_mode is not ExecutionMode.MANUAL:
        issues.append(Issue("managed_not_supported", "execution_mode"))

    provenance_paths = sorted(p.path for p in version.provenance)
    if provenance_paths != sorted(paths):
        issues.append(Issue("provenance_incomplete", "provenance"))
    try:
        parse_utc(version.created_at)
        for item in version.provenance:
            parse_utc(item.generated_at)
    except ValueError:
        issues.append(Issue("timestamp_invalid", "created_at"))

    if version.content_hash != compute_content_hash(version):
        issues.append(Issue("content_hash_mismatch", "content_hash"))
    return issues


def _check_after(op: OperationType, before: str | None, after: str, ctx: TextContext, path: str) -> list[Issue]:
    if op in TEXT_LIMITS:
        return check_text(op, after, ctx, path)
    if op is OperationType.SCHEMA_JSONLD:
        return check_jsonld(after, ctx, path)
    if op is OperationType.INTERNAL_LINK:
        return check_internal_link(after, ctx, path)
    return check_content_edit(before, after, ctx, path)


def _check_derived_sections(version: RecommendationVersion) -> list[Issue]:
    """Предусловия и ожидаемые проверки должны точно соответствовать изменениям: их нельзя подменить."""
    issues: list[Issue] = []
    if _as_multiset(version.preconditions) != _as_multiset(
        derive_preconditions(version.project_id, version.target, version.evidence, version.changes)
    ):
        issues.append(Issue("precondition_mismatch", "preconditions"))
    if _as_multiset(version.expected_checks) != _as_multiset(derive_expected_checks(version.target, version.changes)):
        issues.append(Issue("expected_checks_mismatch", "expected_checks"))
    return issues


def _as_multiset(items) -> list[str]:
    return sorted(canonical_json(item.model_dump(mode="json")) for item in items)
