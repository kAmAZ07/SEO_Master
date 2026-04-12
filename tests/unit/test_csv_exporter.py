from services.reporting_service.exporters.csv_exporter import export_raw_data, export_report_slice


def test_export_raw_data_csv_bytes():
    b = export_raw_data({"a": 1, "b": "x"})
    s = b.decode("utf-8")
    assert "key,value" in s
    assert "a,1" in s
    assert "b,x" in s


def test_export_report_slice_raw_data():
    b = export_report_slice({"raw": {"gsc": [{"clicks": 2, "query": "seo"}]}}, "raw_data")
    s = b.decode("utf-8")
    assert "section" in s
    assert "gsc" in s
    assert "seo" in s
