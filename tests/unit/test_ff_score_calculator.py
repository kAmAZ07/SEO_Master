from services.semantic_service.scoring.ff_score_calculator import calculate_ff_score


def test_ff_score_weighting_and_thresholds():
    res = calculate_ff_score(
        freshness_days_since_update=10,
        serp_shift=-2.0,
        link_velocity=2.0,
        semantic_distance=20.0,
        keyword_coverage=80.0,
        eeat_score=70.0,
        cwv_grade="good",
        broken_links_count=0,
        schema_errors_count=0,
    )
    assert 0.0 <= res["ff_score"] <= 100.0
    w = res["components"]["weights"]
    assert round(w["freshness"] + w["familiarity"] + w["quality"], 2) == 1.0
    assert "rescue_lt" in res["thresholds"]
    assert "growth_gt" in res["thresholds"]