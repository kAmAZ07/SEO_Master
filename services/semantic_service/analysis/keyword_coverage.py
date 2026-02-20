import re


def keyword_coverage(text: str, keywords: list[str]) -> dict:
    t = text.lower()
    present = []
    missing = []
    for k in keywords:
        kk = (k or "").strip().lower()
        if not kk:
            continue
        if re.search(r"\b" + re.escape(kk) + r"\b", t):
            present.append(k)
        else:
            missing.append(k)
    total = len(present) + len(missing)
    cov = 0.0 if total == 0 else (len(present) / total) * 100.0
    return {"coverage": round(cov, 2), "present": present, "missing": missing, "total": total}