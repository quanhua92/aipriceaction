"""Run resumable full-window VN comparisons in small batches under one disk cap."""

import argparse
import asyncio
import hashlib
import json
import tempfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from aipriceaction_api.config import Settings
from scripts.artifact_budget import ArtifactBudget, ArtifactBudgetExceeded
from scripts.compare_vn_feeds import run as compare


def checkpoint(path, value):
    data = (json.dumps(value, indent=2) + "\n").encode()
    if len(data) > 32768:
        raise ValueError("Universe checkpoint exceeds reserved report space")
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        file.write(data)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


async def run(args):
    settings = Settings.from_env()
    entries = json.loads(settings.watchlist.read_text())["vn"]
    symbols = args.symbol or [
        entry if isinstance(entry, str) else entry["symbol"] for entry in entries
    ]
    if len(set(symbols)) != len(symbols) or not symbols:
        raise ValueError("Choose unique symbols")
    if not 1 <= args.batch_size <= 4 or not 16 <= args.artifact_budget_mib <= 4096:
        raise ValueError("Use 1–4 symbols per batch and a 16–4096 MiB artifact budget")
    args.output.mkdir(parents=True, exist_ok=args.resume)
    # Reserve enough for the previous and next atomic summary simultaneously.
    budget = ArtifactBudget(args.output, args.artifact_budget_mib * 1024 * 1024 - 65536)
    request = {
        "symbols": symbols,
        "batch_size": args.batch_size,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "max_pages": args.max_pages,
        "artifact_budget_mib": args.artifact_budget_mib,
        "volume_proofs_sha256": hashlib.sha256(args.vci_volume_proofs.read_bytes()).hexdigest(),
    }
    path = args.output / "request.json"
    if args.resume:
        if json.loads(path.read_text()) != request:
            raise ValueError("Resume must preserve the universe request and proof identity")
    else:
        budget.write(path, (json.dumps(request, indent=2) + "\n").encode())
    summary = {
        "canonical_publication": False,
        "completed": False,
        "symbols": symbols,
        "batches": [],
        "counts": {},
        "provider_errors": 0,
        "incomplete_provider_windows": 0,
        "artifact_budget_mib": args.artifact_budget_mib,
    }
    counts = Counter()
    for first in range(0, len(symbols), args.batch_size):
        batch = args.output / f"batch-{first // args.batch_size:03}"
        try:
            report = await compare(
                SimpleNamespace(
                    output=batch,
                    symbol=symbols[first : first + args.batch_size],
                    interval=["1m"],
                    daily_start=args.start_date,
                    intraday_start=args.start_date,
                    end_date=args.end_date,
                    native_providers=True,
                    paginate=True,
                    max_pages=args.max_pages,
                    vci_volume_proofs=args.vci_volume_proofs,
                    resume=args.resume and (batch / "request.json").exists(),
                    artifact_budget=budget,
                )
            )
        except ArtifactBudgetExceeded:
            summary["stop"] = "artifact_budget"
            checkpoint(args.output / "summary.json", summary)
            print(
                json.dumps(
                    {"stop": "artifact_budget", "completed_batches": len(summary["batches"])}
                ),
                flush=True,
            )
            return summary
        counts.update(report["counts"])
        summary["counts"] = dict(counts)
        summary["provider_errors"] += len(report["errors"])
        summary["incomplete_provider_windows"] += sum(
            not window["reached_requested_start"] for window in report["provider_window_stops"]
        )
        summary["batches"].append({"path": str(batch), "symbols": report["symbols"]})
        checkpoint(args.output / "summary.json", summary)
        print(
            json.dumps(
                {
                    "completed_symbols": min(first + args.batch_size, len(symbols)),
                    "total_symbols": len(symbols),
                    "artifact_bytes": budget.used,
                }
            ),
            flush=True,
        )
        del report
    summary["completed"] = True
    summary["artifact_bytes"] = sum(
        path.stat().st_size for path in args.output.rglob("*") if path.is_file()
    )
    checkpoint(args.output / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--symbol", action="append")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--vci-volume-proofs", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--artifact-budget-mib", type=int, default=2048)
    parser.add_argument("--resume", action="store_true")
    asyncio.run(run(parser.parse_args()))
