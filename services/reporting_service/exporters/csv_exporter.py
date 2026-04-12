import csv
import io
import json
from typing import Any


def _is_record_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)


def _flatten_pairs(value, prefix: str = "") -> list[dict]:
    if isinstance(value, dict):
        rows: list[dict] = []
        for key, nested in value.items():
            nested_prefix = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(_flatten_pairs(nested, nested_prefix))
        return rows

    if isinstance(value, list):
        rows: list[dict] = []
        for index, item in enumerate(value):
            nested_prefix = f"{prefix}[{index}]" if prefix else f"[{index}]"
            rows.extend(_flatten_pairs(item, nested_prefix))
        return rows

    return [{"key": prefix or "value", "value": value}]


def export_raw_data(data) -> bytes:
    rows: list[dict] = []

    if _is_record_list(data):
        rows = list(data)
    elif isinstance(data, dict):
        if data and all(not isinstance(item, (dict, list)) for item in data.values()):
            rows = [{"key": key, "value": value} for key, value in data.items()]
        else:
            preferred_sections = ("rows", "items", "records", "data")
            preferred_dataset = next((data[key] for key in preferred_sections if _is_record_list(data.get(key))), None)
            if preferred_dataset is not None:
                rows = list(preferred_dataset)
            else:
                tabular_rows: list[dict] = []
                flattened_rows: list[dict] = []
                for section, value in data.items():
                    if _is_record_list(value):
                        for item in value:
                            tabular_rows.append({"section": section, **item})
                    elif isinstance(value, dict) and all(not isinstance(item, (dict, list)) for item in value.values()):
                        tabular_rows.append({"section": section, **value})
                    else:
                        for row in _flatten_pairs(value, section):
                            flattened_rows.append({"section": section, **row})

                rows = tabular_rows or flattened_rows
    else:
        rows = [{"value": data}]

    if not rows:
        rows = [{"value": ""}]

    buf = io.StringIO()
    fieldnames = sorted({key for row in rows for key in row.keys()})
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                for key, value in row.items()
            }
        )
    return buf.getvalue().encode("utf-8")


def _flatten_section(section: str, value: Any) -> list[dict]:
    if _is_record_list(value):
        return [{"section": section, **item} for item in value]
    if isinstance(value, dict):
        return [{"section": section, **row} for row in _flatten_pairs(value)]
    return [{"section": section, "value": value}]


def export_report_slice(report_data: dict, slice_name: str) -> bytes:
    if slice_name == "raw_data":
        raw = report_data.get("raw", {}) if isinstance(report_data, dict) else {}
        rows: list[dict] = []
        if isinstance(raw, dict):
            for source, records in raw.items():
                rows.extend(_flatten_section(str(source), records))
        return export_raw_data(rows)

    if slice_name == "aggregates":
        return export_raw_data(report_data.get("aggregates", {}) if isinstance(report_data, dict) else {})

    if slice_name == "changelog":
        changelog = report_data.get("changelog", {}) if isinstance(report_data, dict) else {}
        events = changelog.get("events") if isinstance(changelog, dict) else None
        if _is_record_list(events):
            return export_raw_data(events)
        return export_raw_data(changelog)

    if slice_name == "report_snapshot":
        return export_raw_data(report_data.get("report_snapshot", {}) if isinstance(report_data, dict) else {})

    return export_raw_data(report_data)
