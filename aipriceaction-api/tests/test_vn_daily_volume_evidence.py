import gzip
import json
from copy import deepcopy
from pathlib import Path

import pytest

from aipriceaction_api.domain import DataError, date_bounds
from scripts.vn_daily_volume_evidence import project_vndirect_volumes


def actual():
    return gzip.decompress(
        (Path(__file__).parent / "fixtures/vndirect_gee_invalid_daily.json.gz").read_bytes()
    )


def project(raw):
    return project_vndirect_volumes(
        raw, "GEE", date_bounds("2025-09-03"), date_bounds("2026-10-02", True) + 1, 396
    )


def test_actual_rejected_ohlc_retains_independently_valid_volume_fields():
    raw = actual()
    before = json.loads(raw)
    evidence = project(raw)
    assert len(evidence["rows"]) == 270
    assert evidence["ohlc_rejections"] == [{"time": 1773792000, "error": "Invalid OHLC range"}]
    assert next(r for r in evidence["rows"] if r["time"] == 1773792000) == {
        "time": 1773792000,
        "volume": 2025300,
    }
    assert all(set(r) == {"time", "volume"} for r in evidence["rows"])
    assert json.loads(raw) == before
    index = before["t"].index(1773792000)
    assert before["c"][index] > before["h"][index]


@pytest.mark.parametrize(
    "defect",
    (
        "status",
        "arrays",
        "fractional_volume",
        "negative_volume",
        "bool_volume",
        "fractional_time",
        "bool_time",
        "unknown_offset",
        "duplicate",
        "invalid_price",
        "nan_price",
        "no_ohlc_rejection",
    ),
)
def test_volume_projection_rejects_unverified_protocol_and_numeric_values(defect):
    body = deepcopy(json.loads(actual()))
    i = body["t"].index(1773792000)
    if defect == "status":
        body["s"] = "no_data"
    elif defect == "arrays":
        body["o"].pop()
    elif defect == "fractional_volume":
        body["v"][i] = 2025300.5
    elif defect == "negative_volume":
        body["v"][i] = -1
    elif defect == "bool_volume":
        body["v"][i] = True
    elif defect == "fractional_time":
        body["t"][i] += 0.5
    elif defect == "bool_time":
        body["t"][i] = True
    elif defect == "unknown_offset":
        body["t"][i] += 7200
    elif defect == "duplicate":
        body["t"][i] = body["t"][i - 1]
    elif defect == "invalid_price":
        body["c"][i] = -1
    elif defect == "nan_price":
        body["c"][i] = float("nan")
    elif defect == "no_ohlc_rejection":
        body["h"][i] = body["c"][i]
    with pytest.raises(DataError):
        project(json.dumps(body).encode())
