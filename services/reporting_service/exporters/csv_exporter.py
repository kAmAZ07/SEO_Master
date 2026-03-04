import io
import pandas as pd


def export_raw_data(data: dict) -> bytes:
    if not isinstance(data, dict):
        data = {"value": data}

    rows = []
    for k, v in data.items():
        if isinstance(v, (dict, list)):
            rows.append({"key": k, "value": str(v)})
        else:
            rows.append({"key": k, "value": v})

    df = pd.DataFrame(rows, columns=["key", "value"])
    buf = io.StringIO()
    df.to_csv(buf, index=False, encoding="utf-8")
    return buf.getvalue().encode("utf-8")