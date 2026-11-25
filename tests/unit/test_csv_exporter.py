import csv
import io

import pytest

from services.reporting_service.exporters.csv_exporter import export_raw_data, export_report_slice


def test_export_raw_data_csv_bytes():
    exported = export_raw_data({"a": 1, "b": "x"})
    assert list(csv.reader(io.StringIO(exported.decode("utf-8")))) == [["key", "value"], ["a", "1"], ["b", "x"]]


def test_export_report_slice_raw_data():
    b = export_report_slice({"raw": {"gsc": [{"clicks": 2, "query": "seo"}]}}, "raw_data")
    s = b.decode("utf-8")
    assert "section" in s
    assert "gsc" in s
    assert "seo" in s


@pytest.mark.parametrize("value", ["=метка", "+метка", "-метка", "@метка", " ＝метка", "\ufeff＠метка",
                                  "\tметка", "\rметка", "\nметка", "\x00метка", "'=метка"])
def test_export_report_quotes_formula_like_text_in_columns_and_values(value):
    snapshot = {"raw": {"gsc": [{value: value, "clicks": -2, "query": 'seo, "метка"; строка'}]}}
    exported = export_report_slice(snapshot, "raw_data")
    header, row = csv.reader(io.StringIO(exported.decode("utf-8"), newline=""))
    cells = dict(zip(header, row, strict=True))
    assert cells == {"'" + value: "'" + value, "clicks": "-2", "query": 'seo, "метка"; строка', "section": "gsc"}
    assert snapshot["raw"]["gsc"][0][value] == value


def test_csv_preserves_distinct_formula_like_columns():
    exported = export_raw_data([{"=метка": "first", "'=метка": "second"}])
    assert next(csv.DictReader(io.StringIO(exported.decode("utf-8")))) == {"'=метка": "first", "''=метка": "second"}
