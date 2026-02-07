from services.audit_service.analyzers.meta_checker import check_meta


def test_meta_checker_missing_fields():
    findings = check_meta("https://example.com/", None, None, None)
    codes = {f["code"] for f in findings}
    assert "title_missing" in codes
    assert "description_missing" in codes
    assert "h1_missing" in codes