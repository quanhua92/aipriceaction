import hashlib
import json
from argparse import Namespace
from dataclasses import replace
from pathlib import Path

import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, date_bounds
from aipriceaction_api.storage import Repository
from scripts import review_hourly_gap_candidates as review


def captured_legacy(root):
    day = "2024-04-17"
    row = {
        "symbol": "FPT",
        "time": "2024-04-17T02:00:00Z",
        "open": 10.0,
        "high": 11.0,
        "low": 9.0,
        "close": 10.5,
        "volume": 100,
    }
    raw = json.dumps({"FPT": [row]}).encode()
    digest = hashlib.sha256(raw).hexdigest()
    path = root / ("FPT-1h-" + digest + ".response")
    path.write_bytes(raw)
    normalized = {"time": date_bounds(day) + 7200, **{k: row[k] for k in review.FIELDS}}
    record = {
        "start_date": day,
        "end_date": day,
        "capture": str(path),
        "http_status": 200,
        "rows": [normalized],
    }
    record_path = root / "FPT-1h.json"
    record_path.write_text(json.dumps(record))
    return record_path, record, row


def test_candidate_replays_exact_raw_values_without_hour_rounding(tmp_path):
    path, record, original = captured_legacy(tmp_path)
    raw_path = Path(record["capture"])
    body = {"FPT": [original | {"time": "2024-04-17T07:45:00Z"}]}
    raw = json.dumps(body).encode()
    new_path = tmp_path / ("FPT-1h-" + hashlib.sha256(raw).hexdigest() + ".response")
    new_path.write_bytes(raw)
    record["capture"] = str(new_path)
    record["rows"][0]["time"] = date_bounds("2024-04-17") + 7 * 3600 + 45 * 60
    path.write_text(json.dumps(record))
    candles, identity = review.legacy_candidate(tmp_path, path, "FPT", "2024-04-17")
    assert candles[0].time == record["rows"][0]["time"]
    assert candles[0].provider == "legacy-api"
    assert identity["raw_legacy_replayed"]
    assert identity["raw_capture_sha256"] == hashlib.sha256(raw).hexdigest()
    assert raw_path.exists()


@pytest.mark.parametrize(
    "mutation",
    ["bytes", "row", "symbol", "window", "status", "outside", "duplicate", "unordered", "bad_ohlc"],
)
def test_changed_or_unbound_legacy_candidates_refused(tmp_path, mutation):
    path, record, row = captured_legacy(tmp_path)
    capture = Path(record["capture"])
    if mutation == "bytes":
        capture.write_bytes(capture.read_bytes() + b" ")
    elif mutation == "row":
        record["rows"][0]["volume"] += 1
    elif mutation == "window":
        record["start_date"] = "2024-04-16"
    elif mutation == "status":
        record["http_status"] = 500
    elif mutation == "outside":
        record["capture"] = str(tmp_path.parent / "outside.response")
    else:
        if mutation == "symbol":
            rows = [row | {"symbol": "SHS"}]
        elif mutation == "duplicate":
            rows = [row, row]
        elif mutation == "unordered":
            rows = [row | {"time": "2024-04-17T03:00:00Z"}, row]
        else:
            rows = [row | {"close": 12.0}]
        raw = json.dumps({"FPT": rows}).encode()
        new_path = tmp_path / ("FPT-1h-" + hashlib.sha256(raw).hexdigest() + ".response")
        new_path.write_bytes(raw)
        record["capture"] = str(new_path)
        if mutation == "bad_ohlc":
            record["rows"][0]["close"] = 12.0
    path.write_text(json.dumps(record))
    with pytest.raises(DataError):
        review.legacy_candidate(tmp_path, path, "FPT", "2024-04-17")


@pytest.mark.parametrize("failure", [False, True])
def test_small_fixture_round_trip_cleanup_on_success_and_failure(monkeypatch, failure):
    paths = []
    original = review.tempfile.TemporaryDirectory

    def temporary(*args, **kwargs):
        result = original(*args, **kwargs)
        paths.append(Path(result.name))
        return result

    monkeypatch.setattr(review.tempfile, "TemporaryDirectory", temporary)
    if failure:

        def fail(*args):
            raise DataError("Injected Parquet failure")

        monkeypatch.setattr(review.Archive, "_write", fail)
    candles = [
        Candle(
            "vn",
            "FPT",
            "1h",
            date_bounds("2024-04-17") + 7200,
            10.0,
            11.0,
            9.0,
            10.5,
            100,
            "legacy-api",
            "candidate",
            123,
        )
    ]
    if failure:
        with pytest.raises(DataError, match="Injected Parquet failure"):
            review.round_trip(candles)
    else:
        review.round_trip(candles)
    assert paths and all(not path.exists() for path in paths)


@pytest.mark.parametrize("state", ["candidate", "existing_hour", "legacy_unavailable"])
def test_gap_review_keeps_live_rows_and_unavailable_dates_unchanged(tmp_path, monkeypatch, state):
    settings = replace(Settings(), database=tmp_path / "live.sqlite3")
    repo = Repository(settings.database)
    repo.initialize()
    stamp = date_bounds("2024-04-17")
    daily = Candle("vn", "FPT", "1D", stamp, 10.0, 11.0, 9.0, 10.5, 101)
    if state == "legacy_unavailable":
        daily = replace(daily, open=10.0, high=10.0, low=10.0, close=10.0, volume=0)
    repo.put([daily])
    if state == "existing_hour":
        repo.put([replace(daily, interval="1h", time=stamp + 7200)])
    original_rows = repo.read("vn", "FPT", "1D") + repo.read("vn", "FPT", "1h")
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    audit = tmp_path / "audit"
    day = audit / "2024-04-17"
    legacy = day / "legacy"
    legacy.mkdir(parents=True)
    path, record, _ = captured_legacy(legacy)
    if state == "legacy_unavailable":
        del record["rows"]
        record["error"] = "Unexpected legacy response envelope"
        path.write_text(json.dumps(record))
    report = {
        "intraday_start": "2024-04-17",
        "end_date": "2024-04-17",
        "intervals": ["1h"],
        "feeds": list(review.FEEDS),
        "canonical_publication": False,
        "requests": 4,
        "symbols": ["FPT"],
    }
    (day / "report.json").write_text(json.dumps(report))
    for feed in review.FEEDS[:3]:
        directory = day / feed
        directory.mkdir()
        raw = b'{"s":"no_data"}'
        capture = directory / "capture.json"
        capture.write_bytes(raw)
        (directory / "FPT-1h.json").write_text(
            json.dumps(
                {
                    "start_date": "2024-04-17",
                    "end_date": "2024-04-17",
                    "rows": [],
                    "captures": [
                        {
                            "path": str(capture),
                            "sha256": hashlib.sha256(raw).hexdigest(),
                            "bytes": len(raw),
                            "status": 200,
                        }
                    ],
                }
            )
        )
    output = tmp_path / "output"
    if state == "existing_hour":
        with pytest.raises(DataError, match="already contains live"):
            review.run(Namespace(audit=audit, output=output))
        assert not output.exists()
    else:
        result = review.run(Namespace(audit=audit, output=output))
        assert result["recovered_hourly_rows"] == (1 if state == "candidate" else 0)
        assert result["canonical_publication"] is False
        if state == "candidate":
            assert result["controls"][0]["volume_difference"] == -1
            assert not result["controls"][0]["publication_license"]
        else:
            assert len(result["unavailable"]) == 1
            assert not result["unavailable"][0]["candidate_preserved"]
        assert not list(output.rglob("*.sqlite3*"))
        assert not list(output.rglob("*.parquet"))
    assert repo.read("vn", "FPT", "1D") + repo.read("vn", "FPT", "1h") == original_rows
