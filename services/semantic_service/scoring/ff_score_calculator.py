def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _freshness_component(days_since_update: int, serp_shift: float, link_velocity: float) -> float:
    freshness = _clamp(100.0 - min(100.0, (days_since_update / 180.0) * 100.0))
    serp = _clamp(50.0 + (-serp_shift) * 10.0)
    lv = _clamp(min(100.0, link_velocity * 20.0))
    return _clamp(0.5 * freshness + 0.3 * serp + 0.2 * lv)


def _familiarity_component(semantic_distance: float, keyword_coverage: float, eeat_score: float) -> float:
    dist_score = _clamp(100.0 - semantic_distance)
    cov = _clamp(keyword_coverage)
    eeat = _clamp(eeat_score)
    return _clamp(0.5 * dist_score + 0.3 * cov + 0.2 * eeat)


def _quality_component(cwv_grade: str, broken_links_count: int, schema_errors_count: int) -> float:
    cwv_grade = (cwv_grade or "").lower()
    cwv_map = {"good": 90.0, "needs_improvement": 60.0, "poor": 30.0, "unknown": 50.0}
    cwv = cwv_map.get(cwv_grade, 50.0)
    links_penalty = min(60.0, broken_links_count * 6.0)
    schema_penalty = min(50.0, schema_errors_count * 5.0)
    return _clamp(cwv - links_penalty * 0.6 - schema_penalty * 0.4)


def calculate_ff_score(
    freshness_days_since_update: int,
    serp_shift: float,
    link_velocity: float,
    semantic_distance: float,
    keyword_coverage: float,
    eeat_score: float,
    cwv_grade: str,
    broken_links_count: int,
    schema_errors_count: int,
) -> dict:
    w = {"freshness": 0.30, "familiarity": 0.40, "quality": 0.30}

    freshness = _freshness_component(freshness_days_since_update, serp_shift, link_velocity)
    familiarity = _familiarity_component(semantic_distance, keyword_coverage, eeat_score)
    quality = _quality_component(cwv_grade, broken_links_count, schema_errors_count)

    ff = _clamp(freshness * w["freshness"] + familiarity * w["familiarity"] + quality * w["quality"])

    thresholds = {"rescue_lt": 40.0, "growth_gt": 60.0}

    return {
        "ff_score": round(ff, 2),
        "components": {"freshness": round(freshness, 2), "familiarity": round(familiarity, 2), "quality": round(quality, 2), "weights": w},
        "inputs": {
            "freshness_days_since_update": freshness_days_since_update,
            "serp_shift": serp_shift,
            "link_velocity": link_velocity,
            "semantic_distance": semantic_distance,
            "keyword_coverage": keyword_coverage,
            "eeat_score": eeat_score,
            "cwv_grade": cwv_grade,
            "broken_links_count": broken_links_count,
            "schema_errors_count": schema_errors_count,
        },
        "thresholds": thresholds,
    }