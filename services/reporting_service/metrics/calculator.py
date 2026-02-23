import re


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def calculate_trust_sentiment(
    has_https: bool = True,
    has_privacy_policy: bool = False,
    has_contacts: bool = False,
    brand_mentions: int = 0,
    negative_reviews: int = 0,
    text: str = "",
) -> dict:
    trust = 0.0
    trust += 40.0 if has_https else 0.0
    trust += 30.0 if has_privacy_policy else 0.0
    trust += 20.0 if has_contacts else 0.0
    trust += min(10.0, brand_mentions * 2.0)
    trust -= min(40.0, negative_reviews * 8.0)
    trust = _clamp(trust)

    pos = len(re.findall(r"\b(рекоменду|отличн|качеств|надежн|помог)\w*\b", text.lower()))
    neg = len(re.findall(r"\b(плох|обман|ужас|претенз|мошен)\w*\b", text.lower()))
    sentiment = 50.0 + (pos - neg) * 8.0
    sentiment = _clamp(sentiment)

    return {
        "trust_score": round(trust, 2),
        "sentiment_score": round(sentiment, 2),
        "signals": {
            "has_https": has_https,
            "has_privacy_policy": has_privacy_policy,
            "has_contacts": has_contacts,
            "brand_mentions": brand_mentions,
            "negative_reviews": negative_reviews,
            "pos_hits": pos,
            "neg_hits": neg,
        },
    }