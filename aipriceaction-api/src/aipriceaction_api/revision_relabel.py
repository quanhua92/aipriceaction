"""Verify and relabel one Yahoo daily candle without changing its market values.

This is a narrow repair for a stranded old revision at a retention boundary.
The provider must match the target and at least three adjacent candles already
on the active revision. A transactional audit record retains the evidence.
"""

import argparse
import asyncio
import json
import math
import time
from datetime import date

from .config import Settings
from .domain import DataError, parse_time
from .providers import Providers
from .storage import COLUMNS, Repository


def _matches(left, right):
    return (
        left.time == right.time
        and left.volume == right.volume
        and all(
            math.isclose(getattr(left, name), getattr(right, name), rel_tol=1e-6, abs_tol=1e-9)
            for name in ("open", "high", "low", "close")
        )
    )


async def repair(repo, providers, symbol, day, *, apply=False):
    source, interval = "yahoo", "1D"
    stamp = parse_time(day.isoformat())
    state = repo.state(source, symbol, interval)
    if not state or state["status"] != "ready" or state["provider"] != "yahoo":
        raise DataError("The Yahoo daily series must be ready on the Yahoo provider")
    rows = repo.read(source, symbol, interval, start=stamp - 16 * 86400, end=stamp + 16 * 86400, forward=True)
    target = next((row for row in rows if row.time == stamp), None)
    if not target or target.provider != "yahoo":
        raise DataError("The exact Yahoo candle is not present in SQLite")
    if target.revision == state["revision"]:
        return {"status": "already_current", "symbol": symbol, "date": day.isoformat()}
    page = await providers.page(
        source, symbol, interval, before=stamp + 17 * 86400,
        count=50, provider="yahoo", start=stamp - 16 * 86400,
    )
    observed = {row.time: row for row in page.rows}
    if page.provider != "yahoo" or stamp not in observed or not _matches(target, observed[stamp]):
        raise DataError("The target candle does not match the current Yahoo observation")
    references = [
        row for row in rows
        if row.revision == state["revision"] and row.provider == "yahoo"
        and row.time in observed and _matches(row, observed[row.time])
    ]
    if len(references) < 3:
        raise DataError("At least three active-revision reference candles must match Yahoo")
    evidence = {
        "kind": "yahoo_revision_relabel_v1", "source": source, "symbol": symbol,
        "interval": interval, "date": day.isoformat(), "from_revision": target.revision,
        "to_revision": state["revision"], "relative_price_tolerance": 1e-6,
        "volume_exact": True, "stored": target.record(), "observed": observed[stamp].record(),
        "references": [
            {"stored": row.record(), "observed": observed[row.time].record()}
            for row in references
        ],
        "verified_at_ns": time.time_ns(),
    }
    if not apply:
        return {"status": "verified_dry_run", "evidence": evidence}

    with repo.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        current_state = con.execute(
            "SELECT * FROM series WHERE source=? AND symbol=? AND interval=?",
            (source, symbol, interval),
        ).fetchone()
        current = con.execute(
            f"SELECT {','.join(COLUMNS)} FROM candles WHERE source=? AND symbol=? AND interval=? AND time=?",
            (source, symbol, interval, stamp),
        ).fetchone()
        active = con.execute(
            "SELECT 1 FROM jobs WHERE source=? AND symbol=? AND interval=? AND kind='repair' AND status IN ('pending','running')",
            (source, symbol, interval),
        ).fetchone()
        if not current_state or dict(current_state) != state or not current or dict(current) != target.record() or active:
            raise DataError("Series changed during verification or a repair is active; retry later")
        con.execute(
            "UPDATE candles SET revision=? WHERE source=? AND symbol=? AND interval=? AND time=?",
            (state["revision"], source, symbol, interval, stamp),
        )
        now = int(time.time())
        con.execute(
            "INSERT INTO quality(source,symbol,interval,kind,detail,first_seen,last_seen) VALUES (?,?,?,'revision_relabel_verified',?,?,?)",
            (source, symbol, interval, json.dumps(evidence, sort_keys=True, separators=(",", ":")), now, now),
        )
        repo.bump(con, market=True)
    return {"status": "applied", "evidence": evidence}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol")
    parser.add_argument("day", type=date.fromisoformat)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    repo = Repository(settings.database)
    repo.initialize()
    providers = Providers(settings)

    async def run():
        try:
            return await repair(repo, providers, args.symbol, args.day, apply=args.apply)
        finally:
            await providers.close()

    print(json.dumps(asyncio.run(run()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
