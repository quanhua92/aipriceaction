"""Prepare an explicit, isolated DNSE representation-equivalence evidence bundle."""

import argparse
import json
from collections import defaultdict
from pathlib import Path

from aipriceaction_api.storage import Repository
from scripts.dnse_precision_witnesses import derive_precision_witnesses


def run(args):
    staged = json.loads((args.candidate / "report.json").read_text())
    symbol = staged["symbol"]
    groups = defaultdict(list)
    for row in Repository(args.candidate / "candidate.sqlite3").read("vn", symbol, "1m"):
        groups[row.time // 86400 * 86400].append(row.record())
    records = {
        f: json.loads((args.fresh_daily / f / f"{symbol}-1D.json").read_text())
        for f in ("vndirect", "dnse")
    }
    evidence = derive_precision_witnesses(staged, groups, records)
    args.output.mkdir(parents=True, exist_ok=False)
    for path in sorted(args.daily.glob("*/*-1D.json")):
        source = (
            args.fresh_daily / path.parent.name / path.name
            if path.name == f"{symbol}-1D.json"
            else path
        )
        record = json.loads(source.read_text())
        if path.name == f"{symbol}-1D.json" and path.parent.name == "dnse":
            record["volume_precision_evidence"] = evidence
        target = args.output / path.parent.name / path.name
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(record, indent=2) + "\n")
    report = {
        "main_publication": False,
        "symbol": symbol,
        "rounded_dates": len(evidence["dates"]),
        "maximum_representation_difference": max(
            abs(r["reported_volume"] - r["exact_volume"]) for r in evidence["dates"]
        ),
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--daily", type=Path, required=True)
    parser.add_argument("--fresh-daily", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
