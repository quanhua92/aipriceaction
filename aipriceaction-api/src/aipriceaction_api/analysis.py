import math
from datetime import UTC, datetime
from time import perf_counter

from .analysis_cache import VersionedAnalysisCache
from .calculations import (
    _rust_sum,
    enhance_latest_many,
    enhance_many_selected,
    enhance_selected,
    jdk_many,
    numpy_align_rows,
    numpy_numeric_orders,
    numpy_sequential_sum,
    volume_profile,
)
from .domain import PERIODS, DataError, date_bounds, format_chrono_date, parse_time
from .redis_snapshots import (
    joined_snapshot_record,
    joined_snapshot_response,
    read_redis_snapshot,
    validate_joined_data_rows,
    write_redis_snapshot,
)

# The Rust analysis routes use the legacy curated group files as their
# membership source. The Python catalog adds ALL_STOCKS for ingestion and
# keeps SJC under its own storage source; neither is an independent RRG source.
_RRG_SOURCE_ORDER = {
    "vn": ("vn",),
    "crypto": ("crypto",),
    "yahoo": ("yahoo",),
    "all": ("vn", "yahoo", "crypto"),
}
_RRG_INDEXES = frozenset(
    {
        "VNINDEX",
        "VN30",
        "VN30F1M",
        "HNX30",
        "VN100",
        "VNMIDCAP",
        "VNSMALLCAP",
        "VNALLSHARE",
        "VNXALLSHARE",
        "VNMITECH",
        "VNUTI",
        "VNCONS",
        "VNCOND",
        "VNHEAL",
        "VNIND",
        "VNFIN",
        "VNFINLEAD",
        "VNFINSELECT",
        "VNDIAMOND",
        "VNDIVIDEND",
        "VNREAL",
        "VNENE",
    }
)


def envelope(kind, data, date="latest", total=None):
    return {
        "analysis_date": date,
        "analysis_type": kind,
        "total_analyzed": total if total is not None else len(data),
        "data": data,
    }


def _is_vn_ticker(symbol):
    return (
        len(symbol) == 3
        and symbol.isascii()
        and all("A" <= character <= "Z" for character in symbol)
    )


def _jdk_lookback_limit(period, trails, ema):
    """Return enough raw bars for JDK's requested recent points.

    JDK uses finite rolling windows. Its last ``trails`` points depend only on
    the latest ``3 * period + trails - 2`` aligned closes, with a minimum of
    ``3 * period + 1`` bars for the algorithm's existing validity check.
    Keep Rust's larger limit as the cap for sparse histories and fallback reads.
    """
    rust_limit = 250 + (600 if ema else 200)
    required = max(3 * period + 1, 3 * period + max(0, trails) - 2)
    return min(rust_limit, required)


def _sector_lookup(groups):
    """Build Rust's lexicographically first sector label for each symbol."""
    result = {}
    for group in sorted(groups):
        for symbol in groups[group]:
            result.setdefault(symbol, group)
    return result


def _numpy_benchmark_vectors(benchmark):
    import numpy as np

    times = np.fromiter(sorted(benchmark), dtype=np.int64, count=len(benchmark))
    closes = np.fromiter(
        (benchmark[int(timestamp)] for timestamp in times), dtype=np.float64, count=len(times)
    )
    return times, closes


class Analysis:
    def __init__(self, history, catalog, parallel_map=None):
        self.history, self.catalog = history, catalog
        self.parallel_map = parallel_map or (lambda items, function: list(map(function, items)))
        self._rrg_result_cache = VersionedAnalysisCache()

    def universe(self, mode):
        # The Rust analysis routes enumerate the curated legacy groups rather
        # than every locally registered ticker. Reuse the same catalog mapping
        # already used by RRG so Python-only ingestion entries stay out.
        return self._rrg_universe(mode, self._rrg_groups_by_source())

    def _rrg_groups_by_source(self):
        """Return the curated groups used by Rust RRG, keyed by data source.

        Python's catalog deliberately adds ALL_STOCKS for broader ingestion
        and stores SJC separately. Rust RRG uses the curated VN/crypto groups
        and merges SJC into the Yahoo group map. Load the same source files
        Rust reads so a Python catalog snapshot cannot widen this RRG universe.
        """
        vn = self.catalog.load("ticker_group.json")
        crypto_rows = self.catalog.load("binance_tickers.json").get("data", [])
        if not isinstance(crypto_rows, list):
            crypto_rows = []
        crypto = {
            "CRYPTO_TOP_100": [
                row["symbol"]
                for row in crypto_rows
                if isinstance(row, dict) and isinstance(row.get("symbol"), str)
            ]
        }

        yahoo = {}
        for source in ("global", "sjc"):
            rows = self.catalog.load(f"{source}_tickers.json").get("data", [])
            for row in rows if isinstance(rows, list) else ():
                if not isinstance(row, dict) or not isinstance(row.get("symbol"), str):
                    continue
                group = row.get("category")
                group = group if isinstance(group, str) else "Other"
                yahoo.setdefault(group, []).append(row["symbol"])

        # Rust's crypto and merged Yahoo loaders return BTreeMaps. Its legacy
        # VN loader uses a HashMap, so sector-label precedence is made explicit
        # by _sector_for_symbol rather than depending on map iteration order.
        return {
            "vn": {name: list(symbols) for name, symbols in vn.items()},
            "crypto": dict(sorted(crypto.items())),
            "yahoo": dict(sorted(yahoo.items())),
            # Rust MASCORE mode=all falls back to all SJC database tickers
            # even though its Redis symbol list for SJC is empty. These rows
            # have no source-specific sector map there.
            "sjc": {},
        }

    def _rrg_universe(self, mode, groups_by_source):
        sources = _RRG_SOURCE_ORDER.get(mode, ())
        return [
            {"source": source, "symbol": symbol}
            for source in sources
            for symbols in groups_by_source[source].values()
            for symbol in symbols
        ]

    @staticmethod
    def _sector_for_symbol(groups, symbol):
        """Choose the lexicographically first matching curated sector.

        Rust's legacy VN group map is a HashMap, so selecting its first match
        directly would make labels vary between requests. Both servers use
        this explicit precedence for overlapping group membership.
        """
        for group in sorted(groups):
            symbols = groups[group]
            if symbol in symbols:
                return group
        return None

    @staticmethod
    def _rrg_sector(source, symbol, groups_by_source):
        return Analysis._sector_for_symbol(groups_by_source[source], symbol)

    def latest(self, mode, date=None, ema=False, snap=True, raw_limit=None, periods=None):
        universe = self.universe(mode)
        symbols_by_source = {}
        seen_by_source = {}
        for ticker in universe:
            source = ticker["source"]
            symbols = symbols_by_source.setdefault(source, [])
            seen = seen_by_source.setdefault(source, set())
            if ticker["symbol"] not in seen:
                symbols.append(ticker["symbol"])
                seen.add(ticker["symbol"])

        redis_client = getattr(self.history, "redis_client", None)
        redis_limit = raw_limit if raw_limit is not None else 1 + (600 if ema else 200)
        rows_by_identity = {}
        snapshot_complete_sources = set()

        if snap and redis_client is not None:
            for source, symbols in symbols_by_source.items():

                def read_snapshot(symbol, source=source):
                    try:
                        raw = read_redis_snapshot(
                            redis_client,
                            source,
                            symbol,
                            "1D",
                            redis_limit,
                            ema=ema,
                            joined=True,
                        )
                    except Exception:
                        return symbol, None
                    return symbol, validate_joined_data_rows(raw)

                snapshots = self.parallel_map(symbols, read_snapshot)
                if len(snapshots) == len(symbols) and all(value for _, value in snapshots):
                    snapshot_complete_sources.add(source)
                    for symbol, values in snapshots:
                        rows_by_identity[(source, symbol)] = [
                            joined_snapshot_response(row) for row in values
                        ]

        def fetch(t):
            source, symbol = t["source"], t["symbol"]
            cached = rows_by_identity.get((source, symbol))
            if cached is not None:
                return source, symbol, cached
            if raw_limit is not None:
                raw_rows = []
                cache_tail = getattr(self.history, "cache_tail", None)
                if redis_client is not None and cache_tail is not None:
                    try:
                        raw_rows = cache_tail(source, symbol, "1D", limit=redis_limit)
                    except Exception:
                        raw_rows = []
                if not raw_rows:
                    try:
                        raw_rows = self.history.read(
                            source, symbol, "1D", end=None, limit=redis_limit
                        )
                    except DataError:
                        raw_rows = []
                rows = (
                    enhance_selected(
                        raw_rows,
                        "1D",
                        [len(raw_rows) - 1],
                        True,
                        ema,
                        periods=periods,
                    )
                    if raw_rows
                    else []
                )
                return source, symbol, rows
            try:
                rows = self.history.query(
                    source,
                    symbol,
                    "1D",
                    end=None,
                    limit=1,
                    ema=ema,
                    strict_source_intervals=True,
                    periods=periods,
                )
            except DataError:
                # Rust's batch analysis fallback logs a failed PostgreSQL
                # read and keeps any rows already available for other symbols.
                # A local repair/gap error for one ticker must not fail the
                # entire performers or sector request.
                rows = []
            return source, symbol, rows

        if redis_client is None and hasattr(self.history, "query_many"):
            fetched_by_identity = {}
            for source, symbols in symbols_by_source.items():
                if raw_limit is not None:
                    loaded = self.history.read_many(
                        source,
                        symbols,
                        "1D",
                        limit=redis_limit,
                        return_exceptions=True,
                    )
                    valid_contexts = {
                        symbol: raw_rows
                        for symbol, raw_rows in loaded.items()
                        if not isinstance(raw_rows, Exception) and raw_rows
                    }
                    enhanced = enhance_latest_many(
                        valid_contexts,
                        {
                            symbol: [row.close for row in raw_rows]
                            for symbol, raw_rows in valid_contexts.items()
                        },
                        "1D",
                        True,
                        ema,
                        periods=periods,
                    )
                    for symbol, raw_rows in loaded.items():
                        fetched_by_identity[(source, symbol)] = (
                            []
                            if isinstance(raw_rows, Exception) or not raw_rows
                            else enhanced[symbol]
                        )
                else:
                    loaded = self.history.query_many(
                        source,
                        symbols,
                        "1D",
                        end=None,
                        limit=1,
                        ema=ema,
                        strict_source_intervals=True,
                        return_exceptions=True,
                        periods=periods,
                    )
                    for symbol, rows in loaded.items():
                        fetched_by_identity[(source, symbol)] = (
                            [] if isinstance(rows, Exception) else rows
                        )
            fetched = [
                (
                    ticker["source"],
                    ticker["symbol"],
                    fetched_by_identity.get((ticker["source"], ticker["symbol"]), []),
                )
                for ticker in universe
            ]
        else:
            fetched = self.parallel_map(universe, fetch)
        if redis_client is not None:
            for source, symbol, rows in fetched:
                if rows and source not in snapshot_complete_sources:
                    rows_by_identity[(source, symbol)] = rows
                    try:
                        record = joined_snapshot_record(rows[-1])
                        write_redis_snapshot(
                            redis_client,
                            source,
                            symbol,
                            "1D",
                            redis_limit,
                            [record],
                            ema=ema,
                            joined=True,
                        )
                    except Exception:
                        # Snapshot persistence is an optimization; an invalid
                        # or unavailable cache must not fail the analysis.
                        pass
        else:
            for source, symbol, rows in fetched:
                rows_by_identity[(source, symbol)] = rows

        return [(source, rows[-1]) for (source, _symbol), rows in rows_by_identity.items() if rows]

    def performers(
        self,
        mode="vn",
        date=None,
        ema=False,
        sort_by="close_changed",
        direction="desc",
        limit=10,
        min_volume=10000,
        sector=None,
        snap=True,
    ):
        allowed = {
            "close_changed",
            "volume",
            "volume_changed",
            "total_money_changed",
            *(f"ma{p}_score" for p in PERIODS),
        }
        if sort_by not in allowed:
            sort_by = "close_changed"  # Rust default on unknown metrics
        result = []
        keys = (
            "close_changed",
            "volume_changed",
            *(f"ma{p}" for p in PERIODS),
            *(f"ma{p}_score" for p in PERIODS),
        )
        try:
            cutoff = date_bounds(date, True)
        except DataError:
            # Rust's parse_analysis_date falls back to the current UTC time
            # for a missing or invalid date instead of rejecting the request.
            cutoff = None
        if cutoff is None:
            cutoff = int(datetime.now(UTC).timestamp())
        vn_groups = self.catalog.load("ticker_group.json")
        sector_by_symbol = _sector_lookup(vn_groups)
        sector_symbols = set(vn_groups.get(sector, ())) if sector is not None else None

        # Rust's source batch returns a map keyed by ticker, so a symbol listed
        # in two curated groups of the same source is fetched once. Preserve
        # cross-source identities in mode=all while dropping same-source group
        # repeats here.
        latest = {}
        for source, row in self.latest(mode, date, ema, snap):
            latest.setdefault((source, row["symbol"]), row)
        for (source, _symbol), row in latest.items():
            if parse_time(row["time"]) > cutoff:
                continue
            if sector_symbols is not None and row["symbol"] not in sector_symbols:
                continue
            if row["symbol"].upper() in _RRG_INDEXES:
                continue
            if _is_vn_ticker(row["symbol"]) and row["volume"] < min_volume:
                continue
            group = sector_by_symbol.get(row["symbol"])
            out = (
                {k: row[k] for k in ("symbol", "close", "volume")}
                | {k: row.get(k) for k in keys}
                | {"sector": group}
                | {"total_money_changed": row.get("total_money_changed")}
            )
            if mode == "all":
                out["source"] = source
            result.append(out)

        # Keep Rust's ordering for missing metrics (last descending, first ascending).
        def tie_breaker(row):
            return row["symbol"], row.get("source", "")

        def metric(row):
            return row.get(sort_by) is not None, row.get(sort_by) or 0

        orders = numpy_numeric_orders(result, sort_by)
        if orders is None:
            desc = sorted(result, key=tie_breaker)
            desc.sort(key=metric, reverse=True)
            asc = sorted(result, key=tie_breaker)
            asc.sort(key=metric)
        else:
            desc_order, asc_order = orders
            desc = [result[index] for index in desc_order]
            asc = [result[index] for index in asc_order]
        n = max(1, min(100, limit))
        top, worst = (asc[:n], desc[:n]) if direction == "asc" else (desc[:n], asc[:n])
        return envelope(
            "top_performers",
            {"performers": top, "worst_performers": worst, "hourly": None},
            date if date is not None else "latest",
            len(top) + len(worst),
        )

    def sectors(
        self,
        mode="vn",
        date=None,
        ema=False,
        ma_period=20,
        min_score=0,
        above_threshold_only=False,
        top_per_sector=10,
        snap=True,
    ):
        if ma_period not in PERIODS:
            raise DataError("Invalid MA period. Must be one of: 10, 20, 50, 100, 200", 400)
        latest = {
            (src, row["symbol"]): row
            for src, row in self.latest(mode, date, ema, snap, None, (ma_period,))
        }
        groups_by_source = self._rrg_groups_by_source()
        # Rust's MA-by-sector handler always uses VN sector definitions for
        # single-source modes; Yahoo/crypto sector definitions are appended
        # only for mode=all. The selected source's rows are still looked up by
        # those VN symbols in single-source modes (so crypto is usually empty,
        # while Yahoo symbols shared with VN groups can appear).
        sources = ("vn", "yahoo", "crypto") if mode == "all" else ("vn",)
        sector_entries = []
        total = 0
        for src in sources:
            for name, symbols in groups_by_source[src].items():
                # ALL_STOCKS is a Python ingestion directory, not one of the
                # curated sector groups used by Rust's analysis endpoint.
                if src == "vn" and name == "ALL_STOCKS":
                    continue
                stocks = []
                above = 0
                for symbol in symbols:
                    if src == "vn" and symbol.upper() in _RRG_INDEXES:
                        continue
                    row_source = src if mode == "all" else mode
                    row = latest.get((row_source, symbol))
                    ma_key = f"ma{ma_period}"
                    score_key = f"ma{ma_period}_score"
                    if not row or row.get(ma_key) is None or row.get(score_key) is None:
                        continue
                    score = row[score_key]
                    if score >= min_score:
                        above += 1
                    elif above_threshold_only:
                        continue
                    stock = {k: row[k] for k in ("symbol", "close", "volume")} | {
                        "ma_value": row[ma_key],
                        "ma_score": score,
                        "close_changed": row.get("close_changed"),
                        "volume_changed": row.get("volume_changed"),
                    }
                    if mode == "all":
                        stock["source"] = src
                    stocks.append(stock)
                if stocks:
                    total += len(stocks)
                    scores = [row["ma_score"] for row in stocks]
                    score_sum = numpy_sequential_sum(scores)
                    if score_sum is None:
                        score_sum = _rust_sum(scores)
                    stock_orders = numpy_numeric_orders(stocks, "ma_score")
                    if stock_orders is None:
                        top_stocks = sorted(
                            stocks,
                            key=lambda r: (
                                -r["ma_score"],
                                r["symbol"],
                                r.get("source", ""),
                            ),
                        )
                    else:
                        top_stocks = [stocks[index] for index in stock_orders[0]]
                    sector_entries.append(
                        (
                            {
                                "sector_name": name,
                                "total_stocks": len(stocks),
                                "stocks_above_threshold": above,
                                "average_score": score_sum / len(stocks),
                                "top_stocks": top_stocks[: max(0, min(50, top_per_sector))],
                            },
                            src,
                        )
                    )
        ordered_sectors = [
            sector
            for sector, _source in sorted(
                sector_entries,
                key=lambda item: (
                    -item[0]["average_score"],
                    item[0]["sector_name"],
                    item[1],
                ),
            )
        ]
        return envelope(
            "ma_scores_by_sector",
            {
                "sectors": ordered_sectors,
                "ma_period": ma_period,
                # serde_json serializes non-finite Rust floats as JSON null.
                "threshold": min_score if math.isfinite(min_score) else None,
            },
            date if date is not None else "latest",
            total,
        )

    def profile(
        self,
        symbol,
        mode="vn",
        date=None,
        start_date=None,
        end_date=None,
        bins=50,
        value_area_pct=70,
    ):
        if not symbol:
            raise DataError("symbol parameter is required", 400)
        if date is not None:
            start, end = date, date
        elif start_date is not None:
            start = start_date
            end = start_date if end_date is None else end_date
        else:
            raise DataError(
                "Either 'date' or 'start_date' parameter is required (YYYY-MM-DD format)", 400
            )
        try:
            lo = date_bounds(start)
        except DataError as exc:
            raise DataError("Invalid start date format. Use YYYY-MM-DD", 400) from exc
        try:
            hi = date_bounds(end, True)
        except DataError as exc:
            raise DataError("Invalid end date format. Use YYYY-MM-DD", 400) from exc
        if hi < lo:
            raise DataError("end_date must be >= start_date", 400)
        # Rust's volume-profile handler intentionally falls back to VN for
        # unknown modes (including "all") and accepts case-insensitive names.
        normalized_mode = str(mode).lower()
        source = {"crypto": "crypto", "yahoo": "yahoo"}.get(normalized_mode, "vn")
        rows = None
        cache_tail = getattr(self.history, "cache_tail", None)
        cache_size = getattr(getattr(self.history, "settings", None), "redis_minute_max_size", 0)
        if cache_tail is not None and cache_size > 0:
            try:
                retained = cache_tail(source, symbol, "1m", limit=cache_size)
            except DataError:
                retained = []
            if any(lo <= row.time <= hi for row in retained):
                # Rust uses the whole retained Redis tail whenever any row
                # overlaps the requested dates, then filters that tail. It
                # does not complete a partially covered range from Postgres.
                rows = retained
        if rows is None:
            # Rust reads the available SQL/Redis candles without consulting
            # Python's separate history-gap ledger for this endpoint.
            rows = self.history.read(source, symbol, "1m", lo, hi, ignore_history_gaps=True)
        rows = [row for row in rows if lo <= row.time <= hi]

        if not rows:
            date_desc = start if start == end else f"{start} to {end}"
            raise DataError(f"No minute data found for {symbol} on {date_desc}", 404)
        if not any(row.volume for row in rows):
            # Rust returns this calculation error as HTTP 200 with a bare
            # {"error": ...} body rather than the normal analysis envelope.
            return {"error": "No volume profile data generated"}

        data = volume_profile(rows, symbol, source, bins, value_area_pct)
        if data is None:
            return {"error": "No volume profile data generated"}
        return envelope(
            "volume_profile", data, start if start == end else f"{start} to {end}", len(rows)
        )

    def rrg(
        self,
        mode="vn",
        date=None,
        ema=False,
        algorithm="jdk",
        benchmark=None,
        period=10,
        trails=10,
        min_volume=100000,
        snap=True,
        timing_stats=None,
    ):
        """Cache VN JDK results against the versions of their daily inputs."""
        if mode != "vn" or algorithm != "jdk":
            started = perf_counter()
            result = self._rrg_uncached(
                mode,
                date,
                ema,
                algorithm,
                benchmark,
                period,
                trails,
                min_volume,
                snap,
            )
            if timing_stats is not None:
                timing_stats.update(
                    cache_eligible=False, calculate_ms=round((perf_counter() - started) * 1000, 3)
                )
            return result

        # RRG reads may prefer a live Redis tail over SQLite. Redis publication
        # happens after SQLite commits, so the SQLite series version cannot
        # prove that a Redis-backed result is current during that handoff.
        # Avoid retaining RRG results when reads can come from Redis.
        if getattr(self.history, "redis_client", None) is not None:
            started = perf_counter()
            result = self._rrg_uncached(
                mode,
                date,
                ema,
                algorithm,
                benchmark,
                period,
                trails,
                min_volume,
                snap,
            )
            if timing_stats is not None:
                timing_stats.update(
                    cache_eligible=False,
                    cache_bypass="redis_configured",
                    calculate_ms=round((perf_counter() - started) * 1000, 3),
                )
            return result

        repository = getattr(self.history, "repo", None)
        history_versions = getattr(repository, "history_versions", None)
        if not callable(history_versions):
            started = perf_counter()
            result = self._rrg_uncached(
                mode,
                date,
                ema,
                algorithm,
                benchmark,
                period,
                trails,
                min_volume,
                snap,
            )
            if timing_stats is not None:
                timing_stats.update(
                    cache_eligible=False,
                    cache_bypass="history_versions_unavailable",
                    calculate_ms=round((perf_counter() - started) * 1000, 3),
                )
            return result

        version_started = perf_counter()
        groups_by_source = self._rrg_groups_by_source()
        groups = groups_by_source["vn"]
        universe = self._rrg_universe("vn", groups_by_source)
        benchmark_symbol = ("VNINDEX" if benchmark is None else benchmark).upper()
        symbols = sorted({ticker["symbol"] for ticker in universe} | {benchmark_symbol})
        membership = tuple((group, tuple(groups[group])) for group in sorted(groups))
        versions = history_versions("vn", symbols, "1D")
        version = membership, tuple(sorted(versions.items()))
        if timing_stats is not None:
            timing_stats["version_check_ms"] = round((perf_counter() - version_started) * 1000, 3)
        normalized_period = max(4, min(50, period))
        normalized_trails = 0 if trails == 0 else max(1, min(120, trails))
        key = (
            mode,
            date,
            bool(ema),
            algorithm,
            benchmark_symbol,
            normalized_period,
            normalized_trails,
            min_volume,
            bool(snap),
        )
        cache_started = perf_counter()
        cached = self._rrg_result_cache.get(key, version, timing_stats=timing_stats)
        if timing_stats is not None:
            timing_stats["cache_lookup_ms"] = round((perf_counter() - cache_started) * 1000, 3)
            timing_stats["cache_eligible"] = True
            timing_stats["cache_hit"] = cached is not None
        if cached is not None:
            return cached

        calculate_started = perf_counter()
        result = self._rrg_uncached(
            mode,
            date,
            ema,
            algorithm,
            benchmark,
            period,
            trails,
            min_volume,
            snap,
        )
        if timing_stats is not None:
            timing_stats["calculate_ms"] = round((perf_counter() - calculate_started) * 1000, 3)
        cache_store_started = perf_counter()
        self._rrg_result_cache.put(key, version, result)
        if timing_stats is not None:
            timing_stats["cache_store_ms"] = round((perf_counter() - cache_store_started) * 1000, 3)
        return result

    def _rrg_uncached(
        self,
        mode="vn",
        date=None,
        ema=False,
        algorithm="jdk",
        benchmark=None,
        period=10,
        trails=10,
        min_volume=100000,
        snap=True,
    ):
        if algorithm not in ("jdk", "mascore"):
            raise DataError("Invalid RRG algorithm", 400)
        period = max(4, min(50, period))
        trails = 0 if trails == 0 else max(1, min(120, trails))
        try:
            end = date_bounds(date, True)
        except DataError as exc:
            raise DataError(f"Invalid date format '{date}'. Use YYYY-MM-DD", 400) from exc
        analysis_date = format_chrono_date(date) if date is not None else "latest"
        benchmark = ("VNINDEX" if benchmark is None else benchmark).upper()
        groups_by_source = self._rrg_groups_by_source()
        universe = self._rrg_universe(mode, groups_by_source)
        if algorithm == "mascore":
            # Rust's MASCORE fetchers materialize source rows in maps keyed by
            # ticker, so the same source/ticker listed in multiple curated
            # groups is analyzed once. JDK iterates its original symbol list
            # when building output, so preserve its duplicate-member behavior.
            unique_universe = {}
            for ticker in universe:
                unique_universe.setdefault((ticker["source"], ticker["symbol"]), ticker)
            universe = list(unique_universe.values())
        redis_client = getattr(self.history, "redis_client", None)
        batch_raw_rows = {}

        def read_raw(source, symbol, limit):
            """Read the Rust Redis tail first, then fall back to durable history."""
            batched = batch_raw_rows.get((source, symbol))
            if batched is not None:
                if isinstance(batched, Exception):
                    raise batched
                return batched
            cache_tail = getattr(self.history, "cache_tail", None)
            sjc_postgres_only = algorithm == "mascore" and mode == "all" and source == "sjc"
            if redis_client is not None and cache_tail is not None and not sjc_postgres_only:
                try:
                    cached = cache_tail(source, symbol, "1D", end=end, limit=limit)
                except Exception:
                    cached = []
                if cached:
                    return cached
            return self.history.read(source, symbol, "1D", end=end, limit=limit)

        if algorithm == "mascore" and mode == "all":
            # Rust's MASCORE all-mode PostgreSQL fallback enumerates every SJC
            # ticker despite SJC having an empty curated Redis symbol list.
            universe.extend(
                {"source": "sjc", "symbol": ticker["symbol"]}
                for ticker in self.history.repo.tickers(["sjc"])
            )
        bench = None
        jdk_full_limit = 250 + (600 if ema else 200)
        jdk_source = None
        if algorithm == "jdk":
            benchmark_found = False
            # JDK's requested tail depends on a finite number of aligned bars.
            # Sparse series are expanded below, so use the compact limit for
            # Redis and SQLite alike.
            jdk_limit = _jdk_lookback_limit(period, trails, ema)
            source_order = _RRG_SOURCE_ORDER.get(mode, ())
            active_sources = [
                source
                for source in source_order
                if any(ticker["source"] == source for ticker in universe)
            ]
            for src in active_sources:
                try:
                    # Keep the benchmark's full tail available for sparse
                    # ticker retries, which may need older aligned dates.
                    candidate = read_raw(src, benchmark, jdk_full_limit)
                except DataError:
                    continue
                if candidate:
                    benchmark_found = True
                    jdk_source = src
                    bench = {r.time: r.close for r in candidate if r.close}
                    break
            if not benchmark_found:
                raise DataError(f"Benchmark '{benchmark}' not found", 400)
            benchmark_times, benchmark_closes = _numpy_benchmark_vectors(bench)
            required_aligned = jdk_limit

        fast_mascore = algorithm == "mascore" and trails == 0 and date is None
        fast_latest = (
            {
                (source, row["symbol"]): row
                for source, row in self.latest(
                    mode,
                    date,
                    ema,
                    snap,
                    raw_limit=1 + 100,
                    periods=(20, 100),
                )
            }
            if fast_mascore
            else {}
        )

        if redis_client is None and hasattr(self.history, "read_many"):
            if algorithm == "jdk":
                batch_limit = jdk_limit
            elif fast_mascore:
                batch_limit = 1 + 100
            else:
                batch_limit = (1 if trails == 0 else trails) + (600 if ema else 200)
            symbols_by_source = {}
            for ticker in universe:
                if fast_mascore and ticker["source"] != "sjc":
                    continue
                symbols_by_source.setdefault(ticker["source"], []).append(ticker["symbol"])
            for source, symbols in symbols_by_source.items():
                loaded = self.history.read_many(
                    source,
                    list(dict.fromkeys(symbols)),
                    "1D",
                    end=end,
                    limit=batch_limit,
                    return_exceptions=True,
                )
                batch_raw_rows.update({(source, symbol): rows for symbol, rows in loaded.items()})

            if algorithm == "jdk":
                # The last RRG trail points use finite rolling windows, so the
                # common case needs only a short tail. If missing sessions or
                # sparse sources leave too few benchmark-aligned bars, retain
                # the full Rust lookback for just those symbols.
                fallback_by_source = {}
                for ticker in universe:
                    source, symbol = ticker["source"], ticker["symbol"]
                    if symbol == benchmark or symbol.upper() in _RRG_INDEXES:
                        continue
                    rows = batch_raw_rows.get((source, symbol))
                    if isinstance(rows, Exception) or not rows:
                        continue
                    aligned, _, _ = numpy_align_rows(rows, benchmark_times, benchmark_closes)
                    if len(aligned) < required_aligned:
                        fallback_by_source.setdefault(source, []).append(symbol)
                if fallback_by_source and jdk_source is not None:
                    # A sparse security may need earlier aligned dates than
                    # the short benchmark tail contains. Expand both sides so
                    # those symbols can retain the full Rust lookback parity.
                    expanded_benchmark = self.history.read_many(
                        jdk_source,
                        [benchmark],
                        "1D",
                        end=end,
                        limit=jdk_full_limit,
                        return_exceptions=True,
                    ).get(benchmark)
                    if expanded_benchmark and not isinstance(expanded_benchmark, Exception):
                        batch_raw_rows[(jdk_source, benchmark)] = expanded_benchmark
                        bench = {row.time: row.close for row in expanded_benchmark if row.close}
                        benchmark_times, benchmark_closes = _numpy_benchmark_vectors(bench)
                for source, symbols in fallback_by_source.items():
                    expanded = self.history.read_many(
                        source,
                        list(dict.fromkeys(symbols)),
                        "1D",
                        end=end,
                        limit=jdk_full_limit,
                        return_exceptions=True,
                    )
                    batch_raw_rows.update(
                        {(source, symbol): rows for symbol, rows in expanded.items()}
                    )

        mascore_selected = {}
        if algorithm == "mascore" and not fast_mascore and batch_raw_rows:
            selected_count = max(1, trails)
            for source, symbols in symbols_by_source.items():
                contexts = {
                    symbol: rows
                    for symbol in dict.fromkeys(symbols)
                    if isinstance((rows := batch_raw_rows.get((source, symbol))), list) and rows
                }
                if not contexts:
                    continue
                selected_indices = {
                    symbol: range(max(0, len(rows) - selected_count), len(rows))
                    for symbol, rows in contexts.items()
                }
                enhanced = enhance_many_selected(
                    contexts,
                    selected_indices,
                    "1D",
                    ma=True,
                    ema=ema,
                    periods=(20, 100),
                )
                mascore_selected.update(
                    {(source, symbol): rows for symbol, rows in enhanced.items()}
                )

        jdk_prepared = {}
        if algorithm == "jdk":
            unique_tickers = {}
            for ticker in universe:
                unique_tickers.setdefault((ticker["source"], ticker["symbol"]), ticker)

            def prepare_jdk(ticker):
                source, symbol = ticker["source"], ticker["symbol"]
                if symbol.upper() in _RRG_INDEXES or symbol == benchmark:
                    return None
                try:
                    rows = read_raw(source, symbol, jdk_limit)
                except DataError:
                    # Preserve per-ticker isolation for gaps and pending repairs.
                    return None
                if not rows or rows[-1].volume < min_volume:
                    return None
                aligned_indices, security, benchmark_values = numpy_align_rows(
                    rows, benchmark_times, benchmark_closes
                )
                if len(aligned_indices) < required_aligned and jdk_limit < jdk_full_limit:
                    # Redis-backed reads do not pass through the SQL batch
                    # expansion below. Retry just the sparse ticker with the
                    # full Rust lookback, then keep only the finite window the
                    # last requested RRG points use.
                    try:
                        rows = read_raw(source, symbol, jdk_full_limit)
                    except DataError:
                        return None
                    if not rows or rows[-1].volume < min_volume:
                        return None
                    aligned_indices, security, benchmark_values = numpy_align_rows(
                        rows, benchmark_times, benchmark_closes
                    )
                if len(aligned_indices) < 3 * period + 1:
                    return None
                aligned_indices = aligned_indices[-required_aligned:]
                security = security[-required_aligned:]
                benchmark_values = benchmark_values[-required_aligned:]
                aligned = [rows[int(index)] for index in aligned_indices]
                return (source, symbol), rows, aligned, security, benchmark_values

            prepared = [
                item
                for item in self.parallel_map(list(unique_tickers.values()), prepare_jdk)
                if item is not None
            ]
            computed = jdk_many(
                [(item[3], item[4]) for item in prepared],
                period,
                tail=max(1, trails),
            )
            for item, points in zip(prepared, computed, strict=True):
                if points:
                    jdk_prepared[item[0]] = (item[1], item[2], points)

        def analyze_ticker(ticker):
            symbol, source = ticker["symbol"], ticker["source"]
            if symbol.upper() in _RRG_INDEXES or symbol == benchmark and algorithm == "jdk":
                return None
            if algorithm == "jdk":
                prepared = jdk_prepared.get((source, symbol))
                if prepared is None:
                    return None
                rows, aligned, computed = prepared
            elif fast_mascore:
                latest = fast_latest.get((source, symbol))
                if latest is None and source == "sjc":
                    # Rust's mode=all MASCORE fast path intentionally asks
                    # PostgreSQL for all SJC tickers because its curated Redis
                    # symbol list is empty.
                    try:
                        latest_rows = read_raw(source, symbol, 1 + 100)
                    except DataError:
                        return None
                    if not latest_rows:
                        return None
                    latest = enhance_selected(latest_rows, "1D", [len(latest_rows) - 1], True, ema)[
                        0
                    ]
                if latest is None:
                    return None
                if latest["volume"] < min_volume:
                    return None
                rows = None
            elif algorithm == "mascore":
                ma_buffer = 600 if ema else 200
                effective_limit = 1 if trails == 0 else trails
                read_limit = effective_limit + ma_buffer
                try:
                    rows = read_raw(source, symbol, read_limit)
                except DataError:
                    # The Rust batch path continues with rows from other symbols
                    # when a series has a gap or pending repair.
                    return None
            else:
                read_limit = jdk_limit
                try:
                    rows = read_raw(source, symbol, read_limit)
                except DataError:
                    # The Rust batch path continues with rows from other symbols
                    # when a series has a gap or pending repair.
                    return None
            if (
                algorithm == "mascore"
                and not fast_mascore
                and (not rows or rows[-1].volume < min_volume)
            ):
                return None
            if algorithm == "mascore":
                if fast_mascore:
                    x = latest.get("ma20_score")
                    y = latest.get("ma100_score")
                    latest_close = latest["close"]
                    latest_volume = latest["volume"]
                    points = [{"date": latest["time"], "rs_ratio": x, "rs_momentum": y}]
                else:
                    selected_count = max(1, trails)
                    first_selected = max(0, len(rows) - selected_count)
                    selected = mascore_selected.get((source, symbol))
                    if selected is None:
                        selected = enhance_selected(
                            rows,
                            "1D",
                            range(first_selected, len(rows)),
                            True,
                            ema,
                            periods=(20, 100),
                        )
                    latest_enhanced = selected[-1]
                    x = latest_enhanced.get("ma20_score")
                    y = latest_enhanced.get("ma100_score")
                    if x is None or y is None:
                        # Preserve Rust-compatible fallback for instruments
                        # whose latest MA is absent (for example a futures
                        # series with non-positive prices). This is uncommon;
                        # ordinary positive-price histories need only enhance
                        # the requested trail window.
                        full = enhance_selected(
                            rows,
                            "1D",
                            range(len(rows)),
                            True,
                            ema,
                            periods=(20, 100),
                        )
                        if x is None:
                            x = next(
                                (
                                    row.get("ma20_score")
                                    for row in reversed(full)
                                    if row.get("ma20_score") is not None
                                ),
                                None,
                            )
                        if y is None:
                            y = next(
                                (
                                    row.get("ma100_score")
                                    for row in reversed(full)
                                    if row.get("ma100_score") is not None
                                ),
                                None,
                            )
                    points = [
                        {
                            "date": r["time"],
                            "rs_ratio": r.get("ma20_score", x),
                            "rs_momentum": r.get("ma100_score", y),
                        }
                        for r in selected
                    ]
                    latest_close = rows[-1].close
                    latest_volume = rows[-1].volume
                if x is None or y is None:
                    return None
                raw = 0.0
            else:
                if not computed:
                    return None
                relevant = aligned[-len(computed) :]
                points = [
                    {
                        "date": datetime.fromtimestamp(r.time, UTC).strftime("%Y-%m-%d"),
                        "rs_ratio": x,
                        "rs_momentum": y,
                    }
                    for r, (x, y) in zip(relevant, computed, strict=True)
                ]
                raw = aligned[-1].close / bench[aligned[-1].time]
                latest_close = aligned[-1].close
                latest_volume = rows[-1].volume
            last = points[-1]
            item = {
                "symbol": symbol,
                "rs_ratio": last["rs_ratio"],
                "rs_momentum": last["rs_momentum"],
                "raw_rs": raw,
                "close": latest_close,
                "volume": latest_volume,
                "sector": self._rrg_sector(source, symbol, groups_by_source),
            }
            if mode == "all":
                item["source"] = source
            if trails:
                item["trails"] = points[-trails:]
            return item

        result = [item for item in self.parallel_map(universe, analyze_ticker) if item is not None]
        data = {}
        if algorithm == "jdk":
            data["benchmark"] = benchmark
        data["algorithm"] = algorithm
        if algorithm == "jdk":
            data["period"] = period
        data["tickers"] = sorted(result, key=lambda r: (r["symbol"], r.get("source", "")))
        return envelope(
            "rrg",
            data,
            analysis_date,
            len(result),
        )
