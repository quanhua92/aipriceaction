"""Probe VCI on exact remaining minute-basis dates; no canonical mutation."""

import argparse
import asyncio
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, date_bounds
from aipriceaction_api.providers import Providers
from aipriceaction_api.storage import Repository
from scripts.compare_vn_feeds import FEEDS, FIELDS, compare
from scripts.stage_yahoo_daily_history import RecordingTransport


def session(rows):
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: r["time"])
    return {
        "open": rows[0]["open"],
        "high": max(r["high"] for r in rows),
        "low": min(r["low"] for r in rows),
        "close": rows[-1]["close"],
        "volume": sum(r["volume"] for r in rows),
    }


async def run(args):
    findings = json.loads(args.findings.read_text())
    daily_report = json.loads((args.daily / "report.json").read_text())
    if (
        daily_report["canonical_publication"]
        or daily_report["requests"] != len(daily_report["symbols"]) * 4
    ):
        raise DataError("Use a completed read-only four-feed daily audit")
    settings = replace(
        Settings.from_env(),
        allow_direct=True,
        proxies=(),
        vci_history_fallback=True,
        vci_volume_proofs=args.volume_proofs,
    )
    args.output.mkdir(parents=True, exist_ok=False)
    repo = Repository(settings.database)
    transport = RecordingTransport(args.output)
    providers = Providers(settings, transport=transport)
    report = {
        "main_publication": False,
        "controls": [],
        "summary": {},
        "daily_source_errors": daily_report["errors"],
        "limitations": [
            "Selected observed controls do not certify full-year accuracy or calendar coverage.",
            "Daily peers can disagree and share upstreams; never infer adjustment factors.",
        ],
    }

    def save():
        report["captures"] = transport.captures
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")

    save()
    try:
        for finding in findings:
            symbol, sessions = finding["symbol"], finding["sessions"]
            dates = sorted(
                {
                    sessions[0]["date"],
                    sessions[len(sessions) // 2]["date"],
                    sessions[-1]["date"],
                    args.latest,
                }
            )
            daily_feeds = {}
            for feed in FEEDS:
                record = json.loads((args.daily / feed / f"{symbol}-1D.json").read_text())
                daily_feeds[feed] = {r["time"]: r for r in record.get("rows", [])}
            for date in dates:
                start, before = date_bounds(date), date_bounds(date, True) + 1
                original = repo.read("vn", symbol, "1m", start, before - 1)
                retained_daily = repo.read("vn", symbol, "1D", start, before - 1)
                control = {
                    "symbol": symbol,
                    "date": date,
                    "stored_rows": [r.record() for r in original],
                    "retained_daily": [r.record() for r in retained_daily],
                    "daily_feeds": {f: rows.get(start) for f, rows in daily_feeds.items()},
                }
                report["controls"].append(control)
                first_capture = len(transport.captures)
                try:
                    page = await providers.page(
                        "vn", symbol, "1m", before, count=500, provider="vci", start=start
                    )
                    selected = [r for r in page.rows if start <= r.time < before]
                    if not selected or page.cursor is None or page.cursor >= start:
                        raise DataError(
                            "VCI control did not traverse the whole requested observed day"
                        )
                    control["vci_rows"] = [r.record() for r in selected]
                    control["volume_proofs"] = list(page.volume_proofs)
                    control["pairs"] = compare(
                        {"stored": control["stored_rows"], "vci": control["vci_rows"]}
                    )["pairs"]
                    derived = session(control["vci_rows"])
                    control["minute_aggregate"] = derived
                    control["daily_comparisons"] = {
                        feed: {
                            "max_price_difference_vnd": max(
                                abs(derived[k] - row[k]) for k in FIELDS[:4]
                            ),
                            "volume_difference": derived["volume"] - row["volume"],
                        }
                        for feed, row in control["daily_feeds"].items()
                        if row is not None
                    }
                    if retained_daily:
                        control["retained_price_difference_vnd"] = max(
                            abs(derived[k] - getattr(retained_daily[0], k)) for k in FIELDS[:4]
                        )
                    current = repo.read("vn", symbol, "1m", start, before - 1)
                    if [r.record() | {"updated_at": 0} for r in current] != [
                        r.record() | {"updated_at": 0} for r in original
                    ]:
                        raise DataError(
                            "Canonical OHLCV/provider basis changed during control capture"
                        )
                    control["canonical_ohlcv_unchanged"] = True
                except DataError as exc:
                    control["error"] = str(exc)
                control["captures"] = transport.captures[first_capture:]
                save()
                print(
                    json.dumps(
                        {
                            "symbol": symbol,
                            "date": date,
                            "vci_rows": len(control.get("vci_rows", [])),
                            "retained_price_difference_vnd": control.get(
                                "retained_price_difference_vnd"
                            ),
                            "error": control.get("error"),
                        }
                    ),
                    flush=True,
                )
        report["summary"] = dict(
            Counter(
                "error"
                if "error" in c
                else "vci_price_matches_retained_daily_within_1vnd"
                if c.get("retained_price_difference_vnd", float("inf")) <= 1
                else "price_disagreement"
                for c in report["controls"]
            )
        )
        save()
    finally:
        await providers.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--findings", type=Path, required=True)
    parser.add_argument("--daily", type=Path, required=True)
    parser.add_argument("--volume-proofs", type=Path, required=True)
    parser.add_argument("--latest", required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
