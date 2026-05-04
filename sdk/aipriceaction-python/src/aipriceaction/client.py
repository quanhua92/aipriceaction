from __future__ import annotations

import io
import json
import os
import tempfile
import time as _time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

import requests
import pandas as pd

from .exceptions import AIPriceActionError
from .models import TickerInfo

if TYPE_CHECKING:
    from .ticker import Ticker

# Source auto-detection priority (matches Rust's resolve_ticker_sources)
_SOURCE_PRIORITY = ["vn", "yahoo", "sjc", "crypto"]

_OHLCV_COLUMNS = ["time", "open", "high", "low", "close", "volume"]

_MA_PERIODS = [10, 20, 50, 100, 200]
_MA_COLUMNS = [
    "ma10",
    "ma20",
    "ma50",
    "ma100",
    "ma200",
    "ma10_score",
    "ma20_score",
    "ma50_score",
    "ma100_score",
    "ma200_score",
    "close_changed",
    "volume_changed",
    "total_money_changed",
]

_ALL_INTERVALS = {
    "1D",
    "1d",
    "daily",
    "1H",
    "1h",
    "hourly",
    "1m",
    "minute",
    "5m",
    "15m",
    "30m",
    "4h",
    "1W",
    "2W",
    "1M",
}

# Live data overlay settings
_LIVE_CACHE_TTL = 120.0
_LIVE_REQUEST_TIMEOUT = 5.0
_LIVE_LIMITS: dict[str, int] = {"1D": 1, "1h": 5, "1m": 60}
_LIVE_NATIVE_INTERVALS = {"1D", "1h", "1m"}
_DATE_ONLY_INTERVALS = {"1D", "1W", "2W", "1M"}


def _parse_utc(utc_string: str) -> datetime | None:
    """Parse a UTC ISO string to a datetime.

    Handles: '2025-11-09T14:00:00Z', '2025-11-09 14:00:00', '2025-11-09'.
    """
    if not utc_string:
        return None
    s = utc_string.strip().replace(" ", "T")
    if not s.endswith("Z"):
        if "+" in s[10:] or "-" in s[10:]:
            pass
        else:
            s += "Z"
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _ma_buffer_days(interval: str) -> int:
    """Calendar days of extra history to fetch before start_date for MA warm-up."""
    if interval == "1D":
        return 400  # ~200 trading days + padding
    if interval == "1h":
        return 50  # 200 bars ÷ ~6.5 trading hours/day ≈ 31 trading days
    if interval == "1m":
        return 5  # 200 bars ÷ ~390 min/day < 1 day
    return 400


class AIPriceAction:
    """Read OHLCV data from aipriceaction S3 archive via plain HTTP.

    No S3 credentials needed — the bucket must be public-read.

    Args:
        base_url: S3 archive base URL. Defaults to "https://s3.aipriceaction.com".
        cache_dir: Local disk cache directory. Defaults to a temp dir. Pass None to disable.
        freshness_ttl: Seconds to trust disk cache before re-checking server hash (default 300).
        use_live: Overlay live API data on top of S3 data (default False). When enabled,
            the last candle(s) from S3 are overwritten with live data and any newer candles
            are appended. Falls back to stale S3 data if the live API is unreachable.
        live_url: Base URL for the live data API. Defaults to "https://api.aipriceaction.com".
        utc_offset: UTC offset in hours for display (default 7). Pass 0 to keep raw UTC.
    """

    DEFAULT_BASE_URL = "https://s3.aipriceaction.com"

    def __init__(
        self,
        base_url: Optional[str] = None,
        cache_dir: Optional[str] = None,
        *,
        freshness_ttl: float = 300.0,
        use_live: bool = False,
        live_url: str = "https://api.aipriceaction.com",
        utc_offset: int = 7,
    ):
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self._session = requests.Session()
        self._tickers_cache: list[TickerInfo] | None = None
        self._freshness: dict[str, dict] = {}
        self._freshness_ttl = freshness_ttl
        self.use_live = use_live
        self._live_url = live_url.rstrip("/")
        self._live_cache: dict[str, dict] = {}
        self._utc_offset = timezone(timedelta(hours=utc_offset)) if utc_offset != 0 else None

        # Disk cache for downloaded CSVs
        if cache_dir is not None:
            self._cache_dir = Path(cache_dir)
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        else:
            self._cache_dir = Path(tempfile.gettempdir()) / "aipriceaction-s3-cache"
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    # ── Freshness helpers ──

    def _head_content_hash(self, url: str) -> Optional[str]:
        """HEAD request returning x-amz-meta-content-hash. Returns None on error."""
        try:
            resp = self._session.head(url, timeout=10)
            if resp.status_code in (404, 403):
                return None
            resp.raise_for_status()
            return resp.headers.get("x-amz-meta-content-hash")
        except Exception:
            return None

    def _save_hash(self, cache_path: str, content_hash: str) -> None:
        """Write .hash sidecar file with the server content hash."""
        Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
        Path(f"{cache_path}.hash").write_text(content_hash)

    def _read_hash(self, cache_path: str) -> Optional[str]:
        """Read .hash sidecar file. Returns None if missing."""
        p = Path(f"{cache_path}.hash")
        if not p.exists():
            return None
        try:
            return p.read_text().strip()
        except OSError:
            return None

    def _is_fresh(self, cache_path: str, url: str) -> bool:
        """Check if a disk-cached file is still fresh.

        Within TTL → True (no HEAD needed).
        Beyond TTL → HEAD the server, compare with .hash file.
        Hash matches → update timestamp, return True.
        Hash differs → return False.
        HEAD fails + disk cache exists → return True (conservative).
        No .hash file yet (upgrade path) → do HEAD, write .hash, trust cache.
        """
        if not os.path.exists(cache_path):
            return False

        now = _time.monotonic()
        entry = self._freshness.get(cache_path)

        if entry and (now - entry["checked_at"]) < self._freshness_ttl:
            return True

        # Beyond TTL or no entry — check server
        server_hash = self._head_content_hash(url)
        disk_hash = self._read_hash(cache_path)

        if server_hash is None:
            # HEAD failed (network error or 404/403)
            if disk_hash is not None:
                # We have a cached hash and disk file — trust cache conservatively
                self._freshness[cache_path] = {"hash": disk_hash, "checked_at": now}
                return True
            # No hash file yet (upgrade path) and server HEAD failed — do HEAD
            # can't determine freshness, skip cache
            return False

        if disk_hash is None:
            # No .hash file yet — write it and trust existing disk cache
            self._save_hash(cache_path, server_hash)
            self._freshness[cache_path] = {"hash": server_hash, "checked_at": now}
            return True

        if server_hash == disk_hash:
            self._freshness[cache_path] = {"hash": disk_hash, "checked_at": now}
            return True

        # Hash changed — need to re-download
        return False

    # ── Ticker metadata ──

    def get_tickers(
        self,
        *,
        source: Optional[str] = None,
        use_cache: bool = True,
    ) -> list[TickerInfo]:
        """Get ticker metadata from meta/tickers.json.

        Args:
            source: Filter by source ("vn", "yahoo", "crypto", "sjc"). None = all.
            use_cache: Use in-memory cache.

        Returns:
            List of TickerInfo objects.
        """
        if use_cache and self._tickers_cache is not None:
            tickers = self._tickers_cache
        else:
            tickers = self._fetch_tickers()
            self._tickers_cache = tickers

        if source:
            tickers = [t for t in tickers if t.source == source]

        return tickers

    def _fetch_tickers(self) -> list[TickerInfo]:
        """Fetch and parse tickers.json from S3, with disk caching."""
        url = f"{self.base_url}/meta/tickers.json"
        cache_path = str(self._cache_dir / "_meta" / "tickers.json")
        Path(cache_path).parent.mkdir(parents=True, exist_ok=True)

        # Try disk cache first (with freshness check)
        if os.path.exists(cache_path) and self._is_fresh(cache_path, url):
            try:
                raw = json.loads(Path(cache_path).read_text())
                return [
                    TickerInfo(
                        **{
                            k: v
                            for k, v in t.items()
                            if k in TickerInfo.__dataclass_fields__
                        }
                    )
                    for t in raw
                ]
            except (json.JSONDecodeError, TypeError):
                pass  # stale cache, refetch

        resp = self._session.get(url)
        if resp.status_code == 404:
            raise AIPriceActionError(f"tickers.json not found: {url}")
        resp.raise_for_status()

        # Write to disk cache
        raw = resp.json()
        Path(cache_path).write_text(json.dumps(raw))

        # Write hash sidecar and update freshness
        content_hash = self._head_content_hash(url)
        if content_hash:
            self._save_hash(cache_path, content_hash)
        self._freshness[cache_path] = {
            "hash": content_hash or "",
            "checked_at": _time.monotonic(),
        }

        return [
            TickerInfo(
                **{k: v for k, v in t.items() if k in TickerInfo.__dataclass_fields__}
            )
            for t in raw
        ]

    def _find_source(self, ticker: str, source: Optional[str]) -> tuple[str, str]:
        """Resolve (source, ticker) for a given ticker symbol.

        If source is provided, use it directly. Otherwise auto-detect
        from tickers.json using priority: vn > yahoo > sjc > crypto.
        """
        if source:
            return source, ticker

        tickers = self.get_tickers()
        for src in _SOURCE_PRIORITY:
            for t in tickers:
                if t.ticker == ticker and t.source == src:
                    return src, ticker

        raise AIPriceActionError(f"Ticker '{ticker}' not found in any source")

    def _resolve_tickers(
        self,
        tickers: Optional[list[str]],
        source: Optional[str],
    ) -> list[tuple[str, str]]:
        """Resolve a list of ticker symbols into [(source, ticker), ...].

        If tickers is None, returns all tickers for the given source.
        """
        all_tickers = self.get_tickers()

        if source:
            all_tickers = [t for t in all_tickers if t.source == source]

        if tickers is not None:
            resolved: list[tuple[str, str]] = []
            for sym in tickers:
                if source:
                    resolved.append((source, sym))
                else:
                    resolved.append(self._find_source(sym, None))
            return resolved

        # All tickers
        return [(t.source, t.ticker) for t in all_tickers]

    # ── Date helpers ──

    @staticmethod
    def _parse_date(d: Union[str, date, datetime]) -> date:
        if isinstance(d, datetime):
            return d.date()
        if isinstance(d, date):
            return d
        return date.fromisoformat(d)

    @staticmethod
    def _date_range(start: date, end: date) -> list[date]:
        """Return list of dates from start to end (inclusive)."""
        days = (end - start).days + 1
        if days <= 0:
            return []
        return [start + timedelta(days=i) for i in range(days)]

    # ── S3 key helpers ──

    def _csv_key(self, source: str, ticker: str, interval: str, day: date) -> str:
        return f"ohlcv/{source}/{ticker}/{interval}/{ticker}-{interval}-{day.isoformat()}.csv"

    def _cache_key(self, source: str, ticker: str, interval: str, day: date) -> str:
        """Local filesystem cache path for a CSV file."""
        return str(
            self._cache_dir
            / source
            / ticker
            / interval
            / f"{ticker}-{interval}-{day.isoformat()}.csv"
        )

    def _csv_key_yearly(
        self, source: str, ticker: str, interval: str, year: int
    ) -> str:
        """S3 key for a yearly aggregate CSV file."""
        return f"ohlcv/{source}/{ticker}/yearly/{ticker}-{interval}-{year}.csv"

    def _cache_key_yearly(
        self, source: str, ticker: str, interval: str, year: int
    ) -> str:
        """Local filesystem cache path for a yearly CSV file."""
        return str(
            self._cache_dir
            / source
            / ticker
            / "yearly"
            / f"{ticker}-{interval}-{year}.csv"
        )

    def _fetch_csv_from_url(
        self,
        url: str,
        cache_path: str,
        *,
        use_cache: bool = True,
    ) -> Optional[pd.DataFrame]:
        """Fetch a CSV from a URL with disk caching and hash sidecar.

        Shared logic for _fetch_csv and _fetch_csv_yearly.
        Returns a DataFrame, or None if the file doesn't exist (404/403).
        """
        # Try disk cache (with freshness check)
        if use_cache and self._is_fresh(cache_path, url):
            try:
                text = Path(cache_path).read_text()
                return self._parse_csv(text)
            except (OSError, pd.errors.EmptyDataError):
                pass

        # Fetch from S3
        resp = self._session.get(url)
        if resp.status_code in (404, 403):
            return None
        resp.raise_for_status()

        text = resp.text

        # Write to disk cache
        if use_cache:
            Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
            Path(cache_path).write_text(text)

            # Write hash sidecar and update freshness
            content_hash = self._head_content_hash(url)
            if content_hash:
                self._save_hash(cache_path, content_hash)
            self._freshness[cache_path] = {
                "hash": content_hash or "",
                "checked_at": _time.monotonic(),
            }

        return self._parse_csv(text)

    def _fetch_csv(
        self,
        source: str,
        ticker: str,
        interval: str,
        day: date,
        *,
        use_cache: bool = True,
    ) -> Optional[pd.DataFrame]:
        """Fetch a single day's CSV as a DataFrame.

        Returns a DataFrame, or None if the file doesn't exist.
        """
        cache_path = self._cache_key(source, ticker, interval, day)
        url = f"{self.base_url}/{self._csv_key(source, ticker, interval, day)}"
        return self._fetch_csv_from_url(url, cache_path, use_cache=use_cache)

    @staticmethod
    def _parse_csv(text: str) -> Optional[pd.DataFrame]:
        """Parse CSV text (no header row) into a DataFrame."""
        if not text.strip():
            return None
        df = pd.read_csv(
            io.StringIO(text),
            header=None,
            names=_OHLCV_COLUMNS,
        )
        # Normalize time format: replace T separator with space
        df["time"] = df["time"].astype(str).str.replace("T", " ", n=1)
        return df

    def _fetch_csv_yearly(
        self,
        source: str,
        ticker: str,
        interval: str,
        year: int,
        *,
        use_cache: bool = True,
    ) -> Optional[pd.DataFrame]:
        """Fetch a yearly aggregate CSV as a DataFrame.

        Returns a DataFrame with all bars for the given year,
        or None if the file doesn't exist (404).
        """
        cache_path = self._cache_key_yearly(source, ticker, interval, year)
        url = f"{self.base_url}/{self._csv_key_yearly(source, ticker, interval, year)}"
        return self._fetch_csv_from_url(url, cache_path, use_cache=use_cache)

    @staticmethod
    def _covered_dates_from_yearly(df: pd.DataFrame) -> set[date]:
        """Extract unique dates from a yearly DataFrame's 'time' column."""
        if df is None or df.empty:
            return set()
        dates: set[date] = set()
        for val in df["time"].dropna().unique():
            s = str(val).strip()
            date_str = s.split(" ")[0] if " " in s else s
            try:
                dates.add(date.fromisoformat(date_str))
            except (ValueError, TypeError):
                continue
        return dates

    @staticmethod
    def _max_date_from_yearly(df: pd.DataFrame) -> date:
        """Get the latest date from a yearly DataFrame's 'time' column."""
        if df is None or df.empty:
            return date.min
        max_val = str(df["time"].dropna().iloc[-1]).strip()
        date_str = max_val.split(" ")[0] if " " in max_val else max_val
        return date.fromisoformat(date_str)

    def _fetch_ohlcv_for_ticker(
        self,
        source: str,
        ticker: str,
        interval: str,
        days: list[date],
        *,
        need_rows: Optional[int] = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """Fetch OHLCV data for a single ticker across a date range.

        For 1D interval: tries yearly files first, then per-day fallback.
        For other intervals: fetches from latest to past, stops when enough rows collected.
        When need_rows is set and no explicit start_date given, uses greedy backwards fetch.
        """
        if interval not in ("1D", "1h") or not days:
            # Non-1D or empty: greedy backwards fetch (stop when enough rows)
            if need_rows is not None:
                return self._fetch_backwards(
                    source, ticker, interval, days, need_rows, use_cache=use_cache
                )
            frames: list[pd.DataFrame] = []
            for day in days:
                df = self._fetch_csv(source, ticker, interval, day, use_cache=use_cache)
                if df is not None and not df.empty:
                    frames.append(df)
            if not frames:
                return pd.DataFrame(columns=_OHLCV_COLUMNS)
            return pd.concat(frames, ignore_index=True)

        # 1D and 1h intervals: prefer yearly files
        start = min(days)
        end = max(days)
        yearly_frames: list[pd.DataFrame] = []
        # Track which years have complete yearly coverage
        yearly_years_covered: set[int] = set()
        yearly_max_date: date | None = None

        # Identify years overlapping the requested range
        years = sorted(set(start.year + i for i in range(end.year - start.year + 1)))

        # Try fetching yearly files for each year (newest first for early stop)
        yearly_row_count = 0
        for year in reversed(years):
            df = self._fetch_csv_yearly(
                source, ticker, interval, year, use_cache=use_cache
            )
            if df is not None and not df.empty:
                yearly_frames.append(df)
                yearly_years_covered.add(year)
                yearly_row_count += len(df)
                # Track the latest date across all yearly files
                max_in_year = self._max_date_from_yearly(df)
                if yearly_max_date is None or max_in_year > yearly_max_date:
                    yearly_max_date = max_in_year
            # Stop early if we have enough rows
            if need_rows is not None and yearly_row_count >= need_rows:
                break

        # If yearly files already have enough rows, skip per-day fallback
        if need_rows is not None and yearly_row_count >= need_rows:
            result = (
                pd.concat(yearly_frames, ignore_index=True)
                if yearly_frames
                else pd.DataFrame(columns=_OHLCV_COLUMNS)
            )
            if not result.empty and "time" in result.columns:
                result = result.drop_duplicates(
                    subset=["time"], keep="first"
                ).reset_index(drop=True)
            return result

        # Compute remaining days: only dates NOT within a fully-covered year,
        # and only dates at the tail end (after yearly_max_date) in case
        # today's data hasn't been aggregated into the yearly file yet.
        if yearly_max_date:
            remaining_days = [
                d
                for d in days
                if d.year not in yearly_years_covered or d > yearly_max_date
            ]
        else:
            # No yearly files fetched, fall back to all days
            remaining_days = days

        # Fetch remaining days one-by-one
        fallback_frames: list[pd.DataFrame] = []
        for day in remaining_days:
            df = self._fetch_csv(source, ticker, interval, day, use_cache=use_cache)
            if df is not None and not df.empty:
                fallback_frames.append(df)

        # Merge all frames
        all_frames = yearly_frames + fallback_frames
        if not all_frames:
            return pd.DataFrame(columns=_OHLCV_COLUMNS)

        result = pd.concat(all_frames, ignore_index=True)

        # Deduplicate by time (in case a day appears in both yearly and per-day)
        if not result.empty and "time" in result.columns:
            result = result.drop_duplicates(subset=["time"], keep="first").reset_index(
                drop=True
            )

        return result

    def _fetch_backwards(
        self,
        source: str,
        ticker: str,
        interval: str,
        days: list[date],
        need_rows: int,
        *,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """Fetch per-day CSVs from newest to oldest, stopping when enough rows collected.

        Iterates the days list in reverse (newest first). Once we have need_rows
        total bars across all fetched files, stop fetching more. Also stops early
        after a streak of consecutive 404s (non-trading days or future dates).
        """
        frames: list[pd.DataFrame] = []
        total_rows = 0
        consecutive_misses = 0

        # VN stocks: weekday-only, so 3+ misses in a row means we've gone past data
        # Crypto: 24/7, so use a tighter threshold
        max_consecutive_misses = 7 if source == "vn" else 14

        for day in reversed(days):
            if total_rows >= need_rows:
                break
            if consecutive_misses >= max_consecutive_misses:
                break

            df = self._fetch_csv(source, ticker, interval, day, use_cache=use_cache)
            if df is not None and not df.empty:
                frames.append(df)
                total_rows += len(df)
                consecutive_misses = 0
            else:
                consecutive_misses += 1

        if not frames:
            return pd.DataFrame(columns=_OHLCV_COLUMNS)

        # Reverse so result is chronological (oldest first)
        frames.reverse()
        return pd.concat(frames, ignore_index=True)

    # ── Content hash (change detection without downloading) ──

    def get_content_hash(
        self,
        ticker: str,
        interval: str,
        day: Union[str, date, datetime],
        *,
        source: Optional[str] = None,
    ) -> Optional[str]:
        """Get the content-hash for a CSV file without downloading it.

        Returns the SHA-256 hash string, or None if the file doesn't exist.
        Uses HTTP HEAD to read the x-amz-meta-content-hash header.
        """
        source, ticker = self._find_source(ticker, source)
        day = self._parse_date(day)
        key = self._csv_key(source, ticker, interval, day)
        url = f"{self.base_url}/{key}"

        return self._head_content_hash(url)

    # ── Live data overlay ──

    def fetch_live_data(self, interval: str) -> Optional[dict]:
        """Fetch live OHLCV data from the REST API with caching.

        Returns the parsed JSON dict (ticker -> list of candles), or None on failure.
        Uses an in-memory cache with TTL to avoid hammering the API.
        On failure, returns stale cached data if available.
        """
        now = _time.monotonic()
        cached = self._live_cache.get(interval)
        if cached and (now - cached["fetched_at"]) < _LIVE_CACHE_TTL:
            return cached["data"]

        limit = _LIVE_LIMITS.get(interval, 1)
        url = (
            f"{self._live_url}/tickers"
            f"?interval={interval}&mode=all&format=json&limit={limit}&ma=true"
        )

        try:
            resp = self._session.get(url, timeout=_LIVE_REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            self._live_cache[interval] = {"data": data, "fetched_at": now}
            return data
        except Exception:
            # Return stale cache if available
            if cached is not None:
                return cached["data"]
            return None

    @staticmethod
    def _normalize_time_str(t: str) -> str:
        """Normalize time string for comparison between S3 and live data.

        S3 format: "2025-04-29 00:00:00" or "2025-04-29T14:00:00"
        Live format: "2025-04-29" or "2025-04-29T14:00:00"
        Normalize to the shorter form by stripping " 00:00:00" suffix.
        """
        t = str(t).strip().replace("T", " ")
        if t.endswith(" 00:00:00"):
            return t[:10]
        return t

    def _merge_live_data(
        self,
        s3_df: pd.DataFrame,
        live_data: dict,
        resolved: list[tuple[str, str]],
    ) -> pd.DataFrame:
        """Merge live API data on top of S3 data.

        For each requested ticker:
        1. Extract only OHLCV columns from live data (drop extra API fields).
        2. Remove S3 rows whose time overlaps with live data.
        3. Append live rows (overwrites + newer candles).
        4. Sort by time.

        Tickers not present in live data are left unchanged.
        Live candles older than the oldest S3 candle are dropped.
        """
        if s3_df.empty:
            return s3_df

        all_rows: list[pd.DataFrame] = []

        for sym, group in s3_df.groupby("symbol", sort=False):
            ticker = sym
            live_candles = live_data.get(ticker)
            if not live_candles:
                all_rows.append(group)
                continue

            # Build live DataFrame with only OHLCV columns
            live_rows = []
            for c in live_candles:
                live_rows.append({col: c.get(col) for col in _OHLCV_COLUMNS})
            live_df = pd.DataFrame(live_rows)

            if live_df.empty:
                all_rows.append(group)
                continue

            # Normalize time strings for comparison
            s3_normalized = group["time"].apply(self._normalize_time_str)
            live_normalized = live_df["time"].apply(self._normalize_time_str)

            # Drop S3 rows whose time overlaps with live data
            live_times = set(live_normalized)
            s3_mask = ~s3_normalized.isin(live_times)
            filtered = group[s3_mask]

            # Drop live candles older than the oldest S3 candle
            if not filtered.empty:
                oldest_s3_time = self._normalize_time_str(filtered["time"].iloc[0])
                live_df = live_df[live_normalized >= oldest_s3_time]

            live_df["symbol"] = ticker
            merged = pd.concat([filtered, live_df], ignore_index=True)
            merged = merged.sort_values("time").reset_index(drop=True)
            all_rows.append(merged)

        if not all_rows:
            return s3_df

        return pd.concat(all_rows, ignore_index=True)

    # ── OHLCV data (mirrors /tickers endpoint) ──

    def _convert_time_str(self, time_str: str, interval: str) -> str:
        """Convert a UTC time string to the configured timezone."""
        dt = _parse_utc(time_str)
        if dt is None:
            return time_str
        local = dt.astimezone(self._utc_offset)
        if interval in _DATE_ONLY_INTERVALS:
            return local.strftime("%Y-%m-%d")
        return local.strftime("%Y-%m-%d %H:%M:%S")

    def _convert_time_column(self, df: pd.DataFrame, interval: str) -> pd.DataFrame:
        """Apply timezone conversion to the time column."""
        df = df.copy()
        df["time"] = df["time"].apply(lambda t: self._convert_time_str(str(t), interval))
        return df

    def get_ohlcv(
        self,
        ticker: Optional[str] = None,
        tickers: Optional[list[str]] = None,
        interval: str = "1D",
        *,
        limit: Optional[int] = None,
        start_date: Optional[Union[str, date, datetime]] = None,
        end_date: Optional[Union[str, date, datetime]] = None,
        source: Optional[str] = None,
        ma: bool = True,
        ema: bool = False,
    ) -> pd.DataFrame:
        """Get OHLCV data as a pandas DataFrame.

        Mirrors the /tickers REST API endpoint parameters.

        When ``use_live=True`` is set on the client and the interval is a native
        one (``1D``, ``1h``, ``1m``), live data from the REST API is overlaid on
        top of S3 data — the last candle(s) are overwritten and any newer candles
        are appended. If the live API is unreachable, stale S3 data is used.

        Args:
            ticker: Single ticker symbol (e.g. "VCB", "BTCUSDT"). None = all tickers.
            tickers: Multiple ticker symbols. Mutually exclusive with ticker.
            interval: Time interval. Native: "1D", "1h", "1m". Aggregated: "5m", "15m",
                      "30m", "4h", "1W", "2W", "1M". Default: "1D".
            limit: Max rows per ticker. Applied after fetching. Default: 252 for single ticker,
                1 for multiple tickers. None = all rows in date range.
            start_date: Start date (inclusive). String "YYYY-MM-DD" or date object.
            end_date: End date (inclusive). String "YYYY-MM-DD" or date object.
            source: Override source ("vn", "yahoo", "crypto", "sjc"). None = auto-detect.
            ma: Calculate MA indicators and scores (default: True).
                When True, fetches extra history (400 days before start_date) to
                warm the MA-200 buffer.
            ema: Use EMA instead of SMA for MA indicators (default: False).

        Returns:
            DataFrame with columns: time, open, high, low, close, volume, symbol.
            When ma=True, also includes: ma10..ma200, ma10_score..ma200_score,
            close_changed, volume_changed, total_money_changed.
            Empty DataFrame if no data found.
        """
        if ticker and tickers:
            raise ValueError("Use either 'ticker' or 'tickers', not both")

        if interval.upper() not in {i.upper() for i in _ALL_INTERVALS}:
            raise ValueError(f"Invalid interval '{interval}'. Valid: {_ALL_INTERVALS}")

        # Normalize interval to S3 key format
        norm_interval = self._normalize_interval(interval)

        # Resolve ticker symbols
        sym_list = None
        if ticker:
            sym_list = [ticker]
        elif tickers:
            sym_list = tickers

        resolved = self._resolve_tickers(sym_list, source)

        # Compute effective limit (matches Rust /tickers defaults)
        is_single = len(resolved) == 1
        has_explicit_dates = start_date is not None
        if limit is None:
            if is_single:
                limit = 10000 if has_explicit_dates else 252
            else:
                limit = 1

        # Compute date range
        end = self._parse_date(end_date) if end_date else date.today()
        start = self._parse_date(start_date) if start_date else None

        # Determine how many candles we need in total.
        # limit is user-visible candles; ma=True adds 200 buffer candles for MA-200.
        # This is in candles/rows, NOT days.
        _MA_BUFFER_ROWS = 200
        total_need = (limit + _MA_BUFFER_ROWS) if ma else limit

        has_explicit_start = start_date is not None
        need_rows = total_need if not has_explicit_start else None

        if start is None:
            # No explicit start: generous upper bound in calendar days.
            # _fetch_backwards stops early when enough rows are collected.
            lookback = _ma_buffer_days(norm_interval) + 500 if ma else 400
            start = end - timedelta(days=lookback)

        # Expand start for MA buffer only when explicit start_date is given
        # (when no start_date, need_rows handles early termination instead)
        ma_buffer_start = start
        user_start = start
        if ma and has_explicit_start:
            ma_buffer_start = start - timedelta(days=_ma_buffer_days(norm_interval))

        days = self._date_range(ma_buffer_start, end)

        # Fetch and concatenate
        frames: list[pd.DataFrame] = []
        for src, sym in resolved:
            df = self._fetch_ohlcv_for_ticker(
                src,
                sym,
                norm_interval,
                days,
                need_rows=need_rows,
            )
            if df.empty:
                continue
            df["symbol"] = sym
            frames.append(df)

        if not frames:
            return pd.DataFrame(
                columns=["time", "open", "high", "low", "close", "volume", "symbol"]
            )

        result = pd.concat(frames, ignore_index=True)

        # Overlay live data on top of S3 data (before MA computation)
        if self.use_live and norm_interval in _LIVE_NATIVE_INTERVALS:
            live_data = self.fetch_live_data(norm_interval)
            if live_data is not None:
                result = self._merge_live_data(result, live_data, resolved)

        # Compute MA indicators per symbol
        if ma:
            from .indicators import compute_indicators

            all_rows: list[pd.DataFrame] = []
            for sym, group in result.groupby("symbol", sort=False):
                group = group.sort_values("time").reset_index(drop=True)

                closes = group["close"].tolist()
                volumes = group["volume"].astype(int).tolist()
                indicators = compute_indicators(closes, volumes, use_ema=ema)

                for col in _MA_COLUMNS:
                    group[col] = indicators[col]

                all_rows.append(group)

            result = pd.concat(all_rows, ignore_index=True)

            # Trim to user-requested date range
            if ma_buffer_start < user_start:
                result["_time_parsed"] = pd.to_datetime(result["time"])
                cutoff = pd.Timestamp(user_start.isoformat())
                result = result[result["_time_parsed"] >= cutoff].drop(
                    columns=["_time_parsed"]
                )

        # Apply limit per symbol
        if limit is not None:
            result = (
                result.groupby("symbol", sort=False).tail(limit).reset_index(drop=True)
            )

        # Convert time column to configured timezone
        if self._utc_offset is not None:
            result = self._convert_time_column(result, interval)

        return result

    def to_ticker_records(self, df: pd.DataFrame) -> dict[str, list[Ticker]]:
        """Convert get_ohlcv() DataFrame to dict of Ticker lists for AIContextBuilder.

        Args:
            df: DataFrame from get_ohlcv().

        Returns:
            Dict mapping symbol -> list of Ticker objects.
        """
        from .context import AIContextBuilder

        return AIContextBuilder._df_to_records(df)

    # ── Download CSVs ──

    def download_csv(
        self,
        ticker: str,
        interval: str = "1D",
        *,
        limit: Optional[int] = None,
        start_date: Optional[Union[str, date, datetime]] = None,
        end_date: Optional[Union[str, date, datetime]] = None,
        source: Optional[str] = None,
        output_dir: str = ".",
    ) -> list[str]:
        """Download CSV files to a local folder.

        Args:
            ticker: Ticker symbol (e.g. "VCB", "BTCUSDT")
            interval: Time interval ("1D", "1h", "1m"). Default: "1D".
            limit: Max number of days to fetch.
            start_date: Start date (inclusive).
            end_date: End date (inclusive).
            source: Override source.
            output_dir: Directory to save files.

        Returns:
            List of downloaded file paths.
        """
        source, ticker = self._find_source(ticker, source)
        norm_interval = self._normalize_interval(interval)

        end = self._parse_date(end_date) if end_date else date.today()
        start = (
            self._parse_date(start_date) if start_date else end - timedelta(days=365)
        )
        days = self._date_range(start, end)

        if limit is not None:
            days = days[-limit:]

        os.makedirs(output_dir, exist_ok=True)
        paths: list[str] = []

        if norm_interval in ("1D", "1h"):
            # Fetch all data at once (yearly + fallback), then split by day
            all_df = self._fetch_ohlcv_for_ticker(source, ticker, norm_interval, days)
            if not all_df.empty:
                for day in days:
                    day_str = day.isoformat()
                    matching = all_df[
                        all_df["time"].astype(str).str.startswith(day_str)
                    ]
                    if matching.empty:
                        continue
                    filename = f"{ticker}-{norm_interval}-{day_str}.csv"
                    filepath = os.path.join(output_dir, filename)
                    matching.to_csv(filepath, index=False)
                    paths.append(filepath)
        else:
            # Non-1D: existing per-day behavior
            for day in days:
                df = self._fetch_csv(source, ticker, norm_interval, day)
                if df is None:
                    continue
                filename = f"{ticker}-{norm_interval}-{day.isoformat()}.csv"
                filepath = os.path.join(output_dir, filename)
                df.to_csv(filepath, index=False)
                paths.append(filepath)

        return paths

    # ── Interval normalization ──

    @staticmethod
    def _normalize_interval(interval: str) -> str:
        """Normalize interval string to S3 key format.

        S3 keys use: 1D, 1h, 1m (native intervals only).
        Aggregated intervals (5m, 15m, etc.) are not stored in S3.
        """
        upper = interval.upper()
        mapping = {
            "DAILY": "1D",
            "HOURLY": "1h",
            "MINUTE": "1m",
        }
        if upper in mapping:
            return mapping[upper]
        if interval in {"1D", "1h", "1m"}:
            return interval
        raise ValueError(
            f"Aggregated interval '{interval}' is not available in S3 archive. "
            f"Native intervals: 1D, 1h, 1m"
        )
