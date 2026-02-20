import re
from urllib.parse import urlparse


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _word_count(text: str) -> int:
    return len([w for w in re.split(r"\s+", text.strip()) if w])


def _readability_proxy(text: str) -> float:
    words = _word_count(text)
    if words == 0:
        return 0.0
    long_words = len([w for w in re.findall(r"\b\w+\b", text) if len(w) >= 7])
    return _clamp(100.0 - (long_words / max(words, 1)) * 100.0)


def _experience_score(text: str, has_author_schema: bool) -> float:
    cues = re.findall(r"\b(я|мой|моя|мы|лично|опыт|использовал|использовала)\b", text.lower())
    base = 20.0 + min(60.0, len(cues) * 5.0)
    if has_author_schema:
        base += 20.0
    return _clamp(base)


def _expertise_score(text: str, authoritative_outbound_links: int) -> float:
    wc = _word_count(text)
    read = _readability_proxy(text)
    depth = _clamp((wc / 1200.0) * 100.0)
    sources = _clamp(min(100.0, authoritative_outbound_links * 10.0))
    return _clamp(0.6 * depth + 0.4 * ((read + sources) / 2.0))


def _authoritativeness_score(backlinks_count: int, brand_mentions: int) -> float:
    bl = _clamp(min(100.0, backlinks_count / 10.0))
    bm = _clamp(min(100.0, brand_mentions * 8.0))
    return _clamp(0.7 * bl + 0.3 * bm)


def _trustworthiness_score(has_https: bool, has_privacy_policy: bool, has_contacts: bool, text: str) -> float:
    base = 0.0
    base += 40.0 if has_https else 0.0
    base += 30.0 if has_privacy_policy else 0.0
    base += 20.0 if has_contacts else 0.0
    spam = len(re.findall(r"\b(купить|дешево|скидка|топ)\b", text.lower()))
    penalty = min(30.0, spam * 6.0)
    return _clamp(base + 10.0 - penalty)


def analyze_eeat(
    text: str,
    root_url: str,
    backlinks_count: int = 0,
    has_https: bool = True,
    has_privacy_policy: bool = False,
    has_contacts: bool = False,
    has_author_schema: bool = False,
    authoritative_outbound_links: int = 0,
    brand_mentions: int = 0,
) -> dict:
    p = urlparse(root_url)
    has_https = has_https and (p.scheme == "https")

    exp = _experience_score(text, has_author_schema)
    expt = _expertise_score(text, authoritative_outbound_links)
    auth = _authoritativeness_score(backlinks_count, brand_mentions)
    trust = _trustworthiness_score(has_https, has_privacy_policy, has_contacts, text)

    weights = {"experience": 0.25, "expertise": 0.30, "authoritativeness": 0.25, "trustworthiness": 0.20}
    score = _clamp(exp * weights["experience"] + expt * weights["expertise"] + auth * weights["authoritativeness"] + trust * weights["trustworthiness"])

    return {
        "score": round(score, 2),
        "breakdown": {
            "experience": round(exp, 2),
            "expertise": round(expt, 2),
            "authoritativeness": round(auth, 2),
            "trustworthiness": round(trust, 2),
        },
        "signals": {
            "weights": weights,
            "word_count": _word_count(text),
            "readability_proxy": round(_readability_proxy(text), 2),
            "backlinks_count": backlinks_count,
            "brand_mentions": brand_mentions,
            "authoritative_outbound_links": authoritative_outbound_links,
            "has_https": has_https,
            "has_privacy_policy": has_privacy_policy,
            "has_contacts": has_contacts,
            "has_author_schema": has_author_schema,
        },
    }