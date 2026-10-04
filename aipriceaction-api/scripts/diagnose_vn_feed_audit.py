"""Explain each feed's minute/hour disagreement from captured observations."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from scripts.compare_vn_feeds import FEEDS, same


def aggregate_minutes(rows):
    rows = sorted(rows, key=lambda r: r["time"])
    return dict(
        time=rows[0]["time"] // 3600 * 3600,
        open=rows[0]["open"],
        high=max(r["high"] for r in rows),
        low=min(r["low"] for r in rows),
        close=rows[-1]["close"],
        volume=sum(r["volume"] for r in rows),
    )


def diagnose(minutes, hours):
    groups = defaultdict(list)
    for row in minutes:
        groups[row["time"] // 3600 * 3600].append(row)
    counts, issues = Counter(), []
    hour_times = set()
    for hour in hours:
        time = hour["time"]
        hour_times.add(time)
        bars = groups.get(time)
        if not bars:
            counts["hour_without_observed_minutes"] += 1
            issues.append({"time": time, "kind": "hour_without_observed_minutes"})
            continue
        derived = aggregate_minutes(bars)
        if same(hour, derived):
            counts["native_hour_matches_observed_minutes"] += 1
            continue
        early = [r for r in bars if r["time"] < time + 1800]
        late = [r for r in bars if r["time"] >= time + 1800]
        # Captured 14:00 ICT native bars sometimes match only the observations
        # before 14:30. Identify that precise omission; never infer missing trades.
        omitted = (
            time % 86400 == 7 * 3600 and early and late and same(hour, aggregate_minutes(early))
        )
        kind = "late_session_observations_excluded" if omitted else "other_minute_hour_disagreement"
        counts[kind] += 1
        issues.append(
            {
                "time": time,
                "kind": kind,
                "native_hour": hour,
                "minute_aggregate": derived,
                "late_observations": late if omitted else [],
            }
        )
    missing_hours = sorted(groups.keys() - hour_times)
    counts["minute_bucket_without_native_hour"] = len(missing_hours)
    return {
        "counts": dict(counts),
        "issues": issues,
        "minute_buckets_without_native_hour": missing_hours,
    }


def run(args):
    audit = json.loads((args.audit_directory / "report.json").read_text())
    reports = []
    summary = {feed: Counter() for feed in FEEDS}
    errors = []
    for symbol in audit["symbols"]:
        for feed in FEEDS:
            records = []
            for iv in ("1m", "1h"):
                record = json.loads(
                    (args.audit_directory / feed / f"{symbol}-{iv}.json").read_text()
                )
                if "error" in record:
                    errors.append(
                        dict(symbol=symbol, feed=feed, interval=iv, error=record["error"])
                    )
                records.append(record)
            if any("rows" not in r for r in records):
                continue
            result = diagnose(records[0]["rows"], records[1]["rows"])
            reports.append(dict(symbol=symbol, feed=feed, **result))
            summary[feed].update(result["counts"])
    report = {
        "audit_directory": str(args.audit_directory),
        "main_publication": False,
        "summary": {f: dict(c) for f, c in summary.items()},
        "errors": errors,
        "series": reports,
        "limitations": [
            "Minute and hourly values can use different source conventions.",
            "An observed aggregation mismatch is a quality finding, not permission to rewrite candles.",
            "Observed inputs do not prove an independent complete trading calendar.",
        ],
    }
    if args.output.exists():
        raise ValueError("Preserve previous evidence; choose a new output filename")
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
