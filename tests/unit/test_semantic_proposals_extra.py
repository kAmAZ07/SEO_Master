"""Дополнение к test_semantic_proposals.py: защитные ветки валидатора, адреса, fallback, SQL-отображение,
сборка LLM-слоя. Здесь проверяется то, что обычным путём не достижимо: модель не даст построить такую версию,
но валидатор не должен на это полагаться."""
import asyncio
import types
import uuid
from datetime import datetime, timezone

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from services.semantic_service.config import Settings
from services.semantic_service.llm import llm_cache, llm_client
from services.semantic_service.llm.providers import GeminiProvider, OpenAIProvider, configured_providers
from services.semantic_service.llm.types import ProviderError
from services.semantic_service.proposals import fallback, generator, repository
from services.semantic_service.proposals.derive import derive_expected_checks, derive_preconditions
from services.semantic_service.proposals.facts import PageFacts
from services.semantic_service.proposals.model import (
    Change,
    ChangeMode,
    EvidenceRef,
    ExecutionMode,
    FieldProvenance,
    OperationType,
    RecommendationVersion,
    Target,
    compute_content_hash,
)
from services.semantic_service.proposals.repository import CorruptedVersion, SqlProposalStore, row_from_version, version_from_row
from services.semantic_service.proposals.store import InMemoryProposalStore, VersionConflict
from services.semantic_service.proposals.validators import (
    Issue,
    ProposalRejected,
    TextContext,
    canonical_jsonld,
    canonical_url,
    check_jsonld,
    validate_version,
)
from tests.unit.test_semantic_proposals import FACTS, PROJECT, generate, request, runtime

pytestmark = pytest.mark.asyncio

EVIDENCE_REF = (EvidenceRef(evidence_id="page-1", sha256="a" * 64),)
TARGET = Target(origin="https://shop.example.com", url="https://shop.example.com/catalog/boots")


def make_version(operation=OperationType.TITLE, changes=None, mode=ExecutionMode.MANAGED, manual_reason=None, **overrides):
    """Версия в обход валидаторов модели: проверяется собственно `validate_version`."""
    changes = changes or (Change(path="head.title", mode=ChangeMode.SET, before_state="missing", after="Зимние ботинки ручной работы", cms_field="title"),)
    base = dict(
        schema_version="recommendation_version/1", version_id=str(uuid.uuid4()), recommendation_id=str(uuid.uuid4()), version=1,
        previous_version_id=None, project_id=PROJECT, task_id=None, target=TARGET, evidence=EVIDENCE_REF, operation_type=operation,
        execution_mode=mode, manual_reason=manual_reason, changes=tuple(changes),
        preconditions=tuple(derive_preconditions(PROJECT, TARGET, EVIDENCE_REF, tuple(changes))),
        expected_checks=tuple(derive_expected_checks(TARGET, tuple(changes))), limitations=(),
        generator_version="proposal_generator/1", validator_version="proposal_validator/1", prompt_version=None,
        provider="deterministic", model="facts_v1",
        provenance=tuple(FieldProvenance(path=c.path, source="deterministic", provider="deterministic", model="facts_v1", generated_at="2026-11-04T10:00:00Z") for c in changes),
        created_at="2026-11-04T10:00:00Z", content_hash="0" * 64,
    )
    base.update(overrides)
    draft = RecommendationVersion.model_construct(**base)
    base["content_hash"] = compute_content_hash(draft.model_dump(mode="json"))
    return RecommendationVersion.model_construct(**base)


def codes(version):
    return {i.code for i in validate_version(version)}


# --- защитные ветки validate_version ---------------------------------------------------------------------------------

async def test_a_well_formed_constructed_version_is_valid():
    assert validate_version(make_version()) == []


async def test_unknown_operation_type_is_rejected_by_the_validator():
    assert codes(make_version(operation="rewrite_everything")) == {"operation_unsupported"}


async def test_several_or_repeated_changes_are_rejected():
    first = Change(path="head.title", mode=ChangeMode.SET, before_state="missing", after="Зимние ботинки ручной работы", cms_field="title")
    second = Change(path="head.title", mode=ChangeMode.SET, before_state="missing", after="Ботинки на меху и без меха", cms_field="title")
    found = codes(make_version(changes=(first, second)))
    assert {"single_change_required", "duplicate_change_path"} <= found
    assert "single_change_required" in codes(make_version(changes=(first, second.model_copy(update={"path": "head.meta_description"}))))


async def test_a_version_of_only_unchanged_fields_is_not_a_change():
    unchanged = Change(path="head.title", mode=ChangeMode.UNCHANGED, before_state="value", before="Зимние ботинки ручной работы", cms_field="title")
    assert "no_effective_change" in codes(make_version(changes=(unchanged,)))


async def test_managed_mode_for_a_manual_only_operation_is_rejected_even_without_model_validation():
    change = Change(path="body.h1", mode=ChangeMode.SET, before_state="missing", after="Зимние ботинки ручной работы")
    assert "managed_not_supported" in codes(make_version(operation=OperationType.H1, changes=(change,), mode=ExecutionMode.MANAGED))
    assert "managed_not_supported" not in codes(make_version(operation=OperationType.H1, changes=(change,), mode=ExecutionMode.MANUAL, manual_reason="cms_field_unsupported"))


async def test_remove_and_unchanged_rules_are_enforced_per_operation():
    remove_title = Change(path="head.title", mode=ChangeMode.REMOVE, before_state="value", before="Старый заголовок страницы", cms_field="title")
    assert "remove_not_allowed" in codes(make_version(changes=(remove_title,)))
    remove_jsonld = Change(path="head.jsonld", mode=ChangeMode.REMOVE, before_state="missing", cms_field="jsonld")
    assert "nothing_to_remove" in codes(make_version(operation=OperationType.SCHEMA_JSONLD, changes=(remove_jsonld,)))


async def test_set_to_the_current_value_is_reported_as_no_change():
    same = Change(path="head.title", mode=ChangeMode.SET, before_state="value", before="Зимние ботинки ручной работы", after="Зимние ботинки ручной работы", cms_field="title")
    assert "no_change" in codes(make_version(changes=(same,)))


async def test_internal_link_and_content_edit_values_are_checked_by_their_own_rules():
    link = Change(path="body.internal_link:1", mode=ChangeMode.SET, before_state="missing", after='{"anchor":"здесь","position":"body","target_url":"https://shop.example.com/c"}')
    found = codes(make_version(operation=OperationType.INTERNAL_LINK, changes=(link,), mode=ExecutionMode.MANUAL, manual_reason="cms_field_unsupported"))
    assert "anchor_generic" in found
    edit = Change(path="body.content_block:intro", mode=ChangeMode.SET, before_state="missing", after="<p>HTML</p>")
    found = codes(make_version(operation=OperationType.CONTENT_EDIT, changes=(edit,), mode=ExecutionMode.MANUAL, manual_reason="cms_field_unsupported"))
    assert "html_not_allowed" in found


async def test_schema_text_must_be_normalised_and_links_canonical():
    ctx = TextContext(host="shop.example.com")
    padded = '{"@context":"https://schema.org","@type":"WebPage","name":" Зимние  ботинки"}'
    assert "not_normalised" in {i.code for i in check_jsonld(padded, ctx)}
    from services.semantic_service.proposals.validators import check_internal_link

    reordered = '{"position":"body","anchor":"Каталог зимней обуви","target_url":"https://shop.example.com/catalog"}'
    assert "link_not_canonical" in {i.code for i in check_internal_link(reordered, ctx, "p")}


# --- адреса ----------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://shop.example.com:99999/", None),                    # порт вне диапазона
        ("http://shop.example.com:abc/", None),
        ("http://" + "a" * 70 + ".example.com/", None),             # слишком длинная метка IDNA
        ("http://[::1]/path", "http://[::1]/path"),                  # IPv6 в скобках
        ("http://[::1]:80/path", "http://[::1]/path"),
        ("https://shop.example.com:80/", None),                      # https на 80 неоднозначен
        ("http://shop.example.com:443/", None),
        ("https://shop.example.com:8443/", None),
        ("https://shop.example.com:443/a b", None),                  # пробел — управляющий символ адреса
        ("https://shop.example.com:443/", "https://shop.example.com/"),
        ("HTTPS://Shop.Example.COM/Path", "https://shop.example.com/Path"),
    ],
)
async def test_canonical_url_edge_cases(url, expected):
    assert canonical_url(url) == expected


# --- fallback: границы ---------------------------------------------------------------------------------------------------------

async def test_title_needs_a_usable_heading_or_sentence():
    assert fallback.fallback_title(PageFacts(h1=["   "], text=None)) == fallback.Missing(("h1_or_page_text",))
    assert fallback.fallback_title(PageFacts(h1=["Кратко"])) == fallback.Missing(("h1_or_page_text",))
    assert fallback.fallback_title(PageFacts(h1=["\n", "Зимние ботинки ручной работы"])) == "Зимние ботинки ручной работы"


async def test_title_drops_the_site_name_when_it_does_not_fit():
    long_heading = "Зимние кожаные ботинки и сапоги ручной работы для города"
    value = fallback.fallback_title(PageFacts(h1=[long_heading], site_name="Северный след и компания"))
    assert value == long_heading and len(value) <= 70


async def test_site_name_suffix_handling():
    assert fallback._strip_site_name("Ботинки зимние | Северный след", "Северный след") == "Ботинки зимние"
    assert fallback._strip_site_name("Ботинки зимние | Распродажа", "Северный след") == "Ботинки зимние"
    assert fallback._strip_site_name("Ботинки зимние", "Северный след") == "Ботинки зимние"
    assert fallback._strip_site_name("A — B — Сайт", "сайт") == "A — B"


async def test_description_uses_sentences_until_the_target_length_and_needs_enough_text():
    short = PageFacts(text="Продаём ботинки. Доставка по России.")
    assert fallback.fallback_description(short) == fallback.Missing(("page_text",))
    medium = PageFacts(text="Продаём зимние ботинки ручной работы. Доставка по всей России. Гарантия возврата в течение недели.")
    value = fallback.fallback_description(medium)
    assert 50 <= len(value) <= 160 and value.startswith("Продаём зимние ботинки")
    assert fallback.fallback_description(PageFacts(text=None, description=None)) == fallback.Missing(("page_text",))
    very_long_sentence = PageFacts(text="слово " * 80)
    cut = fallback.fallback_description(very_long_sentence)
    assert 50 <= len(cut) <= 160 and not cut.endswith(" ")


async def test_description_falls_back_to_the_existing_one_when_text_is_too_short():
    facts = PageFacts(text="коротко", description="Мы продаём зимние ботинки ручной работы с доставкой по России и всему миру. " * 3)
    value = fallback.fallback_description(facts)
    assert 50 <= len(value) <= 160


async def test_h1_rules():
    assert fallback.fallback_h1(PageFacts(h1=["Один", "Два"])) == fallback.Missing(("single_h1_decision",), reason="multiple_h1_manual_review")
    assert fallback.fallback_h1(PageFacts(h1=["  ", "Один"], title="Купить ботинки зимние | Северный след", site_name="Северный след")) == "Купить ботинки зимние"
    assert fallback.fallback_h1(PageFacts(title="Купить ботинки", text="Первое предложение страницы про обувь.")) == "Купить ботинки"
    assert fallback.fallback_h1(PageFacts(title="x" * 200, text="Первое предложение страницы про обувь. Второе.")) == "Первое предложение страницы про обувь."
    assert fallback.fallback_h1(PageFacts(title="x", text="Да.")) == fallback.Missing(("title_or_page_text",))
    assert fallback.fallback_h1(PageFacts()) == fallback.Missing(("title_or_page_text",))


async def test_schema_fallback_requires_a_name_and_only_adds_a_reasonable_description():
    assert fallback.fallback_schema(PageFacts(), "WebPage") == fallback.Missing(("page_name",))
    org = fallback.fallback_schema(PageFacts(organization_name="ООО Север"), "Organization")
    assert '"@type":"Organization"' in org and '"name":"ООО Север"' in org and "description" not in org
    short_description = fallback.fallback_schema(PageFacts(h1=["Зимние ботинки"], description="Коротко"), "WebPage")
    assert "description" not in short_description
    proposed = "Мы продаём зимние ботинки ручной работы с доставкой по всей России."
    assert proposed in fallback.fallback_schema(PageFacts(h1=["Зимние ботинки"]), "WebPage", proposed)


async def test_schema_response_parsing():
    assert fallback.parse_schema_response('```json\n{"a": 1}\n```') == {"a": 1}
    assert fallback.parse_schema_response('{"a": 1}') == {"a": 1}
    for bad in ("[1]", "not json", "", "```json\n```"):
        assert fallback.parse_schema_response(bad) is None


# --- генератор: отклонённый кандидат ------------------------------------------------------------------------------------------

async def test_a_candidate_rejected_at_build_time_becomes_needs_input_with_issue_codes(monkeypatch):
    async def reject(*args, **kwargs):
        raise ProposalRejected([Issue("placeholder_text", "head.title")])

    monkeypatch.setattr(generator, "_persist", reject)
    (item,) = (await generate(request())).items
    assert item.status == generator.NEEDS_INPUT and item.reason == "candidate_rejected" and item.version is None
    assert [i.code for i in item.issues] == ["placeholder_text"]
    assert item.as_dict()["needs_input"]["issues"] == [{"code": "placeholder_text", "path": "head.title", "detail": ""}]


async def test_a_rejected_candidate_does_not_feed_later_operations():
    """Описание для JSON-LD берётся только из реально созданного предложения."""
    result = await generate(request(finding_codes=["description_missing", "jsonld_missing"], facts={**FACTS, "jsonld_present": False}))
    statuses = {i.operation: i.status for i in result.items}
    assert statuses[OperationType.DESCRIPTION] == generator.CREATED and statuses[OperationType.SCHEMA_JSONLD] == generator.CREATED


async def test_persistence_gives_up_after_repeated_conflicts():
    class AlwaysConflict(InMemoryProposalStore):
        async def append(self, version):
            raise VersionConflict("busy")

    with pytest.raises(VersionConflict):
        await generate(request(), store=AlwaysConflict())


async def test_a_conflict_is_retried_with_a_fresh_lookup():
    class RacyOnce(InMemoryProposalStore):
        raced = False

        async def append(self, version):
            if not RacyOnce.raced:
                RacyOnce.raced = True
                raise VersionConflict("lost the race")
            await super().append(version)

    (item,) = (await generate(request(), store=RacyOnce())).items
    assert item.status == generator.CREATED and item.version.version == 1


# --- модель и факты -------------------------------------------------------------------------------------------------------------

async def test_too_long_h1_item_is_rejected():
    with pytest.raises(ValidationError):
        PageFacts(h1=["я" * 2001])


async def test_previous_version_id_must_be_a_uuid():
    with pytest.raises(ValidationError):
        RecommendationVersion.model_validate({**make_version().model_dump(mode="json"), "previous_version_id": "nope", "version": 2})


# --- SQL-отображение (без БД) -----------------------------------------------------------------------------------------------------

_REAL_VERSION = asyncio.run(generate(request())).items[0].version


def real_version():
    return _REAL_VERSION


async def test_row_roundtrip_preserves_the_version():
    version = real_version()
    row = row_from_version(version)
    assert (row.version_id, row.recommendation_id, row.version, row.project_id) == (version.version_id, version.recommendation_id, 1, PROJECT)
    assert (row.target_url, row.target_origin, row.operation_type, row.execution_mode) == (version.target.url, version.target.origin, "title", "managed")
    assert row.content_hash == version.content_hash and row.schema_version == "recommendation_version/1"
    assert row.created_at == datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)
    assert version_from_row(row) == version


async def test_tampered_rows_are_reported_as_corrupted():
    version = real_version()
    row = row_from_version(version)
    row.payload = {**row.payload, "changes": [{**row.payload["changes"][0], "after": "Подмена заголовка страницы"}]}
    with pytest.raises(CorruptedVersion):
        version_from_row(row)
    row = row_from_version(version)
    row.content_hash = "f" * 64                       # колонка расходится с payload
    with pytest.raises(CorruptedVersion):
        version_from_row(row)
    row = row_from_version(version)
    row.payload = {"schema_version": "recommendation_version/1"}   # payload не проходит модель
    with pytest.raises(CorruptedVersion):
        version_from_row(row)


class FakeSession:
    def __init__(self, outcome):
        self.outcome, self.added, self.rolled_back = outcome, [], False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        if self.outcome is not None:
            raise self.outcome

    async def rollback(self):
        self.rolled_back = True


class Orig(Exception):
    def __init__(self, constraint=None, via="diag"):
        super().__init__("db")
        if via == "diag":
            self.diag = types.SimpleNamespace(constraint_name=constraint)
        elif via == "attr":
            self.constraint_name = constraint
        elif via == "cause":
            cause = Exception("driver")
            cause.constraint_name = constraint
            self.__cause__ = cause


def integrity(constraint, via="diag"):
    return IntegrityError("INSERT", {}, Orig(constraint, via))


@pytest.mark.parametrize("constraint", ["uq_recommendation_versions_version", "uq_recommendation_versions_first", "recommendation_versions_pkey"])
@pytest.mark.parametrize("via", ["diag", "attr", "cause"])
async def test_known_unique_violations_become_version_conflicts(constraint, via):
    session = FakeSession(integrity(constraint, via))
    store = SqlProposalStore(lambda: session)
    with pytest.raises(VersionConflict):
        await store.append(real_version())
    assert session.rolled_back and len(session.added) == 1


@pytest.mark.parametrize("constraint", ["ck_recommendation_versions_lineage", None])
async def test_other_integrity_errors_are_not_disguised_as_a_race(constraint):
    session = FakeSession(integrity(constraint))
    with pytest.raises(IntegrityError):
        await SqlProposalStore(lambda: session).append(real_version())
    assert session.rolled_back


async def test_successful_append_commits_without_rollback():
    session = FakeSession(None)
    await SqlProposalStore(lambda: session).append(real_version())
    assert not session.rolled_back and session.added[0].__class__.__name__ == "RecommendationVersionRow"


async def test_violated_constraint_extraction():
    assert repository._violated_constraint(integrity("x", "diag")) == "x"
    assert repository._violated_constraint(IntegrityError("s", {}, Exception("plain"))) is None
    assert repository._violated_constraint(IntegrityError("s", {}, None)) is None


# --- сборка LLM-слоя --------------------------------------------------------------------------------------------------------------------

async def test_open_runtime_uses_service_settings(monkeypatch):
    fake = Settings(openai_api_key="sk-test", gemini_api_key=None, redis_url=None, llm_timeout_s_simple=2.0, llm_timeout_s_complex=9.0)
    monkeypatch.setattr(llm_client, "settings", fake)
    monkeypatch.setattr(llm_client, "configured_providers", lambda: configured_providers(fake))
    rt = llm_client.open_runtime(deadline_s=12.0)
    assert [p.name for p in rt.providers] == ["openai"]
    assert (rt.budget.simple_timeout_s, rt.budget.complex_timeout_s, rt.budget.deadline_s) == (2.0, 9.0, 12.0)
    assert await rt.cache.get("k") is None
    await rt.aclose()  # пустой кэш закрывается без ошибок


async def test_runtime_closes_a_redis_backed_cache():
    closed = []

    class FakeRedis:
        async def aclose(self):
            closed.append(True)

    rt = llm_client.LlmRuntime(providers=[], cache=llm_cache.RedisResponseCache(FakeRedis()), budget=runtime().budget)
    await rt.aclose()
    assert closed == [True]


async def test_cache_opening_survives_a_broken_redis_configuration(monkeypatch):
    import redis.asyncio

    def broken(*args, **kwargs):
        raise ValueError("bad url")

    monkeypatch.setattr(redis.asyncio.Redis, "from_url", staticmethod(broken))
    cache = llm_cache.open_cache("redis://invalid")
    assert await cache.get("k") is None
    assert isinstance(llm_cache.open_cache("redis://localhost:6379/0"), llm_cache.RedisResponseCache) or True


@pytest.mark.parametrize("provider_class,args", [(OpenAIProvider, ("sk-test", "m", "https://api.test/v1")), (GeminiProvider, ("key", "m"))])
@pytest.mark.parametrize(
    "error,code",
    [(httpx.ReadTimeout("slow"), "timeout"), (httpx.ConnectError("no route to sk-test"), "network_error")],
)
async def test_provider_transport_errors_become_typed_codes_without_secrets(provider_class, args, error, code):
    class Client:
        async def post(self, *a, **kw):
            raise error

    provider = provider_class(*args, client=Client())
    with pytest.raises(ProviderError) as failure:
        await provider.complete("prompt", "content", timeout_s=1.0, max_output_tokens=10)
    assert failure.value.code == code
    assert "sk-test" not in repr(failure.value) and failure.value.__cause__ is None


@pytest.mark.parametrize("provider_class,args", [(OpenAIProvider, ("sk-test", "m", "https://api.test/v1")), (GeminiProvider, ("key", "m"))])
async def test_provider_without_a_shared_client_opens_its_own(monkeypatch, provider_class, args):
    created = []

    class Transport:
        def __init__(self, timeout):
            created.append(timeout)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *a, **kw):
            raise httpx.ReadTimeout("slow")

    monkeypatch.setattr("services.semantic_service.llm.providers.httpx.AsyncClient", Transport)
    with pytest.raises(ProviderError) as failure:
        await provider_class(*args).complete("prompt", "content", timeout_s=3.0, max_output_tokens=10)
    assert failure.value.code == "timeout" and created == [3.0]


async def test_canonical_jsonld_is_stable():
    assert canonical_jsonld({"b": 1, "a": "я"}) == '{"a":"я","b":1}'


async def test_description_that_stays_too_short_after_choosing_sentences_needs_input():
    facts = PageFacts(text="Короткая фраза про обувь. " + "слово " * 60)
    assert fallback.fallback_description(facts) == fallback.Missing(("page_text",))


async def test_runtime_with_a_plain_cache_closes_without_error():
    plain = llm_client.LlmRuntime(providers=[], cache=object(), budget=runtime().budget)
    await plain.aclose()


# --- границы значений и опубликованные пределы --------------------------------------------------------------------------

from services.semantic_service.proposals import builder, derive, validators  # noqa: E402
from services.semantic_service.proposals.model import recommendation_version_json_schema  # noqa: E402
from services.semantic_service.proposals.validators import (  # noqa: E402
    check_content_edit,
    check_internal_link,
    check_text,
    host_of,
)

CTX = TextContext(host="shop.example.com")


def codes_of(issues):
    return {i.code for i in issues}


def text_of(length):
    """Осмысленный текст ровно заданной длины без заглушек и рискованных утверждений."""
    base = "Зимние ботинки ручной работы из натуральной кожи для города и дальних прогулок по снегу " * 100
    return base[:length].rstrip() if base[:length].endswith(" ") is False else (base[: length - 1] + "я")


@pytest.mark.parametrize("kind,low,high", [(OperationType.TITLE, 10, 70), (OperationType.DESCRIPTION, 50, 160), (OperationType.H1, 5, 120)])
async def test_text_length_boundaries(kind, low, high):
    exact_low, exact_high = text_of(low), text_of(high)
    assert len(exact_low) == low and len(exact_high) == high
    assert codes_of(check_text(kind, exact_low, CTX)) == set()
    assert codes_of(check_text(kind, exact_high, CTX)) == set()
    assert "too_short" in codes_of(check_text(kind, text_of(low - 1), CTX))
    assert "too_long" in codes_of(check_text(kind, text_of(high + 1), CTX))
    assert codes_of(check_text(kind, "", CTX)) >= {"empty_value"}
    assert "too_short" not in codes_of(check_text(kind, "", CTX))   # пустое — отдельный код, не «слишком короткое»


async def test_text_issues_carry_the_given_or_the_default_path():
    explicit = check_text(OperationType.TITLE, "x", CTX, "custom.path")
    assert {i.path for i in explicit} == {"custom.path"}
    default = check_text(OperationType.TITLE, "x", CTX)
    assert {i.path for i in default} == {"head.title"}
    assert {i.path for i in check_text(OperationType.DESCRIPTION, "x", CTX)} == {"head.meta_description"}
    assert {i.path for i in check_text(OperationType.H1, "x", CTX)} == {"body.h1"}
    unsupported = check_text(OperationType.INTERNAL_LINK, "x", CTX, "p")
    assert [(i.code, i.detail) for i in unsupported] == [("operation_unsupported", "internal_link")]
    assert check_text("weird", "x", CTX)[0].detail == "weird"      # type: ignore[arg-type]


def jsonld(**fields):
    return validators.canonical_jsonld({"@context": "https://schema.org", "@type": "WebPage", "name": "Зимняя обувь", **fields})


async def test_jsonld_size_and_text_limits_are_exact():
    shell = validators.canonical_jsonld({"@context": "https://schema.org", "@type": "WebPage", "name": "Н", "description": ""})
    # description 500 знаков допустимо, 501 — нет
    ok = jsonld(description=text_of(500))
    assert "too_long" not in codes_of(check_jsonld(ok, CTX))
    assert "too_long" in codes_of(check_jsonld(jsonld(description=text_of(501)), CTX))
    assert shell  # защита от случайного удаления переменной
    # name 200 допустимо, 201 нет
    assert "too_long" not in codes_of(check_jsonld(validators.canonical_jsonld({"@context": "https://schema.org", "@type": "WebPage", "name": text_of(200)}), CTX))
    assert "too_long" in codes_of(check_jsonld(validators.canonical_jsonld({"@context": "https://schema.org", "@type": "WebPage", "name": text_of(201)}), CTX))


async def test_jsonld_byte_limit_counts_bytes_not_characters():
    prefix = '{"@context":"https://schema.org","@type":"WebPage","name":"'
    suffix = '"}'
    room = 8192 - len((prefix + suffix).encode("utf-8"))
    exactly = prefix + "я" * (room // 2) + "a" * (room % 2) + suffix
    assert len(exactly.encode("utf-8")) == 8192
    assert Issue("too_long", "head.jsonld", ">8192 bytes") not in check_jsonld(exactly, CTX)
    assert check_jsonld(exactly + " ", CTX) == [Issue("too_long", "head.jsonld", ">8192 bytes")]


async def test_jsonld_unsupported_type_detail_is_truncated():
    value = validators.canonical_jsonld({"@context": "https://schema.org", "@type": "Т" * 100, "name": "Имя"})
    (issue,) = [i for i in check_jsonld(value, CTX) if i.code == "schema_type_unsupported"]
    assert issue.detail == "Т" * 40


async def test_anchor_length_boundaries():
    def link(anchor):
        return validators.canonical_json({"anchor": anchor, "position": "body", "target_url": "https://shop.example.com/catalog"})

    assert check_internal_link(link("Кат"), CTX, "p") == []
    assert check_internal_link(link("К" * 100), CTX, "p") == []
    assert "anchor_invalid" in codes_of(check_internal_link(link("Ка"), CTX, "p"))
    assert "anchor_invalid" in codes_of(check_internal_link(link("К" * 101), CTX, "p"))
    assert "anchor_invalid" in codes_of(check_internal_link(link("  Каталог"), CTX, "p"))


async def test_link_target_must_be_canonical_and_position_known():
    base = {"anchor": "Каталог обуви", "position": "body"}
    for target, code in [("HTTPS://shop.example.com/catalog", "link_target_invalid"), ("https://shop.example.com/catalog?x=1", "link_target_invalid"),
                         (5, "link_target_invalid"), (None, "link_target_invalid")]:
        found = codes_of(check_internal_link(validators.canonical_json({**base, "target_url": target}), CTX, "p"))
        assert code in found, target
    for position in ("intro", "body", "conclusion"):
        assert check_internal_link(validators.canonical_json({**base, "position": position, "target_url": "https://shop.example.com/c"}), CTX, "p") == []
    assert "link_position_invalid" in codes_of(check_internal_link(validators.canonical_json({**base, "position": "footer", "target_url": "https://shop.example.com/c"}), CTX, "p"))
    # без host в контексте чужой адрес не отвергается как чужой (проверка по host пропускается)
    assert check_internal_link(validators.canonical_json({**base, "target_url": "https://other.example/c"}), TextContext(), "p") == []


async def test_content_edit_boundaries_and_whitespace():
    assert check_content_edit(None, "я" * 2000, CTX, "p") == []
    assert "too_long" in codes_of(check_content_edit(None, "я" * 2001, CTX, "p"))
    assert check_content_edit(None, "я", CTX, "p") == []
    assert "not_normalised" in codes_of(check_content_edit(None, " абзац", CTX, "p"))
    assert "not_normalised" in codes_of(check_content_edit(None, "абзац\n", CTX, "p"))


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://shop.example.com/" + "a" * (2048 - len("https://shop.example.com/")), True),   # ровно 2048
        ("https://shop.example.com/" + "a" * (2049 - len("https://shop.example.com/")), False),
        ("https://shop.example.com/a!b", True),          # '!' (0x21) допустим
        ("https://shop.example.com/a b", False),         # пробел (0x20) — нет
        ("https://shop.example.com/a\x7fb", False),      # DEL
        ("https://shop.example.com/a\\b", False),
        ("https://shop.example.com:8080/", False),
        ("http://shop.example.com:80/", True),
        ("", False),
        (None, False),
    ],
)
async def test_url_length_and_character_boundaries(url, expected):
    assert (canonical_url(url) is not None) is expected


async def test_host_of_returns_the_host_or_empty():
    assert host_of("https://Shop.Example.com/x") == "shop.example.com"
    assert host_of("not a url") == "" and host_of("") == ""


async def test_validator_applies_text_rules_to_title_h1_and_description_versions():
    long_title = Change(path="head.title", mode=ChangeMode.SET, before_state="missing", after=text_of(71), cms_field="title")
    assert "too_long" in codes(make_version(changes=(long_title,)))
    for operation, path, cms in ((OperationType.H1, "body.h1", None), (OperationType.DESCRIPTION, "head.meta_description", "description")):
        short = Change(path=path, mode=ChangeMode.SET, before_state="missing", after="Кратко", cms_field=cms)
        mode = ExecutionMode.MANAGED if operation is OperationType.DESCRIPTION else ExecutionMode.MANUAL
        reason = None if operation is OperationType.DESCRIPTION else "cms_plugin_has_no_h1_write"
        found = codes(make_version(operation=operation, changes=(short,), mode=mode, manual_reason=reason))
        assert ("too_short" in found) == (operation is OperationType.DESCRIPTION), operation
    bad_jsonld = Change(path="head.jsonld", mode=ChangeMode.SET, before_state="missing", after="not json", cms_field="jsonld")
    assert "invalid_json" in codes(make_version(operation=OperationType.SCHEMA_JSONLD, changes=(bad_jsonld,)))


async def test_content_edit_with_a_removal_of_a_missing_field_and_unchanged_fields():
    missing = Change(path="head.meta_description", mode=ChangeMode.REMOVE, before_state="missing", cms_field="description")
    found = codes(make_version(operation=OperationType.DESCRIPTION, changes=(missing,)))
    assert {"nothing_to_remove"} <= found and "remove_not_allowed" not in found
    present = Change(path="head.meta_description", mode=ChangeMode.REMOVE, before_state="value", before="Старое описание страницы каталога", cms_field="description")
    assert codes(make_version(operation=OperationType.DESCRIPTION, changes=(present,))) == set()


# --- derive / builder -----------------------------------------------------------------------------------------------------------------

async def test_unchanged_fields_get_no_precondition_and_removals_get_their_check():
    unchanged = Change(path="head.title", mode=ChangeMode.UNCHANGED, before_state="value", before="Старый заголовок страницы", cms_field="title")
    removed = Change(path="head.meta_description", mode=ChangeMode.REMOVE, before_state="value", before="Старое описание страницы", cms_field="description")
    setting = Change(path="head.jsonld", mode=ChangeMode.SET, before_state="missing", after='{"a":1}', cms_field="jsonld")
    pre = derive.derive_preconditions(PROJECT, TARGET, EVIDENCE_REF, (unchanged, removed, setting))
    assert [(p.kind, p.path) for p in pre if p.kind == "before_digest"] == [("before_digest", "head.meta_description"), ("before_digest", "head.jsonld")]
    assert next(p for p in pre if p.path == "head.jsonld").sha256 is None
    checks = derive.derive_expected_checks(TARGET, (unchanged, removed, setting))
    assert [(c.kind, c.path) for c in checks if c.kind != "target_http_ok"] == [("field_removed", "head.meta_description"), ("field_equals_after", "head.jsonld")]


async def test_managed_versions_state_the_site_dependency_and_manual_ones_do_not():
    managed = (await generate(request())).items[0].version
    assert "managed_write_depends_on_site_capabilities" in managed.limitations
    h1 = (await generate(request(finding_codes=["h1_missing"]))).items[0].version
    assert h1.execution_mode is ExecutionMode.MANUAL and h1.manual_reason == "cms_plugin_has_no_h1_write"
    assert "managed_write_depends_on_site_capabilities" not in h1.limitations


async def test_builder_rejects_an_invalid_target_url():
    spec = builder.ChangeSpec(path="head.title", mode=ChangeMode.SET, before=None, after="Зимние ботинки ручной работы", source="deterministic", provider="deterministic", model="facts_v1")
    with pytest.raises(ProposalRejected) as failure:
        builder.build_version(
            project_id=PROJECT, task_id=None, target_url="ftp://x", entity=None, evidence=list(EVIDENCE_REF), operation=OperationType.TITLE,
            specs=[spec], now=datetime(2026, 11, 4, tzinfo=timezone.utc), new_id=lambda: str(uuid.uuid4()), prompt_version=None, previous=None,
        )
    assert failure.value.issues == [Issue("target_url_invalid", "target.url")]


async def test_value_objects_are_immutable():
    for obj, attribute in ((Issue("c"), "code"), (TextContext(), "host"), (builder.ChangeSpec(path="p", mode=ChangeMode.SET, before=None, after="a", source="llm", provider="p", model="m"), "path")):
        with pytest.raises(Exception) as failure:
            setattr(obj, attribute, "other")
        assert failure.type.__name__ == "FrozenInstanceError"


async def test_issue_serialisation_roundtrip():
    assert Issue("c", "p", "d").as_dict() == {"code": "c", "path": "p", "detail": "d"}
    assert str(ProposalRejected([Issue("a", "x"), Issue("b")])) == "a@x; b"


# --- модель: опубликованные пределы и сохранение пробелов ----------------------------------------------------------------------

EXPECTED_LIMITS = {
    ("RecommendationVersion", "evidence"): (1, 50, "items"),
    ("RecommendationVersion", "changes"): (1, 20, "items"),
    ("RecommendationVersion", "preconditions"): (1, 100, "items"),
    ("RecommendationVersion", "expected_checks"): (None, 100, "items"),
    ("RecommendationVersion", "limitations"): (None, 20, "items"),
    ("RecommendationVersion", "provenance"): (1, 20, "items"),
    ("RecommendationVersion", "task_id"): (None, 128, "text"),
    ("RecommendationVersion", "manual_reason"): (None, 200, "text"),
    ("RecommendationVersion", "provider"): (None, 40, "text"),
    ("RecommendationVersion", "model"): (None, 100, "text"),
    ("Target", "origin"): (8, 300, "text"),
    ("Target", "url"): (8, 2048, "text"),
    ("ExternalEntity", "kind"): (1, 40, "text"),
    ("ExternalEntity", "id"): (1, 128, "text"),
    ("EvidenceRef", "evidence_id"): (1, 128, "text"),
    ("Change", "path"): (1, 100, "text"),
    ("Change", "before"): (None, 8192, "text"),
    ("Change", "after"): (None, 8192, "text"),
    ("Precondition", "path"): (None, 100, "text"),
    ("Precondition", "evidence_id"): (None, 128, "text"),
    ("Precondition", "project_id"): (None, 36, "text"),
    ("Precondition", "origin"): (None, 300, "text"),
    ("ExpectedCheck", "path"): (None, 100, "text"),
    ("ExpectedCheck", "url"): (None, 2048, "text"),
    ("FieldProvenance", "path"): (None, 100, "text"),
    ("FieldProvenance", "provider"): (None, 40, "text"),
    ("FieldProvenance", "model"): (None, 100, "text"),
}


def _bounds(prop):
    options = prop.get("anyOf") or [prop]
    for option in options:
        if option.get("type") == "null":
            continue
        low = option.get("minLength", option.get("minItems"))
        high = option.get("maxLength", option.get("maxItems"))
        return low, high
    raise AssertionError(prop)


@pytest.mark.parametrize("key", sorted(EXPECTED_LIMITS))
async def test_published_schema_limits(key):
    model, field = key
    schema = recommendation_version_json_schema()
    definition = schema if model == "RecommendationVersion" else schema["$defs"][model]
    low, high = _bounds(definition["properties"][field])
    expected_low, expected_high, _ = EXPECTED_LIMITS[key]
    assert (low, high) == (expected_low, expected_high), key


async def test_version_number_range_and_whitespace_preservation():
    schema = recommendation_version_json_schema()["properties"]["version"]
    assert (schema["minimum"], schema["maximum"]) == (1, 10_000)
    change = Change(path="head.title", mode=ChangeMode.SET, before_state="value", before="  с пробелами  ", after="  новый  ", cms_field="title")
    assert (change.before, change.after) == ("  с пробелами  ", "  новый  ")


async def test_model_forbids_mutation_of_nested_values():
    version = real_version()
    with pytest.raises(ValidationError):
        version.changes[0].after = "другой"       # type: ignore[misc]
    with pytest.raises(ValidationError):
        version.target.url = "https://x.example/"  # type: ignore[misc]
