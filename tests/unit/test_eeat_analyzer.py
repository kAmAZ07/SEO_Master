"""eeat_v1: прежняя формула при полных сигналах, `None` = «не проверялось», честный null."""
import pytest

from services.semantic_service.scoring import eeat_analyzer
from services.semantic_service.scoring.eeat_analyzer import FORMULA_VERSION, analyze_eeat

TEXT = (
    "Я использовал этот метод на практике. Мой опыт показывает, что важно ссылаться на источники. "
    "купить дёшево скидка"
)
FULL = dict(
    backlinks_count=50,
    has_https=True,
    has_privacy_policy=True,
    has_contacts=True,
    has_author_schema=True,
    authoritative_outbound_links=2,
    brand_mentions=3,
)


def test_eeat_weights_and_range():
    r = analyze_eeat(
        text="Я использовал этот метод на практике. Мой опыт показывает, что важно ссылаться на источники.",
        root_url="https://example.com",
        **FULL,
    )
    assert 0.0 <= r["score"] <= 100.0
    b = r["breakdown"]
    assert set(b.keys()) == {"experience", "expertise", "authoritativeness", "trustworthiness"}
    assert r["formula_version"] == FORMULA_VERSION == "eeat_v1"
    assert r["status"] == "complete"
    assert r["heuristic"] is True
    assert r["signals"]["weights"] == {
        "experience": 0.25, "expertise": 0.30, "authoritativeness": 0.25, "trustworthiness": 0.20,
    }


def test_full_signals_reproduce_the_legacy_formula():
    # Значения получены прежней реализацией (origin/main) на тех же входах.
    r = analyze_eeat(TEXT, "https://example.com", **FULL)
    assert r["score"] == 40.97
    assert r["breakdown"] == {"experience": 60.0, "expertise": 18.97, "authoritativeness": 10.7, "trustworthiness": 88.0}
    r = analyze_eeat(
        TEXT, "https://example.com", backlinks_count=5000, has_https=True, has_privacy_policy=False,
        has_contacts=True, has_author_schema=False, authoritative_outbound_links=12, brand_mentions=20,
    )
    assert r["score"] == 57.09
    assert r["breakdown"] == {"experience": 40.0, "expertise": 34.97, "authoritativeness": 100.0, "trustworthiness": 58.0}


def test_nothing_checked_gives_null_score_and_no_zero_placeholders():
    r = analyze_eeat("", "https://example.com")
    assert r["score"] is None
    assert r["status"] == "insufficient_data"
    assert r["partial"] is None
    assert all(value is None for value in r["breakdown"].values())
    # Отсутствие найденной политики/контактов не превращается в False, ссылок и упоминаний — в 0.
    for name in ("backlinks_count", "brand_mentions", "has_privacy_policy", "has_contacts", "has_author_schema"):
        assert r["signals"][name] == {"value": None, "availability": "unavailable", "reason": "not_checked"}
    assert r["missing_inputs"]["authoritativeness"] == ["backlinks_count", "brand_mentions"]


def test_missing_backlinks_make_authoritativeness_unavailable_and_keep_the_rest():
    signals = {k: v for k, v in FULL.items() if k not in ("backlinks_count", "brand_mentions")}
    r = analyze_eeat(TEXT, "https://example.com", **signals)
    assert r["score"] is None and r["status"] == "insufficient_data"
    assert r["breakdown"]["authoritativeness"] is None
    assert r["breakdown"]["experience"] == 60.0
    assert r["breakdown"]["expertise"] == 18.97
    assert r["breakdown"]["trustworthiness"] == 88.0
    partial = r["partial"]
    assert partial["formula_version"] == "eeat_partial_v1"
    assert partial["components_used"] == ["experience", "expertise", "trustworthiness"]
    assert partial["comparable_to_full_score"] is False
    assert partial["coverage"] == 0.75
    expected = (60.0 * 0.25 + 18.97 * 0.30 + 88.0 * 0.20) / 0.75
    assert partial["score"] == round(expected, 2)
    assert r["missing_inputs"] == {"authoritativeness": ["backlinks_count", "brand_mentions"]}


@pytest.mark.parametrize("missing", ["has_privacy_policy", "has_contacts"])
def test_unknown_policy_or_contacts_is_not_treated_as_absent(missing):
    signals = {k: v for k, v in FULL.items() if k != missing}
    r = analyze_eeat(TEXT, "https://example.com", **signals)
    assert r["breakdown"]["trustworthiness"] is None
    assert r["score"] is None
    assert missing in r["missing_inputs"]["trustworthiness"]
    # явное False — это проверенное «нет», оно считается
    explicit = analyze_eeat(TEXT, "https://example.com", **{**FULL, missing: False})
    assert explicit["breakdown"]["trustworthiness"] is not None
    assert explicit["score"] is not None


def test_explicit_false_privacy_policy_lowers_trust_unlike_unknown():
    with_policy = analyze_eeat(TEXT, "https://example.com", **FULL)["breakdown"]["trustworthiness"]
    without = analyze_eeat(TEXT, "https://example.com", **{**FULL, "has_privacy_policy": False})["breakdown"]["trustworthiness"]
    assert with_policy - without == 30.0


def test_empty_text_makes_text_based_components_unavailable():
    r = analyze_eeat("   ", "https://example.com", **FULL)
    assert r["breakdown"]["experience"] is None
    assert r["breakdown"]["expertise"] is None
    assert r["breakdown"]["trustworthiness"] is None
    assert r["breakdown"]["authoritativeness"] == 10.7
    assert r["signals"]["word_count"] == 0
    assert r["signals"]["readability_proxy"] is None
    assert r["score"] is None


def test_https_is_derived_from_the_url_and_explicit_false_wins():
    http = analyze_eeat(TEXT, "http://example.com", **FULL)
    assert http["signals"]["has_https"]["value"] is False
    https = analyze_eeat(TEXT, "https://example.com", **{**FULL, "has_https": False})
    assert https["signals"]["has_https"]["value"] is False
    derived = analyze_eeat(TEXT, "https://example.com", **{k: v for k, v in FULL.items() if k != "has_https"})
    assert derived["signals"]["has_https"] == {"value": True, "availability": "provided", "reason": None, "source": "root_url"}
    assert derived["score"] == 40.97


def test_unsupported_scheme_leaves_https_unknown():
    r = analyze_eeat(TEXT, "ftp://example.com", **{k: v for k, v in FULL.items() if k != "has_https"})
    assert r["signals"]["has_https"]["value"] is None
    assert r["breakdown"]["trustworthiness"] is None


def test_none_text_is_treated_as_empty():
    assert analyze_eeat(None, "https://example.com")["signals"]["word_count"] == 0


# --- формулы компонентов: пределы, пороги и округление -------------------------------------------------------------

def test_experience_cues_are_capped_and_author_schema_adds_a_fixed_bonus():
    assert eeat_analyzer._experience_score("страница", False) == 20.0
    assert eeat_analyzer._experience_score("мы " * 12, False) == 80.0
    assert eeat_analyzer._experience_score("мы " * 13, False) == 80.0       # 65 обрезается до 60
    assert eeat_analyzer._experience_score("мы " * 50, False) == 80.0
    assert eeat_analyzer._experience_score("мы " * 50, True) == 100.0
    assert eeat_analyzer._experience_score("страница", True) == 40.0


def test_readability_counts_words_of_seven_letters_as_long():
    assert eeat_analyzer._readability_proxy("") == 0.0
    assert eeat_analyzer._readability_proxy("кот пёс дом") == 100.0
    assert eeat_analyzer._readability_proxy("абвгде кот") == 100.0            # 6 букв — не длинное слово
    assert eeat_analyzer._readability_proxy("абвгдеж кот") == 50.0            # ровно 7 букв — длинное
    assert eeat_analyzer._readability_proxy("абвгдежз абвгдежз") == 0.0


def test_expertise_depth_reaches_full_scale_at_twelve_hundred_words_and_stops():
    twelve_hundred = " ".join(["кот"] * 1200)
    assert eeat_analyzer._expertise_score(twelve_hundred, 10) == 100.0
    assert eeat_analyzer._expertise_score(" ".join(["кот"] * 2400), 10) == 100.0
    half = " ".join(["кот"] * 600)
    assert eeat_analyzer._expertise_score(half, 0) == pytest.approx(0.6 * 50.0 + 0.4 * (100.0 + 0.0) / 2)
    assert eeat_analyzer._expertise_score(half, 5) == pytest.approx(0.6 * 50.0 + 0.4 * (100.0 + 50.0) / 2)


def test_authoritativeness_scales_and_saturates():
    assert eeat_analyzer._authoritativeness_score(0, 0) == 0.0
    assert eeat_analyzer._authoritativeness_score(500, 0) == pytest.approx(0.7 * 50.0)
    assert eeat_analyzer._authoritativeness_score(1000, 0) == pytest.approx(70.0)
    assert eeat_analyzer._authoritativeness_score(10_000_000, 0) == pytest.approx(70.0)
    assert eeat_analyzer._authoritativeness_score(0, 5) == pytest.approx(0.3 * 40.0)
    assert eeat_analyzer._authoritativeness_score(0, 12) == pytest.approx(0.3 * 96.0)
    assert eeat_analyzer._authoritativeness_score(0, 13) == pytest.approx(30.0)
    assert eeat_analyzer._authoritativeness_score(10_000, 10_000) == 100.0


def test_trust_baseline_flags_and_spam_penalty_cap():
    trust = eeat_analyzer._trustworthiness_score
    assert trust(False, False, False, "") == 10.0                  # базовые 10 без флагов
    assert trust(True, False, False, "") == 50.0
    assert trust(False, True, False, "") == 40.0
    assert trust(False, False, True, "") == 30.0
    assert trust(True, True, True, "") == 100.0
    assert trust(True, True, True, "купить") == 94.0               # штраф 6 за слово
    assert trust(True, True, True, "купить дешево скидка топ") == 76.0
    assert trust(True, True, True, "купить " * 5) == 70.0          # штраф 30 — предел
    assert trust(True, True, True, "купить " * 6) == 70.0          # дальше штраф не растёт
    assert trust(False, False, False, "купить " * 20) == 0.0       # итог не ниже нуля


def test_single_word_text_counts_as_text():
    result = analyze_eeat("слово", "https://a.test/", has_author_schema=False, authoritative_outbound_links=0,
                          backlinks_count=0, brand_mentions=0, has_privacy_policy=False, has_contacts=False)
    assert result["status"] == "complete" and result["signals"]["word_count"] == 1


def test_partial_report_states_weights_coverage_and_rounding():
    result = analyze_eeat(
        "мы " * 40, "https://a.test/", backlinks_count=345, brand_mentions=3,
    )
    partial = result["partial"]
    assert result["score"] is None and partial["components_used"] == ["authoritativeness"]
    assert partial["weights_applied"] == {"authoritativeness": 1.0} and partial["coverage"] == 0.25
    two = analyze_eeat("мы " * 40, "https://a.test/", backlinks_count=345, brand_mentions=3, has_author_schema=True)["partial"]
    assert two["components_used"] == ["experience", "authoritativeness"]
    assert two["weights_applied"] == {"experience": round(0.25 / 0.5, 4), "authoritativeness": round(0.25 / 0.5, 4)}
    three = analyze_eeat(
        "мы " * 40, "https://a.test/", backlinks_count=345, brand_mentions=3, has_author_schema=True,
        has_privacy_policy=True, has_contacts=True,
    )["partial"]
    assert three["weights_applied"] == {"experience": 0.3571, "authoritativeness": 0.3571, "trustworthiness": 0.2857}
    assert three["coverage"] == 0.7
    # experience 100, authoritativeness 0.7·34.5 + 0.3·24 = 31.35, trustworthiness 100
    assert three["score"] == round((100.0 * 0.25 + 31.35 * 0.25 + 100.0 * 0.20) / 0.7, 2) == 75.48
    assert two["score"] == pytest.approx((100.0 + 31.35) / 2, abs=0.006)
    assert result["partial"]["score"] == pytest.approx(31.35, abs=0.006)
