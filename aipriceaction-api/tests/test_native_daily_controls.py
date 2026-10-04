import asyncio
import hashlib
import json
from argparse import Namespace
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlencode

import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, date_bounds
from aipriceaction_api.providers import Providers
from scripts.review_vn_historical_price_basis import verified_successful_daily_controls
from scripts.verify_native_daily_controls import run, verify_record


def control(root, feed="vps", symbol="FPT"):
    first = date_bounds("2026-09-28")
    before = date_bounds("2026-09-29", end=True) + 1
    payload = {
        "s": "ok",
        "symbol": symbol,
        "t": [first],
        "o": [10],
        "h": [11],
        "l": [9],
        "c": [10.5],
        "v": [12345],
    }
    raw = json.dumps(payload).encode()
    directory = root / feed
    directory.mkdir(exist_ok=True)
    path = directory / "capture.json"
    path.write_bytes(raw)
    host = {
        "vps": "https://histdatafeed.vps.com.vn/tradingview/history",
        "vndirect": "https://dchart-api.vndirect.com.vn/dchart/history",
        "dnse": "https://api.dnse.com.vn/chart-api/v2/ohlcs/"
        + ("index" if symbol == "VN30" else "stock"),
    }[feed]
    params = {
        "symbol": symbol,
        "resolution": "1D" if feed == "dnse" else "D",
        "from": before - 100 * 3 * 86400,
        "to": before - 1,
        "countback": 100,
    }
    factor = 1 if symbol == "VN30" else 1000
    candle = Candle(
        "vn",
        symbol,
        "1D",
        first,
        10.0 * factor,
        11.0 * factor,
        9.0 * factor,
        10.5 * factor,
        12345,
        feed,
    )
    row = asdict(candle)
    return {
        "start_date": "2026-09-28",
        "end_date": "2026-09-29",
        "captures": [
            {
                "path": str(path),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
                "status": 200,
                "url": host + "?" + urlencode(params),
            }
        ],
        "rows": [{key: row[key] for key in ("time", "open", "high", "low", "close", "volume")}],
    }


@pytest.mark.parametrize(
    "feed,symbol", [("vps", "FPT"), ("vndirect", "FPT"), ("dnse", "FPT"), ("dnse", "VN30")]
)
def test_native_stock_and_index_controls_replay(tmp_path, feed, symbol):
    record = control(tmp_path, feed, symbol)
    result = asyncio.run(
        verify_record(Settings(), tmp_path, symbol, feed, record, "2026-09-28", "2026-09-29")
    )
    assert result["replayed_rows"] == 1
    assert result["native_parser_replayed"]
    assert result["runtime_request_verified"]


@pytest.mark.parametrize(
    "mutation",
    [
        "bytes",
        "hash",
        "status",
        "symbol",
        "resolution",
        "endpoint",
        "window",
        "rows",
        "outside",
        "duplicate_query",
    ],
)
def test_mutated_native_control_refuses_verification(tmp_path, mutation):
    record = control(tmp_path)
    capture = record["captures"][0]
    if mutation == "bytes":
        capture["bytes"] += 1
    elif mutation == "hash":
        capture["sha256"] = "0" * 64
    elif mutation == "status":
        capture["status"] = 500
    elif mutation == "symbol":
        capture["url"] = capture["url"].replace("symbol=FPT", "symbol=SHS")
    elif mutation == "resolution":
        capture["url"] = capture["url"].replace("resolution=D", "resolution=1")
    elif mutation == "endpoint":
        capture["url"] = capture["url"].replace("histdatafeed.vps.com.vn", "example.com")
    elif mutation == "window":
        record["start_date"] = "2026-09-27"
    elif mutation == "rows":
        record["rows"][0]["volume"] += 1
    elif mutation == "outside":
        original = Path(capture["path"])
        outside = tmp_path / "outside.json"
        outside.write_bytes(original.read_bytes())
        capture["path"] = str(outside)
    else:
        capture["url"] += "&symbol=FPT"
    with pytest.raises(DataError):
        asyncio.run(
            verify_record(Settings(), tmp_path, "FPT", "vps", record, "2026-09-28", "2026-09-29")
        )


def test_audit_exposes_rejected_and_tampered_controls_without_copying_database(tmp_path):
    audit = tmp_path / "audit"
    audit.mkdir()
    report = {
        "feeds": ["vps", "vndirect", "dnse", "legacy"],
        "intervals": ["1D"],
        "canonical_publication": False,
        "symbols": ["FPT"],
        "requests": 4,
        "daily_start": "2026-09-28",
        "end_date": "2026-09-29",
    }
    (audit / "report.json").write_text(json.dumps(report))
    for feed in ("vps", "vndirect", "dnse"):
        record = control(audit, feed)
        if feed == "vndirect":
            record["rows"][0]["close"] += 1
        if feed == "dnse":
            del record["rows"]
            record["error"] = "original invalid OHLC"
        (audit / feed / "FPT-1D.json").write_text(json.dumps(record))
    output = tmp_path / "output"
    result = asyncio.run(run(Namespace(audit=audit, output=output)))
    assert result["completed"]
    assert not result["all_successful_controls_verified"]
    assert result["verified_controls"] == 1
    assert len(result["errors"]) == 1
    assert len(result["original_rejected_controls"]) == 1
    assert result["canonical_publication"] is False
    assert [path.name for path in output.iterdir()] == ["report.json"]


@pytest.mark.parametrize("failure", [False, True])
def test_provider_clients_close_on_replay_success_or_failure(tmp_path, monkeypatch, failure):
    record = control(tmp_path)
    if failure:
        record["rows"][0]["volume"] += 1
    closed = []
    original = Providers.close

    async def close(self):
        clients = list(self.clients.values())
        await original(self)
        closed.append(bool(clients) and all(client.is_closed for client in clients))

    monkeypatch.setattr(Providers, "close", close)
    task = verify_record(Settings(), tmp_path, "FPT", "vps", record, "2026-09-28", "2026-09-29")
    if failure:
        with pytest.raises(DataError, match="differs from native runtime replay"):
            asyncio.run(task)
    else:
        asyncio.run(task)
    assert closed == [True]


def test_coherence_helper_binds_successful_records_and_keeps_rejections(tmp_path):
    for feed in ("vps", "vndirect", "dnse"):
        record = control(tmp_path, feed)
        if feed == "dnse":
            del record["rows"]
            record["error"] = "invalid range"
        (tmp_path / feed / "FPT-1D.json").write_text(json.dumps(record))
    result = asyncio.run(
        verified_successful_daily_controls(
            Settings(), tmp_path, {"daily_start": "2026-09-28", "end_date": "2026-09-29"}, ["FPT"]
        )
    )
    assert set(result) == {("FPT", "vps"), ("FPT", "vndirect")}
    for (symbol, feed), identity in result.items():
        raw = (tmp_path / feed / f"{symbol}-1D.json").read_bytes()
        assert identity["original_record_sha256"] == hashlib.sha256(raw).hexdigest()
        assert identity["runtime_request_verified"]
