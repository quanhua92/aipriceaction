"""Refresh candidate volume proofs with native daily witnesses; never activate."""

import argparse
import asyncio
import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, date_bounds
from aipriceaction_api.providers import Providers
from aipriceaction_api.vci_volume import proof_from_capture, validate_volume_proof
from scripts.artifact_budget import ArtifactBudget
from scripts.compare_vn_feeds import BudgetedTransport


async def refresh(providers, raw, symbol, stamp, witnesses, *, allow_multiple=False):
    day = stamp // 86400 * 86400
    daily = []
    for feed in ("vndirect", "dnse"):
        key = (symbol, day, feed)
        if key not in witnesses:
            page = await providers.page(
                "vn", symbol, "1D", day + 86400, count=100, start=day, provider=feed
            )
            if (
                len(page.rows) != 1
                or page.rows[0].time != day
                or (
                    page.rows[0].source,
                    page.rows[0].symbol,
                    page.rows[0].interval,
                    page.rows[0].provider,
                )
                != ("vn", symbol, "1D", feed)
            ):
                raise DataError("Fresh daily witness identity or coverage differs")
            witnesses[key] = page.rows[0].validate().record()
        daily.append(witnesses[key])
    proof = proof_from_capture(raw, daily, stamp, time.time_ns(), allow_multiple=allow_multiple)
    target = validate_volume_proof(proof)
    if (target.symbol, target.time) != (symbol, stamp):
        raise DataError("Refreshed volume proof target differs")
    return proof


async def run(args):
    existing = json.loads(args.existing.read_text())
    proposed = json.loads((args.proposals / "proofs.json").read_text())
    discoveries = json.loads((args.proposals / "report.json").read_text())
    proposals = discoveries["proposals"]
    if getattr(args, "include_blocked", False):
        proposals = [*proposals, *discoveries["blocked"]]
    known = {(proof["symbol"], validate_volume_proof(proof).time): proof for proof in existing}
    candidates = {(proof["symbol"], validate_volume_proof(proof).time): proof for proof in proposed}
    if len(known) != len(existing) or len(candidates) != len(proposed):
        raise DataError("Duplicate proof targets in input catalogs")
    first, before = date_bounds(args.start_date), date_bounds(args.end_date, end=True) + 1
    if first >= before:
        raise DataError("Choose an ordered proof verification window")
    args.output.mkdir(parents=True, exist_ok=False)
    budget = ArtifactBudget(args.output, 64 * 1024 * 1024)
    transport = BudgetedTransport(args.output, budget)
    providers = Providers(
        replace(Settings.from_env(), proxies=(), allow_direct=True), transport=transport
    )
    report = {
        "canonical_publication": False,
        "active_catalog_changed": False,
        "verified": [],
        "blocked": [],
        "excluded": [],
        "captures": [],
    }
    witnesses = {}
    try:
        for proposal in proposals:
            symbol, stamp = proposal["symbol"], proposal["time"]
            if not first <= stamp < before:
                report["excluded"].append(
                    {"symbol": symbol, "time": stamp, "reason": "outside_requested_window"}
                )
                continue
            if (symbol, stamp) in known:
                continue
            raw = Path(proposal["capture"]).read_bytes()
            source = candidates.get((symbol, stamp))
            raw_checksum = hashlib.sha256(raw).hexdigest()
            if (source is not None and raw_checksum != source["source_capture_sha256"]) or Path(
                proposal["capture"]
            ).name != f"native-{raw_checksum}.json":
                raise DataError("Candidate source capture changed")
            try:
                proof = await refresh(
                    providers,
                    raw,
                    symbol,
                    stamp,
                    witnesses,
                    allow_multiple=source is None or source["schema_version"] == 2,
                )
                if len(known) >= 100:
                    raise DataError("Refreshed proof catalog exceeds runtime proof-count budget")
                known[symbol, stamp] = proof
                report["verified"].append(
                    {
                        "symbol": symbol,
                        "time": stamp,
                        "capture": proposal["capture"],
                        "corrected_volume": validate_volume_proof(proof).volume,
                    }
                )
            except DataError as exc:
                report["blocked"].append({"symbol": symbol, "time": stamp, "error": str(exc)})
            budget.write(
                args.output / "proofs.json",
                (json.dumps(list(known.values()), indent=2) + "\n").encode(),
            )
            report["captures"] = transport.captures
            budget.write(
                args.output / "report.json", (json.dumps(report, indent=2) + "\n").encode()
            )
        budget.write(
            args.output / "proofs.json",
            (json.dumps(list(known.values()), indent=2) + "\n").encode(),
        )
        budget.write(args.output / "report.json", (json.dumps(report, indent=2) + "\n").encode())
        print(
            json.dumps(
                {
                    "verified": len(report["verified"]),
                    "blocked": len(report["blocked"]),
                    "excluded": len(report["excluded"]),
                    "catalog_proofs": len(known),
                    "artifact_bytes": budget.used,
                }
            ),
            flush=True,
        )
        return report
    finally:
        await providers.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposals", type=Path, required=True)
    parser.add_argument("--existing", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument(
        "--include-blocked",
        action="store_true",
        help="Recheck discovery failures with exact-date fresh witnesses",
    )
    asyncio.run(run(parser.parse_args()))
