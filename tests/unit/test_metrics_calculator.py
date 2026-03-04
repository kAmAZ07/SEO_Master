from services.reporting_service.metrics.roi_calculator import calculate_roi_metrics
from services.reporting_service.metrics.calculator import calculate_trust_sentiment


def test_roi_calculator_basic():
    r = calculate_roi_metrics(cost=100.0, revenue=250.0, hitl_actions=2, automated_actions=8)
    assert r["profit"] == 150.0
    assert r["roi_percent"] > 0.0
    assert 0.0 <= r["hitl_efficiency_percent"] <= 100.0


def test_trust_sentiment_range():
    r = calculate_trust_sentiment(
        has_https=True,
        has_privacy_policy=True,
        has_contacts=True,
        brand_mentions=2,
        negative_reviews=0,
        text="Отличное качество, рекомендую. Надежный сервис.",
    )
    assert 0.0 <= r["trust_score"] <= 100.0
    assert 0.0 <= r["sentiment_score"] <= 100.0