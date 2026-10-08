import math
import time
from dataclasses import replace

from .calculations import aggregate, enhance
from .domain import DataError, base_interval, bucket, completed_vn_sessions, cutoff


class History:
    def __init__(self, repo, archive, settings):
        self.repo, self.archive, self.settings = repo, archive, settings

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
        self, source, symbol, iv, start=None, end=None, limit=None, forward=False, revision=None
    ):
        """Return chronological native candles; consult only required objects."""
        local = self.repo.read(source, symbol, iv, start, end, limit, forward=forward)
        other_local = [r for r in local if revision is not None and r.revision != revision]
        if revision is not None:
            local = [r for r in local if r.revision == revision]
        local_times = {r.time for r in local}
        merged = {r.time: r for r in local}
        if len(merged) > self.settings.archive_max_rows:
            raise DataError("Historical request exceeds resource limit", 400)
        objects = self.repo.archives(source, symbol, iv, start, end)
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
        gaps = self.repo.history_gaps(source, symbol, iv, lower, upper)
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
        if gaps:
            raise DataError(f"Historical data unavailable for {symbol} {iv}: {gaps[0]['reason']}")
        if len({r.revision for r in result}) > 1:
            raise DataError(f"Incompatible adjustment revisions for {symbol} {iv}")
        self.repo.validate_basis(result)
        if source == "vn" and iv == "1m" and result:
            from .volume_corrections import apply_records

            result = apply_records(
                result,
                self.repo.volume_corrections(source, symbol, iv, result[0].time, result[-1].time),
            )
        return result

    def native_interval(self, source, symbol, iv):
        native = base_interval(iv)
        if native == "1h" and not self.repo.state(source, symbol, native):
            # Minute-only selections still serve hourly web controls. Preserve
            # any existing hourly series/archives, including pending repairs.
            if not self.repo.archives(source, symbol, native):
                return "1m"
        return native

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

    def vn_daily_target(self, symbol, start, end, limit=None, forward=False, revision=None):
        """Overlay completed recent VND daily candles from certified local minutes."""
        native = self.read(
            "vn", symbol, "1D", start, end, limit, forward=forward, revision=revision
        )
        if not native:
            return []
        minute_state = self.repo.state("vn", symbol, "1m")
        if not minute_state or minute_state["provider"] not in {"vps", "dnse"}:
            return native
        minute_cutoff = cutoff(self.settings.minute_years)
        completed_before = completed_vn_sessions()
        first = max(native[0].time, minute_cutoff)
        last = min(native[-1].time + 86399, completed_before - 1)
        if first > last:
            return native
        minutes = self.repo.read("vn", symbol, "1m", first, last)
        groups = {}
        for row in minutes:
            groups.setdefault(row.time // 86400 * 86400, []).append(row)
        result = []
        for row in native:
            observed = groups.get(row.time)
            if (
                row.provider != "vndirect"
                or not observed
                or row.time >= completed_before
                or any(item.provider not in {"vps", "dnse"} for item in observed)
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
        self, source, symbol, iv, start=None, end=None, limit=252, native=None, revision=None
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
                source, symbol, native, lower, end, count, forward=forward, revision=revision
            )
            if not rows:
                return []
            bars = aggregate(rows, iv, source, self.repo.validate_basis)
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
                    )
                    tail = [r for r in tail if bucket(r.time, iv, source) == last]
                    completed = aggregate(
                        [r for r in rows if r.time < last] + tail,
                        iv,
                        source,
                        self.repo.validate_basis,
                    )
                    return completed[:limit]
                # The first fetched bucket may be partial. Read its complete
                # native range before publishing any selected output candle.
                first = bucket(rows[0].time, iv, source)
                prefix = self.read(
                    source, symbol, native, first, rows[0].time - 1, revision=rows[0].revision
                )
                bars = aggregate(prefix + rows, iv, source, self.repo.validate_basis)
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

    def query(self, source, symbol, iv, start=None, end=None, limit=252, ma=True, ema=False):
        native = self.native_interval(source, symbol, iv)
        buffer = 600 if ma and ema else 200 if ma else 1
        # Fetch native bars before aggregation, then acquire enough *aggregated*
        # lookback buckets. This avoids a calendar-day approximation for MA200.
        if native == iv:
            target = (
                self.vn_hourly_target(symbol, start, end, limit)
                if source == "vn" and iv == "1h"
                else self.vn_daily_target(symbol, start, end, limit, forward=start is not None)
                if source == "vn" and iv == "1D"
                else self.read(source, symbol, native, start, end, limit, forward=start is not None)
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
                    ),
                    buffer,
                )
            else:
                if source == "vn" and iv == "1D":
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
                        ),
                        buffer,
                    )
            rows = earlier + target
        else:
            target = self.aggregated(source, symbol, iv, start, end, limit, native=native)
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
                ),
                buffer,
            )
            rows = earlier + target
        if len({r.revision for r in rows}) > 1:
            raise DataError(f"Incompatible adjustment revisions for {symbol} {native}")
        self.repo.validate_basis(rows)
        result = enhance(rows, iv, ma, ema)
        return result[-len(target) :]
