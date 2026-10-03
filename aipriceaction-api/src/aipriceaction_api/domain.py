import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

PERIODS = (10, 20, 50, 100, 200)
SOURCES = ("vn", "yahoo", "sjc", "crypto")
INDEXES = {
    "VNINDEX",
    "VN30",
    "VN30F1M",
    "HNXINDEX",
    "HNX30",
    "UPCOMINDEX",
    "VNXALL",
    "VNX50",
    "VN100",
    "VNMIDCAP",
    "VNSMALLCAP",
    "VNALLSHARE",
    "VNXALLSHARE",
    "VNMITECH",
    "VNCOND",
    "VNDIVIDEND",
    "VNALL",
    "VNMID",
    "VNSML",
    "VNFIN",
    "VNFINLEAD",
    "VNFINSELECT",
    "VNDIAMOND",
    "VNREAL",
    "VNENE",
    "VNCONS",
    "VNIND",
    "VNHEAL",
    "VNIT",
    "VNUTI",
}


class DataError(Exception):
    def __init__(self, message: str, status: int = 503):
        self.status = status
        super().__init__(message)


def parse_time(value: str | int | float | datetime) -> int:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        return int(value)
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int((dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp())


def date_bounds(value: str | None, end=False) -> int | None:
    if value is None:
        return None
    try:
        dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise DataError("Invalid date format. Use YYYY-MM-DD", 400) from exc
    return int((dt + (timedelta(days=1) if end else timedelta())).timestamp()) - int(end)


def cutoff(years: int, now: datetime | None = None) -> int:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    try:
        result = now.replace(year=now.year - years)
    except ValueError:  # February 29 -> February 28 in a non-leap year.
        result = now.replace(year=now.year - years, day=28)
    return int(result.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def completed_vn_sessions(now: datetime | None = None) -> int:
    """Exclusive UTC day bound for completed VN sessions.

    VN trading ends at 15:00 ICT (08:00 UTC). Allow 15 minutes for delayed
    closing bars. This is a finality bound, not an inferred holiday calendar:
    a session is only observed when actual provider candles exist.
    """
    now = (now or datetime.now(UTC)).astimezone(UTC)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if now.weekday() < 5 and now >= day + timedelta(hours=8, minutes=15):
        day += timedelta(days=1)
    return int(day.timestamp())


def interval(raw: str) -> str:
    if raw in ("5m", "15m", "30m", "4h", "1W", "2W", "1M"):
        return raw
    aliases = {"1D": "1D", "DAILY": "1D", "1H": "1h", "HOURLY": "1h", "1M": "1m", "MINUTE": "1m"}
    if raw.upper() not in aliases:
        raise DataError(
            f"Invalid interval '{raw}'. Must be one of: 1D, 1H, 1m, 5m, 15m, 30m, 4h, 1W, 2W, 1M (or daily, hourly, minute)",
            400,
        )
    return aliases[raw.upper()]


def mode(raw: str) -> str:
    result = {"stock": "vn", "stocks": "vn", "cryptos": "crypto"}.get(raw, raw)
    if result not in ("vn", "crypto", "yahoo", "all"):
        raise DataError("Invalid mode", 400)
    return result


def base_interval(value: str) -> str:
    return (
        "1m"
        if value in ("5m", "15m", "30m")
        else "1h"
        if value == "4h"
        else "1D"
        if value in ("1W", "2W", "1M")
        else value
    )


def bucket(time: int, value: str, source: str) -> int:
    dt = datetime.fromtimestamp(time, UTC)
    if value in ("1W", "2W"):
        day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        monday = day - timedelta(days=day.weekday())
        if value == "2W":
            # Legacy pairing anchors even ISO weeks; odd weeks shift back.
            if dt.isocalendar().week % 2 == 1:
                monday -= timedelta(days=7)
        return int(monday.timestamp())
    if value == "1M":
        return int(dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
    size = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400}.get(value)
    if size is None:
        return time
    offset = 7200 if value == "4h" and source == "vn" else 0
    return (time - offset) // size * size + offset


@dataclass(frozen=True)
class Candle:
    source: str
    symbol: str
    interval: str
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: int
    provider: str = "import"
    revision: str = "initial"
    updated_at: int = 0

    def validate(self):
        if self.source not in SOURCES or self.interval not in ("1D", "1h", "1m"):
            raise DataError("Invalid source or native interval", 400)
        if not self.symbol or any(ord(c) < 32 for c in self.symbol):
            raise DataError("Invalid symbol", 400)
        prices = (self.open, self.high, self.low, self.close)
        futures = self.source == "yahoo" and self.symbol.endswith("=F")
        if any(not math.isfinite(x) or (x <= 0 and not futures) for x in prices) or self.volume < 0:
            raise DataError("Non-positive/non-finite price or negative volume", 400)
        settlement_quote = futures and self.interval == "1D"
        range_values = (
            (self.close,)
            if self.source == "sjc"
            else (self.open,)
            if settlement_quote
            else (self.open, self.close)
        )
        # Legacy SJC represents bid/ask quotes: open is the previous midpoint,
        # which can lie outside today's bid/ask range. It is not a traded candle.
        # Daily Yahoo futures can carry a settlement-style close independently
        # of the traded range. Preserve that quote; open/high/low and all
        # intraday candles still have to describe a valid trading range.
        if self.high < max(*range_values, self.low) or self.low > min(range_values):
            raise DataError("Invalid OHLC range", 400)
        if self.interval == "1D" and self.time % 86400:
            raise DataError("Daily candle must be midnight UTC", 400)
        # The legacy API includes timestamped zero-volume futures quotes in
        # its intraday history. Preserve their seconds; they are observations,
        # not newly fetched native candles or inferred trades.
        legacy_intraday_quote = (
            futures
            and self.interval in {"1h", "1m"}
            and self.provider == "legacy-api"
            and self.volume == 0
            and self.open == self.high == self.low == self.close
        )
        if self.time % 60 and not legacy_intraday_quote:
            raise DataError("Candle must be aligned to a minute", 400)
        return self

    def record(self):
        return asdict(self)
