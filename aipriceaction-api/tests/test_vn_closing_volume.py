import gzip
import json
from dataclasses import replace
from pathlib import Path

import pytest

from aipriceaction_api.domain import Candle, DataError
from scripts.diagnose_vn_closing_volume import locate_residual


def cases():
    return json.loads(
        gzip.decompress(
            (Path(__file__).parent / "fixtures/mwg_closing_volume_controls.json.gz").read_bytes()
        )
    )


@pytest.mark.parametrize("index,expected", ((0, 2600), (1, 600)))
def test_actual_native_closing_residual_is_located_without_licensing_a_minute(index, expected):
    case = cases()[index]
    minutes, hourly, daily = (
        [Candle(**r) for r in case[key]] for key in ("minutes", "hourly", "daily")
    )
    before = list(minutes)
    result = locate_residual(minutes, hourly, daily)
    assert len(result["buckets"]) == 5 and len(result["residual_buckets"]) == 1
    assert result["residual_buckets"][0]["difference"] == expected
    assert result["residual_buckets"][0]["time"] == max(r.time for r in hourly)
    assert result["native_hourly_total"] - result["minute_total"] == expected
    assert not result["minute_attribution_verified"] and not result["publication_licensed"]
    assert minutes == before


@pytest.mark.parametrize(
    "defect",
    (
        "duplicate_minute",
        "missing_hour",
        "duplicate_hour",
        "wrong_hour_provider",
        "wrong_day",
        "daily_total",
        "daily_provider",
        "invalid_daily",
    ),
)
def test_residual_diagnosis_rejects_incomplete_or_incompatible_controls(defect):
    case = cases()[0]
    minutes, hourly, daily = (
        [Candle(**r) for r in case[key]] for key in ("minutes", "hourly", "daily")
    )
    if defect == "duplicate_minute":
        minutes.append(minutes[0])
    elif defect == "missing_hour":
        hourly.pop()
    elif defect == "duplicate_hour":
        hourly.append(hourly[0])
    elif defect == "wrong_hour_provider":
        hourly[0] = replace(hourly[0], provider="legacy-api")
    elif defect == "wrong_day":
        minutes[0] = replace(minutes[0], time=minutes[0].time - 86400)
    elif defect == "daily_total":
        daily[0] = replace(daily[0], volume=daily[0].volume + 100)
    elif defect == "daily_provider":
        daily[0] = replace(daily[0], provider="dnse")
    elif defect == "invalid_daily":
        daily[0] = replace(daily[0], close=daily[0].high + 1000)
    with pytest.raises(DataError):
        locate_residual(minutes, hourly, daily)
