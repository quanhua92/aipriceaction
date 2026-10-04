"""Prepare temporary source-backed activation inputs from completed capture rehearsals."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from aipriceaction_api.app import create_app
from aipriceaction_api.archive import Archive
from aipriceaction_api.domain import DataError, cutoff, date_bounds
from aipriceaction_api.history import History
from aipriceaction_api.storage import Repository
from scripts.rehearse_captured_vci_replacement import daily_checks
from scripts.replay_vci_candidate_pages import replay_record
from scripts.vci_activation_inputs import values
from scripts.vn_daily_volume_evidence import captured


async def fresh_controls(providers, rows):
    if len(rows) < 2000:
        raise DataError("Complete captured source requires at least two thousand candles")
    by_time = {row.time: row for row in rows}
    controls = []
    for label, index in (("oldest", 999), ("middle", len(rows) // 2)):
        page = await providers.page(
            "vn",
            rows[0].symbol,
            "1m",
            rows[index].time + 60,
            count=1000,
            start=rows[0].time,
            provider="vci",
        )
        if (
            page.provider != "vci"
            or len(page.rows) != 1000
            or any(row.time not in by_time for row in page.rows)
        ):
            raise DataError("Fresh source control lacks the exact captured window")
        expected = [by_time[row.time] for row in page.rows]
        if values(page.rows) != values(expected) or [
            row.time for row in rows if page.rows[0].time <= row.time <= page.rows[-1].time
        ] != [row.time for row in page.rows]:
            raise DataError("Fresh source control differs from captured OHLCV or omits candles")
        controls.append(
            {
                "control": label,
                "rows": len(page.rows),
                "first": page.rows[0].time,
                "last": page.rows[-1].time,
            }
        )
    return controls


def http_controls(settings, symbol):
    """Check the existing JSON route against the verified candidate through raw/SMA/EMA."""
    floor = cutoff(settings.minute_years)
    start, end = (
        datetime.fromtimestamp(stamp, UTC).strftime("%Y-%m-%d")
        for stamp in (floor - 7 * 86400, floor + 7 * 86400)
    )
    repo = Repository(settings.database)
    reference = History(repo, Archive(repo, settings), settings)
    controls = []
    with TestClient(create_app(settings)) as client:
        for interval in ("1m", "15m"):
            for ma, ema in ((False, False), (True, False), (True, True)):
                limit = 3000 if interval == "1m" else 200
                response = client.get(
                    "/tickers",
                    params={
                        "symbol": symbol,
                        "mode": "vn",
                        "interval": interval,
                        "start_date": start,
                        "end_date": end,
                        "limit": limit,
                        "ma": str(ma).lower(),
                        "ema": str(ema).lower(),
                        "cache": "false",
                        "snap": "false",
                    },
                )
                expected = reference.query(
                    "vn",
                    symbol,
                    interval,
                    date_bounds(start),
                    date_bounds(end, end=True),
                    limit,
                    ma,
                    ema,
                )
                if (
                    response.status_code != 200
                    or not expected
                    or response.json() != {symbol: expected}
                ):
                    raise DataError("HTTP route differs from the verified candidate query")
                controls.append({"interval": interval, "ma": ma, "ema": ema, "rows": len(expected)})
    return controls


async def captured_inputs(args, settings, main, temporary, providers):
    raw = (args.captured_extension / "report.json").read_bytes()
    extension = json.loads(raw)
    rehearsal = json.loads((args.captured_rehearsal / "report.json").read_text())
    if (
        not extension["completed"]
        or extension["canonical_publication"]
        or extension["proofs_sha256"] != hashlib.sha256(args.volume_proofs.read_bytes()).hexdigest()
        or not rehearsal["passed"]
        or rehearsal["canonical_publication"]
        or not rehearsal["temporary_storage_removed"]
        or not rehearsal["temporary_s3_prefix_removed"]
        or rehearsal["extension_sha256"] != hashlib.sha256(raw).hexdigest()
    ):
        raise DataError(
            "Captured activation requires matching completed source and cleaned rehearsal evidence"
        )
    eligible = {row["symbol"]: row for row in extension["series"] if row["candidate_ready"]}
    rehearsed = {row["symbol"]: row for row in rehearsal["series"]}
    if (
        not args.symbol
        or len(args.symbol) != len(set(args.symbol))
        or not set(args.symbol) <= eligible.keys() & rehearsed.keys()
        or set(args.symbol) & {"VN30", "VNINDEX"}
    ):
        raise DataError("Choose unique explicitly rehearsed stock candidates")
    inputs, artifacts = [], {}
    for symbol in args.symbol:
        path = args.captured_extension / f"{symbol}-record.json"
        source_raw = path.read_bytes()
        if hashlib.sha256(source_raw).hexdigest() != rehearsed[symbol]["source_record_sha256"]:
            raise DataError("Captured candidate changed after completed rehearsal")
        artifacts[hashlib.sha256(source_raw).hexdigest()] = {"path": str(path)}
        record = json.loads(source_raw)
        replay = await replay_record(settings, symbol, record, retain_rows=True)
        rows = sorted(replay.pop("rows"), key=lambda row: row.time)
        if not replay["captured_pages_passed"] or len(rows) != eligible[symbol]["accepted_rows"]:
            raise DataError("Captured activation source replay no longer matches its checkpoint")
        for capture in record["captures"]:
            captured({"captures": [capture]}, artifacts)
        checks = await daily_checks(
            settings, args.daily, symbol, rows, main.read("vn", symbol, "1D")
        )
        for feed in ("vps", "vndirect", "dnse"):
            captured(json.loads((args.daily / feed / f"{symbol}-1D.json").read_text()), artifacts)
        checks["fresh_historical_controls"] = await fresh_controls(providers, rows)
        candidate_settings = replace(
            settings,
            database=temporary / f"{symbol}-candidate.sqlite3",
            cache_dir=temporary / f"{symbol}-cache",
        )
        candidate = Repository(candidate_settings.database)
        candidate.initialize()
        candidate.put([replace(row, revision="captured-vci-candidate") for row in rows])
        checks["http_controls"] = http_controls(candidate_settings, symbol)
        inputs.append((symbol, candidate, candidate.read("vn", symbol, "1m"), checks))
    return inputs, artifacts
