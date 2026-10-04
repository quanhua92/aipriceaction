import time

from .calculations import aggregate, enhance
from .domain import DataError, base_interval, bucket, cutoff


class History:
    def __init__(self, repo, archive, settings):
        self.repo, self.archive, self.settings = repo, archive, settings

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
        return result

    def native_interval(self, source, symbol, iv):
        native = base_interval(iv)
        if native == "1h" and not self.repo.state(source, symbol, native):
            # Minute-only selections still serve hourly web controls. Preserve
            # any existing hourly series/archives, including pending repairs.
            if not self.repo.archives(source, symbol, native):
                return "1m"
        return native

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
        count = min((limit + 1) * factor, self.settings.archive_max_rows)
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
            count = min(count * 2, self.settings.archive_max_rows)

    def query(self, source, symbol, iv, start=None, end=None, limit=252, ma=True, ema=False):
        native = self.native_interval(source, symbol, iv)
        buffer = 600 if ma and ema else 200 if ma else 1
        # Fetch native bars before aggregation, then acquire enough *aggregated*
        # lookback buckets. This avoids a calendar-day approximation for MA200.
        if native == iv:
            target = self.read(source, symbol, native, start, end, limit, forward=start is not None)
            if not target:
                return []
            earlier = self.read(
                source,
                symbol,
                native,
                end=target[0].time - 1,
                limit=buffer,
                revision=target[0].revision,
            )
            rows = earlier + target
        else:
            target = self.aggregated(source, symbol, iv, start, end, limit, native=native)
            if not target:
                return []
            earlier = self.aggregated(
                source,
                symbol,
                iv,
                end=target[0].time - 1,
                limit=buffer,
                native=native,
                revision=target[0].revision,
            )
            rows = earlier + target
        if len({r.revision for r in rows}) > 1:
            raise DataError(f"Incompatible adjustment revisions for {symbol} {native}")
        self.repo.validate_basis(rows)
        result = enhance(rows, iv, ma, ema)
        return result[-len(target) :]
