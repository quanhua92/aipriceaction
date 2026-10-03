"""Read-only VN provider matrix; no database or candles are written."""

import argparse
import asyncio
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, cutoff
from aipriceaction_api.providers import Providers


async def probe(output, direct):
    settings = replace(Settings.from_env(), allow_direct=direct)
    providers = Providers(settings)
    results = []
    try:
        for provider in settings.vn_providers:
            for symbol, iv, period in [
                (s, i, p) for s in ("FPT",) for i in ("1D", "1h", "1m") for p in ("recent", "floor")
            ] + [("VNINDEX", "1D", "recent")]:
                before = (
                    int(time.time()) + 1
                    if period == "recent"
                    else cutoff(1 if iv == "1m" else 3) + 7 * 86400
                )
                begun = time.perf_counter()
                result = {"provider": provider, "symbol": symbol, "interval": iv, "period": period}
                try:
                    page = await providers.page(
                        "vn", symbol, iv, before, count=3, provider=provider
                    )
                    result.update(
                        rows=len(page.rows),
                        no_data=page.no_data,
                        earliest=datetime.fromtimestamp(page.rows[0].time, UTC).isoformat()
                        if page.rows
                        else None,
                        latest=datetime.fromtimestamp(page.rows[-1].time, UTC).isoformat()
                        if page.rows
                        else None,
                    )
                except DataError as exc:
                    result["error"] = str(exc)
                result["elapsed_ms"] = round((time.perf_counter() - begun) * 1000, 2)
                results.append(result)
                print(json.dumps(result), flush=True)
    finally:
        await providers.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"checked_at": datetime.now(UTC).isoformat(), "probes": results}, indent=2)
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-direct", action="store_true")
    args = parser.parse_args()
    asyncio.run(probe(args.output, args.allow_direct))
