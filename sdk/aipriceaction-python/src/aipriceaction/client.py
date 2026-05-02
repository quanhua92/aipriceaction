from __future__ import annotations

import io
import json
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Union

import requests
import pandas as pd

from .exceptions import AIPriceActionError
from .models import TickerInfo

# Source auto-detection priority (matches Rust's resolve_ticker_sources)
_SOURCE_PRIORITY = ["vn", "yahoo", "sjc", "crypto"]

_OHLCV_COLUMNS = ["time", "open", "high", "low", "close", "volume"]

_ALL_INTERVALS = {
    "1D", "1d", "daily",
    "1H", "1h", "hourly",
    "1m", "minute",
    "5m", "15m", "30m", "4h",
    "1W", "2W", "1M",
}


class AIPriceAction:
    """Read OHLCV data from aipriceaction S3 archive via plain HTTP.

    No S3 credentials needed — the bucket must be public-read.

    Args:
        base_url: S3 archive base URL (e.g. "http://localhost:9000/aipriceaction-archive")
        cache_dir: Local disk cache directory. Defaults to a temp dir. Pass None to disable.
    """

    def __init__(
        self,
        base_url: str,
        cache_dir: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._tickers_cache: list[TickerInfo] | None = None

        # Disk cache for downloaded CSVs
        if cache_dir is not None:
            self._cache_dir = Path(cache_dir)
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        else:
            self._cache_dir = Path(tempfile.mkdtemp(prefix="aipriceaction-"))

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
        cache_path = self._cache_dir / "_meta" / "tickers.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        # Try disk cache first
        if cache_path.exists():
            try:
                raw = json.loads(cache_path.read_text())
                return [TickerInfo(**t) for t in raw]
            except (json.JSONDecodeError, TypeError):
                pass  # stale cache, refetch

        resp = self._session.get(url)
        if resp.status_code == 404:
            raise AIPriceActionError(f"tickers.json not found: {url}")
        resp.raise_for_status()

        # Write to disk cache
        raw = resp.json()
        cache_path.write_text(json.dumps(raw))

        return [TickerInfo(**t) for t in raw]

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
        # Try disk cache
        cache_path = self._cache_key(source, ticker, interval, day)
        if use_cache and os.path.exists(cache_path):
            try:
                text = Path(cache_path).read_text()
                return self._parse_csv(text)
            except (OSError, pd.errors.EmptyDataError):
                pass

        # Fetch from S3
        url = f"{self.base_url}/{self._csv_key(source, ticker, interval, day)}"
        resp = self._session.get(url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()

        text = resp.text

        # Write to disk cache
        if use_cache:
            Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
            Path(cache_path).write_text(text)

        return self._parse_csv(text)

    @staticmethod
    def _parse_csv(text: str) -> Optional[pd.DataFrame]:
        """Parse CSV text (no header row) into a DataFrame."""
        if not text.strip():
            return None
        return pd.read_csv(
            io.StringIO(text),
            header=None,
            names=_OHLCV_COLUMNS,
        )

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

        resp = self._session.head(url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()

        return resp.headers.get("x-amz-meta-content-hash")

    # ── OHLCV data (mirrors /tickers endpoint) ──

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
    ) -> pd.DataFrame:
        """Get OHLCV data as a pandas DataFrame.

        Mirrors the /tickers REST API endpoint parameters.

        Args:
            ticker: Single ticker symbol (e.g. "VCB", "BTCUSDT"). None = all tickers.
            tickers: Multiple ticker symbols. Mutually exclusive with ticker.
            interval: Time interval. Native: "1D", "1h", "1m". Aggregated: "5m", "15m",
                      "30m", "4h", "1W", "2W", "1M". Default: "1D".
            limit: Max rows per ticker. Applied after fetching. None = all rows.
            start_date: Start date (inclusive). String "YYYY-MM-DD" or date object.
            end_date: End date (inclusive). String "YYYY-MM-DD" or date object.
            source: Override source ("vn", "yahoo", "crypto", "sjc"). None = auto-detect.

        Returns:
            DataFrame with columns: time, open, high, low, close, volume, symbol.
            Empty DataFrame if no data found.
        """
        if ticker and tickers:
            raise ValueError("Use either 'ticker' or 'tickers', not both")

        if interval.upper() not in {i.upper() for i in _ALL_INTERVALS}:
            raise ValueError(
                f"Invalid interval '{interval}'. "
                f"Valid: {_ALL_INTERVALS}"
            )

        # Normalize interval to S3 key format
        norm_interval = self._normalize_interval(interval)

        # Resolve ticker symbols
        sym_list = None
        if ticker:
            sym_list = [ticker]
        elif tickers:
            sym_list = tickers

        resolved = self._resolve_tickers(sym_list, source)

        # Compute date range
        end = self._parse_date(end_date) if end_date else date.today()
        start = self._parse_date(start_date) if start_date else end - timedelta(days=365)
        days = self._date_range(start, end)

        # Fetch and concatenate
        frames: list[pd.DataFrame] = []
        for src, sym in resolved:
            for day in days:
                df = self._fetch_csv(src, sym, norm_interval, day)
                if df is None or df.empty:
                    continue
                df["symbol"] = sym
                frames.append(df)

        if not frames:
            return pd.DataFrame(
                columns=["time", "open", "high", "low", "close", "volume", "symbol"]
            )

        result = pd.concat(frames, ignore_index=True)

        # Apply limit per symbol
        if limit is not None:
            result = result.groupby("symbol", sort=False).tail(limit).reset_index(drop=True)

        return result

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
        start = self._parse_date(start_date) if start_date else end - timedelta(days=365)
        days = self._date_range(start, end)

        if limit is not None:
            days = days[-limit:]

        os.makedirs(output_dir, exist_ok=True)
        paths: list[str] = []

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
