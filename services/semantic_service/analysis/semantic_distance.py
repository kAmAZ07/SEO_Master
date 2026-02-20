import math
import re


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-zа-я0-9]+", text.lower()) if t]


def _tf(tokens: list[str]) -> dict:
    d = {}
    for t in tokens:
        d[t] = d.get(t, 0) + 1
    return d


def _cosine(a: dict, b: dict) -> float:
    keys = set(a.keys()) | set(b.keys())
    dot = 0.0
    na = 0.0
    nb = 0.0
    for k in keys:
        va = float(a.get(k, 0))
        vb = float(b.get(k, 0))
        dot += va * vb
        na += va * va
        nb += vb * vb
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def serp_minus_10_distance(target_text: str, serp_top10_texts: list[str]) -> dict:
    t = _tf(_tokens(target_text))
    sims = []
    for txt in serp_top10_texts[:10]:
        s = _cosine(t, _tf(_tokens(txt)))
        sims.append(s)
    if not sims:
        return {"semantic_distance": 100.0, "similarity_avg": 0.0, "similarity_min": 0.0, "n": 0}
    sim_avg = sum(sims) / len(sims)
    sim_min = min(sims)
    dist = max(0.0, (1.0 - sim_avg) * 100.0)
    dist_minus10 = max(0.0, (1.0 - ((0.7 * sim_avg) + (0.3 * sim_min))) * 100.0)
    return {
        "semantic_distance": round(dist_minus10, 2),
        "similarity_avg": round(sim_avg, 4),
        "similarity_min": round(sim_min, 4),
        "n": len(sims),
    }