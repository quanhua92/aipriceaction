"""Review complete isolated VCI candidates against daily peers and old archives."""

import argparse
import json
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from aipriceaction_api.archive import Archive
from aipriceaction_api.coherent_snapshot import capture, publish
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, cutoff
from aipriceaction_api.storage import Repository
from scripts.compare_vn_feeds import FEEDS, FIELDS
from scripts.probe_vn_minute_basis import session


def run(args):
    settings = Settings.from_env()
    if settings.s3_endpoint != "http://127.0.0.1:9100":
        raise ValueError("Use the local RustFS review environment")
    args.output.mkdir(parents=True, exist_ok=False)
    main = Repository(settings.database)
    archive = Archive(main, settings)
    report = {"main_publication": False, "series": []}
    for root in sorted(args.candidates.iterdir()):
        if not root.is_dir() or not (root / "report.json").exists():
            continue
        staged = json.loads((root / "report.json").read_text())
        symbol = staged["symbol"]
        entry = {"symbol": symbol, "stage_complete": staged["complete"], "review_passed": False}
        report["series"].append(entry)
        if not staged["complete"]:
            entry["error"] = staged.get("error", "Incomplete candidate")
            continue
        rows = Repository(root / "candidate.sqlite3").read("vn", symbol, "1m")
        groups = defaultdict(list)
        for row in rows:
            groups[row.time // 86400 * 86400].append(row.record())
        daily = {r.time: r for r in main.read("vn", symbol, "1D")}
        aggregates = {day: session(bars) for day, bars in groups.items()}
        entry["rows"] = len(rows)
        entry["observed_dates"] = len(groups)
        entry["missing_retained_daily_dates"] = sorted(groups.keys() - daily.keys())
        entry["maximum_retained_price_difference_vnd"] = max(
            (
                abs(values[field] - getattr(daily[day], field))
                for day, values in aggregates.items()
                if day in daily
                for field in FIELDS[:4]
            ),
            default=None,
        )
        entry["feeds"] = {}
        matched = defaultdict(list)
        for feed in FEEDS:
            record = json.loads((args.daily / feed / f"{symbol}-1D.json").read_text())
            reference = {r["time"]: r for r in record.get("rows", [])}
            differences = []
            for day, values in aggregates.items():
                if day not in reference:
                    continue
                if values["volume"] == reference[day]["volume"]:
                    if feed != "legacy":
                        matched[day].append(feed)
                else:
                    differences.append(
                        {
                            "day": day,
                            "minute_volume": values["volume"],
                            "daily_volume": reference[day]["volume"],
                        }
                    )
            entry["feeds"][feed] = {
                "compared_dates": len(groups.keys() & reference.keys()),
                "missing_dates": sorted(groups.keys() - reference.keys()),
                "volume_disagreements": differences,
                "source_error": record.get("error"),
            }
        entry["dates_without_two_native_volume_witnesses"] = [
            {
                "day": day,
                "matching_native_feeds": matched[day],
                "minute_volume": aggregates[day]["volume"],
            }
            for day in sorted(groups)
            if len(matched[day]) < 2
        ]
        try:
            snapshot = capture(main, archive, symbol)
            replacement = [
                replace(r, revision="review-vci-" + symbol.lower() + "-" + args.output.name)
                for r in rows
            ]
            entry["storage_preview"] = publish(
                main, archive, snapshot, replacement, cutoff(settings.minute_years)
            )
        except DataError as exc:
            entry["storage_error"] = str(exc)
        entry["review_passed"] = (
            not entry["missing_retained_daily_dates"]
            and entry["maximum_retained_price_difference_vnd"] <= 1
            and not entry["dates_without_two_native_volume_witnesses"]
            and "storage_preview" in entry
        )
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(
            json.dumps(
                {
                    key: entry[key]
                    for key in (
                        "symbol",
                        "rows",
                        "observed_dates",
                        "maximum_retained_price_difference_vnd",
                        "review_passed",
                    )
                }
            ),
            flush=True,
        )
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--daily", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
