"""Capture native closing-session query variants without publishing data."""

import argparse
import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, completed_vn_sessions, date_bounds
from aipriceaction_api.providers import Providers
from scripts.stage_yahoo_daily_history import RecordingTransport


async def run(args):
    targets = [(symbol.upper(), date) for symbol, date in (t.split(":", 1) for t in args.target)]
    if len(set(targets)) != len(targets) or any(
        date_bounds(d) >= completed_vn_sessions(datetime.now(UTC)) for _, d in targets
    ):
        raise DataError("Choose unique completed symbol/date targets")
    args.output.mkdir(parents=True, exist_ok=args.resume)
    path = args.output / "report.json"
    report = (
        json.loads(path.read_text()) if args.resume else {"main_publication": False, "controls": []}
    )
    if report["main_publication"] or report.get("complete"):
        raise DataError("Only incomplete isolated controls may resume")
    keys = [(r["symbol"], r["date"], r["feed"], r["resolution"]) for r in report["controls"]]
    planned = {
        (s, d, f, res) for s, d in targets for f in Providers.VN for res in ("1", "5", "15", "30")
    }
    if len(keys) != len(set(keys)) or set(keys) - planned:
        raise DataError("Resume target/provider/resolution selection differs")
    transport = RecordingTransport(args.output)
    providers = Providers(
        replace(Settings.from_env(), allow_direct=True, proxies=()), transport=transport
    )
    report["complete"] = False
    try:
        for symbol, date in targets:
            day = date_bounds(date)
            for feed in Providers.VN:
                for res in ("1", "5", "15", "30"):
                    key = (symbol, date, feed, res)
                    if key in keys:
                        continue
                    rec = {"symbol": symbol, "date": date, "feed": feed, "resolution": res}
                    captured = len(transport.captures)
                    try:
                        url, referer = providers.VN[feed]
                        rec["payload"] = await providers.request(
                            feed,
                            url,
                            {
                                "symbol": symbol,
                                "resolution": res,
                                "from": day + 6 * 3600,
                                "to": day + 8 * 3600 - 1,
                                "countback": 100,
                            },
                            referer,
                            vn=True,
                        )
                    except DataError as exc:
                        rec["error"] = str(exc)
                    rec["captures"] = transport.captures[captured:]
                    report["controls"].append(rec)
                    path.write_text(json.dumps(report, indent=2) + "\n")
                    body = rec.get("payload", {})
                    print(
                        json.dumps(
                            {
                                "symbol": symbol,
                                "date": date,
                                "feed": feed,
                                "resolution": res,
                                "rows": len(body.get("t") or []),
                                "error": rec.get("error"),
                            }
                        ),
                        flush=True,
                    )
        report["complete"] = True
        path.write_text(json.dumps(report, indent=2) + "\n")
    finally:
        await providers.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="append", required=True, help="SYMBOL:YYYY-MM-DD")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    asyncio.run(run(parser.parse_args()))
