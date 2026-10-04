"""Replay complete candidate/source captures before local minute activation."""

import hashlib
import json
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import httpx

from aipriceaction_api.domain import DataError, date_bounds
from aipriceaction_api.providers import Providers
from aipriceaction_api.storage import Repository
from scripts.compare_vn_feeds import FIELDS
from scripts.probe_vn_minute_basis import session


def values(rows):
    return [(r.time, r.open, r.high, r.low, r.close, r.volume) for r in rows]


def captured(record, artifacts):
    successful = [c for c in record["captures"] if c["status"] == 200]
    if not successful:
        raise DataError("Source record lacks a successful immutable capture")
    for item in record["captures"]:
        raw = Path(item["path"]).read_bytes()
        if len(raw) != item["bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise DataError("Source capture bytes changed since recording")
        artifacts[item["sha256"]] = item
    return Path(successful[-1]["path"]).read_bytes()


async def replay_page(settings, raw, source, symbol, interval, before, count, start):
    # Replay is offline. Its limiter cannot consume the live provider's request
    # budget, and the transport cannot fall through to a real network route.
    providers = Providers(
        replace(settings, requests_per_minute=1000000),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=raw)),
    )
    try:
        return await providers.page(
            "vn",
            symbol,
            interval,
            before,
            count=count,
            start=start,
            provider="vci" if interval == "1m" else source,
        )
    finally:
        await providers.close()


async def reviewed_inputs(args, settings, main):
    review = json.loads((args.review / "report.json").read_text())
    if review["main_publication"] or not args.symbol or len(set(args.symbol)) != len(args.symbol):
        raise DataError("Choose unique explicit symbols from an isolated review")
    passed = {r["symbol"] for r in review["series"] if r["review_passed"]}
    if set(args.symbol) - passed or set(args.symbol) & {"VNINDEX", "VN30"}:
        raise DataError("Only reviewed stock candidates may use this activation path")
    artifacts, inputs = {}, []
    for symbol in args.symbol:
        root = args.candidates / symbol.lower()
        staged = json.loads((root / "report.json").read_text())
        if not staged["complete"] or staged["main_publication"] or staged["symbol"] != symbol:
            raise DataError("Require the completed isolated candidate for each selected symbol")
        candidate = Repository(root / "candidate.sqlite3")
        rows = candidate.read("vn", symbol, "1m")
        if not rows or any((r.provider, r.revision) != ("vci", "vci-candidate") for r in rows):
            raise DataError("Candidate source identity changed")
        start = date_bounds(staged["start_date"])
        before = date_bounds(staged["end_date"], True) + 1
        replayed = {}
        for record in staged["pages"]:
            if record["before"] != before:
                raise DataError("Candidate pagination checkpoints do not form one traversal")
            page = await replay_page(
                settings, captured(record, artifacts), "vn", symbol, "1m", before, 2000, start
            )
            if (
                len(page.rows) != record["rows"]
                or page.cursor != record["cursor"]
                or page.cursor is None
                or page.cursor >= before
            ):
                raise DataError("Replayed candidate page differs from its checkpoint")
            for row in page.rows:
                if row.time in replayed:
                    raise DataError("Candidate traversal repeats a timestamp")
                replayed[row.time] = row
            before = page.cursor
        if before > start or values(rows) != values([replayed[t] for t in sorted(replayed)]):
            raise DataError(
                "Candidate OHLCV no longer matches its entire captured source traversal"
            )
        groups = defaultdict(list)
        for row in rows:
            groups[row.time // 86400 * 86400].append(row.record())
        daily = {r.time: r for r in main.read("vn", symbol, "1D")}
        if any(day not in daily or daily[day].provider not in Providers.VN for day in groups):
            raise DataError("Candidate lacks retained native daily price witnesses")
        aggregates = {day: session(bars) for day, bars in groups.items()}
        maximum = max(
            abs(bars[field] - getattr(daily[day], field))
            for day, bars in aggregates.items()
            for field in FIELDS[:4]
        )
        if maximum > 1:
            raise DataError("Candidate prices no longer match retained native daily witnesses")
        matched = defaultdict(list)
        for feed in Providers.VN:
            record = json.loads((args.daily / feed / f"{symbol}-1D.json").read_text())
            if "error" in record:
                raise DataError("Selected candidate daily witness source is unavailable")
            first, end = (
                date_bounds(record["start_date"]),
                date_bounds(record["end_date"], True) + 1,
            )
            page = await replay_page(
                settings,
                captured(record, artifacts),
                feed,
                symbol,
                "1D",
                end,
                min(10000, max(100, (end - first) // 86400 + 1)),
                first,
            )
            recorded = [(r["time"], *(r[k] for k in FIELDS)) for r in record["rows"]]
            if values(page.rows) != recorded:
                raise DataError("Parsed daily witnesses differ from immutable native captures")
            for row in page.rows:
                if row.time in groups and row.volume == aggregates[row.time]["volume"]:
                    matched[row.time].append(feed)
        if any(len(matched[day]) < 2 for day in groups):
            raise DataError(
                "Candidate lacks two exact native volume witnesses on every observed day"
            )
        checks = {
            "source_replayed_rows": len(replayed),
            "source_replayed_pages": len(staged["pages"]),
            "observed_dates": len(groups),
            "maximum_daily_price_difference_vnd": maximum,
            "daily_source_captures_replayed": 3,
            "native_volume_witnesses": [
                {"day": day, "feeds": matched[day]} for day in sorted(groups)
            ],
        }
        inputs.append((symbol, candidate, rows, checks))
    return inputs, artifacts
