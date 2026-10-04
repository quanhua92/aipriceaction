"""Compare existing VN minute archives with VCI without publishing any changes."""

import argparse
import asyncio
import json
from dataclasses import asdict, replace
from pathlib import Path

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError
from aipriceaction_api.providers import Providers
from aipriceaction_api.storage import Repository
from scripts.compare_vn_feeds import compare, same
from scripts.stage_yahoo_daily_history import RecordingTransport, freeze


async def fetch_window(providers, symbol, start, end, *, max_pages=64):
    """Require pagination to cross the archive floor; empty is not coverage."""
    before, rows = end + 1, {}
    for _ in range(max_pages):
        page = await providers.page(
            "vn", symbol, "1m", before, count=2000, provider="vci", start=start
        )
        if page.provider != "vci" or page.cursor is None or page.cursor >= before:
            raise DataError("VCI archive verification ended before crossing the floor")
        for row in page.rows:
            if not start <= row.time <= end:
                raise DataError("VCI returned a candle outside the archive window")
            previous = rows.get(row.time)
            if previous is not None and not same(asdict(previous), asdict(row)):
                raise DataError("VCI changed a candle during archive pagination")
            rows[row.time] = row
        if page.cursor <= start:
            return [rows[t] for t in sorted(rows)]
        before = page.cursor
    raise DataError("VCI archive verification exceeded its page bound")


async def run(args):
    base = Settings.from_env()
    selected = {
        entry if isinstance(entry, str) else entry["symbol"]
        for entry in json.loads(base.watchlist.read_text())["vn"]
    }
    symbols = sorted(set(s.upper() for s in args.symbol))
    if set(symbols) - selected:
        raise ValueError("Choose configured VN symbols")
    args.output.mkdir(parents=True, exist_ok=False)
    repo = Repository(base.database)
    archive = Archive(repo, base)
    settings = replace(
        base,
        vci_history_fallback=True,
        allow_direct=args.allow_direct,
        proxies=() if args.allow_direct else base.proxies,
    )
    transport = RecordingTransport(args.output)
    providers = Providers(settings, transport=transport)
    objects = [
        obj
        for symbol in symbols
        for obj in repo.archives("vn", symbol, "1m")
        if obj["status"] == "published"
    ]
    report = {
        "main_publication": False,
        "symbols": symbols,
        "objects": [],
        "limitations": [
            "Agreement does not prove independent market accuracy.",
            "Existing archive coverage may itself be incomplete.",
            "Publication requires separate coherent revision and correction proofs.",
        ],
    }
    path = args.output / "report.json"

    def save():
        report["captures"] = transport.captures
        path.write_text(json.dumps(report, indent=2) + "\n")

    save()
    try:
        for obj in objects:
            first_capture = len(transport.captures)
            item = {"archive": obj, "verified": False}
            report["objects"].append(item)
            save()
            try:
                original = archive.read(obj)
                item["original"] = freeze(
                    args.output, "original", json.dumps([asdict(r) for r in original]).encode()
                )
                incoming = await fetch_window(providers, obj["symbol"], obj["start"], obj["end"])
                item["candidate"] = freeze(
                    args.output, "candidate", json.dumps([asdict(r) for r in incoming]).encode()
                )
                item["comparison"] = compare(
                    {
                        "archive": [asdict(r) for r in original],
                        "vci": [asdict(r) for r in incoming],
                    }
                )["pairs"]["archive:vci"]
                item["crossed_floor"] = True
                pair = item["comparison"]
                item["verified"] = bool(original) and not any(
                    pair[key]
                    for key in ("only_left", "price_disagreements", "volume_disagreements")
                )
            except DataError as exc:
                item["error"] = str(exc)
            item["captures"] = transport.captures[first_capture:]
            save()
            print(
                json.dumps(
                    {
                        "symbol": obj["symbol"],
                        "archive": obj["id"],
                        "verified": item["verified"],
                        "error": item.get("error"),
                    }
                ),
                flush=True,
            )
    finally:
        await providers.close()
        save()
    current = {obj["id"]: obj for symbol in symbols for obj in repo.archives("vn", symbol, "1m")}
    report["original_archive_metadata_unchanged"] = all(
        current.get(obj["id"]) == obj for obj in objects
    )
    report["all_original_archives_match"] = bool(objects) and all(
        item["verified"] for item in report["objects"]
    )
    save()
    print(
        json.dumps(
            {
                "report": str(path),
                "objects": len(objects),
                "all_original_archives_match": report["all_original_archives_match"],
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-direct", action="store_true")
    asyncio.run(run(parser.parse_args()))
