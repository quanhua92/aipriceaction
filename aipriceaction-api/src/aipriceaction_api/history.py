import csv
import logging
import math
import threading
import time
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

from .calculations import (
    aggregate,
    enhance,
    enhance_latest_many,
    enhance_many_selected,
    enhance_selected,
)
from .data_loading import SQLiteDataLoader
from .domain import (
    INDEXES,
    SOURCES,
    Candle,
    DataError,
    base_interval,
    bucket,
    completed_vn_sessions,
    cutoff,
)
from .indicator_cache import IndicatorSnapshot, LatestIndicatorCache
from .redis_zset import RedisZsetReader

log = logging.getLogger(__name__)


def load_rust_ticker_identity_order(path):
    """Load a validated Rust ticker identity export as ``(source, symbol) -> id``.

    The export must have the exact ``id,source,symbol`` header and be strictly
    ordered by positive integer ID. Rejecting malformed and duplicate rows is
    safer than silently applying a partial order to public merged responses.
    """
    if path is None:
        return {}

    path = Path(path)
    identities = {}
    seen_ids = set()
    previous_id = 0
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle, strict=True)
            header = next(reader, None)
            if header != ["id", "source", "symbol"]:
                raise ValueError("expected the exact id,source,symbol header")
            for row_number, row in enumerate(reader, start=2):
                if len(row) != 3:
                    raise ValueError(f"row {row_number} must contain exactly three columns")
                raw_id, source, symbol = row
                if not raw_id.isascii() or not raw_id.isdecimal():
                    raise ValueError(f"row {row_number} has a non-decimal ticker ID")
                ticker_id = int(raw_id)
                if ticker_id <= 0:
                    raise ValueError(f"row {row_number} has a non-positive ticker ID")
                if ticker_id <= previous_id or ticker_id in seen_ids:
                    raise ValueError("ticker IDs must be unique and strictly increasing")
                if source not in SOURCES:
                    raise ValueError(f"row {row_number} has an unsupported source")
                if not symbol:
                    raise ValueError(f"row {row_number} has an empty symbol")
                identity = (source, symbol)
                if identity in identities:
                    raise ValueError(f"row {row_number} duplicates a source/symbol identity")
                identities[identity] = ticker_id
                seen_ids.add(ticker_id)
                previous_id = ticker_id
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ValueError(f"Unable to read Rust ticker identity order CSV: {exc}") from exc

    if not identities:
        raise ValueError("Rust ticker identity order CSV contains no identities")
    return identities


class History:
    def __init__(self, repo, archive, settings, redis_client=None):
        self.repo, self.archive, self.settings = repo, archive, settings
        self.data_loader = SQLiteDataLoader(repo)
        self.indicator_cache = LatestIndicatorCache()
        self.redis_client = redis_client
        self.redis_reader = RedisZsetReader(redis_client)
        self._cache_read_state = threading.local()
        self.rust_ticker_identity_order = load_rust_ticker_identity_order(
            getattr(settings, "rust_ticker_identity_order", None)
        )

    def validate_basis(self, candles, adoption=None, adoption_loaded=False):
        """Validate durable rows while accepting values read from Rust Redis."""
        if candles and all(row.provider == "redis-cache" for row in candles):
            return
        if adoption_loaded:
            self.repo.validate_basis(candles, adoption=adoption)
        else:
            self.repo.validate_basis(candles)

    def clear_cache_read_metadata(self):
        self._cache_read_state.result = None

    def take_cache_read_metadata(self):
        result = getattr(self._cache_read_state, "result", None)
        self._cache_read_state.result = None
        return result

    def ordered_merged_sources(self, sources, symbol):
        """Use Rust's persisted ID order when every merged identity is known.

        Partial exports cannot prove the relative order of all requested source
        identities, so retain the caller's fixture/default order in that case.
        """
        sources = tuple(sources)
        ticker_ids = [self.rust_ticker_identity_order.get((source, symbol)) for source in sources]
        if any(ticker_id is None for ticker_id in ticker_ids):
            return sources
        return tuple(source for _, source in sorted(zip(ticker_ids, sources, strict=True)))

    def cache_tail(self, source, symbol, iv, end=None, limit=252, ignore_pending_repairs=False):
        """Read a simulated retained cache tail without applying DB gap policy."""
        self.clear_cache_read_metadata()
        if self.redis_client is not None:
            if limit <= 0:
                return []
            result = self.redis_reader.read(
                source,
                symbol,
                iv,
                limit=limit,
                max_score_ms=end * 1000 if end is not None else None,
            )
            if result is None:
                return []
            self._cache_read_state.result = result
            rows = [
                Candle(
                    source=source,
                    symbol=symbol,
                    interval=iv,
                    time=row.timestamp_ms // 1000,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    volume=row.volume,
                    provider="redis-cache",
                    revision="redis-cache",
                )
                for row in reversed(result.rows)
            ]
            return rows
        try:
            return self.read(
                source,
                symbol,
                iv,
                end=end,
                limit=limit,
                forward=False,
                ignore_pending_repairs=ignore_pending_repairs,
            )
        except DataError as exc:
            # Rust's Redis ZSET has no history-gap metadata and can return the
            # rows it has even when the DB's completeness ledger marks older
            # history unresolved. Fall back to the live SQLite tail for this
            # cache-only emulation; durable PostgreSQL reads still use read().
            if exc.status != 503 or not str(exc).startswith("Historical data unavailable"):
                raise
            rows = self.repo.read(source, symbol, iv, end=end, limit=limit, forward=False)
            if source == "vn" and iv == "1m" and rows:
                from .volume_corrections import apply_records

                rows = apply_records(
                    rows,
                    self.repo.volume_corrections(source, symbol, iv, rows[0].time, rows[-1].time),
                )
            self.validate_basis(rows)
            return rows

    def cache_raw_limit(self, source, symbol, iv, end=None, limit=0):
        """Read Redis with Rust's signed raw-member limit semantics."""
        self.clear_cache_read_metadata()
        if self.redis_client is None:
            return []
        result = self.redis_reader.read_signed(
            source,
            symbol,
            iv,
            limit=limit,
            max_score_ms=end * 1000 if end is not None else None,
        )
        if result is None:
            return []
        self._cache_read_state.result = result
        return [
            Candle(
                source=source,
                symbol=symbol,
                interval=iv,
                time=row.timestamp_ms // 1000,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
                provider="redis-cache",
                revision="redis-cache",
            )
            for row in reversed(result.rows)
        ]

    def redis_covers_start(self, source, symbol, iv, start):
        """Check the actual Redis ZSET like Rust; durable rows are not cache coverage."""
        native = base_interval(iv)
        if self.redis_client is None:
            return False
        return self.redis_reader.covers_start(source, symbol, native, start * 1000)

    @staticmethod
    def warmup(load, limit):
        """Use the longest verified recent context without crossing a bad older range."""
        try:
            return load(limit)
        except DataError as exc:
            if exc.status != 503:
                raise
        earlier = []
        low, high = 0, limit - 1
        while low < high:
            count = (low + high + 1) // 2
            try:
                candidate = load(count)
            except DataError as exc:
                if exc.status != 503:
                    raise
                high = count - 1
            else:
                earlier, low = candidate, count
        return earlier

    def read(
        self,
        source,
        symbol,
        iv,
        start=None,
        end=None,
        limit=None,
        forward=False,
        revision=None,
        ignore_pending_repairs=False,
        ignore_history_gaps=False,
    ):
        return self.read_many(
            source,
            [symbol],
            iv,
            start=start,
            end=end,
            limit=limit,
            forward=forward,
            revision=revision,
            ignore_pending_repairs=ignore_pending_repairs,
            ignore_history_gaps=ignore_history_gaps,
        )[symbol]

    def read_many(
        self,
        source,
        symbols,
        iv,
        start=None,
        end=None,
        limit=None,
        forward=False,
        revision=None,
        ignore_pending_repairs=False,
        ignore_history_gaps=False,
        return_exceptions=False,
    ):
        """Read one or many identities through the shared SQLite batch loader."""
        symbols = list(dict.fromkeys(symbols))
        loaded = self.data_loader.load_many(
            source,
            symbols,
            iv,
            start=start,
            end=end,
            limit=limit,
            forward=forward,
            copy_result=False,
        )
        results = {}
        read_session = getattr(self.archive, "read_session", None)
        session = read_session() if callable(read_session) else nullcontext()
        with session:
            for symbol in symbols:
                try:
                    results[symbol] = self._read_loaded(
                        source,
                        symbol,
                        iv,
                        start=start,
                        end=end,
                        limit=limit,
                        forward=forward,
                        revision=revision,
                        ignore_pending_repairs=ignore_pending_repairs,
                        ignore_history_gaps=ignore_history_gaps,
                        loaded=loaded,
                    )
                except Exception as exc:
                    if not return_exceptions:
                        raise
                    results[symbol] = exc
        return results

    def _read_loaded(
        self,
        source,
        symbol,
        iv,
        start=None,
        end=None,
        limit=None,
        forward=False,
        revision=None,
        ignore_pending_repairs=False,
        ignore_history_gaps=False,
        loaded=None,
    ):
        """Return chronological native candles; consult only required objects."""
        local = (
            loaded.local[symbol]
            if loaded is not None
            else self.repo.read(source, symbol, iv, start, end, limit, forward=forward)
        )
        if (
            loaded is not None
            and revision is None
            and not loaded.archives[symbol]
            and not loaded.gaps[symbol]
            and not loaded.adoptions[symbol]
            and not loaded.volume_corrections[symbol]
        ):
            # The normal live-history case has already been bounded and sorted
            # by the indexed SQLite query. Avoid rebuilding a timestamp map,
            # sorting it, and rescanning empty evidence tables for every
            # ticker in a multi-symbol analysis request.
            if len(local) > self.settings.archive_max_rows:
                raise DataError("Historical request exceeds resource limit", 400)
            if len({row.revision for row in local}) > 1:
                raise DataError(f"Incompatible adjustment revisions for {symbol} {iv}")
            self.validate_basis(local, adoption=None, adoption_loaded=True)
            return list(local)
        other_local = [r for r in local if revision is not None and r.revision != revision]
        if revision is not None:
            local = [r for r in local if r.revision == revision]
        local_times = {r.time for r in local}
        merged = {r.time: r for r in local}
        if len(merged) > self.settings.archive_max_rows:
            raise DataError("Historical request exceeds resource limit", 400)
        objects = (
            loaded.archives[symbol]
            if loaded is not None
            else self.repo.archives(source, symbol, iv, start, end)
        )
        if ignore_pending_repairs:
            # Latest row endpoints match Rust's durable PostgreSQL reads, which
            # do not consult Python's pending archive-repair ledger. Keep
            # explicit historical requests strict by enabling this only at the
            # undated `/tickers` boundary.
            pending = [obj for obj in objects if obj["status"] == "pending_repair"]
            if pending:
                if not local and start is None and end is None and revision is None:
                    latest_published_end = max(
                        (obj["end"] for obj in objects if obj["status"] == "published"),
                        default=None,
                    )
                    latest_pending_end = max(obj["end"] for obj in pending)
                    if latest_published_end is None or latest_pending_end >= latest_published_end:
                        # A pending-only series has no durable candle for Rust
                        # to return. Likewise, do not turn an older or same-time
                        # published archive into a false latest value when a
                        # repair could contain the newest value.
                        log.warning(
                            "omitting archived rows for latest ticker read with pending repair and no live candles %s %s %s",
                            source,
                            symbol,
                            iv,
                        )
                        return []
                pending_to_keep = []
                if start is None and end is None and local:
                    # A pending archive newer than the newest durable candle
                    # could contain the actual latest value. Keep it strict
                    # rather than returning a stale local row as a success.
                    latest_local_time = max(row.time for row in local)
                    pending_to_keep = [obj for obj in pending if obj["end"] >= latest_local_time]
                ignored_count = len(pending) - len(pending_to_keep)
                if ignored_count:
                    log.warning(
                        "ignoring %d older pending archive repair object(s) for latest ticker read %s %s %s",
                        ignored_count,
                        source,
                        symbol,
                        iv,
                    )
                objects = [
                    obj
                    for obj in objects
                    if obj["status"] != "pending_repair" or obj in pending_to_keep
                ]
        snapshots = [obj for obj in objects if obj["status"] == "historical_snapshot"]
        primary = [obj for obj in objects if obj["status"] != "historical_snapshot"]
        years = {
            "1m": self.settings.minute_years,
            "1h": self.settings.hourly_years,
            "1D": self.settings.daily_years,
        }[iv]
        # Frozen public snapshots can serve wholly expired, explicitly bounded
        # history. They never replace local candles or enter live/default reads.
        if revision is None and not local and end is not None and end < cutoff(years) and snapshots:
            # A preceding context row must not displace a longer primary request.
            known = set()
            for obj in primary:
                try:
                    known.update(
                        row.time
                        for row in self.archive.read(
                            obj, start, end, limit or 1, forward=start is not None or limit is None
                        )
                    )
                except DataError:
                    # The ordinary read/coverage checks retain unreadable-object guards.
                    continue
                if len(known) > self.settings.archive_max_rows:
                    raise DataError("Historical request exceeds resource limit", 400)
            if known:
                needed = (
                    sorted(known)[-limit]
                    if start is None and limit and len(known) >= limit
                    else min(known)
                )
                beginnings = {}
                for obj in snapshots:
                    beginnings[obj["revision"]] = min(
                        obj["start"], beginnings.get(obj["revision"], obj["start"])
                    )
                snapshots = [obj for obj in snapshots if beginnings[obj["revision"]] <= needed]
            if snapshots:
                revision = max(
                    snapshots, key=lambda obj: (min(obj["end"], end), obj["created_at"])
                )["revision"]
        if revision is None:
            objects = primary
        other_objects = [
            obj for obj in primary if revision is not None and obj["revision"] != revision
        ]
        if revision is not None:
            objects = [obj for obj in objects if obj["revision"] == revision]
        if forward:
            objects.sort(key=lambda obj: (obj["start"], obj["end"]))
        for obj in objects:
            # If local/current objects already contain enough later rows, older
            # objects cannot change this limit-only result or its adjustment basis.
            boundary = (
                sorted(merged)[limit - 1 if forward else -limit]
                if limit and len(merged) >= limit
                else None
            )
            if boundary is not None and (
                obj["start"] > boundary if forward else obj["end"] < boundary
            ):
                continue
            if obj["status"] == "pending_repair":
                raise DataError(f"Historical adjustment repair pending for {symbol} {iv}")
            fetched = self.archive.read(obj, start, end, limit, forward=forward)
            for row in fetched:
                current = merged.get(row.time)
                if current is None or (
                    row.time not in local_times and row.updated_at > current.updated_at
                ):
                    merged[row.time] = row
            if len(merged) > self.settings.archive_max_rows:
                raise DataError("Historical request exceeds resource limit", 400)
        if revision is not None and (not limit or len(merged) < limit):
            # A pinned context must not hide older observations by returning a
            # shorter lookback when only another adjustment basis contains them.
            if any(r.time not in merged for r in other_local):
                raise DataError(f"Incompatible adjustment revisions for {symbol} {iv}")
            for obj in other_objects:
                try:
                    fetched = self.archive.read(obj, start, end, limit, forward=forward)
                except DataError as exc:
                    if obj["status"] == "pending_repair":
                        raise DataError(
                            f"Historical adjustment repair pending for {symbol} {iv}"
                        ) from exc
                    raise
                if any(r.time not in merged for r in fetched):
                    if obj["status"] == "pending_repair":
                        raise DataError(f"Historical adjustment repair pending for {symbol} {iv}")
                    raise DataError(f"Incompatible adjustment revisions for {symbol} {iv}")
        result = sorted(merged.values(), key=lambda r: r.time)
        if limit:
            result = result[:limit] if forward else result[-limit:]
        lower, upper = start, end
        if limit and len(result) >= limit:
            # Only the range actually needed to satisfy this directional limit
            # can block the read. Recent charts need not inspect unrelated years.
            if forward:
                upper = result[-1].time
            else:
                lower = result[0].time
        if upper is None:
            upper = int(time.time())
        if loaded is None:
            gaps = self.repo.history_gaps(source, symbol, iv, lower, upper)
        else:
            gaps = [
                gap
                for gap in loaded.gaps[symbol]
                if (lower is None or gap["end"] >= lower)
                and (upper is None or gap["start"] <= upper)
            ]
        gaps = [
            gap
            for gap in gaps
            if not (
                gap["evidence"].get("kind") == "public_year_partition_basis"
                and gap["evidence"].get("revision") == revision
                and any(
                    obj["status"] == "historical_snapshot"
                    and obj["revision"] == revision
                    and obj["id"] == gap["evidence"].get("snapshot_id")
                    for obj in objects
                )
            )
        ]
        if gaps and not ignore_history_gaps:
            raise DataError(f"Historical data unavailable for {symbol} {iv}: {gaps[0]['reason']}")
        if len({r.revision for r in result}) > 1:
            raise DataError(f"Incompatible adjustment revisions for {symbol} {iv}")
        if loaded is None:
            self.validate_basis(result)
        else:
            adoption = loaded.adoptions[symbol].get(result[0].revision) if result else None
            self.validate_basis(result, adoption=adoption, adoption_loaded=True)
        if source == "vn" and iv == "1m" and result:
            from .volume_corrections import apply_records

            result = apply_records(
                result,
                (
                    [
                        record
                        for record in loaded.volume_corrections[symbol]
                        if result[0].time <= record["time"] <= result[-1].time
                    ]
                    if loaded is not None
                    else self.repo.volume_corrections(
                        source, symbol, iv, result[0].time, result[-1].time
                    )
                ),
            )
        return result

    def native_interval(self, source, symbol, iv):
        native = base_interval(iv)
        if native == "1h" and not self.repo.state(source, symbol, native):
            # Internal history consumers can derive hourly candles from minute
            # data. The public API bypasses this helper for Rust's native rules.
            if not self.repo.archives(source, symbol, native):
                return "1m"
        return native

    def _native_target(
        self,
        source,
        symbol,
        iv,
        start,
        end,
        limit,
        forward=False,
        strict_source_intervals=False,
        ignore_pending_repairs=False,
    ):
        if strict_source_intervals:
            return self.read(
                source,
                symbol,
                iv,
                start,
                end,
                limit,
                forward=forward,
                ignore_pending_repairs=ignore_pending_repairs,
            )
        if source == "vn" and iv == "1h":
            return self.vn_hourly_target(symbol, start, end, limit)
        if source == "vn" and iv == "1D":
            return self.vn_daily_target(symbol, start, end, limit, forward=forward)
        if source == "vn" and iv == "1m":
            return self.vn_minute_target(symbol, start, end, limit, forward=forward)
        return self.read(
            source,
            symbol,
            iv,
            start,
            end,
            limit,
            forward=forward,
            ignore_pending_repairs=ignore_pending_repairs,
        )

    @staticmethod
    def same_ohlc(left, right):
        return all(
            math.isclose(getattr(left, name), getattr(right, name), rel_tol=1e-12, abs_tol=1e-9)
            for name in ("open", "high", "low", "close")
        )

    @classmethod
    def same_session(cls, rows, daily):
        return (
            bool(rows)
            and cls.same_ohlc(
                replace(
                    rows[0],
                    high=max(row.high for row in rows),
                    low=min(row.low for row in rows),
                    close=rows[-1].close,
                ),
                daily,
            )
            and sum(row.volume for row in rows) == daily.volume
        )

    @staticmethod
    def vn_cash_index_minutes(symbol, rows):
        """Remove synthetic quotes outside the cash-index trading session."""
        if symbol not in INDEXES or symbol == "VN30F1M":
            return rows
        morning_open = 2 * 3600 + (15 * 60 if symbol in {"VNINDEX", "VN30"} else 0)
        return [
            row
            for row in rows
            if morning_open <= row.time % 86400 <= 4 * 3600 + 30 * 60
            or 6 * 3600 <= row.time % 86400 <= 7 * 3600 + 45 * 60
        ]

    def vn_minute_target(self, symbol, start, end, limit, forward=False):
        if symbol not in INDEXES or symbol == "VN30F1M":
            return self.read("vn", symbol, "1m", start, end, limit, forward=forward)
        # Read a few extra native rows so removing synthetic pre/post-session
        # quotes does not shorten a normal limit-only API response.
        rows = self.read("vn", symbol, "1m", start, end, limit + 16, forward=forward)
        rows = self.vn_cash_index_minutes(symbol, rows)
        return rows[:limit] if forward else rows[-limit:]

    def vn_daily_target(self, symbol, start, end, limit=None, forward=False, revision=None):
        """Overlay completed recent VND daily candles from certified local minutes."""
        native = self.read(
            "vn", symbol, "1D", start, end, limit, forward=forward, revision=revision
        )
        if not native:
            return []
        minute_state = self.repo.state("vn", symbol, "1m")
        cash_index = symbol in INDEXES and symbol != "VN30F1M"
        if not minute_state or (not cash_index and minute_state["provider"] not in {"vps", "dnse"}):
            return native
        minute_cutoff = cutoff(self.settings.minute_years)
        completed_before = completed_vn_sessions()
        first = max(native[0].time, minute_cutoff)
        last = min(native[-1].time + 86399, completed_before - 1)
        if first > last:
            return native
        minutes = self.vn_cash_index_minutes(
            symbol, self.repo.read("vn", symbol, "1m", first, last)
        )
        groups = {}
        for row in minutes:
            groups.setdefault(row.time // 86400 * 86400, []).append(row)
        minute_providers = {"vps", "dnse", "vndirect"} if cash_index else {"vps", "dnse"}
        result = []
        for row in native:
            observed = groups.get(row.time)
            if (
                (row.provider != "vndirect" and not cash_index)
                or not observed
                or row.time >= completed_before
                or any(item.provider not in minute_providers for item in observed)
            ):
                result.append(row)
                continue
            result.append(
                replace(
                    row,
                    open=observed[0].open,
                    high=max(item.high for item in observed),
                    low=min(item.low for item in observed),
                    close=observed[-1].close,
                    volume=sum(item.volume for item in observed),
                    updated_at=max(row.updated_at, *(item.updated_at for item in observed)),
                )
            )
        return result

    def vn_hourly_target(self, symbol, start, end, limit):
        """Overlay recent minute-derived buckets without shortening native history."""
        native = self.read("vn", symbol, "1h", start, end, limit, forward=start is not None)
        minute_cutoff = cutoff(self.settings.minute_years)
        if end is not None and end < minute_cutoff:
            return native
        if not self.repo.state("vn", symbol, "1m"):
            return native
        try:
            derived = self.aggregated(
                "vn",
                symbol,
                "1h",
                max(start, minute_cutoff) if start is not None else None,
                end,
                # Include a complete VN session around a small latest limit so
                # daily corroboration can prove every hourly replacement.
                min(limit + 8, self.settings.archive_max_rows),
                native="1m",
            )
        except DataError:
            return native
        if not derived or derived[-1].time < minute_cutoff:
            return native
        # An archive-only hourly identity has no active adjustment basis to
        # merge with. Recent reads use the verified minute series by itself;
        # explicitly old reads above already remain on native hourly history.
        if not self.repo.state("vn", symbol, "1h"):
            return derived[:limit] if start is not None else derived[-limit:]
        if not native:
            return derived[:limit] if start is not None else derived[-limit:]

        merged = {row.time: row for row in native}
        groups = {}
        for row in derived:
            groups.setdefault(row.time // 86400 * 86400, []).append(row)
        corroborated = set()
        completed_before = completed_vn_sessions()
        if groups:
            try:
                daily = self.vn_daily_target(symbol, min(groups), max(groups))
            except DataError:
                daily = []
            daily_by_time = {row.time: row for row in daily}
            corroborated = {
                day
                for day, rows in groups.items()
                if day < completed_before
                and day in daily_by_time
                and self.same_session(rows, daily_by_time[day])
            }
        overlaps = {
            row.time
            for row in derived
            if row.time in merged and self.same_ohlc(merged[row.time], row)
        }
        latest_native = native[-1]
        anchored = bool(overlaps)
        for row in derived:
            basis = merged.get(row.time)
            day = row.time // 86400 * 86400
            if basis is not None:
                if row.time not in overlaps and day not in corroborated:
                    continue
            else:
                if day not in corroborated and (row.time <= latest_native.time or not anchored):
                    continue
                basis = latest_native
            # Query-only rows inherit the verified hourly adjustment identity.
            # OHLC equality on overlap proves that only the finer-grained
            # volume/session representation is being substituted.
            merged[row.time] = replace(
                row,
                interval="1h",
                provider=basis.provider,
                revision=basis.revision,
                updated_at=max(row.updated_at, basis.updated_at),
            )
        rows = sorted(merged.values(), key=lambda row: row.time)
        return rows[:limit] if start is not None else rows[-limit:]

    def aggregated(
        self,
        source,
        symbol,
        iv,
        start=None,
        end=None,
        limit=252,
        native=None,
        revision=None,
        ignore_pending_repairs=False,
    ):
        """Expand recent native reads until enough complete output buckets exist."""
        native = native or self.native_interval(source, symbol, iv)
        factor = (
            {"1h": 60, "4h": 240}[iv]
            if native == "1m" and iv in ("1h", "4h")
            else {"5m": 5, "15m": 15, "30m": 30, "4h": 4, "1W": 7, "2W": 14, "1M": 31}[iv]
        )
        count = min(limit + 1, self.settings.archive_max_rows)
        # Legacy dated aggregation clips native observations first. The first
        # output bucket can therefore start before the requested date.
        lower = start
        forward = start is not None
        while True:
            rows = self.read(
                source,
                symbol,
                native,
                lower,
                end,
                count,
                forward=forward,
                revision=revision,
                ignore_pending_repairs=ignore_pending_repairs,
            )
            if source == "vn" and native == "1m":
                rows = self.vn_cash_index_minutes(symbol, rows)
            if not rows:
                return []
            bars = aggregate(rows, iv, source, self.validate_basis)
            selected = bars
            if len(selected) > limit or len(rows) < count:
                if forward:
                    target = selected[:limit]
                    if not target:
                        return []
                    last = target[-1].time
                    size = factor * (60 if native == "1m" else 3600 if native == "1h" else 86400)
                    tail = self.read(
                        source,
                        symbol,
                        native,
                        max(last, start),
                        min(last + size, end) if end is not None else last + size,
                        revision=rows[0].revision,
                        ignore_pending_repairs=ignore_pending_repairs,
                    )
                    tail = [r for r in tail if bucket(r.time, iv, source) == last]
                    completed = aggregate(
                        [r for r in rows if r.time < last] + tail,
                        iv,
                        source,
                        self.validate_basis,
                    )
                    return completed[:limit]
                # The first fetched bucket may be partial. Read its complete
                # native range before publishing any selected output candle.
                first = bucket(rows[0].time, iv, source)
                prefix = self.read(
                    source,
                    symbol,
                    native,
                    first,
                    rows[0].time - 1,
                    revision=rows[0].revision,
                    ignore_pending_repairs=ignore_pending_repairs,
                )
                bars = aggregate(prefix + rows, iv, source, self.validate_basis)
                return bars[-limit:]
            if count == self.settings.archive_max_rows:
                raise DataError("Historical request exceeds resource limit", 400)
            # Grow from observed bucket density rather than assuming every
            # calendar day trades. Unneeded older data must not block an SMA
            # whose full period is already covered by valid recent candles.
            count = min(
                max(count + 1, math.ceil(count * (limit + 1) / len(selected)) + factor),
                self.settings.archive_max_rows,
            )

    def query(
        self,
        source,
        symbol,
        iv,
        start=None,
        end=None,
        limit=252,
        ma=True,
        ema=False,
        redis=True,
        strict_source_intervals=False,
        redis_covered=None,
        ignore_pending_repairs=False,
        periods=None,
    ):
        native = (
            base_interval(iv)
            if strict_source_intervals
            else self.native_interval(source, symbol, iv)
        )
        buffer = 600 if ma and ema else 200 if ma else 1
        if strict_source_intervals and native != iv:
            # Rust's aggregated route reads raw base candles first. For a
            # dated range covered by Redis, both Redis and PostgreSQL paths use
            # the retained native window, enhance the aggregates, then filter
            # by output bucket timestamps. Older uncovered ranges use bounded
            # historical SQL reads.
            factor = {
                "5m": 5,
                "15m": 15,
                "30m": 30,
                "4h": 4,
                "1W": 5,
                "2W": 10,
                "1M": 23,
            }[iv]
            agg_buffer = 5000 if ma else factor
            lookback = limit * factor + agg_buffer
            retained = {
                "1D": self.settings.redis_daily_max_size,
                "1h": self.settings.redis_hourly_max_size,
                "1m": self.settings.redis_minute_max_size,
            }.get(native, 0)
            if redis and (retained > 0 or self.redis_client is not None):
                # Rust applies end scores and raw limits in Redis before
                # parsing. Start-date scans read the retained ZSET plus the
                # indicator buffer; offline SQLite emulation first caps to the
                # retained tail because it has no physical ZSET to query.
                if self.redis_client is not None:
                    if start is not None:
                        tail_limit, tail_end = retained + agg_buffer, None
                    elif lookback > 0:
                        tail_limit, tail_end = lookback, end
                    else:
                        tail_limit, tail_end = retained, None
                else:
                    tail_limit, tail_end = retained, None
                if self.redis_client is not None and start is None and lookback <= 0:
                    # Rust forwards signed aggregate lookbacks to the raw ZSET
                    # range. In particular, zero means ZREVRANGE 0 -1 (all
                    # members), while an end-bounded zero count selects none.
                    tail = self.cache_raw_limit(
                        source,
                        symbol,
                        native,
                        end=end,
                        limit=lookback,
                    )
                else:
                    tail = self.cache_tail(
                        source,
                        symbol,
                        native,
                        end=tail_end,
                        limit=tail_limit,
                        ignore_pending_repairs=ignore_pending_repairs,
                    )
                context = []
                if (
                    start is not None
                    and tail
                    and (redis_covered is True or (redis_covered is None and start >= tail[0].time))
                ):
                    context = tail
                elif start is None:
                    if end is None:
                        if lookback == 0:
                            context = tail  # ZREVRANGE 0 -1 returns all members.
                        elif lookback < 0:
                            context = tail if self.redis_client is not None else tail[-lookback:]
                        else:
                            context = (
                                tail[-lookback:]
                                if self.redis_client is not None
                                else tail[-min(retained, lookback) :]
                            )
                    elif tail and end >= tail[0].time:
                        available = [row for row in tail if row.time <= end]
                        if lookback < 0:
                            context = available
                        elif lookback > 0:
                            context = (
                                available[-lookback:]
                                if self.redis_client is not None
                                else available[-min(retained, lookback) :]
                            )
                if context:
                    if len({row.revision for row in context}) > 1:
                        raise DataError(f"Incompatible adjustment revisions for {symbol} {native}")
                    self.validate_basis(context)
                    bars = aggregate(context, iv, source, self.validate_basis)
                    computed = enhance(bars, iv, ma, ema, periods=periods)
                    selected = [
                        result
                        for bar, result in zip(bars, computed, strict=True)
                        if (start is None or bar.time >= start) and (end is None or bar.time <= end)
                    ]
                    if start is not None:
                        return selected[:limit] if limit >= 0 else selected
                    if limit == 0:
                        return []
                    return selected[-limit:] if limit > 0 else selected
                if redis_covered is True:
                    # Rust treats this as an empty per-symbol cache result. The
                    # request layer retries the whole source batch from SQL
                    # only when every symbol was empty.
                    return []

            if lookback <= 0:
                return []
                # Keep historical requests outside the retained cache window
                # bounded to their requested raw-candle range.
            start_range_covered = start is not None and redis_covered is True
            context = self.read(
                source,
                symbol,
                native,
                None if start_range_covered else start,
                None if start_range_covered else end,
                retained if start_range_covered and retained > 0 else lookback,
                forward=False if start_range_covered else start is not None,
                ignore_pending_repairs=ignore_pending_repairs,
            )
            if not context:
                return []
            if len({row.revision for row in context}) > 1:
                raise DataError(f"Incompatible adjustment revisions for {symbol} {native}")
            self.validate_basis(context)
            bars = aggregate(context, iv, source, self.validate_basis)
            computed = enhance(bars, iv, ma, ema, periods=periods)
            if start is not None:
                if start_range_covered:
                    selected = [
                        result
                        for bar, result in zip(bars, computed, strict=True)
                        if bar.time >= start and (end is None or bar.time <= end)
                    ]
                    return selected[:limit] if limit >= 0 else selected
                return computed[:limit] if limit >= 0 else computed
            if limit == 0:
                return []
            return computed[-limit:] if limit > 0 else computed

        if strict_source_intervals and redis and start is not None:
            retained = {
                "1D": self.settings.redis_daily_max_size,
                "1h": self.settings.redis_hourly_max_size,
                "1m": self.settings.redis_minute_max_size,
            }.get(native, 0)
            if retained > 0 or self.redis_client is not None:
                # Rust scans the retained ZSET plus the indicator buffer
                # before applying date filters. A configured Redis client
                # needs that exact command limit for diagnostics; the local
                # SQLite emulation remains capped to retained rows.
                tail_limit = retained + buffer if self.redis_client is not None else retained
                context = self.cache_tail(
                    source,
                    symbol,
                    iv,
                    limit=tail_limit,
                    ignore_pending_repairs=ignore_pending_repairs,
                )
                if context and (
                    redis_covered is True or (redis_covered is None and start >= context[0].time)
                ):
                    selected = [
                        row
                        for row in context
                        if row.time >= start and (end is None or row.time <= end)
                    ]
                    if limit is not None and limit >= 0:
                        selected = selected[:limit]
                    if not selected:
                        return []
                    if len({row.revision for row in context}) > 1:
                        raise DataError(f"Incompatible adjustment revisions for {symbol} {native}")
                    self.validate_basis(context)
                    selected_times = {row.time for row in selected}
                    computed = enhance(context, iv, ma, ema, periods=periods)
                    return [
                        result
                        for row, result in zip(context, computed, strict=True)
                        if row.time in selected_times
                    ]
                if redis_covered is True and not context:
                    return []
        if limit < 0 and native == iv:
            raw_limit = limit + buffer if ma else limit + 1
            retained = {
                "1D": self.settings.redis_daily_max_size,
                "1h": self.settings.redis_hourly_max_size,
                "1m": self.settings.redis_minute_max_size,
            }.get(native, 0)
            if start is not None:
                # PostgreSQL applies the duration-based warmup in SQL, caps
                # the ascending input at limit+buffer, then filters start.
                # A negative SQL LIMIT is an error and Rust returns an empty
                # result map for this query.
                if raw_limit <= 0:
                    return []
                duration_minutes = {"1m": 1, "1h": 60, "1D": 2016}[iv]
                effective_start = start - (duration_minutes * buffer * 60 if ma else 0)
                context = self._native_target(
                    source,
                    symbol,
                    iv,
                    effective_start,
                    end,
                    raw_limit,
                    forward=True,
                    strict_source_intervals=strict_source_intervals,
                )
                selected = [row for row in context if row.time >= start]
                if not selected:
                    return []
                if len({row.revision for row in context}) > 1:
                    raise DataError(f"Incompatible adjustment revisions for {symbol} {native}")
                self.validate_basis(context)
                selected_times = {row.time for row in selected}
                computed = enhance(context, iv, ma, ema, periods=periods)
                return [
                    result
                    for row, result in zip(context, computed, strict=True)
                    if row.time in selected_times
                ]

            if raw_limit <= 0:
                if not redis:
                    return []
                if retained <= 0 and self.redis_client is None:
                    return []
                if self.redis_client is not None:
                    # Rust applies signed limits to the raw ZSET members
                    # before parsing and records that exact value in headers.
                    context = self.cache_raw_limit(source, symbol, iv, end, raw_limit)
                else:
                    # SQLite-backed cache emulation contains only parsed rows,
                    # so approximate the ZSET rank behavior on that retained
                    # tail. A negative ZREVRANGE end omits oldest members;
                    # a negative score-bounded count includes all matches.
                    if end is not None and raw_limit == 0:
                        return []
                    context = self.cache_tail(
                        source,
                        symbol,
                        iv,
                        limit=retained,
                        ignore_pending_repairs=ignore_pending_repairs,
                    )
                    if end is not None:
                        context = [row for row in context if row.time <= end]
                    elif raw_limit < 0:
                        context = context[min(-raw_limit, len(context)) :]
                # Rust filters the parsed candle timestamp after Redis has
                # selected members by score. Preserve the raw/aligned Redis
                # metadata captured above before applying this request bound.
                if end is not None:
                    context = [row for row in context if row.time <= end]
            else:
                if redis:
                    if retained <= 0 and self.redis_client is None:
                        return []
                    # Rust applies the score bound and raw limit before parsing.
                    # Preserve the exact command limit for configured Redis;
                    # offline emulation caps its SQLite tail to retention first.
                    if end is not None and self.redis_client is not None:
                        context = self.cache_tail(
                            source,
                            symbol,
                            iv,
                            end=end,
                            limit=raw_limit,
                            ignore_pending_repairs=ignore_pending_repairs,
                        )
                    else:
                        context = self.cache_tail(
                            source,
                            symbol,
                            iv,
                            limit=raw_limit if self.redis_client is not None else retained,
                            ignore_pending_repairs=ignore_pending_repairs,
                        )
                        if end is not None:
                            context = [row for row in context if row.time <= end]
                    # Redis bounds members by ZSET score, but Rust also filters
                    # the parsed candle timestamp after the read. A malformed
                    # or inconsistent score/member pair must not leak a candle
                    # beyond the requested end date.
                    if end is not None:
                        context = [row for row in context if row.time <= end]
                    context = context[-raw_limit:]
                else:
                    # A SQL LIMIT smaller than the available history is a
                    # valid partial result. Ignore older gap markers that
                    # fall outside the requested tail, as Postgres does.
                    context = self.read(
                        source,
                        symbol,
                        iv,
                        end=end,
                        limit=raw_limit,
                        forward=False,
                        ignore_pending_repairs=ignore_pending_repairs,
                        # Rust's LIMIT-based latest SQL read has no history-gap
                        # ledger. A gap older than the available tail must not
                        # reject rows the query actually found, even when the
                        # SQL warmup count exceeds the available row count.
                        ignore_history_gaps=True,
                    )
            if not context:
                return []
            if len({row.revision for row in context}) > 1:
                raise DataError(f"Incompatible adjustment revisions for {symbol} {native}")
            self.validate_basis(context)
            return enhance(context, iv, ma, ema, periods=periods)
        if strict_source_intervals and redis and native == iv and start is None and limit >= 0:
            retained = {
                "1D": self.settings.redis_daily_max_size,
                "1h": self.settings.redis_hourly_max_size,
                "1m": self.settings.redis_minute_max_size,
            }.get(native, 0)
            if retained > 0 or self.redis_client is not None:
                # Rust's no-start native Redis path reads at most limit+buffer
                # rows from the capped ZSET, optionally score-bounded by end.
                raw_limit = limit + buffer
                # Rust applies the score bound and limit before parsing. When
                # Redis is configured, request exactly that bounded raw tail;
                # otherwise cap the SQLite emulation to retained rows before
                # applying the requested end date.
                if end is not None and self.redis_client is not None:
                    tail_limit, tail_end = raw_limit, end
                elif end is not None:
                    tail_limit, tail_end = retained, None
                elif self.redis_client is not None:
                    tail_limit, tail_end = raw_limit, None
                else:
                    tail_limit, tail_end = min(retained, max(1, raw_limit)), None
                tail = self.cache_tail(
                    source,
                    symbol,
                    iv,
                    end=tail_end,
                    limit=tail_limit,
                    ignore_pending_repairs=ignore_pending_repairs,
                )
                available = [row for row in tail if end is None or row.time <= end]
                context = available[-raw_limit:] if raw_limit > 0 else []
                if context:
                    if len({row.revision for row in context}) > 1:
                        raise DataError(f"Incompatible adjustment revisions for {symbol} {native}")
                    self.validate_basis(context)
                    computed = enhance(context, iv, ma, ema, periods=periods)
                    return computed[-limit:] if limit > 0 else []
                if redis_covered is True:
                    return []
        # Fetch native bars before aggregation, then acquire enough *aggregated*
        # lookback buckets. This avoids a calendar-day approximation for MA200.
        if native == iv:
            if strict_source_intervals and start is not None:
                # Rust's PostgreSQL path shifts a dated query backward by its
                # MA warmup duration, fetches up to limit + buffer rows in
                # ascending order, computes indicators across that context,
                # then returns the oldest requested rows from start_date.
                # Without MA it still fetches limit + 1 rows, matching the
                # shared query's change-indicator context.
                raw_limit = limit + buffer if ma else limit + 1
                if raw_limit <= 0:
                    return []
                duration_minutes = {"1m": 1, "1h": 60, "1D": 2016}[iv]
                effective_start = start - (duration_minutes * buffer * 60 if ma else 0)
                context = self._native_target(
                    source,
                    symbol,
                    iv,
                    effective_start,
                    end,
                    raw_limit,
                    forward=True,
                    strict_source_intervals=True,
                )
                selected = [row for row in context if row.time >= start]
                if limit >= 0:
                    selected = selected[:limit]
                if not selected:
                    return []
                if len({row.revision for row in context}) > 1:
                    raise DataError(f"Incompatible adjustment revisions for {symbol} {native}")
                self.validate_basis(context)
                selected_times = {row.time for row in selected}
                computed = enhance(context, iv, ma, ema, periods=periods)
                return [
                    result
                    for row, result in zip(context, computed, strict=True)
                    if row.time in selected_times
                ]

            target = self._native_target(
                source,
                symbol,
                iv,
                start,
                end,
                limit,
                forward=start is not None,
                strict_source_intervals=strict_source_intervals,
                ignore_pending_repairs=ignore_pending_repairs,
            )
            if not target:
                return []
            if iv == "1h" and target[0].interval == "1m":
                earlier = self.warmup(
                    lambda count: self.aggregated(
                        source,
                        symbol,
                        iv,
                        end=target[0].time - 1,
                        limit=count,
                        native="1m",
                        revision=target[0].revision,
                        ignore_pending_repairs=ignore_pending_repairs,
                    ),
                    buffer,
                )
            else:
                if source == "vn" and iv == "1D" and not strict_source_intervals:
                    earlier = self.warmup(
                        lambda count: self.vn_daily_target(
                            symbol,
                            None,
                            target[0].time - 1,
                            count,
                            revision=target[0].revision,
                        ),
                        buffer,
                    )
                else:
                    earlier = self.warmup(
                        lambda count: self.read(
                            source,
                            symbol,
                            native,
                            end=target[0].time - 1,
                            limit=count,
                            revision=target[0].revision,
                            ignore_pending_repairs=ignore_pending_repairs,
                        ),
                        buffer,
                    )
            rows = earlier + target
        else:
            target = self.aggregated(
                source,
                symbol,
                iv,
                start,
                end,
                limit,
                native=native,
                ignore_pending_repairs=ignore_pending_repairs,
            )
            if not target:
                return []
            earlier = self.warmup(
                lambda count: self.aggregated(
                    source,
                    symbol,
                    iv,
                    end=target[0].time - 1,
                    limit=count,
                    native=native,
                    revision=target[0].revision,
                    ignore_pending_repairs=ignore_pending_repairs,
                ),
                buffer,
            )
            rows = earlier + target
        if len({r.revision for r in rows}) > 1:
            raise DataError(f"Incompatible adjustment revisions for {symbol} {native}")
        self.validate_basis(rows)
        result = enhance(rows, iv, ma, ema, periods=periods)
        return result[-len(target) :]

    def _query_many_latest_ma(
        self,
        source,
        symbols,
        iv,
        context_limit,
        ema,
        redis,
        ignore_pending_repairs,
        return_exceptions,
        periods=None,
    ):
        """Fast latest MA rows while retaining archive and data-quality policy."""
        loaded = self.data_loader.load_many(
            source,
            symbols,
            iv,
            limit=context_limit,
            forward=False,
            compact_tail=True,
            copy_result=False,
        )
        safe_contexts = {}
        fallback_symbols = []
        results = {}
        latest_read_upper = int(time.time())

        for symbol in symbols:
            tail = loaded.compact_tails.get(symbol)
            if tail is None:
                if loaded.archives[symbol] or loaded.gaps[symbol] or loaded.adoptions[symbol]:
                    fallback_symbols.append(symbol)
                else:
                    results[symbol] = []
                continue

            local = loaded.local[symbol]
            primary_archives = [
                record
                for record in loaded.archives[symbol]
                if record["status"] != "historical_snapshot"
            ]
            needs_fallback = bool(loaded.adoptions[symbol] or loaded.volume_corrections[symbol])
            if not local:
                needs_fallback |= bool(primary_archives or loaded.gaps[symbol])
            else:
                latest_time = local[-1].time
                complete_context = len(tail.closes) >= context_limit
                lower = tail.oldest_time if complete_context else None
                for record in primary_archives:
                    if (
                        ignore_pending_repairs
                        and record["status"] == "pending_repair"
                        and record["end"] < latest_time
                    ):
                        continue
                    if not complete_context or record["end"] >= lower:
                        needs_fallback = True
                        break
                if any(
                    (lower is None or gap["end"] >= lower) and gap["start"] <= latest_read_upper
                    for gap in loaded.gaps[symbol]
                ):
                    needs_fallback = True

            if needs_fallback or tail.mixed_revision or tail.mixed_provider:
                fallback_symbols.append(symbol)
                continue
            safe_contexts[symbol] = local

        snapshots = [
            IndicatorSnapshot.from_loaded(
                source,
                symbol,
                iv,
                loaded.versions.get(symbol, 0),
                loaded.compact_tails[symbol],
                safe_contexts[symbol],
            )
            for symbol in safe_contexts
        ]
        results.update(self.indicator_cache.resolve(snapshots, ema=ema, periods=periods))
        if fallback_symbols:
            try:
                fallback_contexts = self.read_many(
                    source,
                    fallback_symbols,
                    iv,
                    limit=context_limit,
                    forward=False,
                    ignore_pending_repairs=ignore_pending_repairs,
                    return_exceptions=True,
                )
            except Exception:
                # If batch metadata itself is invalid, preserve per-symbol
                # isolation by using the established query path for this rare
                # malformed-state case.
                fallback_contexts = {}
                for symbol in fallback_symbols:
                    try:
                        results[symbol] = self.query(
                            source,
                            symbol,
                            iv,
                            start=None,
                            end=None,
                            limit=1,
                            ma=True,
                            ema=ema,
                            redis=redis,
                            strict_source_intervals=True,
                            ignore_pending_repairs=ignore_pending_repairs,
                            periods=periods,
                        )
                    except Exception as exc:
                        if not return_exceptions:
                            raise
                        results[symbol] = exc
            for symbol in fallback_symbols:
                if symbol not in fallback_contexts:
                    continue
                context = fallback_contexts[symbol]
                if isinstance(context, Exception):
                    # The established per-symbol path can shorten MA warmup
                    # around a history gap. A bulk read cannot make that
                    # choice independently for each symbol, so retry this
                    # exceptional identity through the compatibility path.
                    try:
                        results[symbol] = self.query(
                            source,
                            symbol,
                            iv,
                            start=None,
                            end=None,
                            limit=1,
                            ma=True,
                            ema=ema,
                            redis=redis,
                            strict_source_intervals=True,
                            ignore_pending_repairs=ignore_pending_repairs,
                            periods=periods,
                        )
                    except Exception as exc:
                        if not return_exceptions:
                            raise
                        results[symbol] = exc
                elif context:
                    results[symbol] = enhance_selected(
                        context,
                        iv,
                        [len(context) - 1],
                        ma=True,
                        ema=ema,
                        periods=periods,
                    )
                else:
                    results[symbol] = []
        return results

    def query_many(
        self,
        source,
        symbols,
        iv,
        start=None,
        end=None,
        limit=252,
        ma=True,
        ema=False,
        redis=True,
        strict_source_intervals=False,
        ignore_pending_repairs=False,
        return_exceptions=False,
        periods=None,
    ):
        """Query many symbols through one native latest-tail SQLite load.

        Redis, dated, aggregated, and non-native requests retain the established
        per-symbol query implementation. The common DB-backed latest request
        shares one SQLite snapshot and only enhances the rows it returns.
        """
        symbols = list(dict.fromkeys(symbols))
        native = base_interval(iv) if strict_source_intervals else iv
        retained = {
            "1D": self.settings.redis_daily_max_size,
            "1h": self.settings.redis_hourly_max_size,
            "1m": self.settings.redis_minute_max_size,
        }.get(native, 0)
        fast = (
            bool(symbols)
            and self.redis_client is None
            and strict_source_intervals
            and native == iv
            and start is None
            and end is None
            and limit > 0
        )
        if not fast:
            results = {}
            for symbol in symbols:
                try:
                    results[symbol] = self.query(
                        source,
                        symbol,
                        iv,
                        start=start,
                        end=end,
                        limit=limit,
                        ma=ma,
                        ema=ema,
                        redis=redis,
                        strict_source_intervals=strict_source_intervals,
                        ignore_pending_repairs=ignore_pending_repairs,
                        periods=periods,
                    )
                except Exception as exc:
                    if not return_exceptions:
                        raise
                    results[symbol] = exc
            return results

        buffer = 600 if ma and ema else 200 if ma else 1
        raw_limit = limit + buffer if ma else limit + 1
        context_limit = min(retained, max(1, raw_limit)) if redis and retained > 0 else raw_limit
        if limit == 1 and ma:
            return self._query_many_latest_ma(
                source,
                symbols,
                iv,
                context_limit,
                ema,
                redis,
                ignore_pending_repairs,
                return_exceptions,
                periods,
            )
        contexts = self.read_many(
            source,
            symbols,
            iv,
            limit=context_limit,
            forward=False,
            ignore_pending_repairs=ignore_pending_repairs,
            return_exceptions=True,
        )
        results = {}
        vectorized_latest = {}
        if limit == 1:
            safe_contexts = {
                symbol: context
                for symbol, context in contexts.items()
                if isinstance(context, list) and context
            }
            vectorized_latest = enhance_latest_many(
                safe_contexts,
                {symbol: [row.close for row in rows] for symbol, rows in safe_contexts.items()},
                iv,
                ma=ma,
                ema=ema,
                periods=periods,
            )
        batch_contexts = {}
        selected_indices = {}
        for symbol in symbols:
            context = contexts[symbol]
            if isinstance(context, Exception):
                try:
                    results[symbol] = self.query(
                        source,
                        symbol,
                        iv,
                        start=start,
                        end=end,
                        limit=limit,
                        ma=ma,
                        ema=ema,
                        redis=redis,
                        strict_source_intervals=strict_source_intervals,
                        ignore_pending_repairs=ignore_pending_repairs,
                        periods=periods,
                    )
                except Exception as exc:
                    if not return_exceptions:
                        raise
                    results[symbol] = exc
            elif not context:
                results[symbol] = []
            elif limit == 1:
                results[symbol] = vectorized_latest[symbol]
            else:
                first = max(0, len(context) - limit)
                if ma:
                    batch_contexts[symbol] = context
                    selected_indices[symbol] = range(first, len(context))
                else:
                    results[symbol] = enhance_selected(
                        context, iv, range(first, len(context)), ma=False, ema=ema
                    )
        if batch_contexts:
            results.update(
                enhance_many_selected(
                    batch_contexts,
                    selected_indices,
                    iv,
                    ma=True,
                    ema=ema,
                    periods=periods,
                )
            )
        return results

    def _read_merged_source(
        self,
        source,
        symbol,
        iv,
        start=None,
        end=None,
        limit=None,
        forward=False,
        skip_pending_empty=False,
        ignore_pending_repairs=False,
    ):
        try:
            return self.read(
                source,
                symbol,
                iv,
                start,
                end,
                limit,
                forward=forward,
                ignore_pending_repairs=ignore_pending_repairs,
            )
        except DataError as exc:
            expected = f"Historical adjustment repair pending for {symbol} {iv}"
            if (
                not skip_pending_empty
                or exc.status != 503
                or str(exc) != expected
                or self.repo.read(source, symbol, iv, limit=1)
            ):
                raise
            log.warning(
                "omitting source %s for latest merged ticker %s with pending repair and no live candles",
                source,
                symbol,
            )
            return []

    def query_merged_native_sources(
        self,
        sources,
        symbol,
        iv,
        start=None,
        end=None,
        limit=252,
        ma=True,
        ema=False,
        skip_pending_empty=False,
        ignore_pending_repairs=False,
    ):
        """Match Rust's SQL batch behavior when extra sources share a ticker name."""
        if iv not in {"1m", "1h", "1D"}:
            raise ValueError(f"Expected a native interval, got {iv!r}")
        if limit == 0:
            return []

        buffer = 600 if ma and ema else 200 if ma else 1
        raw_limit = limit + buffer if ma else limit + 1
        if raw_limit <= 0:
            return []

        ordered_sources = self.ordered_merged_sources(sources, symbol)
        if start is not None:
            duration_minutes = {"1m": 1, "1h": 60, "1D": 2016}[iv]
            effective_start = start - (duration_minutes * buffer * 60 if ma else 0)
            context = [
                row
                for source in ordered_sources
                for row in self._read_merged_source(
                    source,
                    symbol,
                    iv,
                    effective_start,
                    end,
                    raw_limit,
                    forward=True,
                    skip_pending_empty=skip_pending_empty,
                    ignore_pending_repairs=ignore_pending_repairs,
                )
            ]
            if not context:
                return []
            computed = enhance(context, iv, ma, ema)
            selected = [
                result for row, result in zip(context, computed, strict=True) if row.time >= start
            ]
            return selected[:limit] if limit > 0 else selected

        # PostgreSQL orders a Yahoo batch by ticker identity, then by time
        # descending. Rust reverses the combined list before enhancing it, so
        # process source identities in reverse ID order on this no-start path.
        context_by_source = {
            source: self._read_merged_source(
                source,
                symbol,
                iv,
                end=end,
                limit=raw_limit,
                skip_pending_empty=skip_pending_empty,
                ignore_pending_repairs=ignore_pending_repairs,
            )
            for source in ordered_sources
        }
        context = [row for source in reversed(ordered_sources) for row in context_by_source[source]]
        if not context:
            return []
        computed = enhance(context, iv, ma, ema)
        return computed[-limit:] if limit > 0 else computed

    def query_merged_aggregated_sources(
        self,
        sources,
        symbol,
        iv,
        start=None,
        end=None,
        limit=252,
        ma=True,
        ema=False,
        skip_pending_empty=False,
        ignore_pending_repairs=False,
    ):
        """Aggregate duplicate source identities together, as Rust's extra-source SQL does."""
        native = base_interval(iv)
        if native == iv:
            raise ValueError(f"Expected an aggregated interval, got {iv!r}")
        factor = {
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "4h": 4,
            "1W": 5,
            "2W": 10,
            "1M": 23,
        }[iv]
        agg_buffer = 5000 if ma else factor
        lookback = limit * factor + agg_buffer
        if lookback <= 0:
            return []

        # Rust's extra-source query groups identities by persisted ticker ID,
        # then Aggregator performs a stable time sort. Keep this identity order
        # so equal-time rows have the same OHLC tie behavior.
        ordered_sources = self.ordered_merged_sources(sources, symbol)
        context = [
            row
            for source in ordered_sources
            for row in self._read_merged_source(
                source,
                symbol,
                native,
                start,
                end,
                lookback,
                forward=start is not None,
                skip_pending_empty=skip_pending_empty,
                ignore_pending_repairs=ignore_pending_repairs,
            )
        ]
        if not context:
            return []

        # Rust's OHLCV table does not carry provider/revision identity, so two
        # source records sharing a ticker name are aggregated even if their
        # Python provenance differs.
        bars = aggregate(context, iv, ordered_sources[0], lambda _rows: None)
        computed = enhance(bars, iv, ma, ema)
        if start is not None:
            return computed[:limit] if limit >= 0 else computed
        if limit == 0:
            return []
        return computed[-limit:] if limit > 0 else computed
