"""ff_score_v1: контрольные примеры формулы, честный null при нехватке данных, наблюдения (readings)."""
import math

import pytest

from services.semantic_service.scoring.ff_score_calculator import (
    COMPONENT_INPUTS,
    FORMULA_VERSION,
    PARTIAL_FORMULA_VERSION,
    calculate_ff_score,
    mode_for,
)
from services.semantic_service.scoring.readings import (
    ALL_INPUTS,
    Reading,
    ReadingError,
    complete_readings,
    make_reading,
    unavailable,
)


def _full(**overrides):
    values = {
        "freshness_days_since_update": 10,
        "serp_shift": -2.0,
        "link_velocity": 2.0,
        "semantic_distance": 20.0,
        "keyword_coverage": 80.0,
        "eeat_score": 70.0,
        "cwv_grade": "good",
        "broken_links_count": 0,
        "schema_errors_count": 0,
    }
    values.update(overrides)
    return {name: make_reading(name, value, source="test") for name, value in values.items() if value is not None}


# Контрольные значения получены прежней реализацией (origin/main) на тех же входах:
# формулы и веса сохранены, поэтому полный балл не изменился.
LEGACY_CONTROL = [
    ((10, -2.0, 2.0, 20.0, 80.0, 70.0, "good", 0, 0), 81.07, (76.22, 78.0, 90.0)),
    ((400, 5.0, 0.0, 90.0, 10.0, 20.0, "poor", 12, 9), 4.8, (0.0, 12.0, 0.0)),
    ((180, 0.0, 5.0, 50.0, 50.0, 50.0, "needs_improvement", 3, 2), 44.06, (35.0, 50.0, 45.2)),
    ((0, -50.0, 100.0, 0.0, 100.0, 100.0, "good", 0, 0), 97.0, (100.0, 100.0, 90.0)),
]


@pytest.mark.parametrize("raw,expected_score,expected_components", LEGACY_CONTROL)
def test_full_score_reproduces_legacy_formula(raw, expected_score, expected_components):
    readings = {name: make_reading(name, value) for name, value in zip(ALL_INPUTS_ORDER, raw)}
    result = calculate_ff_score(readings)
    assert result["status"] == "complete"
    assert result["ff_score"] == expected_score
    got = tuple(result["components"][name]["score"] for name in ("freshness", "familiarity", "quality"))
    assert got == expected_components


ALL_INPUTS_ORDER = (
    "freshness_days_since_update", "serp_shift", "link_velocity",
    "semantic_distance", "keyword_coverage", "eeat_score",
    "cwv_grade", "broken_links_count", "schema_errors_count",
)


def test_weights_sum_to_one_and_formula_is_versioned():
    result = calculate_ff_score(_full())
    assert result["formula_version"] == FORMULA_VERSION == "ff_score_v1"
    assert result["weights"] == {"freshness": 0.30, "familiarity": 0.40, "quality": 0.30}
    assert round(sum(result["weights"].values()), 10) == 1.0
    assert result["thresholds"] == {"rescue_lt": 40.0, "growth_gt": 60.0}
    assert result["heuristic"] is True
    assert result["coverage"]["components_available"] == 3
    assert result["coverage"]["missing_inputs"] == []
    assert result["partial"] is None


def test_every_input_is_documented_with_unit_and_provenance_in_the_result():
    result = calculate_ff_score(_full())
    assert set(result["inputs"]) == set(ALL_INPUTS)
    days = result["inputs"]["freshness_days_since_update"]
    assert days == {
        "value": 10, "unit": "days", "availability": "provided",
        "source": "test", "observed_at": None, "reason": None,
    }
    assert set(COMPONENT_INPUTS) == {"freshness", "familiarity", "quality"}
    assert sorted(n for names in COMPONENT_INPUTS.values() for n in names) == sorted(ALL_INPUTS)


def test_no_inputs_at_all_gives_null_score_and_no_partial():
    result = calculate_ff_score({})
    assert result["ff_score"] is None
    assert result["status"] == "insufficient_data"
    assert result["mode"] is None
    assert result["partial"] is None
    assert result["coverage"]["components_available"] == 0
    assert result["coverage"]["missing_components"] == ["freshness", "familiarity", "quality"]
    assert set(result["coverage"]["missing_inputs"]) == set(ALL_INPUTS)
    assert all(c["score"] is None and c["status"] == "unavailable" for c in result["components"].values())


@pytest.mark.parametrize("missing", ALL_INPUTS_ORDER)
def test_any_single_missing_input_makes_full_score_null(missing):
    """Ни один недостающий вход не заменяется числом по умолчанию."""
    readings = _full(**{missing: None})
    assert missing not in readings
    result = calculate_ff_score(readings)
    assert result["ff_score"] is None
    assert result["status"] == "insufficient_data"
    assert missing in result["coverage"]["missing_inputs"]
    assert result["inputs"][missing]["availability"] == "unavailable"
    assert result["inputs"][missing]["value"] is None
    assert result["components"][_component_of(missing)]["missing_inputs"] == [missing]


def _component_of(name):
    return next(c for c, names in COMPONENT_INPUTS.items() if name in names)


def test_partial_score_is_separately_named_with_explicit_weights_and_coverage():
    readings = _full(link_velocity=None)  # freshness недоступна
    result = calculate_ff_score(readings)
    assert result["ff_score"] is None
    partial = result["partial"]
    assert partial["formula_version"] == PARTIAL_FORMULA_VERSION == "ff_score_partial_v1"
    assert partial["components_used"] == ["familiarity", "quality"]
    assert partial["comparable_to_full_score"] is False
    assert partial["coverage"] == 0.7
    assert partial["weights_applied"] == {"familiarity": round(0.4 / 0.7, 4), "quality": round(0.3 / 0.7, 4)}
    # (78.0 * 0.4 + 90.0 * 0.3) / 0.7 = 83.14...
    assert partial["score"] == round((78.0 * 0.4 + 90.0 * 0.3) / 0.7, 2)
    assert result["coverage"]["weight_available"] == 0.7
    # доступные компоненты показываются отдельно и не теряются
    assert result["components"]["familiarity"]["score"] == 78.0
    assert result["components"]["quality"]["score"] == 90.0
    assert result["components"]["freshness"]["score"] is None


def test_partial_with_a_single_component_uses_weight_one():
    readings = {name: make_reading(name, value) for name, value in
                (("cwv_grade", "good"), ("broken_links_count", 0), ("schema_errors_count", 0))}
    partial = calculate_ff_score(readings)["partial"]
    assert partial["components_used"] == ["quality"]
    assert partial["weights_applied"] == {"quality": 1.0}
    assert partial["score"] == 90.0
    assert partial["coverage"] == 0.3


@pytest.mark.parametrize("availability", ["estimated", "proxy", "mock", "sample"])
def test_estimated_proxy_mock_and_sample_values_never_enter_the_full_score(availability):
    readings = _full()
    readings["link_velocity"] = make_reading("link_velocity", 4.0, availability=availability, source="clicks_momentum")
    result = calculate_ff_score(readings)
    assert result["ff_score"] is None
    assert result["components"]["freshness"]["missing_reasons"] == {"link_velocity": f"excluded_{availability}"}
    # значение при этом не скрыто: оно видно в inputs под своей доступностью
    assert result["inputs"]["link_velocity"]["value"] == 4.0
    assert result["inputs"]["link_velocity"]["availability"] == availability


def test_unavailable_reading_reason_is_reported():
    readings = _full()
    readings["link_velocity"] = unavailable("link_velocity", "no_backlink_source")
    result = calculate_ff_score(readings)
    assert result["components"]["freshness"]["missing_reasons"] == {"link_velocity": "no_backlink_source"}


def test_default_reason_is_not_provided():
    result = calculate_ff_score({})
    assert result["components"]["quality"]["missing_reasons"]["cwv_grade"] == "not_provided"


def test_unknown_cwv_grade_is_not_a_value():
    with pytest.raises(ReadingError) as info:
        make_reading("cwv_grade", "unknown")
    assert info.value.code == "value_invalid"


@pytest.mark.parametrize("score,mode", [(0.0, "rescue"), (39.99, "rescue"), (40.0, "maintain"),
                                         (60.0, "maintain"), (60.01, "growth"), (100.0, "growth")])
def test_threshold_modes_are_heuristic_labels(score, mode):
    result = mode_for(score)
    assert result["name"] == mode
    assert result["heuristic"] is True


def test_mode_is_reported_only_for_a_full_score():
    full = calculate_ff_score(_full())
    assert full["mode"]["name"] == "growth" and full["mode"]["heuristic"] is True
    assert calculate_ff_score(_full(link_velocity=None))["mode"] is None


def test_lower_edge_scores_stay_in_range():
    worst = calculate_ff_score(_full(freshness_days_since_update=3650, serp_shift=50.0, link_velocity=0.0,
                                     semantic_distance=100.0, keyword_coverage=0.0, eeat_score=0.0,
                                     cwv_grade="poor", broken_links_count=1_000_000, schema_errors_count=1_000_000))
    assert worst["ff_score"] == 0.0
    best = calculate_ff_score(_full(freshness_days_since_update=0, serp_shift=-50.0, link_velocity=100.0,
                                    semantic_distance=0.0, keyword_coverage=100.0, eeat_score=100.0))
    assert 0.0 <= best["ff_score"] <= 100.0


# --- readings -----------------------------------------------------------------

@pytest.mark.parametrize("name,value,code", [
    ("freshness_days_since_update", -1, "value_out_of_range"),
    ("freshness_days_since_update", 3651, "value_out_of_range"),
    ("freshness_days_since_update", 1.5, "value_not_integer"),
    ("serp_shift", 50.1, "value_out_of_range"),
    ("serp_shift", math.nan, "value_not_finite"),
    ("serp_shift", math.inf, "value_not_finite"),
    ("serp_shift", -math.inf, "value_not_finite"),
    ("semantic_distance", 100.01, "value_out_of_range"),
    ("keyword_coverage", -0.01, "value_out_of_range"),
    ("eeat_score", 101, "value_out_of_range"),
    ("broken_links_count", -1, "value_out_of_range"),
    ("broken_links_count", True, "value_invalid"),
    ("link_velocity", "5", "value_invalid"),
    ("cwv_grade", 5, "value_invalid"),
    ("cwv_grade", "excellent", "value_invalid"),
])
def test_invalid_values_are_rejected_with_stable_codes(name, value, code):
    with pytest.raises(ReadingError) as info:
        make_reading(name, value)
    assert info.value.code == code


def test_integral_float_is_accepted_for_counts_and_normalised():
    reading = make_reading("broken_links_count", 3.0)
    assert reading.value == 3 and isinstance(reading.value, int)


def test_enum_value_is_normalised_to_lower_case():
    assert make_reading("cwv_grade", "GOOD").value == "good"


def test_unknown_input_name_is_rejected():
    with pytest.raises(ReadingError) as info:
        make_reading("nonsense", 1)
    assert info.value.code == "input_unknown"
    with pytest.raises(ReadingError):
        complete_readings({"nonsense": unavailable("serp_shift", "x")})


def test_none_becomes_unavailable_with_reason():
    reading = make_reading("serp_shift", None, reason="gsc_not_connected")
    assert reading.availability == "unavailable" and reading.value is None and reading.reason == "gsc_not_connected"
    assert make_reading("serp_shift", None).reason == "not_provided"


@pytest.mark.parametrize("observed_at", ["2026-11-04T10:00:00Z", "2026-11-04T10:00:00+00:00", "2026-11-04"])
def test_observed_at_accepts_iso_timestamps(observed_at):
    assert make_reading("serp_shift", 1.0, observed_at=observed_at).observed_at == observed_at


def test_observed_at_rejects_garbage():
    with pytest.raises(ReadingError) as info:
        make_reading("serp_shift", 1.0, observed_at="yesterday")
    assert info.value.code == "observed_at_invalid"


def test_reading_invariants():
    with pytest.raises(ReadingError) as info:
        Reading("serp_shift", "positions", value=1.0, availability="unavailable")
    assert info.value.code == "unavailable_with_value"
    with pytest.raises(ReadingError) as info:
        Reading("serp_shift", "positions", value=None, availability="measured")
    assert info.value.code == "value_missing"
    with pytest.raises(ReadingError) as info:
        Reading("serp_shift", "positions", value=1.0, availability="invented")
    assert info.value.code == "availability_invalid"
    with pytest.raises(ReadingError) as info:
        unavailable("nonsense", "x")
    assert info.value.code == "input_unknown"


def test_readings_report_counted_flag():
    assert make_reading("serp_shift", 1.0, availability="measured").counted
    assert make_reading("serp_shift", 1.0, availability="derived").counted
    assert not make_reading("serp_shift", 1.0, availability="proxy").counted
    assert not unavailable("serp_shift", "x").counted


# --- границы входов и единицы ----------------------------------------------------------------------------------

_BOUNDS = {
    "freshness_days_since_update": (0, 3650, True),
    "serp_shift": (-50.0, 50.0, False),
    "link_velocity": (0.0, 100.0, False),
    "semantic_distance": (0.0, 100.0, False),
    "keyword_coverage": (0.0, 100.0, False),
    "eeat_score": (0.0, 100.0, False),
    "broken_links_count": (0, 1_000_000, True),
    "schema_errors_count": (0, 1_000_000, True),
}


@pytest.mark.parametrize("name", sorted(_BOUNDS))
def test_each_numeric_input_accepts_its_bounds_and_rejects_values_just_outside(name):
    low, high, integer = _BOUNDS[name]
    assert make_reading(name, low).value == low
    assert make_reading(name, high).value == high
    step = 1 if integer else 0.01
    for outside in (low - step, high + step):
        with pytest.raises(ReadingError) as info:
            make_reading(name, outside)
        assert info.value.code == "value_out_of_range", (name, outside)


@pytest.mark.parametrize("name", sorted(_BOUNDS))
def test_integer_inputs_accept_whole_floats_only_and_real_inputs_keep_fractions(name):
    integer = _BOUNDS[name][2]
    if integer:
        reading = make_reading(name, 3.0)
        assert reading.value == 3 and isinstance(reading.value, int)
        with pytest.raises(ReadingError) as info:
            make_reading(name, 3.5)
        assert info.value.code == "value_not_integer"
    else:
        reading = make_reading(name, 3.5)
        assert reading.value == 3.5 and isinstance(reading.value, float)
        assert isinstance(make_reading(name, 3).value, float)


@pytest.mark.parametrize(
    "name,unit",
    [
        ("freshness_days_since_update", "days"),
        ("serp_shift", "positions"),
        ("link_velocity", "links_per_period"),
        ("semantic_distance", "percent"),
        ("keyword_coverage", "percent"),
        ("eeat_score", "score_0_100"),
        ("broken_links_count", "count"),
        ("schema_errors_count", "count"),
        ("cwv_grade", "grade"),
    ],
)
def test_every_input_has_its_unit_for_value_and_missing_readings(name, unit):
    assert unavailable(name, "x").unit == unit
    value = "good" if name == "cwv_grade" else 1
    assert make_reading(name, value).unit == unit


def test_readings_are_immutable():
    reading = make_reading("serp_shift", 1.0)
    with pytest.raises(Exception) as failure:
        reading.value = 2.0  # type: ignore[misc]
    assert failure.type.__name__ == "FrozenInstanceError"


# --- компонент качества: оценки CWV и пределы штрафов -----------------------------------------------------------

def _quality(grade, broken, schema):
    result = calculate_ff_score(_full(cwv_grade=grade, broken_links_count=broken, schema_errors_count=schema))
    return result["components"]["quality"]["score"]


@pytest.mark.parametrize("grade,expected", [("good", 90.0), ("needs_improvement", 60.0), ("poor", 30.0)])
def test_cwv_grade_scores(grade, expected):
    assert _quality(grade, 0, 0) == expected


def test_broken_link_penalty_grows_then_stops_at_its_cap():
    assert _quality("good", 1, 0) == pytest.approx(90.0 - 6 * 0.6)
    assert _quality("good", 9, 0) == pytest.approx(90.0 - 54 * 0.6)
    assert _quality("good", 10, 0) == pytest.approx(90.0 - 60 * 0.6)
    assert _quality("good", 11, 0) == _quality("good", 10, 0) == _quality("good", 1_000_000, 0)


def test_schema_error_penalty_grows_then_stops_at_its_cap():
    assert _quality("good", 0, 1) == pytest.approx(90.0 - 5 * 0.4)
    assert _quality("good", 0, 9) == pytest.approx(90.0 - 45 * 0.4)
    assert _quality("good", 0, 10) == pytest.approx(90.0 - 50 * 0.4)
    assert _quality("good", 0, 11) == _quality("good", 0, 10) == _quality("good", 0, 1_000_000)


def test_quality_never_goes_below_zero():
    assert _quality("poor", 10_000, 10_000) == 0.0


def test_freshness_inputs_saturate_instead_of_leaving_the_scale():
    stale = calculate_ff_score(_full(freshness_days_since_update=3650, serp_shift=50.0, link_velocity=0.0))
    assert stale["components"]["freshness"]["score"] == 0.0
    fast = calculate_ff_score(_full(freshness_days_since_update=0, serp_shift=-50.0, link_velocity=100.0))
    assert fast["components"]["freshness"]["score"] == 100.0
