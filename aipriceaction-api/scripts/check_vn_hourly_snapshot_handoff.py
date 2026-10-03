"""Read-only VN hourly handoff preflights with raw and normalized witnesses."""

import argparse
import asyncio
import json
import math
from dataclasses import replace
from pathlib import Path

from aipriceaction_api.adoption import adopt_snapshot
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError
from aipriceaction_api.providers import Providers
from aipriceaction_api.storage import Repository

try:
    from scripts.stage_yahoo_daily_history import RecordingTransport
except ModuleNotFoundError as exc:
    if exc.name != "scripts":
        raise
    from stage_yahoo_daily_history import RecordingTransport


class RecordedProviders(Providers):
    last_page = None

    async def page(self, *args, **kwargs):
        self.last_page = await super().page(*args, **kwargs)
        return self.last_page


async def run(args):
    settings = replace(Settings.from_env(), allow_direct=args.allow_direct)
    repo = Repository(args.database or settings.database)
    symbols = sorted({symbol.upper() for symbol in args.symbol})
    originals = {symbol: repo.read("vn", symbol, "1h") for symbol in symbols}
    states = {symbol: repo.state("vn", symbol, "1h") for symbol in symbols}
    for symbol in symbols:
        if (
            not states[symbol]
            or states[symbol]["provider"] != "legacy-api"
            or not originals[symbol]
        ):
            raise ValueError("Choose a populated legacy-api VN hourly snapshot")
    epoch, adoptions = repo.epoch(), repo.adoptions()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"database": str(repo.path), "executed": False, "input_unchanged": False, "probes": []}
    path = args.output / "report.json"
    transport = RecordingTransport(args.output)
    providers = RecordedProviders(settings, transport=transport)
    try:
        for symbol in symbols:
            existing = {row.time: row for row in originals[symbol]}
            for provider in settings.vn_providers:
                providers.last_page = None
                first_capture = len(transport.captures)
                result = {"symbol": symbol, "provider": provider, "handoff_passed": False}
                try:
                    result["proof"] = await adopt_snapshot(
                        repo, providers, symbol, provider, iv="1h"
                    )
                    result["handoff_passed"] = True
                except DataError as exc:
                    result["error"] = str(exc)
                if providers.last_page is not None:
                    rows = providers.last_page.rows
                    native_path = args.output / f"{symbol}-{provider}-normalized.json"
                    native_path.write_text(
                        json.dumps([row.record() for row in rows], indent=2) + "\n"
                    )
                    differences = []
                    exact = 0
                    for row in rows:
                        previous = existing.get(row.time)
                        if previous is None:
                            differences.append({"time": row.time, "absent_from_snapshot": True})
                            continue
                        changed = {
                            key: [getattr(previous, key), getattr(row, key)]
                            for key in ("open", "high", "low", "close", "volume")
                            if (
                                previous.volume != row.volume
                                if key == "volume"
                                else not math.isclose(
                                    getattr(previous, key),
                                    getattr(row, key),
                                    rel_tol=0,
                                    abs_tol=1e-8,
                                )
                            )
                        }
                        if changed:
                            differences.append({"time": row.time, "fields": changed})
                        else:
                            exact += 1
                    result.update(
                        native_rows=len(rows),
                        exact_shared_rows=exact,
                        differences=differences,
                        normalized_capture=str(native_path),
                    )
                result["captures"] = transport.captures[first_capture:]
                report["probes"].append(result)
                path.write_text(json.dumps(report, indent=2) + "\n")
                print(
                    json.dumps(
                        {
                            key: result[key]
                            for key in (
                                "symbol",
                                "provider",
                                "handoff_passed",
                                "native_rows",
                                "exact_shared_rows",
                                "error",
                            )
                            if key in result
                        }
                    ),
                    flush=True,
                )
    finally:
        await providers.close()
        if (
            repo.epoch() != epoch
            or repo.adoptions() != adoptions
            or any(
                repo.read("vn", symbol, "1h") != rows
                or repo.state("vn", symbol, "1h") != states[symbol]
                for symbol, rows in originals.items()
            )
        ):
            raise AssertionError("Input snapshot changed during preflight")
        report["input_unchanged"] = True
        path.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-direct", action="store_true")
    asyncio.run(run(parser.parse_args()))
