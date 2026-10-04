import hashlib
import json
from argparse import Namespace
from dataclasses import replace

import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, date_bounds
from aipriceaction_api.storage import Repository
from scripts.vci_activation_inputs import reviewed_inputs
from scripts.vn_daily_volume_evidence import project_vndirect_volumes


@pytest.fixture
def setup(tmp_path):
    settings = replace(
        Settings(),
        database=tmp_path / "main.db",
        cache_dir=tmp_path / "cache",
        vci_history_fallback=True,
    )
    main = Repository(settings.database)
    main.initialize()
    days = [date_bounds("2025-10-06"), date_bounds("2025-10-07")]
    rows = [
        Candle("vn", "BSR", "1m", day + 8100, 100, 120, 90, 115, 30, "vci", "vci-candidate")
        for day in days
    ]
    main.put(
        [replace(r, interval="1D", time=d, provider="vps") for d, r in zip(days, rows, strict=True)]
    )
    root = tmp_path / "candidates" / "bsr"
    root.mkdir(parents=True)
    candidate = Repository(root / "candidate.sqlite3")
    candidate.initialize()
    candidate.put(rows)

    def capture(parent, name, body):
        raw = json.dumps(body).encode()
        path = parent / name
        path.write_bytes(raw)
        return {
            "path": str(path),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "status": 200,
        }

    times = [days[0] - 86400 + 8100] + [r.time for r in rows]
    body = [
        {
            "symbol": "BSR",
            "t": times,
            "o": [100] * 3,
            "h": [120] * 3,
            "l": [90] * 3,
            "c": [115] * 3,
            "v": [30] * 3,
            "accumulatedVolume": [30] * 3,
        }
    ]
    source = capture(root, "raw.json", body)
    stage = {
        "complete": True,
        "main_publication": False,
        "symbol": "BSR",
        "start_date": "2025-10-06",
        "end_date": "2025-10-07",
        "pages": [
            {"before": days[-1] + 86400, "cursor": times[0], "rows": 2, "captures": [source]}
        ],
    }
    (root / "report.json").write_text(json.dumps(stage))
    daily = tmp_path / "daily"
    for feed in ("vps", "vndirect", "dnse"):
        folder = daily / feed
        folder.mkdir(parents=True)
        body = {
            "t": days,
            "o": [0.1] * 2,
            "h": [0.12] * 2,
            "l": [0.09] * 2,
            "c": [0.115] * 2,
            "v": [30] * 2,
        }
        raw = capture(folder, "raw.json", body)
        record = {
            "start_date": "2025-10-06",
            "end_date": "2025-10-07",
            "captures": [raw],
            "rows": [
                {"time": d, "open": 100.0, "high": 120.0, "low": 90.0, "close": 115.0, "volume": 30}
                for d in days
            ],
        }
        (folder / "BSR-1D.json").write_text(json.dumps(record))
    review = tmp_path / "review"
    review.mkdir()
    (review / "report.json").write_text(
        json.dumps(
            {"main_publication": False, "series": [{"symbol": "BSR", "review_passed": True}]}
        )
    )
    args = Namespace(review=review, candidates=root.parent, daily=daily, symbol=["BSR"])
    return args, settings, main, candidate


@pytest.mark.asyncio
async def test_complete_replay_rechecks_source_values_and_native_daily_peers(setup):
    args, settings, main, candidate = setup
    before = candidate.read("vn", "BSR", "1m")
    inputs, artifacts = await reviewed_inputs(args, settings, main)
    assert inputs[0][3]["source_replayed_rows"] == 2
    assert inputs[0][3]["observed_dates"] == 2
    assert all(len(day["feeds"]) == 3 for day in inputs[0][3]["native_volume_witnesses"])
    assert artifacts
    assert candidate.read("vn", "BSR", "1m") == before


@pytest.mark.asyncio
async def test_stale_review_cannot_license_edited_candidate(setup):
    args, settings, main, candidate = setup
    with candidate.connect() as con:
        con.execute("UPDATE candles SET close=116 WHERE interval='1m'")
    with pytest.raises(DataError, match="captured source traversal"):
        await reviewed_inputs(args, settings, main)


@pytest.mark.asyncio
async def test_parsed_daily_witness_edit_cannot_override_immutable_capture(setup):
    args, settings, main, _ = setup
    path = args.daily / "dnse" / "BSR-1D.json"
    record = json.loads(path.read_text())
    record["rows"][0]["volume"] = 31
    path.write_text(json.dumps(record))
    with pytest.raises(DataError, match="daily witnesses differ"):
        await reviewed_inputs(args, settings, main)


@pytest.mark.asyncio
async def test_changed_capture_bytes_are_rejected_even_if_values_match(setup):
    args, settings, main, _ = setup
    path = args.candidates / "bsr" / "raw.json"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(DataError, match="bytes changed"):
        await reviewed_inputs(args, settings, main)


@pytest.mark.asyncio
async def test_unreviewed_selection_is_rejected_before_capture_replay(setup):
    args, settings, main, _ = setup
    args.symbol = ["GEE"]
    with pytest.raises(DataError, match="Only reviewed stock"):
        await reviewed_inputs(args, settings, main)


@pytest.mark.asyncio
async def test_fresh_native_disagreement_blocks_stale_passed_review(setup):
    args, settings, main, _ = setup
    for feed in ("vndirect", "dnse"):
        folder = args.daily / feed
        raw = json.loads((folder / "raw.json").read_text())
        raw["v"] = [31, 31]
        data = json.dumps(raw).encode()
        (folder / "raw.json").write_bytes(data)
        path = folder / "BSR-1D.json"
        record = json.loads(path.read_text())
        record["captures"][0].update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
        for row in record["rows"]:
            row["volume"] = 31
        path.write_text(json.dumps(record))
    with pytest.raises(DataError, match="two exact native volume witnesses"):
        await reviewed_inputs(args, settings, main)


def scoped_record(args):
    folder = args.daily / "vndirect"
    raw = json.loads((folder / "raw.json").read_text())
    raw["s"] = "ok"
    raw["h"][0] = 0.11
    data = json.dumps(raw).encode()
    (folder / "raw.json").write_bytes(data)
    path = folder / "BSR-1D.json"
    record = json.loads(path.read_text())
    record["captures"][0].update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
    record.pop("rows")
    end = date_bounds("2025-10-08")
    record["captures"][0]["url"] = (
        f"https://dchart-api.vndirect.com.vn/dchart/history?symbol=BSR&resolution=D&from={end - 300 * 86400}&to={end - 1}&countback=100"
    )
    record["error"] = "VN providers unavailable: Invalid OHLC range"
    record["volume_only_evidence"] = project_vndirect_volumes(
        data, "BSR", date_bounds("2025-10-06"), date_bounds("2025-10-08"), 100
    )
    path.write_text(json.dumps(record))
    return path, record


@pytest.mark.asyncio
async def test_field_scoped_witness_is_replayed_and_never_supplies_prices(setup):
    args, settings, main, candidate = setup
    scoped_record(args)
    before = main.read("vn", "BSR", "1D")
    inputs, artifacts = await reviewed_inputs(args, settings, main)
    checks = inputs[0][3]
    assert all(len(d["feeds"]) == 3 for d in checks["native_volume_witnesses"])
    assert checks["field_scoped_witnesses"][0]["scope"] == "volume_only"
    assert checks["field_scoped_witnesses"][0]["ohlc_rejections"]
    assert artifacts
    assert main.read("vn", "BSR", "1D") == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    (
        "unscoped",
        "edited",
        "capture",
        "hidden_error",
        "invented_prices",
        "wrong_source",
        "wrong_symbol",
        "wrong_window",
    ),
)
async def test_volume_only_activation_rejects_missing_or_tampered_evidence(setup, defect):
    args, settings, main, _ = setup
    path, record = scoped_record(args)
    if defect == "unscoped":
        record.pop("volume_only_evidence")
    elif defect == "edited":
        record["volume_only_evidence"]["rows"][0]["volume"] += 1
    elif defect == "capture":
        (args.daily / "vndirect" / "raw.json").write_bytes(b"{}")
    elif defect == "hidden_error":
        record.pop("error")
    elif defect == "invented_prices":
        record["rows"] = [{"time": date_bounds("2025-10-06"), "close": 115}]
    elif defect == "wrong_source":
        record["captures"][0]["url"] = record["captures"][0]["url"].replace(
            "dchart-api.vndirect.com.vn", "example.com"
        )
    elif defect == "wrong_symbol":
        record["captures"][0]["url"] = record["captures"][0]["url"].replace(
            "symbol=BSR", "symbol=GEE"
        )
    elif defect == "wrong_window":
        record["captures"][0]["url"] = record["captures"][0]["url"].replace(
            "countback=100", "countback=99"
        )
    path.write_text(json.dumps(record))
    with pytest.raises(DataError):
        await reviewed_inputs(args, settings, main)
