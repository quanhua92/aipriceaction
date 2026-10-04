"""Replay native controls and propose peer-backed corrections; never publish."""

import argparse
import asyncio
import hashlib
import json
import math
import time
from dataclasses import replace
from pathlib import Path

import httpx

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, date_bounds
from aipriceaction_api.providers import Providers
from aipriceaction_api.vci_peer_volume import peer_proof_from_capture
from scripts.vn_daily_volume_evidence import captured


def proposal(symbol, date, pages, daily, cumulative_total):
    """Require whole observed-day peers and a single exact volume disagreement."""
    day = date_bounds(date)
    by_feed = {}
    for feed in ("vci", "vndirect", "dnse"):
        page = pages[feed]
        if not page.rows or page.cursor is None or page.cursor >= day:
            raise DataError("Peer control does not traverse the whole observed day")
        selected = {r.time: r for r in page.rows}
        for row in page.rows:
            row.validate()
        if len(selected) != len(page.rows) or any(
            (r.source, r.symbol, r.interval, r.provider) != ("vn", symbol, "1m", feed)
            or not day <= r.time < day + 86400
            for r in page.rows
        ):
            raise DataError("Peer control identity or timestamps differ")
        by_feed[feed] = selected
    source = by_feed["vci"]
    if any(set(by_feed[f]) != set(source) for f in ("vndirect", "dnse")):
        raise DataError("Peer timestamps differ; never allocate missing-minute volume")
    changed = []
    for stamp, row in source.items():
        left, right = by_feed["vndirect"][stamp], by_feed["dnse"][stamp]
        if left.volume != right.volume:
            raise DataError("Native minute peers disagree on volume")
        if left.volume != row.volume:
            if not all(
                math.isclose(getattr(left, f), getattr(row, f), rel_tol=0, abs_tol=1e-8)
                for f in ("open", "high", "low", "close")
            ):
                raise DataError("Exact correction target differs from VNDirect price basis")
            changed.append(stamp)
    if len(changed) != 1:
        raise DataError("Require exactly one independently witnessed volume disagreement")
    stamp = changed[0]
    row, corrected = source[stamp], by_feed["vndirect"][stamp]
    corrected_total = sum(r.volume for r in source.values()) - row.volume + corrected.volume
    if corrected.volume <= 0 or corrected_total != cumulative_total:
        raise DataError("Proposed correction does not match native final cumulative volume")
    if (
        {r.provider for r in daily} != {"vndirect", "dnse"}
        or len(daily) != 2
        or any(
            (r.source, r.symbol, r.interval, r.time, r.volume)
            != ("vn", symbol, "1D", day, corrected_total)
            for r in daily
        )
    ):
        raise DataError("Proposed correction lacks two exact native daily witnesses")
    return {
        "kind": "vci_peer_volume_correction_proposal",
        "symbol": symbol,
        "date": date,
        "target_time": stamp,
        "original_volume": row.volume,
        "proposed_volume": corrected.volume,
        "corrected_daily_volume": corrected_total,
        "observed_rows": len(source),
        "target_ohlc": {f: getattr(row, f) for f in ("open", "high", "low", "close")},
        "peer_target_rows": [by_feed[f][stamp].record() for f in ("vndirect", "dnse")],
        "daily_witnesses": [r.record() for r in daily],
        "runtime_licensed": False,
        "main_publication": False,
    }


async def run(args):
    from aipriceaction_api.domain import Candle

    settings = replace(
        Settings.from_env(), vci_history_fallback=True, vci_volume_proofs=args.volume_proofs
    )
    controls = json.loads((args.controls / "report.json").read_text())
    if controls["main_publication"]:
        raise DataError("Require isolated read-only native controls")
    args.output.mkdir(parents=True, exist_ok=False)
    artifacts, report = {}, {"main_publication": False, "proposals": [], "blocked": []}
    proofs = json.loads(args.volume_proofs.read_text())
    targets = sorted({(r["symbol"], r["date"]) for r in controls["controls"]})
    for symbol, date in targets:
        pages, raws = {}, {}
        day = date_bounds(date)
        try:
            for feed in ("vci", "vndirect", "dnse"):
                records = [
                    r
                    for r in controls["controls"]
                    if (r["symbol"], r["date"], r["feed"]) == (symbol, date, feed)
                ]
                record = min(records, key=lambda r: r["count"])
                if "error" in record:
                    raise DataError("Native minute control is unavailable")
                raw = captured(record, artifacts)
                providers = Providers(
                    settings,
                    transport=httpx.MockTransport(
                        lambda request, frozen=raw: httpx.Response(200, content=frozen)
                    ),
                )
                try:
                    page = await providers.page(
                        "vn",
                        symbol,
                        "1m",
                        day + 86400,
                        count=record["count"],
                        start=day,
                        provider=feed,
                    )
                finally:
                    await providers.close()
                if [r.record() for r in page.rows] != record["rows"] or page.cursor != record[
                    "cursor"
                ]:
                    raise DataError("Minute controls differ from captured native responses")
                pages[feed], raws[feed] = page, raw
            body = json.loads(raws["vci"])
            if isinstance(body, dict):
                body = body["data"]
            native = sorted(
                (int(t), int(v))
                for t, v in zip(body[0]["t"], body[0]["accumulatedVolume"], strict=True)
                if day <= int(t) < day + 86400
            )
            daily, witness_hashes = (
                [],
                [hashlib.sha256(raws[f]).hexdigest() for f in ("vndirect", "dnse")],
            )
            for feed in ("vndirect", "dnse"):
                record = json.loads((args.daily / feed / f"{symbol}-1D.json").read_text())
                raw = captured(record, artifacts)
                witness_hashes.append(hashlib.sha256(raw).hexdigest())
                first, end = (
                    date_bounds(record["start_date"]),
                    date_bounds(record["end_date"], True) + 1,
                )
                providers = Providers(
                    settings,
                    transport=httpx.MockTransport(
                        lambda request, frozen=raw: httpx.Response(200, content=frozen)
                    ),
                )
                try:
                    page = await providers.page(
                        "vn",
                        symbol,
                        "1D",
                        end,
                        count=min(10000, max(100, (end - first) // 86400 + 1)),
                        start=first,
                        provider=feed,
                    )
                finally:
                    await providers.close()
                expected = [Candle("vn", symbol, "1D", provider=feed, **r) for r in record["rows"]]
                if page.rows != expected:
                    raise DataError("Daily controls differ from captured native responses")
                daily.append(next(r for r in page.rows if r.time == day))
            proposed = proposal(symbol, date, pages, daily, native[-1][1])
            proof = peer_proof_from_capture(
                raws["vci"],
                [r.record() for r in daily],
                {f: [r.record() for r in pages[f].rows] for f in ("vndirect", "dnse")},
                proposed["target_time"],
                time.time_ns(),
                witness_hashes,
            )
            proofs.append(proof)
            report["proposals"].append(proposed)
        except DataError as exc:
            report["blocked"].append({"symbol": symbol, "date": date, "error": str(exc)})
    report["source_artifacts"] = list(artifacts.values())
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.output / "proofs.json").write_text(json.dumps(proofs, indent=2) + "\n")
    print(json.dumps({"proposals": report["proposals"], "blocked": report["blocked"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controls", type=Path, required=True)
    parser.add_argument("--daily", type=Path, required=True)
    parser.add_argument("--volume-proofs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
