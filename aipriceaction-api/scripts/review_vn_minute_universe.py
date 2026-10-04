"""Review collected year-window evidence against SQLite, retaining bounded samples."""

import argparse
import json
from collections import Counter
from pathlib import Path

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError
from scripts.artifact_budget import ArtifactBudget
from scripts.validate_ohlcv import exceptions, local_comparisons


def compact_local(row, samples=20):
    result = {
        key: row[key] for key in ("symbol", "interval", "sqlite_rows", "volume_correction_receipts")
    }
    result["providers"] = {}
    for feed, peer in row["providers"].items():
        result["providers"][feed] = {"shared": peer["shared"], "counts": {}, "samples": {}}
        for kind in ("provider_only", "sqlite_only", "price_disagreements", "volume_disagreements"):
            result["providers"][feed]["counts"][kind] = len(peer[kind])
            result["providers"][feed]["samples"][kind] = peer[kind][:samples]
    for kind in ("unanimous_provider_conflicts", "missing_unanimous_provider_timestamps"):
        result[kind] = {"count": len(row[kind]), "samples": row[kind][:samples]}
    return result


def run(args):
    summary = json.loads((args.audit / "summary.json").read_text())
    if not summary["completed"] and not args.allow_partial:
        raise DataError("Collection is incomplete; explicitly choose a partial review")
    settings = Settings.from_env()
    series, issues, seen = [], [], set()
    for batch in summary["batches"]:
        path = Path(batch["path"])
        if not path.resolve().is_relative_to(args.audit.resolve()):
            raise DataError("Batch outside the selected audit")
        report = json.loads((path / "report.json").read_text())
        if report["symbols"] != batch["symbols"] or report["intervals"] != ["1m"]:
            raise DataError("Comparison batch identity differs")
        for symbol in report["symbols"]:
            if symbol in seen or symbol not in summary["symbols"]:
                raise DataError("Duplicate or unexpected comparison symbol")
            seen.add(symbol)
        # Each batch bounds resident raw rows and detailed timestamp lists.
        local = local_comparisons(settings, path, report)
        issues.extend(exceptions(report, local))
        series.extend(compact_local(row) for row in local)
    if summary["completed"] and seen != set(summary["symbols"]):
        raise DataError("Completed collection is missing selected symbols")
    result = {
        "read_only": True,
        "canonical_publication": False,
        "perfect_data_proven": False,
        "collection_complete": summary["completed"],
        "checked_symbols": len(seen),
        "requested_symbols": len(summary["symbols"]),
        "sqlite_comparison_basis": "raw SQLite plus verified volume correction receipts",
        "exception_counts": dict(Counter(row["kind"] for row in issues)),
        "exceptions": issues,
        "series": series,
        "limitations": [
            "Per-batch SQLite snapshots are read-only; this is not one cross-batch point-in-time snapshot.",
            "Samples are bounded; counts cover every compared timestamp. Full source evidence remains in the audit batches.",
            "Unanimous provider agreement is evidence, not a publication license or independent market truth.",
        ],
    }
    args.output.mkdir(parents=True, exist_ok=False)
    ArtifactBudget(args.output, 8 * 1024 * 1024).write(
        args.output / "report.json", (json.dumps(result, indent=2) + "\n").encode()
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "checked_symbols",
                    "requested_symbols",
                    "collection_complete",
                    "exception_counts",
                )
            }
        )
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-partial", action="store_true")
    run(parser.parse_args())
