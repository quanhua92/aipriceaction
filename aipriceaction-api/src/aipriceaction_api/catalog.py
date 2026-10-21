import csv
import gzip
import json
from functools import cached_property

from .config import trim_rust_whitespace

RUST_TICKER_LIST_KEY = "meta:ticker_list"


class _JsonObject(dict):
    """JSON object retaining its pairs so required-field duplicates are visible."""

    def __init__(self, pairs):
        super().__init__(pairs)
        self.pairs = pairs


def _json_object(pairs):
    return _JsonObject(pairs)


def _reject_nonstandard_json_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


class Catalog:
    def __init__(self, settings):
        self.settings = settings

    def load(self, name):
        return json.loads((self.settings.catalog_dir / name).read_text())

    @cached_property
    def snapshot(self):
        path = self.settings.catalog_snapshot
        if not path.exists():
            return None
        value = json.loads(path.read_text())
        if value.get("schema") != 1 or set(value.get("groups", {})) != {
            "vn",
            "crypto",
            "yahoo",
        }:
            raise ValueError("Invalid catalog snapshot")
        return value

    @cached_property
    def groups_by_source(self):
        if self.snapshot:
            vn = {group: list(symbols) for group, symbols in self.snapshot["groups"]["vn"].items()}
            crypto = self.snapshot["groups"]["crypto"]
        else:
            vn = {group: list(symbols) for group, symbols in self.load("ticker_group.json").items()}
            crypto = {
                "CRYPTO_TOP_100": [r["symbol"] for r in self.load("binance_tickers.json")["data"]]
            }
        # Sector groups are intentionally curated and omit most listed shares.
        # Keep a complete stock directory alongside them so daily discovery is
        # not limited by the legacy web catalog.
        vn["ALL_STOCKS"] = self.vn_stock_symbols
        result = {"vn": vn, "crypto": crypto}
        for source, file in (("yahoo", "global_tickers.json"), ("sjc", "sjc_tickers.json")):
            groups = {}
            for row in self.load(file)["data"]:
                groups.setdefault(row.get("category", "Other"), []).append(row["symbol"])
            result[source] = groups
        if self.snapshot:
            sjc_symbols = {symbol for symbols in result["sjc"].values() for symbol in symbols}
            result["yahoo"] = {
                group: [symbol for symbol in symbols if symbol not in sjc_symbols]
                for group, symbols in self.snapshot["groups"]["yahoo"].items()
            }
            result["yahoo"] = {
                group: symbols for group, symbols in result["yahoo"].items() if symbols
            }
        return result

    @cached_property
    def vn_stock_symbols(self):
        return sorted(symbol for symbol, row in self.vn_info.items() if row["type"] == "stock")

    @cached_property
    def curated_vn_symbols(self):
        groups = self.snapshot["groups"]["vn"] if self.snapshot else self.load("ticker_group.json")
        return {symbol for symbols in groups.values() for symbol in symbols}

    def redis_ticker_list(self, redis_client=None):
        """Read Rust's ``meta:ticker_list`` cache as ``[{source, ticker}, ...]``.

        A missing client/key, Redis error, invalid UTF-8/JSON, or a value that
        cannot deserialize as Rust's ``Vec<TickerInfo>`` returns ``None``.
        A valid empty array remains ``[]`` so callers can preserve Rust's
        distinction between an empty cached list and an unavailable cache.
        """

        if redis_client is None:
            return None
        try:
            raw = redis_client.get(RUST_TICKER_LIST_KEY)
        except Exception:
            # Rust's Redis reader falls back on connection errors and timeouts.
            return None
        if isinstance(raw, bytes):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError:
                return None
        if not isinstance(raw, str):
            return None
        try:
            tickers = json.loads(
                raw,
                object_pairs_hook=_json_object,
                parse_constant=_reject_nonstandard_json_constant,
            )
        except (json.JSONDecodeError, RecursionError, ValueError):
            return None
        if not isinstance(tickers, list):
            return None

        result = []
        for ticker in tickers:
            if not isinstance(ticker, _JsonObject):
                return None
            pairs = ticker.pairs
            if (
                sum(key == "source" for key, _ in pairs) != 1
                or sum(key == "ticker" for key, _ in pairs) != 1
            ):
                return None
            source = ticker.get("source")
            symbol = ticker.get("ticker")
            if not isinstance(source, str) or not isinstance(symbol, str):
                return None
            result.append({"source": source, "ticker": symbol})
        return result

    def groups(self, mode):
        if self.snapshot and mode in self.snapshot["groups"] and mode != "vn":
            return self.snapshot["groups"][mode]
        sources = (
            ("vn", "yahoo", "sjc", "crypto")
            if mode == "all"
            else ("yahoo", "sjc")
            if mode == "yahoo"
            else (mode,)
        )
        merged = {}
        for source in sources:
            for name, symbols in self.groups_by_source[source].items():
                if source == "vn" and name == "ALL_STOCKS":
                    continue
                if name not in merged:
                    merged[name] = list(symbols)
                elif mode == "yahoo" or source == "sjc":
                    merged[name].extend(symbols)
        return dict(sorted(merged.items()))

    @cached_property
    def vn_info(self):
        with (self.settings.catalog_dir / "vn.csv").open() as handle:
            reader = csv.reader(handle)
            next(reader)
            return {
                trim_rust_whitespace(row[0]).upper(): {
                    "ticker": trim_rust_whitespace(row[0]),
                    "organ_name": trim_rust_whitespace(row[1]),
                    "en_organ_name": trim_rust_whitespace(row[2]),
                    "exchange": trim_rust_whitespace(row[3]),
                    "type": trim_rust_whitespace(row[4]),
                }
                for row in reader
                if len(row) >= 5
            }

    @cached_property
    def names_by_source(self):
        valid = self.curated_vn_symbols
        result = {"vn": {s: r["organ_name"] for s, r in self.vn_info.items() if s in valid}}
        for source, file in (
            ("yahoo", "global_tickers.json"),
            ("crypto", "binance_tickers.json"),
            ("sjc", "sjc_tickers.json"),
        ):
            result[source] = {
                row["symbol"]: row["name"] for row in self.load(file)["data"] if "name" in row
            }
        return result

    def names(self, mode, repo=None):
        sources = (
            ("vn", "yahoo", "sjc", "crypto")
            if mode == "all"
            else ("yahoo", "sjc")
            if mode == "yahoo"
            else (mode,)
        )
        result = {}
        for source in sources:
            for symbol, name in self.names_by_source[source].items():
                result.setdefault(symbol, name)
        return dict(sorted(result.items()))

    @cached_property
    def info(self):
        path = self.settings.company_info or self.settings.catalog_dir / "company_info.json.gz"
        if path.suffix == ".gz":
            with gzip.open(path, "rt") as handle:
                rows = json.load(handle)
        else:
            rows = json.loads(path.read_text())
        return sorted(
            (
                self.vn_info[r["ticker"].upper()] | r
                for r in rows
                if r.get("ticker", "").upper() in self.vn_info
            ),
            key=lambda r: r["ticker"],
        )

    def sector(self, source, symbol):
        for sector, symbols in sorted(self.groups_by_source[source].items()):
            if sector != "ALL_STOCKS" and symbol in symbols:
                return sector
        return None

    def initialize(self, repo):
        for source, groups in self.groups_by_source.items():
            for symbol in set(s for symbols in groups.values() for s in symbols):
                repo.register(source, symbol, self.names_by_source[source].get(symbol))
