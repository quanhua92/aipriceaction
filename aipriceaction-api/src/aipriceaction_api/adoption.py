"""Explicit migration adoption after completed-candle verification.

This licenses appending a chosen provider to one frozen legacy-API snapshot.
It is not a declaration about the provider's lifetime adjustment policy.
Optional bounded corrections require independent witnesses and preserve the
original records; unchanged imported candles retain their source provenance.
"""

import asyncio
import hashlib
import json
import math
import time
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime

from .domain import Candle, DataError, completed_vn_sessions


def checksum(rows):
    return hashlib.sha256(
        json.dumps(
            [
                [
                    r.time,
                    r.open,
                    r.high,
                    r.low,
                    r.close,
                    r.volume,
                    r.provider,
                    r.revision,
                    r.updated_at,
                ]
                for r in rows
            ],
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def validate_complete_sessions(record, evidence):
    """Replay a bounded certificate, including the compared original candles."""
    proof = evidence["complete_session_proof"]
    if (
        any(
            type(proof[key]) is not list
            for key in ("minute", "snapshot_minute", "daily", "snapshot_daily")
        )
        or not 5 <= len(proof["minute"]) <= 2000
        or len(proof["snapshot_minute"]) != len(proof["minute"])
        or not 5 <= len(proof["daily"]) <= 20
        or len(proof["snapshot_daily"]) != len(proof["daily"])
    ):
        raise DataError("Invalid complete-session evidence size")
    incoming, original, daily, saved_daily = (
        [Candle(**row).validate() for row in proof[key]]
        for key in ("minute", "snapshot_minute", "daily", "snapshot_daily")
    )
    if not 5 <= len(incoming) <= 2000 or len(original) != len(incoming):
        raise DataError("Invalid complete-session evidence size")
    completed = completed_vn_sessions(
        datetime.fromtimestamp(evidence["verified_at_ns"] / 1_000_000_000, UTC)
    )
    if evidence["completed_before"] != completed:
        raise DataError("Invalid complete-session finality")
    times = [row.time for row in incoming]
    if times != sorted(set(times)) or times[-1] >= completed:
        raise DataError("Invalid complete-session timestamps")
    if (
        len(incoming) != evidence["matched_rows"]
        or times[0] != evidence["overlap_start"]
        or times[-1] != evidence["overlap_end"]
        or checksum(incoming) != evidence["overlap_checksum"]
        or checksum(saved_daily) != evidence["daily_snapshot_checksum"]
    ):
        raise DataError("Invalid complete-session checksums or bounds")
    corrections = []
    corroborated = evidence["kind"] == "corroborated_complete_sessions"
    groups = defaultdict(list)
    for row, previous in zip(incoming, original, strict=True):
        if (
            (row.source, row.symbol, row.interval, row.provider)
            != ("vn", record["symbol"], "1m", record["provider"])
            or (
                previous.source,
                previous.symbol,
                previous.interval,
                previous.provider,
                previous.revision,
            )
            != ("vn", record["symbol"], "1m", "legacy-api", record["revision"])
            or previous.time != row.time
            or row.volume <= 0
            or previous.volume <= 0
            or row.time % 60
            or datetime.fromtimestamp(row.time, UTC).weekday() >= 5
            or not 0 < previous.updated_at <= evidence["verified_at_ns"]
        ):
            raise DataError("Invalid complete-session original/provider candles")
        if any(
            not math.isclose(getattr(row, key), getattr(previous, key), rel_tol=0, abs_tol=1e-8)
            for key in ("open", "high", "low", "close", "volume")
        ):
            if not corroborated:
                raise DataError("Invalid complete-session original/provider candles")
            corrections.append(row)
        groups[row.time // 86400 * 86400].append(row)
    if corroborated:
        witnesses = proof["corroborating_minute"]
        other = evidence["corroborating_provider"]
        if (
            other not in {"vps", "vndirect", "dnse"}
            or other == record["provider"]
            or type(witnesses) is not list
            or not corrections
            or len(witnesses) != len(corrections)
            or evidence["corrected_rows"] != len(corrections)
        ):
            raise DataError("Invalid minute correction witnesses")
        for row, raw in zip(corrections, witnesses, strict=True):
            witness = Candle(**raw).validate()
            if (
                witness.source,
                witness.symbol,
                witness.interval,
                witness.provider,
                witness.time,
            ) != ("vn", record["symbol"], "1m", other, row.time) or any(
                not math.isclose(getattr(row, key), getattr(witness, key), rel_tol=0, abs_tol=1e-8)
                for key in ("open", "high", "low", "close", "volume")
            ):
                raise DataError("Minute correction is not independently corroborated")

        def aggregates(rows):
            buckets = defaultdict(list)
            for row in rows:
                buckets[row.time // 900 * 900].append(row)
            return [
                [
                    values[0].open,
                    max(r.high for r in values),
                    min(r.low for r in values),
                    values[-1].close,
                    sum(r.volume for r in values),
                ]
                for values in buckets.values()
            ]

        for before, after in zip(aggregates(original), aggregates(incoming), strict=True):
            if any(
                not math.isclose(a, b, rel_tol=0, abs_tol=1e-8)
                for a, b in zip(before, after, strict=True)
            ):
                raise DataError("Minute corrections change original 15-minute OHLCV")
    days = sorted(groups)
    if (
        len(days) != evidence["completed_sessions"]
        or len(days) < 5
        or [row.time for row in daily] != days
        or [row.time for row in saved_daily] != days
    ):
        raise DataError("Invalid complete-session daily coverage")
    for current, previous in zip(daily, saved_daily, strict=True):
        rows = groups[current.time]
        values = (rows[0].open, max(r.high for r in rows), min(r.low for r in rows), rows[-1].close)
        if (
            (current.source, current.symbol, current.interval, current.provider)
            != ("vn", record["symbol"], "1D", record["provider"])
            or (
                previous.source,
                previous.symbol,
                previous.interval,
                previous.provider,
                previous.revision,
            )
            != (
                "vn",
                record["symbol"],
                "1D",
                evidence["daily_snapshot_provider"],
                evidence["daily_snapshot_revision"],
            )
            or previous.provider not in {"vps", "vndirect", "dnse"}
            or not 0 < previous.updated_at <= evidence["verified_at_ns"]
            or any(
                not math.isclose(value, getattr(current, key), rel_tol=0, abs_tol=1e-8)
                or not math.isclose(value, getattr(previous, key), rel_tol=0, abs_tol=1e-8)
                for key, value in zip(("open", "high", "low", "close"), values, strict=True)
            )
            or sum(row.volume for row in rows) != current.volume
            or current.volume != previous.volume
        ):
            raise DataError("Complete-session OHLCV disagrees with verified daily candles")


async def adopt_snapshot(
    repo,
    providers,
    symbol,
    provider,
    execute=False,
    complete_sessions=False,
    corroborate=None,
    source="vn",
    iv="1m",
):
    if iv not in {"1m", "1h"} or source not in {"vn", "yahoo"}:
        raise DataError("Intraday adoption supports VN/Yahoo minute and hourly snapshots", 400)
    if iv != "1m" and (complete_sessions or corroborate is not None):
        raise DataError("Complete-session and correction proofs require minute snapshots", 400)
    allowed = (
        providers.settings.vn_providers
        if source == "vn"
        else ("yahoo",)
        if source == "yahoo"
        else ()
    )
    if provider not in allowed:
        raise DataError("Adoption requires a configured provider for its market source", 400)
    if source != "vn" and (complete_sessions or corroborate is not None):
        raise DataError("Complete-session and correction proofs currently require VN data", 400)
    if corroborate is not None and (
        not complete_sessions
        or corroborate not in providers.settings.vn_providers
        or corroborate == provider
    ):
        raise DataError(
            "Corrections require complete sessions and a distinct configured corroborating provider",
            400,
        )
    state = repo.state(source, symbol, iv)
    if not state or state["status"] != "ready" or state["provider"] != "legacy-api":
        raise DataError("Adoption requires a ready legacy-api intraday snapshot", 400)
    rows = repo.read(source, symbol, iv)
    if not rows or {(r.provider, r.revision) for r in rows} != {("legacy-api", state["revision"])}:
        raise DataError("Adoption requires one coherent imported snapshot")
    step = 3600 if iv == "1h" else 60
    completed = completed_vn_sessions() if source == "vn" else int(time.time()) // step * step
    minimum = 100 if iv == "1h" else 1000
    # Futures trade across much longer UTC sessions than stocks. Trimming the
    # same bounded six-day Yahoo response to 2,000 minutes can discard the
    # five date partitions required below even when the full overlap is exact.
    count = 10000 if source == "yahoo" and iv == "1m" and symbol.endswith("=F") else minimum * 2
    page = await asyncio.wait_for(
        providers.page(source, symbol, iv, count=count, provider=provider), timeout=90
    )
    if page.provider != provider or any(
        (r.source, r.symbol, r.interval, r.provider) != (source, symbol, iv, provider)
        for r in page.rows
    ):
        raise DataError("Provider identity changed during snapshot verification")
    incoming = [r for r in page.rows if r.time < completed and r.time <= rows[-1].time]
    if iv == "1h" and [r.time for r in incoming] != sorted({r.time for r in incoming}):
        raise DataError("Hourly overlap must contain ordered unique timestamps")
    by_time = {r.time: r for r in rows}
    corrections = []
    for row in incoming:
        row.validate()
        if iv == "1h" and source == "yahoo" and row.time % 3600:
            raise DataError("Native hourly overlap must use whole-hour timestamps")
        previous = by_time.get(row.time)
        if previous is None:
            raise DataError("Provider timestamp is absent from the imported snapshot")
        if (
            any(
                not math.isclose(getattr(row, k), getattr(previous, k), rel_tol=0, abs_tol=1e-8)
                for k in ("open", "high", "low", "close")
            )
            or previous.volume != row.volume
        ):
            if corroborate is None:
                raise DataError(
                    "Provider disagrees with completed snapshot OHLCV; retained-window recovery required"
                )
            corrections.append(row)
    sessions = {r.time // 86400 for r in incoming}
    published_completed = [r.time for r in rows if r.time < completed]
    if (
        (len(incoming) < minimum and not complete_sessions)
        or len(sessions) < 5
        or not incoming
        or not published_completed
        or incoming[-1].time < max(published_completed)
    ):
        raise DataError(
            f"Adoption requires at least {minimum} exact candles across five completed sessions through the published tail"
        )
    evidence = {
        "kind": "exact_snapshot_overlap",
        "matched_rows": len(incoming),
        "completed_sessions": len(sessions),
        "overlap_start": incoming[0].time,
        "overlap_end": incoming[-1].time,
        "snapshot_rows": len(rows),
        "snapshot_start": rows[0].time,
        "snapshot_end": rows[-1].time,
        "snapshot_checksum": checksum(rows),
        "overlap_checksum": checksum(incoming),
        "verified_at_ns": time.time_ns(),
        "scope": "Exact observed overlap permits append; no inferred historical scaling",
    }
    if source == "yahoo":
        evidence["completed_before"] = completed
        evidence["scope"] = (
            "Exact observed overlap across five completed UTC date partitions permits append; no inferred historical scaling or complete trading-calendar certification"
        )
    elif iv == "1h":
        evidence["kind"] = "exact_vn_hourly_snapshot_overlap"
        evidence["completed_before"] = completed
        evidence["scope"] = (
            "Exact hourly overlap across five observed completed VN session dates permits append; original minute-aligned labels are preserved, with no inferred scaling or complete session/calendar certification"
        )
    daily_state = None
    if complete_sessions:
        # Include every original timestamp in each compared day. A provider page
        # truncated at the beginning or missing an interior candle cannot pass.
        first = incoming[0].time // 86400 * 86400
        original = [r for r in rows if first <= r.time < completed]
        if [r.time for r in original] != [r.time for r in incoming]:
            raise DataError(
                "Complete-session provider timestamps differ from the full snapshot window"
            )
        daily_state = repo.state("vn", symbol, "1D")
        if (
            not daily_state
            or daily_state["status"] != "ready"
            or daily_state["provider"] not in providers.settings.vn_providers
        ):
            raise DataError("Complete-session adoption requires ready selected-provider daily data")
        last = incoming[-1].time // 86400 * 86400
        saved_daily = repo.read("vn", symbol, "1D", first, last)
        daily_page = await asyncio.wait_for(
            providers.page("vn", symbol, "1D", before=last + 86400, count=20, provider=provider),
            timeout=90,
        )
        if daily_page.provider != provider:
            raise DataError("Daily provider identity changed during snapshot verification")
        daily = [r for r in daily_page.rows if first <= r.time <= last]
        evidence.update(
            {
                "kind": "exact_complete_sessions",
                "completed_before": completed,
                "daily_snapshot_checksum": checksum(saved_daily),
                "daily_snapshot_provider": daily_state["provider"],
                "daily_snapshot_revision": daily_state["revision"],
                "complete_session_proof": {
                    "minute": [r.record() for r in incoming],
                    "snapshot_minute": [r.record() for r in original],
                    "daily": [r.record() for r in daily],
                    "snapshot_daily": [r.record() for r in saved_daily],
                },
                "scope": "Every observed minute in five or more completed sessions matches the snapshot and fresh/retained daily OHLCV; no inferred historical scaling",
            }
        )
        if corrections:
            witness_page = await asyncio.wait_for(
                providers.page(
                    "vn", symbol, "1m", before=last + 86400, count=2000, provider=corroborate
                ),
                timeout=90,
            )
            if witness_page.provider != corroborate:
                raise DataError("Corroborating provider identity changed")
            witnesses = {r.time: r for r in witness_page.rows}
            if any(r.time not in witnesses for r in corrections):
                raise DataError("A minute correction lacks its second-provider witness")
            evidence.update(
                {
                    "kind": "corroborated_complete_sessions",
                    "corroborating_provider": corroborate,
                    "corrected_rows": len(corrections),
                    "scope": "Every changed native candle is corroborated independently; original 15-minute and fresh/retained daily OHLCV are preserved; no inferred scaling",
                }
            )
            evidence["complete_session_proof"]["corroborating_minute"] = [
                witnesses[r.time].record() for r in corrections
            ]
    record = {
        "source": source,
        "symbol": symbol,
        "interval": iv,
        "revision": state["revision"],
        "snapshot_provider": state["provider"],
        "provider": provider,
        "evidence": json.dumps(evidence, sort_keys=True),
    }
    repo.validate_adoption(record)
    if execute:
        with repo.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            current = con.execute(
                "SELECT * FROM series WHERE source=? AND symbol=? AND interval=?",
                (source, symbol, iv),
            ).fetchone()
            published = con.execute(
                "SELECT * FROM candles WHERE source=? AND symbol=? AND interval=? ORDER BY time",
                (source, symbol, iv),
            ).fetchall()
            from .domain import Candle

            if (
                not current
                or dict(current) != state
                or checksum([Candle(**dict(r)) for r in published]) != evidence["snapshot_checksum"]
            ):
                raise DataError(
                    "Snapshot changed during adoption verification; retry with a fresh read"
                )
            if complete_sessions:
                current_daily = con.execute(
                    "SELECT * FROM series WHERE source='vn' AND symbol=? AND interval='1D'",
                    (symbol,),
                ).fetchone()
                daily_rows = con.execute(
                    "SELECT * FROM candles WHERE source='vn' AND symbol=? AND interval='1D' AND time BETWEEN ? AND ? ORDER BY time",
                    (symbol, first, last),
                ).fetchall()
                if (
                    not current_daily
                    or dict(current_daily) != daily_state
                    or checksum([Candle(**dict(r)) for r in daily_rows])
                    != evidence["daily_snapshot_checksum"]
                ):
                    raise DataError("Daily snapshot changed during adoption verification")
            if con.execute(
                "SELECT 1 FROM jobs WHERE source=? AND symbol=? AND interval=? AND status IN ('pending','running') AND lease_until>?",
                (source, symbol, iv, int(time.time())),
            ).fetchone():
                raise DataError(
                    "A retained-window job is active; finish its bounded page before adoption"
                )
            con.execute(
                "INSERT INTO snapshot_adoptions VALUES (?,?,?,?,?,?,?)", tuple(record.values())
            )
            if corrections:
                stamp = time.time_ns()
                for row in corrections:
                    updated = replace(row, revision=state["revision"], updated_at=stamp)
                    changed = con.execute(
                        "UPDATE candles SET open=?,high=?,low=?,close=?,volume=?,provider=?,updated_at=? WHERE source='vn' AND symbol=? AND interval='1m' AND time=?",
                        (
                            updated.open,
                            updated.high,
                            updated.low,
                            updated.close,
                            updated.volume,
                            updated.provider,
                            updated.updated_at,
                            symbol,
                            updated.time,
                        ),
                    ).rowcount
                    if changed != 1:
                        raise DataError("Minute correction target changed during publication")
            # Unchanged snapshot prices/provenance stay intact. Corrections have
            # independent witnesses; future updates use the verified provider.
            con.execute(
                "UPDATE series SET provider=? WHERE source=? AND symbol=? AND interval=?",
                (provider, source, symbol, iv),
            )
            jobs = con.execute(
                "SELECT id FROM jobs WHERE source=? AND symbol=? AND interval=? AND kind IN ('bootstrap','repair') AND status NOT IN ('complete','cancelled')",
                (source, symbol, iv),
            ).fetchall()
            for job in jobs:
                con.execute("DELETE FROM staging WHERE job_id=?", (job["id"],))
                con.execute(
                    "UPDATE jobs SET status='cancelled',lease_owner=NULL,lease_until=0 WHERE id=?",
                    (job["id"],),
                )
            schedule = "next_1h" if iv == "1h" else "next_1m"
            con.execute(
                f"UPDATE tickers SET {schedule}=0 WHERE source=? AND symbol=?", (source, symbol)
            )
            repo.bump(con)
    return {
        "dry_run": not execute,
        "provider": provider,
        "revision": state["revision"],
        "evidence": evidence,
    }
