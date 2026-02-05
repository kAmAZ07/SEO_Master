def check_meta(url: str | None, title: str | None, description: str | None, h1: str | None) -> list[dict]:
    findings = []
    u = url or ""

    if not title or not title.strip():
        findings.append({"code": "title_missing", "severity": "medium", "confidence": "high", "details": {"url": u}})
    else:
        tl = len(title.strip())
        if tl < 10:
            findings.append({"code": "title_too_short", "severity": "low", "confidence": "high", "details": {"url": u, "len": tl}})
        if tl > 70:
            findings.append({"code": "title_too_long", "severity": "low", "confidence": "high", "details": {"url": u, "len": tl}})

    if not description or not description.strip():
        findings.append({"code": "description_missing", "severity": "low", "confidence": "high", "details": {"url": u}})
    else:
        dl = len(description.strip())
        if dl < 50:
            findings.append({"code": "description_too_short", "severity": "low", "confidence": "high", "details": {"url": u, "len": dl}})
        if dl > 160:
            findings.append({"code": "description_too_long", "severity": "low", "confidence": "high", "details": {"url": u, "len": dl}})

    if not h1 or not h1.strip():
        findings.append({"code": "h1_missing", "severity": "medium", "confidence": "high", "details": {"url": u}})
    else:
        hl = len(h1.strip())
        if hl < 5:
            findings.append({"code": "h1_too_short", "severity": "low", "confidence": "high", "details": {"url": u, "len": hl}})

    return findings