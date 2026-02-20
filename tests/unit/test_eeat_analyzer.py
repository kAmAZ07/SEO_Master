from services.semantic_service.scoring.eeat_analyzer import analyze_eeat


def test_eeat_weights_and_range():
    r = analyze_eeat(
        text="Я использовал этот метод на практике. Мой опыт показывает, что важно ссылаться на источники.",
        root_url="https://example.com",
        backlinks_count=50,
        has_https=True,
        has_privacy_policy=True,
        has_contacts=True,
        has_author_schema=True,
        authoritative_outbound_links=2,
        brand_mentions=3,
    )
    assert 0.0 <= r["score"] <= 100.0
    b = r["breakdown"]
    assert set(b.keys()) == {"experience", "expertise", "authoritativeness", "trustworthiness"}