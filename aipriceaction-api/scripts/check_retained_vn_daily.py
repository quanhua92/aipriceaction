"""Read-only retained VN daily comparison against captured legacy API data.

Date parity is evidence of observed coverage, not an exchange calendar or proof
that providers use identical corporate-action and volume conventions.
"""

import argparse
import hashlib
import json
import math
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import httpx

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, completed_vn_sessions, cutoff, parse_time

FIELDS = ("open", "high", "low", "close", "volume")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def read_snapshot(path):
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN")
        daily = [
            dict(row)
            for row in con.execute(
                "SELECT * FROM candles WHERE source='vn' AND interval='1D' ORDER BY symbol,time"
            )
        ]
        metadata = {
            table: [dict(row) for row in con.execute(f"SELECT * FROM {table} ORDER BY {order}")]
            for table, order in (
                ("meta", "key"),
                ("tickers", "source,symbol"),
                ("series", "source,symbol,interval"),
                ("source_checks", "source,symbol,interval"),
                ("quality", "id"),
                ("jobs", "id"),
                ("archives", "id"),
                ("legacy_imports", "id"),
                ("snapshot_adoptions", "source,symbol,interval,revision"),
            )
        }
        counts = {table: len(rows) for table, rows in metadata.items()}
        counts["candles"] = con.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
    return (
        daily,
        metadata,
        {
            "counts": counts,
            "all_local_vn_daily_rows": len(daily),
            "all_local_vn_daily_sha256": digest(json.dumps(daily, sort_keys=True).encode()),
            "operational_metadata_sha256": digest(json.dumps(metadata, sort_keys=True).encode()),
        },
    )


def reference(client, symbol, start, end, captures):
    params = {
        "symbol": symbol,
        "interval": "1D",
        "start_date": start,
        "end_date": end,
        "limit": 10000,
        "mode": "vn",
        "ma": "false",
        "cache": "false",
    }
    request = client.build_request("GET", "/tickers", params=params)
    identity = digest(str(request.url).encode())
    receipt_path = captures / f"{symbol}-{identity}.receipt.json"
    reused = receipt_path.exists()
    if reused:
        receipt = json.loads(receipt_path.read_text())
        raw = (captures / receipt["file"]).read_bytes()
        if receipt["url"] != str(request.url) or digest(raw) != receipt["sha256"]:
            raise ValueError("Legacy capture receipt/checksum mismatch")
    else:
        response = client.send(request)
        response.raise_for_status()
        raw = response.content
        checksum = digest(raw)
        file = f"{symbol}-{checksum}.json"
        target = captures / file
        if target.exists():
            if target.read_bytes() != raw:
                raise ValueError("Legacy capture checksum collision")
        else:
            target.write_bytes(raw)
        receipt = {
            "url": str(request.url),
            "captured_at": datetime.now(UTC).isoformat(),
            "status": response.status_code,
            "bytes": len(raw),
            "sha256": checksum,
            "file": file,
        }
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or set(payload) != {symbol}:
        raise ValueError("Legacy capture lacks the exact requested symbol")
    rows = payload[symbol]
    if not isinstance(rows, list) or not rows or len(rows) >= params["limit"]:
        raise ValueError("Legacy capture is invalid or may be truncated")
    return rows, receipt | {"reused": reused}


def compare(symbol, local, legacy, lower, upper):
    invalid, duplicate_dates, outside = [], [], []
    observed, valid = {}, {}
    for raw in legacy:
        if not isinstance(raw, dict) or raw.get("symbol", symbol) != symbol:
            raise ValueError("Legacy row contains another symbol")
        stamp = parse_time(raw["time"]) // 86400 * 86400
        day = datetime.fromtimestamp(stamp, UTC).date().isoformat()
        if not lower <= stamp < upper:
            outside.append(day)
            continue
        if stamp in observed:
            duplicate_dates.append(day)
        observed[stamp] = raw
        try:
            values = [raw[field] for field in FIELDS]
            if any(
                isinstance(value, bool) or not isinstance(value, (int, float)) for value in values
            ):
                raise ValueError("Non-numeric legacy OHLCV")
            if not math.isfinite(values[-1]) or int(values[-1]) != values[-1]:
                raise ValueError("Non-integer legacy volume")
            valid[stamp] = Candle("vn", symbol, "1D", stamp, *values).validate()
        except (DataError, KeyError, ValueError, OverflowError) as exc:
            invalid.append({"date": day, "reason": str(exc), "raw": raw})
    # Never choose among conflicting same-day rows for numerical comparison.
    if duplicate_dates:
        duplicated = {parse_time(day) for day in duplicate_dates}
        valid = {stamp: row for stamp, row in valid.items() if stamp not in duplicated}
    actual = {row.time: row.validate() for row in local if lower <= row.time < upper}
    shared = sorted(actual.keys() & valid.keys())
    exact, changed_price, changed_volume, material_volume, zero_volume = 0, 0, 0, 0, 0
    material_price = 0
    max_price = 0
    for stamp in shared:
        old, new = valid[stamp], actual[stamp]
        exact += all(getattr(old, field) == getattr(new, field) for field in FIELDS)
        price_delta = max(
            abs(getattr(new, field) / getattr(old, field) - 1) for field in FIELDS[:4]
        )
        max_price = max(max_price, price_delta)
        changed_price += price_delta > 1e-7
        material_price += price_delta > 0.01
        changed_volume += old.volume != new.volume
        if old.volume:
            material_volume += abs(new.volume / old.volume - 1) > 0.01
        elif new.volume:
            zero_volume += 1

    def dates(stamps):
        return [datetime.fromtimestamp(stamp, UTC).date().isoformat() for stamp in sorted(stamps)]

    return {
        "symbol": symbol,
        "local_rows": len(actual),
        "legacy_returned_rows": len(legacy),
        "legacy_observed_dates": len(observed),
        "shared_dates": len(actual.keys() & observed.keys()),
        "missing_in_replacement": dates(observed.keys() - actual.keys()),
        "extra_in_replacement": dates(actual.keys() - observed.keys()),
        "duplicate_legacy_dates": duplicate_dates,
        "out_of_scope_legacy_dates": outside,
        "invalid_legacy_rows": invalid,
        "numerically_compared_rows": len(shared),
        "exact_ohlcv_rows": exact,
        "changed_price_rows_relative_gt_1e_7": changed_price,
        "price_difference_gt_1_percent_rows": material_price,
        "max_relative_price_difference_from_legacy": max_price,
        "changed_volume_rows": changed_volume,
        "volume_difference_gt_1_percent_rows": material_volume,
        "zero_legacy_volume_changed_rows": zero_volume,
    }


def main(args):
    settings = Settings.from_env()
    now = datetime.fromtimestamp(parse_time(args.as_of), UTC) if args.as_of else datetime.now(UTC)
    floor, completed = cutoff(settings.daily_years, now), completed_vn_sessions(now)
    daily, metadata, before = read_snapshot(settings.database.resolve())
    enabled = {
        row["symbol"] for row in metadata["tickers"] if row["source"] == "vn" and row["enabled"]
    }
    entries = {
        entry if isinstance(entry, str) else entry["symbol"]: {}
        if isinstance(entry, str)
        else entry
        for entry in json.loads(settings.watchlist.read_text())["vn"]
    }
    requested = set(args.symbol or enabled)
    if not requested or requested - enabled or requested - entries.keys():
        raise ValueError("Choose active tickers from the configured VN watchlist")
    captures = args.captures or args.report.with_suffix("")
    captures.mkdir(parents=True, exist_ok=True)
    local = {symbol: [] for symbol in requested}
    for row in daily:
        if row["symbol"] in requested:
            local[row["symbol"]].append(Candle(**row))
    states = {
        row["symbol"]: row
        for row in metadata["series"]
        if row["source"] == "vn" and row["interval"] == "1D"
    }
    results = []
    with httpx.Client(base_url="https://api.aipriceaction.com", timeout=45) as client:
        for symbol in sorted(requested):
            lower = max(floor, parse_time(entries[symbol].get("history_start", floor)))
            start = datetime.fromtimestamp(lower, UTC).date().isoformat()
            end = datetime.fromtimestamp(completed - 1, UTC).date().isoformat()
            try:
                rows, receipt = reference(client, symbol, start, end, captures)
                result = compare(symbol, local[symbol], rows, lower, completed)
                result.update(
                    legacy_capture=receipt, retained_series=states.get(symbol), start=start, end=end
                )
            except (
                httpx.HTTPError,
                ValueError,
                KeyError,
                TypeError,
                DataError,
                OverflowError,
            ) as exc:
                result = {"symbol": symbol, "error": str(exc), "error_type": type(exc).__name__}
            results.append(result)
            print(
                json.dumps(
                    {
                        key: result[key]
                        for key in (
                            "symbol",
                            "local_rows",
                            "legacy_observed_dates",
                            "missing_in_replacement",
                            "extra_in_replacement",
                            "error_type",
                        )
                        if key in result
                    }
                ),
                flush=True,
            )
    _, _, after = read_snapshot(settings.database.resolve())
    if before != after:
        raise RuntimeError(
            "Local data/operational metadata changed during the comparison; recapture a stable checkpoint"
        )
    compared = [row for row in results if "error" not in row]
    summary = {
        "requested_tickers": len(requested),
        "compared_tickers": len(compared),
        "errors": len(results) - len(compared),
        "exact_observed_date_sets": sum(
            not row["missing_in_replacement"] and not row["extra_in_replacement"]
            for row in compared
        ),
        "local_rows": sum(row["local_rows"] for row in compared),
        "legacy_observed_dates": sum(row["legacy_observed_dates"] for row in compared),
        "missing_in_replacement_dates": sum(len(row["missing_in_replacement"]) for row in compared),
        "extra_in_replacement_dates": sum(len(row["extra_in_replacement"]) for row in compared),
        "invalid_legacy_rows": sum(len(row["invalid_legacy_rows"]) for row in compared),
        "duplicate_legacy_dates": sum(len(row["duplicate_legacy_dates"]) for row in compared),
        "cached_legacy_responses": sum(row["legacy_capture"]["reused"] for row in compared),
        "numerically_compared_rows": sum(row["numerically_compared_rows"] for row in compared),
        "exact_ohlcv_rows": sum(row["exact_ohlcv_rows"] for row in compared),
        "changed_price_rows_relative_gt_1e_7": sum(
            row["changed_price_rows_relative_gt_1e_7"] for row in compared
        ),
        "price_difference_gt_1_percent_rows": sum(
            row["price_difference_gt_1_percent_rows"] for row in compared
        ),
        "changed_volume_rows": sum(row["changed_volume_rows"] for row in compared),
        "volume_difference_gt_1_percent_rows": sum(
            row["volume_difference_gt_1_percent_rows"] for row in compared
        ),
    }
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "as_of": now.isoformat(),
        "retained_floor": floor,
        "completed_before": completed,
        "price_relative_representation_threshold": 1e-7,
        "date_reference": "Captured public legacy daily data; not an independently verified exchange calendar",
        "unchanged_local_daily_and_operational_metadata": True,
        "snapshot": after,
        "summary": summary,
        "results": results,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--symbol", action="append", help="Bound the comparison to selected active tickers"
    )
    parser.add_argument(
        "--as-of", help="UTC ISO timestamp for a reproducible retention/finality boundary"
    )
    parser.add_argument(
        "--report", type=Path, default=Path("data/retained-vn-daily-legacy-comparison.json")
    )
    parser.add_argument(
        "--captures",
        type=Path,
        help="Reuse a checksummed capture directory for a separate comparison report",
    )
    main(parser.parse_args())
