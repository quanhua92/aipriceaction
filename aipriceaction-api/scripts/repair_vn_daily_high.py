"""Verify and correct one current-session VN daily high, with before-images.

The default is a read-only preview. Apply requires four matching price sources,
the original volume to match VPS and KBS, an unchanged SQLite row, and existing
public daily/yearly CSVs. Only the high and its provider provenance are changed.
"""

import argparse
import asyncio
import csv
import hashlib
import io
import json
import math
import re
import sys
import time
import uuid
from contextlib import ExitStack
from datetime import UTC, date, datetime
from datetime import time as clock_time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx

from aipriceaction_api.archive import Archive, S3Store
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, parse_time
from aipriceaction_api.providers import Providers
from aipriceaction_api.public_csv import (
    PublicObject,
    _csv_values,
    _format_float,
    daily_key,
    publish_object,
    rust_content_hash,
    yearly_key,
)
from aipriceaction_api.repair_yahoo_year import _put_immutable
from aipriceaction_api.storage import COLUMNS, Repository

FIELDS = ("open", "high", "low", "close")
QUOTE_FIELDS = {"open": "OP", "high": "HI", "low": "LO", "close": "CP"}


def same(a, b):
    return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=0.0)


def verify_evidence(old, vps, vndirect, vci, quote, session):
    expected = parse_time(session.isoformat())
    if any(
        (row.source, row.symbol, row.interval, row.time) != ("vn", old.symbol, "1D", expected)
        for row in (old, vps, vndirect, vci)
    ):
        raise DataError("Daily sources do not match the requested ticker and session")
    if (vps.provider, vndirect.provider, vci.provider) != ("vps", "vndirect", "vci"):
        raise DataError("Daily witnesses do not have the expected providers")
    try:
        quoted_date = datetime.strptime(quote["TD"], "%d/%m/%Y").date()
        if quote["SB"] != old.symbol or quoted_date != session or int(quote["TT"]) <= 0:
            raise DataError("KBS quote does not prove a trade for the requested session")
        for field in FIELDS:
            value = getattr(vps, field)
            if not all(
                same(value, observed)
                for observed in (
                    getattr(vndirect, field),
                    getattr(vci, field),
                    quote[QUOTE_FIELDS[field]],
                )
            ):
                raise DataError(f"Sources disagree on daily {field}")
        if int(quote["TT"]) != vps.volume or old.volume != vps.volume:
            raise DataError("The original volume is not confirmed by VPS and KBS")
        if any(not same(getattr(old, f), getattr(vps, f)) for f in ("open", "low", "close")):
            raise DataError("More than the original high differs")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise DataError("Malformed or incomplete source evidence") from exc
    if same(old.high, vps.high):
        return False
    old.validate()
    vps.validate()
    vndirect.validate()
    vci.validate()
    return True


def plan_csv(body, key, old, high):
    values = _csv_values(body, key)
    stamp = datetime.fromtimestamp(old.time, UTC)
    expected = (old.open, old.high, old.low, old.close, old.volume)
    current = values.get(stamp)
    if (
        not current
        or current[-1] != expected[-1]
        or any(not same(a, b) for a, b in zip(current[:-1], expected[:-1], strict=True))
    ):
        raise DataError(f"Public CSV does not match the original SQLite candle: {key}")
    records = list(csv.reader(io.StringIO(body.decode("utf-8"), newline="")))
    changed = 0
    for record in records:
        parsed = datetime.fromisoformat(record[0])
        parsed = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        if parsed == stamp:
            record[2] = _format_float(high)
            changed += 1
    if changed != 1:
        raise DataError("Public CSV must contain exactly one target candle")
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(records)
    updated = output.getvalue().encode("utf-8")
    values[stamp] = (old.open, high, old.low, old.close, old.volume)
    fingerprint = [
        SimpleNamespace(time=int(when.timestamp()), close=row[3], volume=row[4])
        for when, row in sorted(values.items())
    ]
    return PublicObject(
        key,
        updated,
        "text/csv",
        hashlib.sha256(updated).hexdigest(),
        rust_content_hash(fingerprint),
        len(values),
    )


async def source_evidence(providers, symbol, session):
    stamp = parse_time(session.isoformat())
    pages = []
    for provider in ("vps", "vndirect"):
        page = await providers.page(
            "vn", symbol, "1D", before=stamp + 86400, count=5, provider=provider, start=stamp
        )
        if not page.rows or page.rows[-1].time != stamp:
            raise DataError(f"{provider} did not return the requested session")
        pages.append(page.rows[-1])
    payload = await providers.request(
        "vci",
        "https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart",
        referer="https://trading.vietcap.com.vn/",
        vn=True,
        json_body={
            "timeFrame": "ONE_DAY",
            "symbols": [symbol],
            "to": stamp + 86400,
            "countBack": 5,
        },
    )
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list) or len(data) != 1 or data[0].get("symbol") != symbol:
        raise DataError("Vietcap returned an invalid daily envelope")
    body = data[0]
    arrays = [body.get(k) for k in ("t", "o", "h", "l", "c", "v")]
    if any(not isinstance(a, list) for a in arrays) or len({len(a) for a in arrays}) != 1:
        raise DataError("Vietcap returned invalid daily arrays")
    indexes = [i for i, value in enumerate(arrays[0]) if int(value) == stamp]
    if len(indexes) != 1:
        raise DataError("Vietcap did not return exactly one target session")
    i = indexes[0]
    volume = float(arrays[5][i])
    if not math.isfinite(volume) or volume < 0 or volume != int(volume):
        raise DataError("Vietcap returned an invalid volume")
    vci = Candle(
        "vn",
        symbol,
        "1D",
        stamp,
        *(float(a[i]) for a in arrays[1:5]),
        int(volume),
        "vci",
    )
    async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=True) as client:
        response = await client.post(
            "https://kbbuddywts.kbsec.com.vn/iis-server/investment/stock/iss",
            json={"code": symbol},
            headers={"Accept": "application/json", "x-lang": "vi", "User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        quotes = response.json()
    if not isinstance(quotes, list) or len(quotes) != 1 or not isinstance(quotes[0], dict):
        raise DataError("KBS returned an invalid quote envelope")
    return pages[0], pages[1], vci, quotes[0]


async def repair(repo, archive, providers, symbol, session, *, apply=False):
    if not re.fullmatch(r"[A-Z0-9]{1,12}", symbol):
        raise DataError("Invalid VN ticker")
    if not isinstance(archive.store, S3Store):
        raise DataError("This correction requires the configured S3 archive")
    now = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
    if session != now.date() or now.time() < clock_time(15, 15):
        raise DataError("Only the completed current VN session can be corrected")
    stamp = parse_time(session.isoformat())
    state = repo.state("vn", symbol, "1D")
    rows = repo.read("vn", symbol, "1D", start=stamp, end=stamp)
    if not state or state["status"] != "ready" or len(rows) != 1:
        raise DataError("The target requires one ready SQLite daily candle")
    old = rows[0]
    if old.revision != state["revision"]:
        raise DataError("SQLite target has an incompatible revision")
    vps, vndirect, vci, quote = await source_evidence(providers, symbol, session)
    changed = verify_evidence(old, vps, vndirect, vci, quote, session)
    if not changed:
        return {"status": "already_current", "symbol": symbol, "date": session.isoformat()}

    keys = [daily_key("vn", symbol, "1D", session), yearly_key("vn", symbol, "1D", session.year)]
    client, bucket = archive.store.client, archive.settings.s3_bucket
    owner = uuid.uuid4().hex
    if apply and not repo.live_claim("vn", symbol, "1D", owner, lease=300):
        raise DataError("The ticker is being updated; retry later")
    try:
        with ExitStack() as stack:
            # A day and year can share a lock stripe; acquire each stripe once.
            stripes = {}
            for key in keys:
                stripe = int.from_bytes(hashlib.sha256(key.encode()).digest()[:4], "big") % 64
                stripes.setdefault(stripe, key)
            for stripe in sorted(stripes):
                stack.enter_context(archive.public_csv_lock(stripes[stripe]))
            plans = []
            for key in keys:
                response = client.get_object(Bucket=bucket, Key=key)
                stream = response["Body"]
                try:
                    body = stream.read()
                finally:
                    stream.close()
                if len(body) > 8 * 1024 * 1024:
                    raise DataError("Public CSV is larger than the correction budget")
                item = plan_csv(body, key, old, vps.high)
                sha = hashlib.sha256(body).hexdigest()
                name = "day" if "/1D/" in key else "year"
                backup = f"{archive.settings.s3_prefix}/repair-evidence/vn/{symbol}/1D/{session}/{name}-{sha}.csv"
                plans.append(
                    {
                        "item": item,
                        "body": body,
                        "etag": response["ETag"],
                        "backup": backup,
                        "sha": sha,
                    }
                )
            evidence = {
                "kind": "vn_daily_high_consensus_v1",
                "symbol": symbol,
                "date": session.isoformat(),
                "original": old.record(),
                "vps": vps.record(),
                "vndirect": vndirect.record(),
                "vci": vci.record(),
                "kbs": {k: quote.get(k) for k in ("SB", "TD", "OP", "HI", "LO", "CP", "TT", "t")},
                "public_before_images": [
                    {
                        "key": p["item"].key,
                        "etag": p["etag"],
                        "sha256": p["sha"],
                        "backup": p["backup"],
                    }
                    for p in plans
                ],
                "verified_at_ns": time.time_ns(),
            }
            if not apply:
                return {"status": "verified_dry_run", "evidence": evidence}
            for plan in plans:
                _put_immutable(client, bucket, plan["backup"], plan["body"], "text/csv")
            seed = json.dumps(
                evidence, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
            evidence_key = (
                f"{archive.settings.s3_prefix}/repair-evidence/vn/{symbol}/1D/{session}/"
                f"evidence-{hashlib.sha256(seed).hexdigest()}.json"
            )
            evidence["evidence_key"] = evidence_key
            proof = json.dumps(
                evidence, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
            _put_immutable(client, bucket, evidence_key, proof, "application/json")
            periods = [f"day:{session.isoformat()}", f"year:{session.year}"]
            with repo.connect() as con:
                con.execute("BEGIN IMMEDIATE")
                current_state = con.execute(
                    "SELECT * FROM series WHERE source='vn' AND symbol=? AND interval='1D'",
                    (symbol,),
                ).fetchone()
                current = con.execute(
                    f"SELECT {','.join(COLUMNS)} FROM candles WHERE source='vn' AND symbol=? AND interval='1D' AND time=?",
                    (symbol, stamp),
                ).fetchone()
                lease = con.execute(
                    "SELECT owner,until FROM live_leases WHERE source='vn' AND symbol=? AND interval='1D'",
                    (symbol,),
                ).fetchone()
                active = con.execute(
                    "SELECT 1 FROM jobs WHERE source='vn' AND symbol=? AND interval='1D' AND kind='repair' AND status IN ('pending','running')",
                    (symbol,),
                ).fetchone()
                archived = con.execute(
                    "SELECT 1 FROM archives WHERE source='vn' AND symbol=? AND interval='1D' AND status IN ('published','pending_repair') AND start<=? AND end>=?",
                    (symbol, stamp, stamp),
                ).fetchone()
                if (
                    not current_state
                    or dict(current_state) != state
                    or not current
                    or dict(current) != old.record()
                    or not lease
                    or lease[0] != owner
                    or lease[1] < int(time.time())
                    or active
                    or archived
                ):
                    raise DataError(
                        "Target, lease, repair state or archive changed during verification"
                    )
                con.execute(
                    "UPDATE candles SET high=?,provider='vps',updated_at=? WHERE source='vn' AND symbol=? AND interval='1D' AND time=?",
                    (vps.high, time.time_ns(), symbol, stamp),
                )
                now_sec = int(time.time())
                con.execute(
                    "INSERT INTO quality(source,symbol,interval,kind,detail,first_seen,last_seen,resolved) VALUES ('vn',?,'1D','verified_daily_high_correction',?,?,?,1)",
                    (
                        symbol,
                        json.dumps(evidence, sort_keys=True, separators=(",", ":")),
                        now_sec,
                        now_sec,
                    ),
                )
                for period in periods:
                    con.execute(
                        "INSERT INTO public_csv_queue(source,symbol,interval,period,updated_at) VALUES ('vn',?,'1D',?,?) ON CONFLICT(source,symbol,interval,period) DO UPDATE SET generation=public_csv_queue.generation+1,retry_at=0,updated_at=excluded.updated_at",
                        (symbol, period, now_sec),
                    )
                generations = {
                    r[0]: r[1]
                    for r in con.execute(
                        "SELECT period,generation FROM public_csv_queue WHERE source='vn' AND symbol=? AND interval='1D'",
                        (symbol,),
                    )
                }
                repo.bump(con, market=True)
            publications = []
            for period, plan in zip(periods, plans, strict=True):
                try:
                    result = publish_object(
                        client, bucket, plan["item"], expected_etag=plan["etag"]
                    )
                    with repo.connect() as con:
                        con.execute(
                            "DELETE FROM public_csv_queue WHERE source='vn' AND symbol=? AND interval='1D' AND period=? AND generation=?",
                            (symbol, period, generations[period]),
                        )
                    publications.append({"key": plan["item"].key, "uploaded": result["uploaded"]})
                except Exception as exc:
                    publications.append(
                        {
                            "key": plan["item"].key,
                            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                        }
                    )
            pending = any("error" in item for item in publications)
            if pending:
                repo.finding(
                    "vn",
                    symbol,
                    "1D",
                    "archive_csv_repair_pending",
                    "Verified daily high corrected; public CSV retry pending",
                )
            return {
                "status": "applied_csv_pending" if pending else "applied",
                "evidence": evidence,
                "publications": publications,
            }
    finally:
        if apply:
            repo.live_release("vn", symbol, "1D", owner)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol")
    parser.add_argument("date", type=date.fromisoformat)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    repo = Repository(settings.database)
    archive = Archive(repo, settings)
    providers = Providers(settings)

    async def run():
        try:
            return await repair(repo, archive, providers, args.symbol, args.date, apply=args.apply)
        finally:
            await providers.close()

    result = asyncio.run(run())
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["status"] == "applied_csv_pending":
        sys.exit(2)


if __name__ == "__main__":
    main()
