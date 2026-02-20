from services.semantic_service.analysis.keyword_coverage import keyword_coverage


def analyze_content_gap(target_text: str, serp_texts: list[str], required_keywords: list[str]) -> dict:
    target_cov = keyword_coverage(target_text, required_keywords)
    serp_covs = [keyword_coverage(t, required_keywords) for t in serp_texts]

    avg_serp_cov = 0.0
    if serp_covs:
        avg_serp_cov = sum(c["coverage"] for c in serp_covs) / len(serp_covs)

    gap = max(0.0, round(avg_serp_cov - target_cov["coverage"], 2))

    suggestions = []
    if gap >= 10.0:
        suggestions.append("Увеличить покрытие ключевых фраз и расширить разделы, которые закрывают интент.")
    if target_cov["missing"]:
        suggestions.append("Добавить отсутствующие ключевые фразы в релевантные блоки (без переспама).")

    return {
        "target_coverage": target_cov,
        "avg_serp_coverage": round(avg_serp_cov, 2),
        "gap": gap,
        "suggestions": suggestions,
    }