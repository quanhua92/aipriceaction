"""Check captured provider dates against verified equal HOSE/HNX schedules."""

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

from aipriceaction_api.domain import DataError
from scripts.check_hose_scheduled_dates import load_reference_calendar
from scripts.check_vn_stock_scheduled_dates import shared_schedule


def expected_dates(catalog, first, last):
    if first > last:
        raise DataError("Calendar review dates are reversed")
    expected = set()
    for year in range(first.year, last.year + 1):
        calendars = []
        for exchange in ("HOSE", "HNX"):
            entries = [
                entry
                for entry in catalog["sources"]
                if (entry["exchange"], entry["year"]) == (exchange, year)
            ]
            if len(entries) != 1:
                raise DataError("Supply exactly one source-backed schedule per exchange/year")
            entry = entries[0]
            calendar = load_reference_calendar(
                Path(entry["calendar"]),
                Path(entry["source"]),
                [Path(path) for path in entry.get("amendments", [])],
            )
            if (calendar[0]["exchange"], calendar[0]["year"]) != (exchange, year):
                raise DataError("Calendar catalog identity differs from its declaration")
            calendars.append(calendar)
        expected.update(
            shared_schedule(*calendars, max(first, date(year, 1, 1)), min(last, date(year, 12, 31)))
        )
    return {value.isoformat() for value in expected}


def run(args):
    request = json.loads((args.audit / "request.json").read_text())
    catalog = json.loads(args.calendar_catalog.read_text())
    expected = expected_dates(
        catalog, date.fromisoformat(request["start_date"]), date.fromisoformat(request["end_date"])
    )
    series, seen = [], set()
    for path in sorted(args.audit.glob("batch-*/report.json")):
        report = json.loads(path.read_text())
        if (
            report["intraday_start"] != request["start_date"]
            or report["end_date"] != request["end_date"]
            or report["intervals"] != ["1m"]
        ):
            raise DataError("Batch comparison dates or intervals differ")
        for row in report["comparisons"]:
            symbol = row["symbol"]
            if symbol in seen or symbol not in request["symbols"]:
                raise DataError("Duplicate or unexpected symbol in comparison batches")
            seen.add(symbol)
            providers = {}
            for feed in report["feeds"]:
                coverage = row["date_coverage"]["providers"].get(feed)
                if coverage is None:
                    providers[feed] = {
                        "usable_observations": False,
                        "missing_scheduled_date_candidates": sorted(expected),
                        "unexpected_dates": [],
                    }
                else:
                    observed = set(coverage["rows_by_date"])
                    providers[feed] = {
                        "usable_observations": True,
                        "observed_dates": len(observed),
                        "missing_scheduled_date_candidates": sorted(expected - observed),
                        "unexpected_dates": sorted(observed - expected),
                    }
            series.append({"symbol": symbol, "providers": providers})
    result = {
        "read_only": True,
        "canonical_publication": False,
        "comparison_collection_complete": seen == set(request["symbols"]),
        "checked_symbols": len(seen),
        "requested_symbols": len(request["symbols"]),
        "scheduled_dates": len(expected),
        "calendar_catalog": str(args.calendar_catalog),
        "calendar_catalog_sha256": hashlib.sha256(args.calendar_catalog.read_bytes()).hexdigest(),
        "calendar_declaration_checksums": {
            entry["calendar"]: hashlib.sha256(Path(entry["calendar"]).read_bytes()).hexdigest()
            for entry in catalog["sources"]
            if date.fromisoformat(request["start_date"]).year
            <= entry["year"]
            <= date.fromisoformat(request["end_date"]).year
        },
        "series": series,
        "limitations": [
            "Announced equal exchange schedules are reference dates; issuer suspensions/listing events require separate evidence.",
            "Presence of a date does not prove every minute is present or OHLCV is correct.",
            "Rejected pages cannot prove absence of source history.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise DataError("Preserve the previous calendar audit; choose a new output")
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "checked_symbols",
                    "requested_symbols",
                    "scheduled_dates",
                    "comparison_collection_complete",
                )
            }
        )
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--calendar-catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
