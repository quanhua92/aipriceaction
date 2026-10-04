import asyncio
import hashlib
import json

import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, date_bounds
from scripts.review_vn_historical_price_basis import verified_daily_subsets


@pytest.mark.parametrize(
    "failure", [None, "row", "rejected", "capture", "scope", "duplicate", "not_diagnostic"]
)
def test_daily_subset_replays_actual_native_values_and_keeps_malformed_dates(tmp_path, failure):
    daily, subsets = tmp_path / "daily", tmp_path / "subsets"
    (daily / "vps").mkdir(parents=True)
    subsets.mkdir()
    stamps = [date_bounds(f"2026-09-{day}") for day in (25, 28, 29)]
    body = {
        "symbol": "FPT",
        "s": "ok",
        "t": stamps,
        "o": [10, 12, 10],
        "h": [11, 11, 11],
        "l": [9, 9, 9],
        "c": [10, 10, 10],
        "v": [100, 100, 100],
    }
    raw = json.dumps(body).encode()
    capture = tmp_path / "capture.json"
    capture.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    (daily / "vps/FPT-1D.json").write_text(
        json.dumps(
            {
                "error": "Invalid OHLC range",
                "captures": [
                    {"path": str(capture), "bytes": len(raw), "sha256": sha, "status": 200}
                ],
            }
        )
    )
    rejected = [{"time": stamps[1], "reason": "Invalid OHLC range"}]
    valid = [
        {
            "time": t,
            "open": 10000.0,
            "high": 11000.0,
            "low": 9000.0,
            "close": 10000.0,
            "volume": 100,
        }
        for t in (stamps[0], stamps[2])
    ]
    saved = {
        "symbol": "FPT",
        "feed": "vps",
        "capture_sha256": sha,
        "rows": valid,
        "rejected": rejected,
        "diagnostic_only": True,
    }
    summary = {
        "diagnostic_only": True,
        "canonical_publication": False,
        "audit_directory": str(daily),
        "errors": [],
        "replays": [{"symbol": "FPT", "feed": "vps", "valid_rows": 2, "rejected": rejected}],
    }
    if failure == "row":
        saved["rows"][0]["close"] = 10001.0
    elif failure == "rejected":
        saved["rejected"] = []
    elif failure == "capture":
        capture.write_bytes(raw + b" ")
    elif failure == "scope":
        summary["audit_directory"] = str(tmp_path / "unrelated")
    elif failure == "duplicate":
        summary["replays"] *= 2
    elif failure == "not_diagnostic":
        saved["diagnostic_only"] = False
    (subsets / "FPT-vps.json").write_text(json.dumps(saved))
    (subsets / "report.json").write_text(json.dumps(summary))
    settings = Settings()
    report = {"daily_start": "2026-09-25", "end_date": "2026-09-29"}
    if failure:
        with pytest.raises(DataError):
            asyncio.run(verified_daily_subsets(settings, daily, subsets, report, {"FPT"}))
    else:
        rows, identities, digest = asyncio.run(
            verified_daily_subsets(settings, daily, subsets, report, {"FPT"})
        )
        assert rows[("FPT", "vps")] == valid
        assert stamps[1] not in {r["time"] for r in rows[("FPT", "vps")]}
        assert identities[("FPT", "vps")]["rejected"] == rejected
        assert identities[("FPT", "vps")]["native_parser_replayed"]
        assert digest == hashlib.sha256((subsets / "report.json").read_bytes()).hexdigest()
        assert capture.read_bytes() == raw
    assert not list(tmp_path.rglob("*.sqlite3*"))
