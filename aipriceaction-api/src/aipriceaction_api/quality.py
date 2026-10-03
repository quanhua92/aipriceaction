"""Bounded SQL audits of observed coverage, without inventing a market calendar."""

import json
import time
from datetime import UTC, datetime

from .domain import completed_vn_sessions

KINDS = (
    "audit_gap",
    "audit_observed_sessions",
    "audit_stale",
    "audit_non_session_day",
    "audit_interval_basis",
)


def interval_basis_findings(con, completed):
    """Compare observed VN minute sessions with daily prices, without corrections.

    A price disagreement can reflect an adjustment convention, a session mismatch,
    or incomplete ingestion. It does not identify a dividend or license scaling.
    Materialize the small session aggregate before indexed timestamp lookups;
    allowing SQLite to reorder the joins can multiply whole minute-series scans.
    """
    rows = con.execute(
        """WITH minute AS MATERIALIZED (
            SELECT c.symbol,c.time/86400*86400 AS day,MIN(c.time) AS first,
                MAX(c.time) AS last,MAX(c.high) AS high,MIN(c.low) AS low,
                COUNT(*) AS minute_rows
            FROM candles c JOIN tickers t USING(source,symbol)
            WHERE c.source='vn' AND c.interval='1m' AND c.time<? AND t.enabled=1
            GROUP BY c.symbol,c.time/86400*86400
        ), compared AS MATERIALIZED (
            SELECT m.*,a.open,z.close,d.open AS daily_open,d.high AS daily_high,
                d.low AS daily_low,d.close AS daily_close,d.provider AS daily_provider,
                MAX(ABS(a.open/d.open-1),ABS(m.high/d.high-1),
                    ABS(m.low/d.low-1),ABS(z.close/d.close-1)) AS difference
            FROM minute m
            CROSS JOIN candles a ON a.source='vn' AND a.symbol=m.symbol
                AND a.interval='1m' AND a.time=m.first
            CROSS JOIN candles z ON z.source='vn' AND z.symbol=m.symbol
                AND z.interval='1m' AND z.time=m.last
            CROSS JOIN candles d ON d.source='vn' AND d.symbol=m.symbol
                AND d.interval='1D' AND d.time=m.day
        ) SELECT * FROM compared WHERE difference>0.01 ORDER BY symbol,day""",
        (completed,),
    )
    by_symbol = {}
    for row in rows:
        by_symbol.setdefault(row["symbol"], []).append(
            {
                "date": datetime.fromtimestamp(row["day"], UTC).date().isoformat(),
                "minute_rows": row["minute_rows"],
                "minute_ohlc": [row[key] for key in ("open", "high", "low", "close")],
                "daily_ohlc": [row["daily_" + key] for key in ("open", "high", "low", "close")],
                "daily_provider": row["daily_provider"],
                "max_relative_difference": row["difference"],
            }
        )
    return [
        (
            "vn",
            symbol,
            "1m",
            "audit_interval_basis",
            json.dumps(
                {
                    "scope": "completed local minute sessions versus observed daily prices",
                    "relative_threshold": 0.01,
                    "sessions": sessions,
                    "review": "Verify upstream adjustment conventions and session coverage; do not infer a factor or overwrite candles from this comparison",
                },
                sort_keys=True,
            ),
        )
        for symbol, sessions in by_symbol.items()
    ]


def audit(repo, now=None):
    now = int(time.time()) if now is None else int(now)
    completed = completed_vn_sessions(datetime.fromtimestamp(now, UTC))
    checked, findings = [], []
    with repo.connect() as con:
        con.execute("BEGIN")  # One consistent WAL snapshot while workers write.
        findings.extend(interval_basis_findings(con, completed))
        series = con.execute(
            """SELECT c.source,c.symbol,c.interval,MIN(c.time) first,MAX(c.time) last
            FROM candles c JOIN tickers t USING(source,symbol) WHERE t.enabled=1
            GROUP BY c.source,c.symbol,c.interval"""
        ).fetchall()
        for row in series:
            ident = (row["source"], row["symbol"], row["interval"])
            checked.append(ident)
            step = {"1D": 86400, "1h": 3600, "1m": 60}[row["interval"]]
            if row["source"] == "crypto" or row["interval"] == "1D":
                threshold = step if row["source"] == "crypto" else 7 * 86400
                gaps = con.execute(
                    """SELECT previous,time FROM (
                    SELECT time,LAG(time) OVER (ORDER BY time) previous FROM candles
                    WHERE source=? AND symbol=? AND interval=?)
                    WHERE time-previous>?""",
                    (*ident, threshold),
                )
                for gap in gaps:
                    findings.append(
                        (
                            *ident,
                            "audit_gap",
                            json.dumps(
                                {
                                    "scope": "local candles",
                                    "from": gap["previous"],
                                    "to": gap["time"],
                                    "continuous": row["source"] == "crypto",
                                    "review": None
                                    if row["source"] == "crypto"
                                    else "Verify holiday, suspension, or listing",
                                },
                                sort_keys=True,
                            ),
                        )
                    )
            if row["source"] == "crypto" and now - row["last"] > max(2 * step, 300):
                findings.append(
                    (
                        *ident,
                        "audit_stale",
                        f"Latest observed {row['last']}; continuous {step}-second series is overdue",
                    )
                )
            if row["source"] != "vn":
                continue
            if row["interval"] == "1D":
                weekends = con.execute(
                    """SELECT time FROM candles WHERE source=? AND symbol=? AND interval=?
                    AND strftime('%w',time,'unixepoch') IN ('0','6') ORDER BY time""",
                    ident,
                ).fetchall()
                if weekends:
                    findings.append(
                        (
                            *ident,
                            "audit_non_session_day",
                            json.dumps(
                                {
                                    "dates": [
                                        datetime.fromtimestamp(d[0], UTC).date().isoformat()
                                        for d in weekends
                                    ],
                                    "review": "Provider emitted VN daily bars outside weekday trading sessions; preserve values pending verification",
                                },
                                sort_keys=True,
                            ),
                        )
                    )
            # VNINDEX observes exchange sessions; each ticker's daily feed
            # observes intraday candidate dates. Absence is a review finding,
            # never proof of missing trades, holidays, or suspension policy.
            reference = "VNINDEX" if row["interval"] == "1D" else row["symbol"]
            floor = con.execute(
                """SELECT MIN(floor) FROM jobs WHERE source=? AND symbol=? AND interval=?
                AND kind IN ('bootstrap','repair') AND status<>'cancelled'""",
                ident,
            ).fetchone()[0]
            lower = floor if floor is not None else row["first"] // 86400 * 86400
            dates = con.execute(
                """SELECT d.time FROM candles d WHERE d.source='vn' AND d.symbol=?
                AND d.interval='1D' AND d.time>=? AND d.time<?
                AND strftime('%w',d.time,'unixepoch') NOT IN ('0','6')
                AND NOT EXISTS (SELECT 1 FROM candles c WHERE c.source=? AND c.symbol=?
                AND c.interval=? AND c.time>=d.time AND c.time<d.time+86400)
                ORDER BY d.time""",
                (reference, lower, completed, *ident),
            ).fetchall()
            if dates:
                findings.append(
                    (
                        *ident,
                        "audit_observed_sessions",
                        json.dumps(
                            {
                                "scope": "local observed daily dates",
                                "reference": reference,
                                "dates": [
                                    datetime.fromtimestamp(d[0], UTC).date().isoformat()
                                    for d in dates
                                ],
                                "review": "Verify listing, suspension, no-trade periods, and provider retention before repair",
                            },
                            sort_keys=True,
                        ),
                    )
                )
    # Resolve only these audit categories on series actually checked. Corporate
    # action/migration findings and inactive historical series remain independent.
    stamp = int(time.time())
    with repo.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        con.executemany(
            "UPDATE quality SET resolved=1 WHERE source=? AND symbol=? AND interval=? AND kind IN ("
            + ",".join("?" for _ in KINDS)
            + ")",
            [(*ident, *KINDS) for ident in checked],
        )
        con.executemany(
            """INSERT INTO quality(source,symbol,interval,kind,detail,first_seen,last_seen)
            VALUES (?,?,?,?,?,?,?) ON CONFLICT(source,symbol,interval,kind,detail)
            DO UPDATE SET last_seen=excluded.last_seen,resolved=0""",
            [(*item, stamp, stamp) for item in findings],
        )
    return repo.findings()
