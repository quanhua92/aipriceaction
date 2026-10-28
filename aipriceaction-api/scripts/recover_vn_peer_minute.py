"""Recover one VN minute day only when two feeds and the daily bar agree.

Dry-run by default. Apply creates an immutable source receipt, then atomically
seeds an empty series and queues its Rust-compatible daily CSV. It does not
enable either witness as a live provider or claim complete historical coverage.
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


def _vci_rows(payload, symbol, interval, start, end, *, cumulative=False):
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list) or len(data) != 1 or data[0].get("symbol") != symbol:
        raise DataError("VCI returned an invalid symbol envelope")
    body = data[0]
    keys = ("t", "o", "h", "l", "c", "v") + (("accumulatedVolume",) if cumulative else ())
    arrays = [body.get(key) for key in keys]
    if any(type(a) is not list for a in arrays) or len({len(a) for a in arrays}) != 1:
        raise DataError("VCI returned invalid OHLCV arrays")
    rows, totals = {}, {}
    try:
        for index, raw_time in enumerate(arrays[0]):
            stamp = int(raw_time)
            if isinstance(raw_time, bool) or float(raw_time) != stamp:
                raise ValueError("non-integral time")
            if not start <= stamp < end:
                continue
            if stamp in rows:
                raise DataError("VCI returned duplicate timestamps")
            volume = float(arrays[5][index])
            if not math.isfinite(volume) or volume < 0 or volume != int(volume):
                raise ValueError("invalid volume")
            row = Candle(
                "vn", symbol, interval, stamp,
                *(float(a[index]) for a in arrays[1:5]), int(volume), "vci", "verified",
            ).validate()
            rows[stamp] = row
            if cumulative:
                total = float(arrays[6][index])
                if not math.isfinite(total) or total < 0 or total != int(total):
                    raise ValueError("invalid accumulated volume")
                totals[stamp] = int(total)
    except (ValueError, TypeError, OverflowError) as exc:
        raise DataError("VCI returned invalid numeric values") from exc
    return rows, totals


def verify_day(symbol, day, peer_page, minute_payload, daily_payload, stored_daily):
    start = parse_time(day.isoformat())
    end = start + 86400
    if peer_page.provider != "dnse":
        raise DataError("The minute peer must be DNSE")
    peer = {row.time: row for row in peer_page.rows if start <= row.time < end}
    rows, cumulative = _vci_rows(minute_payload, symbol, "1m", start, end, cumulative=True)
    daily, _ = _vci_rows(daily_payload, symbol, "1D", start, end)
    if not rows or set(rows) != set(peer) or set(daily) != {start}:
        raise DataError("Minute witnesses or VCI daily coverage disagree")
    ordered = [rows[t] for t in sorted(rows)]
    running = 0
    for row in ordered:
        other = peer[row.time].validate()
        if (other.source, other.symbol, other.interval, other.provider) != ("vn", symbol, "1m", "dnse"):
            raise DataError("Minute peer identity differs")
        if any(not _same(getattr(row, field), getattr(other, field)) for field in FIELDS):
            raise DataError("Minute witnesses disagree on OHLCV")
        running += row.volume
        if cumulative[row.time] != running:
            raise DataError("VCI cumulative volume does not prove the complete day")
    if (stored_daily.source, stored_daily.symbol, stored_daily.interval, stored_daily.time) != (
        "vn", symbol, "1D", start
    ):
        raise DataError("Stored daily candle has the wrong identity")
    aggregate = (ordered[0].open, max(r.high for r in ordered), min(r.low for r in ordered),
                 ordered[-1].close, running)
    for field, value in zip(FIELDS, aggregate, strict=True):
        if not _same(value, getattr(stored_daily, field)) or not _same(value, getattr(daily[start], field)):
            raise DataError("Minute aggregate does not match both daily witnesses")
    return ordered


def commit_seed(repo, symbol, day, rows, daily, owner, evidence_key, revision):
    """Atomically seed an untouched series, its evidence link, and CSV intent."""
    start = parse_time(day.isoformat())
    now, stamp = int(time.time()), time.time_ns()
    with repo.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        ident = ("vn", symbol, "1m")
        lease = con.execute("SELECT owner,until FROM live_leases WHERE source=? AND symbol=? AND interval=?", ident).fetchone()
        current = con.execute(
            f"SELECT {','.join(COLUMNS)} FROM candles WHERE source='vn' AND symbol=? AND interval='1D' AND time=?",
            (symbol, start),
        ).fetchone()
        later_trade = con.execute(
            "SELECT 1 FROM candles WHERE source='vn' AND symbol=? AND interval='1D' AND time>? AND volume>0 LIMIT 1",
            (symbol, start),
        ).fetchone()
        ticker = con.execute("SELECT enabled FROM tickers WHERE source='vn' AND symbol=?", (symbol,)).fetchone()
        state = con.execute("SELECT 1 FROM series WHERE source=? AND symbol=? AND interval=?", ident).fetchone()
        candles = con.execute("SELECT 1 FROM candles WHERE source=? AND symbol=? AND interval=? LIMIT 1", ident).fetchone()
        archive = con.execute("SELECT 1 FROM archives WHERE source=? AND symbol=? AND interval=? AND status<>'superseded' LIMIT 1", ident).fetchone()
        leased = con.execute("SELECT 1 FROM jobs WHERE source=? AND symbol=? AND interval=? AND lease_until>? LIMIT 1", (*ident, now)).fetchone()
        staged = con.execute(
            "SELECT 1 FROM jobs j JOIN staging s ON s.job_id=j.id WHERE j.source=? AND j.symbol=? AND j.interval=? LIMIT 1",
            ident,
        ).fetchone()
        if (
            not lease or lease["owner"] != owner or lease["until"] <= now
            or not ticker or not ticker["enabled"] or not current or dict(current) != daily.record()
            or later_trade or state or candles or archive or leased or staged
        ):
            raise DataError("Series, daily candle, archive, or worker lease changed; no seed was written")
        con.execute(
            "INSERT INTO series(source,symbol,interval,provider,revision,status) VALUES (?,?,?,'vci',?,'ready')",
            (*ident, revision),
        )
        records = [replace(row, revision=revision, updated_at=stamp).record() for row in rows]
        con.executemany(
            f"INSERT INTO candles({','.join(COLUMNS)}) VALUES ({','.join('?' for _ in COLUMNS)})",
            [tuple(record[column] for column in COLUMNS) for record in records],
        )
        con.execute(
            "INSERT INTO public_csv_queue(source,symbol,interval,period,updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(source,symbol,interval,period) DO UPDATE SET generation=generation+1,retry_at=0,updated_at=excluded.updated_at",
            (*ident, f"day:{day.isoformat()}", now),
        )
        for kind, detail, resolved in (
            ("verified_peer_minute_seed", evidence_key, 1),
            ("coverage_pending", "Only the independently verified minute day was seeded; historical coverage is incomplete", 0),
        ):
            con.execute(
                "INSERT INTO quality(source,symbol,interval,kind,detail,first_seen,last_seen,resolved) VALUES (?,?,?,?,?,?,?,?)",
                (*ident, kind, detail, now, now, resolved),
            )
        repo.bump(con, market=True)


async def recover(repo, archive, providers, symbol, day, *, apply=False):
    if not re.fullmatch(r"[A-Z0-9]{1,12}", symbol) or not isinstance(archive.store, S3Store):
        raise DataError("A valid VN symbol and S3 archive are required")
    start, end = parse_time(day.isoformat()), parse_time(day.isoformat()) + 86400
    if end > int(time.time()):
        raise DataError("Only a completed historical day can be recovered")
    owner = uuid.uuid4().hex
    if apply and not repo.live_claim("vn", symbol, "1m", owner, lease=120):
        raise DataError("Minute series is already being updated")
    try:
        stored = repo.read("vn", symbol, "1D", start=start, end=start)
        if len(stored) != 1:
            raise DataError("Stored daily witness is missing")
        captured = {}
        original_request = providers.request

        async def capture(provider, *args, **kwargs):
            payload = await original_request(provider, *args, **kwargs)
            captured[provider] = payload
            return payload

        providers.request = capture
        peer = await providers.vn_page("dnse", symbol, "1m", end, 500)
        raw_dnse = captured["dnse"]
        url = "https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart"
        options = {"referer": "https://trading.vietcap.com.vn/", "vn": True}
        minute = await providers.request("vci", url, json_body={"timeFrame": "ONE_MINUTE", "symbols": [symbol], "to": end - 1, "countBack": 500}, **options)
        daily = await providers.request("vci", url, json_body={"timeFrame": "ONE_DAY", "symbols": [symbol], "to": end - 1, "countBack": 10}, **options)
        rows = verify_day(symbol, day, peer, minute, daily, stored[0])
        evidence = {
            "kind": "vn_peer_minute_seed_v1", "symbol": symbol, "date": day.isoformat(),
            "verified_at_ns": time.time_ns(), "stored_daily": stored[0].record(),
            "dnse_raw": raw_dnse, "vci_minute_raw": minute, "vci_daily_raw": daily,
            "verified_rows": [row.record() for row in rows],
        }
        result = {"symbol": symbol, "date": day.isoformat(), "rows": len(rows), "status": "verified_dry_run"}
        if not apply:
            return result
        payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        digest = hashlib.sha256(payload).hexdigest()
        evidence_key = f"{archive.settings.s3_prefix}/repair-evidence/vn/{symbol}/1m/{day}/peer-{digest}.json"
        client, bucket = archive.store.client, archive.settings.s3_bucket
        key = daily_key("vn", symbol, "1m", day)
        with archive.public_csv_lock(key):
            try:
                client.head_object(Bucket=bucket, Key=key)
                raise DataError("Public minute CSV already exists; compare it manually before seeding")
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchKey", "NotFound"):
                    raise
            _put_immutable(client, bucket, evidence_key, payload, "application/json")
            revision = f"peer-vci-{digest[:24]}"
            commit_seed(repo, symbol, day, rows, stored[0], owner, evidence_key, revision)
        worker = Worker(repo, archive.settings, archive=archive)
        try:
            try:
                await worker._archive_thread(worker._publish_public_candles, "vn", symbol, "1m", [])
            except Exception as exc:
                return result | {"status": "seeded_csv_pending", "evidence_key": evidence_key, "error": type(exc).__name__}
            actual = _csv_values(archive.store.read(key), key)
            for row in rows:
                values = actual.get(datetime.fromtimestamp(row.time, UTC))
                if values is None or any(not _same(a, getattr(row, field)) for a, field in zip(values, FIELDS, strict=True)):
                    raise DataError("Published minute CSV does not match the verified rows")
            return result | {"status": "seeded_and_published", "evidence_key": evidence_key, "csv_key": key}
        finally:
            await worker.providers.close()
    finally:
        if apply:
            repo.live_release("vn", symbol, "1m", owner)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol")
    parser.add_argument("day", type=date.fromisoformat)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    # DNSE is a read-only witness, not an enabled ingestion route.
    providers = Providers(replace(settings, proxies=(), allow_direct=True))
    try:
        report = await recover(Repository(settings.database), Archive(Repository(settings.database), settings), providers, args.symbol.upper(), args.day, apply=args.apply)
        print(json.dumps(report, ensure_ascii=False))
    finally:
        await providers.close()


if __name__ == "__main__":
    asyncio.run(main())
