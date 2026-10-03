import time

from .calculations import aggregate, enhance
from .domain import DataError, base_interval, bucket


class History:
    def __init__(self, repo, archive, settings):
        self.repo, self.archive, self.settings = repo, archive, settings

    def read(self, source, symbol, iv, start=None, end=None, limit=None, forward=False):
        """Return chronological native candles; consult only required objects."""
        local = self.repo.read(source, symbol, iv, start, end, limit, forward=forward)
        local_times = {r.time for r in local}
        merged = {r.time: r for r in local}
        if len(merged) > self.settings.archive_max_rows:
            raise DataError("Historical request exceeds resource limit", 400)
        objects = self.repo.archives(source, symbol, iv, start, end)
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

    def aggregated(self, source, symbol, iv, start=None, end=None, limit=252, native=None):
        """Expand recent native reads until enough complete output buckets exist."""
        native = native or self.native_interval(source, symbol, iv)
        factor = (
            {"1h": 60, "4h": 240}[iv]
            if native == "1m" and iv in ("1h", "4h")
            else {"5m": 5, "15m": 15, "30m": 30, "4h": 4, "1W": 7, "2W": 14, "1M": 31}[iv]
        )
        count = min((limit + 1) * factor, self.settings.archive_max_rows)
        lower = bucket(start, iv, source) if start is not None else None
        forward = start is not None
        while True:
            rows = self.read(source, symbol, native, lower, end, count, forward=forward)
            if not rows:
                return []
            bars = aggregate(rows, iv, source, self.repo.validate_basis)
            selected = [r for r in bars if start is None or r.time >= start]
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
                        last,
                        min(last + size, end) if end is not None else last + size,
                    )
                    tail = [r for r in tail if bucket(r.time, iv, source) == last]
                    completed = aggregate(
                        [r for r in rows if r.time < last] + tail,
                        iv,
                        source,
                        self.repo.validate_basis,
                    )
                    return [r for r in completed if r.time >= start][:limit]
                # The first fetched bucket may be partial. Read its complete
                # native range before publishing any selected output candle.
                first = bucket(rows[0].time, iv, source)
                prefix = self.read(source, symbol, native, first, rows[0].time - 1)
                bars = aggregate(prefix + rows, iv, source, self.repo.validate_basis)
                return [r for r in bars if start is None or r.time >= start][-limit:]
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
            earlier = self.read(source, symbol, native, end=target[0].time - 1, limit=buffer)
            rows = earlier + target
        else:
            target = self.aggregated(source, symbol, iv, start, end, limit, native=native)
            if not target:
                return []
            earlier = self.aggregated(
                source, symbol, iv, end=target[0].time - 1, limit=buffer, native=native
            )
            rows = earlier + target
        if len({r.revision for r in rows}) > 1:
            raise DataError(f"Incompatible adjustment revisions for {symbol} {native}")
        self.repo.validate_basis(rows)
        result = enhance(rows, iv, ma, ema)
        return result[-len(target) :]
