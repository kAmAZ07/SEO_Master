from services.reporting_service.exporters.csv_exporter import export_raw_data


def test_export_raw_data_csv_bytes():
    b = export_raw_data({"a": 1, "b": "x"})
    s = b.decode("utf-8")
    assert "key,value" in s
    assert "a,1" in s
    assert "b,x" in s