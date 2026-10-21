"""Reusable, indexed SQLite history loading for one or many ticker identities.

The loader keeps one read transaction and one SQLite connection for candle and
history metadata reads. Multi-symbol candle tails are selected in indexed SQL
batches so Python does not issue and materialize one query per ticker.
"""

import json
from dataclasses import dataclass, field

from .domain import Candle


@dataclass
class LoadedHistory:
    local: dict[str, list[Candle]] = field(default_factory=dict)
    archives: dict[str, list[dict]] = field(default_factory=dict)
    gaps: dict[str, list[dict]] = field(default_factory=dict)
    adoptions: dict[str, dict[str, dict]] = field(default_factory=dict)
    volume_corrections: dict[str, list[dict]] = field(default_factory=dict)


class SQLiteDataLoader:
    """Load native candle tails and associated validation metadata in batches."""

    # Stay below SQLite builds that still use the historical 999-variable
    # limit. Each requested ticker contributes two bound values.
    _CANDLE_SYMBOL_BATCH_SIZE = 400
    # The batch join shape is intended for longer indicator lookbacks. On
    # SQLite 3.46, SQLite can choose an automatic index for very short tails;
    # a reused indexed point lookup is substantially cheaper for these.
    _CANDLE_SHORT_TAIL_LIMIT = 16

    def __init__(self, repository):
        self.repository = repository

    @staticmethod
    def _chunks(values, size=400):
        for offset in range(0, len(values), size):
            yield values[offset : offset + size]

    @staticmethod
    def _candle_query(source, symbol, interval, start, end, limit, forward):
        where = "source=? AND symbol=? AND interval=?"
        args = [source, symbol, interval]
        if start is not None:
            where += " AND time>=?"
            args.append(start)
        if end is not None:
            where += " AND time<=?"
            args.append(end)
        sql = f"SELECT * FROM candles WHERE {where} ORDER BY time {'ASC' if forward else 'DESC'}"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        return sql, args

    @classmethod
    def _read_candle_batch(
        cls, con, source, symbols, interval, start, end, limit, forward, result
    ):
        """Read indexed tails for symbol chunks and append chronologically.

        The correlated inner lookup uses the candles primary key to seek each
        ticker's newest (or oldest) timestamps. The outer lookup then fetches
        just those complete rows. This avoids scanning a whole interval and
        avoids Python/SQLite round trips per ticker.
        """
        if limit is not None and limit <= cls._CANDLE_SHORT_TAIL_LIMIT:
            # Latest/no-MA requests need only two candles (the requested row
            # plus its change-calculation predecessor). SQLite's prepared
            # primary-key seeks are faster than the VALUES join for this tiny
            # tail and avoid materializing any discarded history.
            sql, _ = cls._candle_query(source, "", interval, start, end, limit, forward)
            for symbol in symbols:
                args = [source, symbol, interval]
                if start is not None:
                    args.append(start)
                if end is not None:
                    args.append(end)
                args.append(limit)
                rows = con.execute(sql, args).fetchall()
                result[symbol].extend(
                    Candle(**dict(row)) for row in (rows if forward else reversed(rows))
                )
            return
        direction = "ASC" if forward else "DESC"
        for offset in range(0, len(symbols), cls._CANDLE_SYMBOL_BATCH_SIZE):
            chunk = symbols[offset : offset + cls._CANDLE_SYMBOL_BATCH_SIZE]
            requested = ",".join("(?,?)" for _ in chunk)
            where = ["c2.source=?", "c2.symbol=r.symbol", "c2.interval=?"]
            args = [value for index, symbol in enumerate(chunk, offset) for value in (index, symbol)]
            args.extend((source, interval))
            if start is not None:
                where.append("c2.time>=?")
                args.append(start)
            if end is not None:
                where.append("c2.time<=?")
                args.append(end)
            inner = (
                "SELECT c2.time FROM candles AS c2 WHERE "
                + " AND ".join(where)
                + f" ORDER BY c2.time {direction}"
            )
            if limit is not None:
                inner += " LIMIT ?"
                args.append(limit)
            sql = f"""
                WITH requested(ord,symbol) AS (VALUES {requested})
                SELECT c.*
                FROM requested AS r
                CROSS JOIN candles AS c
                  ON c.source=? AND c.symbol=r.symbol AND c.interval=?
                 AND c.time IN ({inner})
                ORDER BY r.ord,c.time ASC
            """
            # The source/interval placeholders occur in the outer query before
            # the subquery placeholders in SQL text.
            batch_args = args[: 2 * len(chunk)] + [source, interval] + args[2 * len(chunk) :]
            for row in con.execute(sql, batch_args):
                result[row["symbol"]].append(Candle(**dict(row)))

    def read_candles_many(
        self, source, symbols, interval, start=None, end=None, limit=None, forward=False
    ):
        """Load only native candles for callers that do not need History policy."""
        symbols = list(dict.fromkeys(symbols))
        result = {symbol: [] for symbol in symbols}
        if not symbols:
            return result
        with self.repository.connect() as con:
            con.execute("BEGIN")
            self._read_candle_batch(
                con, source, symbols, interval, start, end, limit, forward, result
            )
        return result

    def load_many(
        self,
        source,
        symbols,
        interval,
        start=None,
        end=None,
        limit=None,
        forward=False,
    ):
        """Read matching rows and metadata for symbols using a single connection."""
        symbols = list(dict.fromkeys(symbols))
        result = LoadedHistory(
            local={symbol: [] for symbol in symbols},
            archives={symbol: [] for symbol in symbols},
            gaps={symbol: [] for symbol in symbols},
            adoptions={symbol: {} for symbol in symbols},
            volume_corrections={symbol: [] for symbol in symbols},
        )
        if not symbols:
            return result

        archive_where = ["source=?", "interval=?", "status IN ('published','pending_repair','historical_snapshot')"]
        archive_args = [source, interval]
        if start is not None:
            archive_where.append("end>=?")
            archive_args.append(start)
        if end is not None:
            archive_where.append("start<=?")
            archive_args.append(end)

        gap_where = [
            "source=?",
            "interval=?",
            "kind='history_unavailable'",
            "resolved=0",
        ]
        gap_args = [source, interval]

        adoption_where = ["source=?", "interval=?"]
        adoption_args = [source, interval]

        correction_where = ["source=?", "interval=?"]
        correction_args = [source, interval]

        with self.repository.connect() as con:
            # Keep all tables on the same SQLite snapshot during a bulk read.
            con.execute("BEGIN")
            self._read_candle_batch(
                con, source, symbols, interval, start, end, limit, forward, result.local
            )

            for chunk in self._chunks(symbols):
                placeholders = ",".join("?" for _ in chunk)
                args = [*archive_args, *chunk]
                rows = con.execute(
                    "SELECT * FROM archives WHERE "
                    + " AND ".join(archive_where)
                    + f" AND symbol IN ({placeholders}) ORDER BY end DESC",
                    args,
                ).fetchall()
                for row in rows:
                    result.archives[row["symbol"]].append(dict(row))

                rows = con.execute(
                    "SELECT source,symbol,interval,detail FROM quality WHERE "
                    + " AND ".join(gap_where)
                    + f" AND symbol IN ({placeholders})",
                    [*gap_args, *chunk],
                ).fetchall()
                for row in rows:
                    try:
                        record = json.loads(row["detail"])
                    except (ValueError, TypeError) as exc:
                        from .domain import DataError

                        raise DataError("Invalid unavailable-history record") from exc
                    self.repository.validate_history_gap(record)
                    if any(record[key] != row[key] for key in ("source", "symbol", "interval")):
                        from .domain import DataError

                        raise DataError("Unavailable-history identity differs from its stored record")
                    if (start is None or record["end"] >= start) and (
                        end is None or record["start"] <= end
                    ):
                        result.gaps[row["symbol"]].append(record)

                rows = con.execute(
                    "SELECT * FROM snapshot_adoptions WHERE "
                    + " AND ".join(adoption_where)
                    + f" AND symbol IN ({placeholders})",
                    [*adoption_args, *chunk],
                ).fetchall()
                for row in rows:
                    result.adoptions[row["symbol"]][row["revision"]] = dict(row)

                if source == "vn" and interval == "1m":
                    correction_sql = (
                        "SELECT * FROM volume_corrections WHERE "
                        + " AND ".join(correction_where)
                        + f" AND symbol IN ({placeholders})"
                    )
                    correction_args_for_chunk = [*correction_args, *chunk]
                    if start is not None:
                        correction_sql += " AND time>=?"
                        correction_args_for_chunk.append(start)
                    if end is not None:
                        correction_sql += " AND time<=?"
                        correction_args_for_chunk.append(end)
                    correction_sql += " ORDER BY time,id"
                    rows = con.execute(correction_sql, correction_args_for_chunk).fetchall()
                    for row in rows:
                        result.volume_corrections[row["symbol"]].append(dict(row))

        for records in result.gaps.values():
            records.sort(
                key=lambda row: (
                    row["source"],
                    row["symbol"],
                    row["interval"],
                    row["start"],
                    row["end"],
                    row["reason"],
                )
            )
        return result
