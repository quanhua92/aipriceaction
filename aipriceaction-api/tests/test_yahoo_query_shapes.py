import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from aipriceaction_api.domain import Candle, date_bounds


@pytest.mark.parametrize(
    "interval,step,wire,legacy_range", [("1m", 60, "1m", "1d"), ("1h", 3600, "60m", "5d")]
)
async def test_query_shapes_preserve_raw_nulls_labels_and_out_of_range_quotes(
    tmp_path, monkeypatch, interval, step, wire, legacy_range
):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    spec = importlib.util.spec_from_file_location(
        "query_shapes", root / "scripts/check_yahoo_minute_query_shapes.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    first = date_bounds("2020-01-06")
    stamps = [first + 13 * 3600 + 1800, first + 86400 + 13 * 3600 + 1800]
    saved = [
        Candle("yahoo", "NVDA", interval, t // step * step, 100, 101, 99, 100, 10) for t in stamps
    ]
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": stamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": [100 + 1e-10, None],
                                "high": [101, 101],
                                "low": [99, 99],
                                "close": [100, 100],
                                "volume": [10, 10],
                            }
                        ]
                    },
                }
            ]
        }
    }
    requests = []

    class Repo:
        def __init__(self, path):
            pass

        def read(self, source, symbol, iv):
            assert (source, symbol, iv) == ("yahoo", "NVDA", interval)
            return saved.copy()

        def epoch(self):
            return 42

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, params):
            requests.append(params.copy())
            return httpx.Response(
                200, json=payload, request=httpx.Request("GET", url, params=params)
            )

    monkeypatch.setattr(module, "Repository", Repo)
    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    monkeypatch.setattr(
        module.Settings, "from_env", lambda: SimpleNamespace(database=tmp_path / "unused.sqlite3")
    )
    output = tmp_path / "capture"
    await module.run(
        SimpleNamespace(symbol="NVDA", date="2020-01-06", interval=interval, output=output)
    )
    report = json.loads((output / "report.json").read_text())
    assert [q["interval"] for q in requests] == [interval, interval, wire]
    assert requests[0]["range"] == legacy_range
    assert report["canonical_epoch_before"] == report["canonical_epoch_after"] == 42
    assert report["retained_data_unchanged"] is True
    assert report["native_handoff"] is False
    dated = report["profiles"][1]
    assert dated["outside_requested_bounds"] == [stamps[1]]
    raw = json.loads(Path(dated["capture"]["path"]).read_text())
    assert raw == payload  # Raw nulls survive; only diagnostic replay supplies defaults.
    normalized = json.loads(Path(dated["normalized"]["path"]).read_text())
    assert normalized[1]["open"] == 0
    assert normalized[0]["time"] == stamps[0] // step * step
    assert len(dated["changed_from_public"]) == 2
    assert dated["material_changed_from_public"] == [
        {
            "time": stamps[1] // step * step,
            "fields": {"open": [100, 0]},
        }
    ]
