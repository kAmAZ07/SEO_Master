def _safe_div(a: float, b: float) -> float:
    if b == 0.0:
        return 0.0
    return a / b


def calculate_roi_metrics(cost: float, revenue: float, hitl_actions: int, automated_actions: int) -> dict:
    profit = revenue - cost
    roi = _safe_div(profit, cost) * 100.0 if cost > 0 else 0.0
    cost_eff = _safe_div(revenue, cost) if cost > 0 else 0.0
    total_actions = max(0, hitl_actions) + max(0, automated_actions)
    hitl_eff = 0.0 if total_actions == 0 else (max(0, automated_actions) / total_actions) * 100.0
    return {
        "profit": round(profit, 2),
        "roi_percent": round(roi, 2),
        "cost_efficiency": round(cost_eff, 4),
        "hitl_efficiency_percent": round(hitl_eff, 2),
        "inputs": {"cost": cost, "revenue": revenue, "hitl_actions": hitl_actions, "automated_actions": automated_actions},
    }