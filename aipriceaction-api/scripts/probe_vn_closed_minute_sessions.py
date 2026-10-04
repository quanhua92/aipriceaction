"""Capture closed-day minute controls from all four VN native providers."""

import argparse
import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, completed_vn_sessions, date_bounds
from aipriceaction_api.providers import Providers
from scripts.probe_vn_minute_basis import session
from scripts.stage_yahoo_daily_history import RecordingTransport


async def run(args):
    targets = []
    for target in args.target:
        symbol, date = target.split(":", 1)
        first = date_bounds(date)
        if first >= completed_vn_sessions(datetime.now(UTC)):
            raise DataError("Choose completed VN sessions")
        targets.append((symbol.upper(), date, first))
    if len(set(targets)) != len(targets):
        raise DataError("Choose unique symbol/date targets")
    args.output.mkdir(parents=True, exist_ok=False)
    settings = replace(
        Settings.from_env(),
        proxies=(),
        allow_direct=True,
        vci_history_fallback=True,
        vci_volume_proofs=args.volume_proofs,
    )
    transport = RecordingTransport(args.output)
    providers = Providers(settings, transport=transport)
    report = {
        "main_publication": False,
        "controls": [],
        "limitations": [
            "Native agreement is corroboration; providers can share upstreams.",
            "A missing candle is not synthesized or assigned to a neighboring minute.",
        ],
    }
    try:
        for symbol, date, first in targets:
            for feed in (*Providers.VN, "vci"):
                for count in (500, 2000, 10000) if feed == "vci" else (1000,):
                    entry = {"symbol": symbol, "date": date, "feed": feed, "count": count}
                    report["controls"].append(entry)
                    captured = len(transport.captures)
                    try:
                        page = await providers.page(
                            "vn",
                            symbol,
                            "1m",
                            first + 86400,
                            count=count,
                            start=first,
                            provider=feed,
                        )
                        entry.update(
                            rows=[r.record() for r in page.rows],
                            cursor=page.cursor,
                            volume_proofs=list(page.volume_proofs),
                        )
                        entry["aggregate"] = session(entry["rows"])
                        entry["crossed_day_start"] = page.cursor is not None and page.cursor < first
                    except DataError as exc:
                        entry["error"] = str(exc)
                    entry["captures"] = transport.captures[captured:]
                    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
                    print(
                        json.dumps(
                            {
                                k: entry.get(k)
                                for k in ("symbol", "date", "feed", "count", "aggregate", "error")
                            }
                        ),
                        flush=True,
                    )
    finally:
        await providers.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="append", required=True, help="SYMBOL:YYYY-MM-DD")
    parser.add_argument("--volume-proofs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
