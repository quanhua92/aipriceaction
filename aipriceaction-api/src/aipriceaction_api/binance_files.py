"""Bounded, checksummed monthly spot candles from Binance's public archive.

This accelerates minute bootstrap only. Ordinary updates retain the live API.
It uses the same spot OHLCV representation, including the legacy integer volume.
"""

import csv
import hashlib
import io
import math
import os
import re
import time
import zipfile
from datetime import UTC, datetime

from .domain import Candle, DataError
from .migration import atomic_write

ZIP_BUDGET = 8 * 1024 * 1024
CSV_BUDGET = 32 * 1024 * 1024
CACHE_BUDGET = 128 * 1024 * 1024


def month_bounds(month):
    start = datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return int(start.timestamp()), int(end.timestamp())


def parse_zip(raw, filename, symbol, iv, month):
    """Verify one complete month; never silently skip malformed or missing bars."""
    start, end = month_bounds(month)
    step = {"1m": 60, "1h": 3600, "1D": 86400}[iv]
    rows, expected = [], start
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            info = archive.infolist()
            if len(info) != 1 or info[0].filename != filename or info[0].file_size > CSV_BUDGET:
                raise DataError("Binance ZIP member or uncompressed size is invalid")
            with (
                archive.open(filename) as member,
                io.TextIOWrapper(member, encoding="utf-8-sig") as text,
            ):
                for fields in csv.reader(text):
                    if len(fields) != 12:
                        raise DataError("Binance candle CSV requires twelve fields")
                    stamp = int(fields[0])
                    # Spot archive open times changed from milliseconds to
                    # microseconds in 2025. Integer division preserves alignment.
                    divisor = 1_000_000 if stamp >= 100_000_000_000_000 else 1000
                    if stamp % divisor:
                        raise DataError("Binance candle timestamp is not a whole second")
                    stamp //= divisor
                    if stamp != expected or stamp >= end:
                        raise DataError(
                            "Binance archive has missing, duplicate, or out-of-order candles"
                        )
                    volume = float(fields[5])
                    if not math.isfinite(volume) or volume < 0:
                        raise DataError("Invalid Binance candle volume")
                    row = Candle(
                        "crypto",
                        symbol,
                        iv,
                        stamp,
                        *(float(v) for v in fields[1:5]),
                        int(volume),
                        "binance",
                    ).validate()
                    rows.append(row)
                    expected += step
    except (zipfile.BadZipFile, UnicodeError, ValueError, OverflowError, RuntimeError) as exc:
        raise DataError("Invalid Binance candle ZIP/CSV") from exc
    if expected != end:
        raise DataError("Binance archive does not cover the complete month")
    return rows


class BinanceFiles:
    def __init__(self, root, request):
        self.root, self.request = root, request
        self.unavailable = {}

    async def month(self, symbol, iv, before):
        if not re.fullmatch(r"[A-Z0-9]+", symbol):
            raise DataError("Invalid Binance archive symbol")
        day = datetime.fromtimestamp(before - 1, UTC)
        month = day.strftime("%Y-%m")
        key = (symbol, iv, month)
        # Monthly files appear after the month ends, sometimes several days later.
        if (
            month >= datetime.now(UTC).strftime("%Y-%m")
            or time.monotonic() - self.unavailable.get(key, -3600) < 3600
        ):
            return None
        wire = "1d" if iv == "1D" else iv
        filename = f"{symbol}-{wire}-{month}"
        url = f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/{wire}/{filename}.zip"
        checksum = await self.request(url + ".CHECKSUM", 4096)
        if checksum is None:
            self.unavailable[key] = time.monotonic()
            return None
        try:
            fields = checksum.decode("ascii").split()
        except UnicodeError as exc:
            raise DataError("Invalid Binance archive checksum") from exc
        if (
            len(fields) != 2
            or not re.fullmatch(r"[a-f0-9]{64}", fields[0])
            or fields[1] != filename + ".zip"
        ):
            raise DataError("Invalid Binance archive checksum")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / (fields[0] + ".zip")
        if path.exists():
            if path.stat().st_size > ZIP_BUDGET:
                raise DataError("Binance archive cache exceeds object size budget")
            raw = path.read_bytes()
        else:
            raw = await self.request(url, ZIP_BUDGET)
            if raw is None:
                raise DataError("Binance archive ZIP missing despite published checksum")
        if hashlib.sha256(raw).hexdigest() != fields[0]:
            raise DataError("Binance archive/cache checksum mismatch")
        rows = parse_zip(raw, filename + ".csv", symbol, iv, month)
        if not path.exists():
            atomic_write(path, raw)
        os.utime(path, None)
        files = sorted(self.root.glob("*.zip"), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        for old in files:
            if total <= CACHE_BUDGET:
                break
            if old != path:
                total -= old.stat().st_size
                old.unlink(missing_ok=True)
        return [r for r in rows if r.time < before] or None
