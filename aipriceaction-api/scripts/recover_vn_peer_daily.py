"""Append one independently corroborated VN daily trade, without rewriting history.

Dry-run by default. Apply records both raw source responses in immutable S3
evidence, appends to an unchanged ready series, and queues only the daily CSV.
This does not assert that the selected date is the latest trade overall.
"""

import argparse
import asyncio
import hashlib
import json
import math
import re
import time
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime

from botocore.exceptions import ClientError

from aipriceaction_api.archive import Archive, S3Store
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, parse_time
from aipriceaction_api.providers import Providers
from aipriceaction_api.public_csv import _csv_values, daily_key
from aipriceaction_api.repair_yahoo_year import _put_immutable
from aipriceaction_api.storage import COLUMNS, Repository
from aipriceaction_api.workers import Worker

FIELDS = ("open", "high", "low", "close", "volume")


def _same(a, b):
    return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-6)


def verify_day(symbol, day, page, payload):
    stamp = parse_time(day.isoformat())
    if page.provider != "vndirect":
        raise DataError("The independent daily peer must be VNDirect")
    peer = [row.validate() for row in page.rows if row.time == stamp]
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict) or data[0].get("symbol") != symbol:
        raise DataError("Vietcap returned an invalid daily symbol envelope")
    arrays = [data[0].get(key) for key in ("t", "o", "h", "l", "c", "v")]
    if any(type(a) is not list for a in arrays) or len({len(a) for a in arrays}) != 1:
        raise DataError("Vietcap returned invalid daily arrays")
    try:
        matches = [i for i, value in enumerate(arrays[0]) if int(value) == stamp and float(value) == stamp]
        if len(peer) != 1 or len(matches) != 1:
            raise DataError("The sources must each contain the requested day exactly once")
        index = matches[0]
        volume = float(arrays[5][index])
        if not math.isfinite(volume) or volume <= 0 or volume != int(volume):
            raise DataError("The corroborated trade must have positive integral volume")
        witness = Candle(
            "vn", symbol, "1D", stamp, *(float(a[index]) for a in arrays[1:5]),
            int(volume), "vci", "verified",
        ).validate()
    except (TypeError, ValueError, OverflowError) as exc:
        raise DataError("Vietcap returned invalid daily values") from exc
    row = peer[0]
    if (row.source, row.symbol, row.interval, row.provider) != ("vn", symbol, "1D", "vndirect"):
        raise DataError("VNDirect returned a different candle identity")
    if any(not _same(getattr(row, field), getattr(witness, field)) for field in FIELDS):
        raise DataError("The independent daily witnesses disagree on OHLCV")
    return row


def unmatched_newer_trades(symbol, stamp, page, payload):
    """List later positive-volume observations not matched by the other feed."""
    if page.provider != "vndirect":
        raise DataError("The recent-history peer must be VNDirect")
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict) or data[0].get("symbol") != symbol:
        raise DataError("Vietcap returned an invalid recent history envelope")
    arrays = [data[0].get(key) for key in ("t", "o", "h", "l", "c", "v")]
    if any(type(a) is not list for a in arrays) or len({len(a) for a in arrays}) != 1:
        raise DataError("Vietcap returned invalid recent history arrays")
    witnesses = {}
    try:
        for index, raw_time in enumerate(arrays[0]):
            when = int(raw_time)
            if isinstance(raw_time, bool) or float(raw_time) != when or when in witnesses:
                raise ValueError("invalid or duplicate timestamp")
            volume = float(arrays[5][index])
            if not math.isfinite(volume) or volume < 0 or volume != int(volume):
                raise ValueError("invalid volume")
            witnesses[when] = Candle(
                "vn", symbol, "1D", when, *(float(a[index]) for a in arrays[1:5]),
                int(volume), "vci", "verified",
            ).validate()
    except (TypeError, ValueError, OverflowError) as exc:
        raise DataError("Vietcap returned invalid recent history values") from exc
    unmatched = []
    for row in page.rows:
        if (row.source, row.symbol, row.interval, row.provider) != ("vn", symbol, "1D", "vndirect"):
            raise DataError("Recent-history peer identity differs")
        if row.time <= stamp or row.volume <= 0:
            continue
        other = witnesses.get(row.time)
        if not other or any(not _same(getattr(row, field), getattr(other, field)) for field in FIELDS):
            unmatched.append(row)
    return sorted(unmatched, key=lambda row: row.time)


def commit_append(repo, symbol, day, row, expected_state, expected_latest, owner, evidence_key, unmatched=None):
    """Atomically append one candle, its receipt, and a daily CSV retry intent."""
    stamp = parse_time(day.isoformat())
    now, updated = int(time.time()), time.time_ns()
    ident = ("vn", symbol, "1D")
    with repo.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        lease = con.execute("SELECT owner,until FROM live_leases WHERE source=? AND symbol=? AND interval=?", ident).fetchone()
        state = con.execute("SELECT * FROM series WHERE source=? AND symbol=? AND interval=?", ident).fetchone()
        latest = con.execute(
            f"SELECT {','.join(COLUMNS)} FROM candles WHERE source=? AND symbol=? AND interval=? ORDER BY time DESC LIMIT 1",
            ident,
        ).fetchone()
        ticker = con.execute("SELECT enabled FROM tickers WHERE source='vn' AND symbol=?", (symbol,)).fetchone()
        existing = con.execute("SELECT 1 FROM candles WHERE source=? AND symbol=? AND interval=? AND time=?", (*ident, stamp)).fetchone()
        archive = con.execute(
            "SELECT 1 FROM archives WHERE source=? AND symbol=? AND interval=? AND status<>'superseded' AND start<=? AND end>=? LIMIT 1",
            (*ident, stamp, stamp),
        ).fetchone()
        leased = con.execute("SELECT 1 FROM jobs WHERE source=? AND symbol=? AND interval=? AND lease_until>? LIMIT 1", (*ident, now)).fetchone()
        repair = con.execute("SELECT 1 FROM jobs WHERE source=? AND symbol=? AND interval=? AND kind='repair' AND status IN ('pending','running') LIMIT 1", ident).fetchone()
        if (
            not lease or lease["owner"] != owner or lease["until"] <= now
            or not ticker or not ticker["enabled"] or not state or dict(state) != expected_state
            or state["status"] != "ready" or not latest or dict(latest) != expected_latest.record()
            or latest["time"] >= stamp or existing or archive or leased or repair
            or (row.source, row.symbol, row.interval, row.time, row.provider) != (*ident, stamp, "vndirect")
        ):
            raise DataError("Series, daily tail, archive, or worker lease changed; no candle was appended")
        record = replace(row, revision=state["revision"], updated_at=updated).validate().record()
        con.execute(
            f"INSERT INTO candles({','.join(COLUMNS)}) VALUES ({','.join('?' for _ in COLUMNS)})",
            tuple(record[column] for column in COLUMNS),
        )
        con.execute(
            "INSERT INTO public_csv_queue(source,symbol,interval,period,updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(source,symbol,interval,period) DO UPDATE SET generation=generation+1,retry_at=0,updated_at=excluded.updated_at",
            (*ident, f"day:{day.isoformat()}", now),
        )
        con.execute(
            "INSERT INTO quality(source,symbol,interval,kind,detail,first_seen,last_seen,resolved) VALUES (?,?,?,?,?,?,?,1)",
            (*ident, "verified_peer_daily_append", evidence_key, now, now),
        )
        if unmatched:
            detail = json.dumps(
                {"evidence_key": evidence_key, "count": len(unmatched),
                 "latest": unmatched[-1].record()}, sort_keys=True, separators=(",", ":"),
            )
            con.execute(
                "INSERT INTO quality(source,symbol,interval,kind,detail,first_seen,last_seen,resolved) VALUES (?,?,?,?,?,?,?,0)",
                (*ident, "unverified_newer_provider_trades", detail, now, now),
            )
        repo.bump(con, market=True)


async def recover(repo, archive, providers, symbol, day, *, apply=False):
    if not re.fullmatch(r"[A-Z0-9]{1,12}", symbol) or not isinstance(archive.store, S3Store):
        raise DataError("A valid VN symbol and S3 archive are required")
    stamp, end = parse_time(day.isoformat()), parse_time(day.isoformat()) + 86400
    if end > int(time.time()):
        raise DataError("Only a completed historical day can be recovered")
    owner = uuid.uuid4().hex
    if apply and not repo.live_claim("vn", symbol, "1D", owner, lease=120):
        raise DataError("Daily series is already being updated")
    try:
        state = repo.state("vn", symbol, "1D")
        tail = repo.read("vn", symbol, "1D", limit=1)
        if not state or state["status"] != "ready" or len(tail) != 1 or tail[0].time >= stamp:
            raise DataError("The requested trade is not newer than an unchanged ready tail")
        captured = {}
        original_request = providers.request

        async def capture(provider, *args, **kwargs):
            payload = await original_request(provider, *args, **kwargs)
            captured[provider] = payload
            return payload

        providers.request = capture
        page = await providers.vn_page("vndirect", symbol, "1D", end, 20)
        vndirect = captured["vndirect"]
        vci = await providers.request(
            "vci", "https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart",
            referer="https://trading.vietcap.com.vn/", vn=True,
            json_body={"timeFrame": "ONE_DAY", "symbols": [symbol], "to": end - 1, "countBack": 20},
        )
        row = verify_day(symbol, day, page, vci)
        recent_page = await providers.vn_page("vndirect", symbol, "1D", int(time.time()) + 1, 500)
        vndirect_recent = captured["vndirect"]
        vci_recent = await providers.request(
            "vci", "https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart",
            referer="https://trading.vietcap.com.vn/", vn=True,
            json_body={"timeFrame": "ONE_DAY", "symbols": [symbol], "to": int(time.time()), "countBack": 500},
        )
        unmatched = unmatched_newer_trades(symbol, stamp, recent_page, vci_recent)
        result = {"symbol": symbol, "date": day.isoformat(), "status": "verified_dry_run", "row": row.record(),
                  "unverified_newer_count": len(unmatched),
                  "unverified_newer_latest": unmatched[-1].record() if unmatched else None}
        if not apply:
            return result
        evidence = {
            "kind": "vn_peer_daily_append_v1", "symbol": symbol, "date": day.isoformat(),
            "verified_at_ns": time.time_ns(), "previous_state": state, "previous_tail": tail[0].record(),
            "vndirect_raw": vndirect, "vci_raw": vci, "verified_row": row.record(),
            "vndirect_recent_raw": vndirect_recent, "vci_recent_raw": vci_recent,
            "unverified_newer": [item.record() for item in unmatched],
        }
        payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        digest = hashlib.sha256(payload).hexdigest()
        evidence_key = f"{archive.settings.s3_prefix}/repair-evidence/vn/{symbol}/1D/{day}/peer-{digest}.json"
        client, bucket = archive.store.client, archive.settings.s3_bucket
        key = daily_key("vn", symbol, "1D", day)
        with archive.public_csv_lock(key):
            try:
                client.head_object(Bucket=bucket, Key=key)
                raise DataError("Public daily CSV already exists; compare it manually before appending")
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchKey", "NotFound"):
                    raise
            _put_immutable(client, bucket, evidence_key, payload, "application/json")
            commit_append(repo, symbol, day, row, state, tail[0], owner, evidence_key, unmatched)
        worker = Worker(repo, archive.settings, archive=archive)
        try:
            try:
                await worker._archive_thread(worker._publish_public_candles, "vn", symbol, "1D", [])
            except Exception as exc:
                return result | {"status": "appended_csv_pending", "evidence_key": evidence_key, "error": type(exc).__name__}
            actual = _csv_values(archive.store.read(key), key).get(datetime.fromtimestamp(stamp, UTC))
            if actual is None or any(not _same(a, getattr(row, field)) for a, field in zip(actual, FIELDS, strict=True)):
                raise DataError("Published daily CSV does not match the corroborated trade")
            return result | {"status": "appended_and_published", "evidence_key": evidence_key, "csv_key": key}
        finally:
            await worker.providers.close()
    finally:
        if apply:
            repo.live_release("vn", symbol, "1D", owner)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol")
    parser.add_argument("day", type=date.fromisoformat)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    providers = Providers(replace(settings, proxies=(), allow_direct=True))
    try:
        repo = Repository(settings.database)
        report = await recover(repo, Archive(repo, settings), providers, args.symbol.upper(), args.day, apply=args.apply)
        print(json.dumps(report, ensure_ascii=False))
    finally:
        await providers.close()


if __name__ == "__main__":
    asyncio.run(main())
