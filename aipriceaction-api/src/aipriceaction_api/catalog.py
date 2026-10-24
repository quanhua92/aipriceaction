import gzip
import json
from functools import cached_property
from pathlib import Path

from .responses import RustJSONResponse, sort_json_value_keys

RUST_TICKER_LIST_KEY = "meta:ticker_list"
# The Oct 24, 2026 catalog snapshot showed this cutoff retains 99% of known
# Vietnamese stock market capitalization. Keep one API default for all metadata routes.
DEFAULT_VN_MIN_MARKET_CAP_VND = 387_175_000_000


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
    def groups_by_source(self):
        vn = self.vn_sector_groups | {"ALL_STOCKS": list(self.vn_stock_symbols)}
        crypto = {
            "CRYPTO_TOP_100": [r["symbol"] for r in self.load("binance_tickers.json")["data"]]
        }
        result = {"vn": vn, "crypto": crypto}
        for source, file in (("yahoo", "global_tickers.json"), ("sjc", "sjc_tickers.json")):
            groups = {}
            for row in self.load(file)["data"]:
                groups.setdefault(row.get("category", "Other"), []).append(row["symbol"])
            result[source] = groups
        return result

    @cached_property
    def vn_stock_rows(self):
        payload = self.load("vn_tickers.json")
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("vn_tickers.json has an unsupported or invalid schema")
        rows = payload.get("stocks")
        if not isinstance(rows, list):
            raise ValueError("vn_tickers.json has an unsupported or invalid schema")
        by_symbol = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("symbol"), str):
                raise ValueError("vn_tickers.json contains a row without a symbol")
            symbol = row["symbol"].strip().upper()
            market_cap = row.get("market_cap_vnd")
            if (
                not symbol
                or row.get("type") != "stock"
                or symbol in by_symbol
                or (market_cap is not None and (type(market_cap) is not int or market_cap < 0))
            ):
                raise ValueError(f"vn_tickers.json contains an invalid stock row: {symbol!r}")
            by_symbol[symbol] = row | {"symbol": symbol}
        return payload, tuple(by_symbol[symbol] for symbol in sorted(by_symbol))

    def vn_tickers(
        self,
        min_market_cap_vnd=None,
        include_unknown_market_cap=False,
        include_delisted=True,
    ):
        """Load the one Vietnamese stock catalog and apply its API filters."""
        _payload, rows = self.vn_stock_rows
        selected = rows if include_delisted else tuple(
            row
            for row in rows
            if row.get("listing_status") != "delisted"
        )
        if min_market_cap_vnd is None:
            return selected
        return tuple(
            row
            for row in selected
            if (
                row.get("market_cap_vnd") is not None
                and row["market_cap_vnd"] >= min_market_cap_vnd
            )
            or (include_unknown_market_cap and row.get("market_cap_vnd") is None)
        )

    @property
    def vn_stock_symbols(self):
        return tuple(row["symbol"] for row in self.vn_tickers())

    @property
    def vn_listed_symbols(self):
        return tuple(row["symbol"] for row in self.vn_tickers(include_delisted=False))

    @cached_property
    def vn_index_rows(self):
        path = getattr(self.settings, "watchlist", None)
        path = Path(path) if path is not None else None
        if path is None or not path.is_file():
            path = self.settings.catalog_dir / "watchlist.json"
        if path is None:
            return ()
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return ()
        items = payload.get("vn", []) if isinstance(payload, dict) else []
        stock_symbols = set(self.vn_stock_symbols)
        by_symbol = {}
        for item in items if isinstance(items, list) else ():
            if not isinstance(item, dict) or item.get("asset_type") != "index":
                continue
            symbol = item.get("symbol")
            if not isinstance(symbol, str):
                continue
            symbol = symbol.strip().upper()
            if symbol and symbol not in stock_symbols:
                by_symbol[symbol] = {"symbol": symbol, "name": item.get("name") or symbol}
        return tuple(by_symbol[symbol] for symbol in sorted(by_symbol))

    @property
    def vn_sector_groups(self):
        return self.vn_groups()

    def vn_groups(
        self,
        min_market_cap_vnd=None,
        include_unknown_market_cap=False,
        include_delisted=True,
        lang="en",
    ):
        if lang not in ("en", "vi"):
            raise ValueError("lang must be en or vi")
        groups = {}
        for row in self.vn_tickers(
            min_market_cap_vnd,
            include_unknown_market_cap,
            include_delisted,
        ):
            preferred = "icb_level2_name_en" if lang == "en" else "icb_level2_name"
            fallback = "icb_level2_name" if lang == "en" else "icb_level2_name_en"
            group = row.get(preferred) or row.get(fallback) or "Unclassified"
            groups.setdefault(group, []).append(row["symbol"])
        if min_market_cap_vnd is None and self.vn_index_rows:
            groups["INDEX"] = [row["symbol"] for row in self.vn_index_rows]
        return {name: sorted(symbols) for name, symbols in sorted(groups.items())}

    def stock_directory(self, min_market_cap_vnd=None, include_unknown_market_cap=False):
        payload, rows = self.vn_stock_rows
        selected = self.vn_tickers(min_market_cap_vnd, include_unknown_market_cap)
        known = sum(row.get("market_cap_vnd") is not None for row in rows)
        return {
            "generated_at": payload.get("generated_at"),
            "total_stocks": len(rows),
            "market_cap_known": known,
            "market_cap_unknown": len(rows) - known,
            "returned": len(selected),
            "min_market_cap_vnd": min_market_cap_vnd,
            "include_unknown_market_cap": include_unknown_market_cap,
            "stocks": list(selected),
        }

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

    def groups(
        self,
        mode,
        min_market_cap_vnd=None,
        include_unknown_market_cap=False,
        lang="en",
    ):
        sources = (
            ("vn", "yahoo", "sjc", "crypto")
            if mode == "all"
            else ("yahoo", "sjc")
            if mode == "yahoo"
            else (mode,)
        )
        merged = {}
        for source in sources:
            source_groups = (
                self.vn_groups(
                    min_market_cap_vnd,
                    include_unknown_market_cap,
                    lang=lang,
                )
                if source == "vn"
                else self.groups_by_source[source]
            )
            for name, symbols in source_groups.items():
                if source == "vn" and name == "ALL_STOCKS":
                    continue
                if name not in merged:
                    merged[name] = list(symbols)
                elif mode == "yahoo" or source == "sjc":
                    merged[name].extend(symbols)
        return dict(sorted(merged.items()))

    @cached_property
    def vn_info(self):
        return {
            row["symbol"]: {
                "ticker": row["symbol"],
                "organ_name": row.get("name") or row.get("en_name") or row["symbol"],
                "en_organ_name": row.get("en_name") or row.get("name") or row["symbol"],
                "exchange": row.get("exchange") or "",
                "type": "stock",
            }
            for row in self.vn_tickers()
        }

    @cached_property
    def names_by_source(self):
        result = {
            "vn": {
                row["symbol"]: row.get("name") or row["en_name"] or row["symbol"]
                for row in self.vn_tickers()
            }
        }
        result["vn"].update({row["symbol"]: row["name"] for row in self.vn_index_rows})
        for source, file in (
            ("yahoo", "global_tickers.json"),
            ("crypto", "binance_tickers.json"),
            ("sjc", "sjc_tickers.json"),
        ):
            result[source] = {
                row["symbol"]: row["name"] for row in self.load(file)["data"] if "name" in row
            }
        return result

    def names(self, mode, repo=None, min_market_cap_vnd=None, include_unknown_market_cap=False):
        sources = (
            ("vn", "yahoo", "sjc", "crypto")
            if mode == "all"
            else ("yahoo", "sjc")
            if mode == "yahoo"
            else (mode,)
        )
        result = {}
        for source in sources:
            source_names = (
                {
                    row["symbol"]: row.get("name") or row.get("en_name") or row["symbol"]
                    for row in self.vn_tickers(
                        min_market_cap_vnd, include_unknown_market_cap
                    )
                }
                if source == "vn"
                else self.names_by_source[source]
            )
            if source == "vn" and min_market_cap_vnd is None:
                source_names.update(
                    {row["symbol"]: row["name"] for row in self.vn_index_rows}
                )
            for symbol, name in source_names.items():
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
        profiles = {
            row["ticker"].upper(): row
            for row in rows
            if isinstance(row.get("ticker"), str)
        }
        enriched = []
        for stock in self.vn_tickers():
            symbol = stock["symbol"]
            profile = profiles.get(symbol, {})
            enriched_row = profile | self.vn_info[symbol]
            enriched_row.update(
                {
                    "ticker": symbol,
                    "name": (
                        profile.get("name")
                        or stock.get("name")
                        or stock.get("en_name")
                        or symbol
                    ),
                    "type": "stock",
                    "listing_status": stock.get("listing_status"),
                    "listing_board": stock.get("listing_board"),
                    "market_cap_vnd": stock.get("market_cap_vnd"),
                    "market_cap_as_of": stock.get("market_cap_as_of"),
                    "market_cap_price_vnd": stock.get("market_cap_price_vnd"),
                    "market_cap_price_basis": stock.get("market_cap_price_basis"),
                    "shares_listed": stock.get("shares_listed"),
                    "icb_level2_code": stock.get("icb_level2_code"),
                    "icb_level2_name": stock.get("icb_level2_name"),
                    "icb_level2_name_en": stock.get("icb_level2_name_en"),
                }
            )
            enriched_row.setdefault("company_info", None)
            enriched_row.setdefault("financial_ratios", [])
            enriched.append(enriched_row)
        return sorted(enriched, key=lambda row: row["ticker"])

    def filtered_info(self, min_market_cap_vnd=None, include_unknown_market_cap=False):
        selected = {
            row["symbol"]
            for row in self.vn_tickers(min_market_cap_vnd, include_unknown_market_cap)
        }
        return [row for row in self.sorted_info if row["ticker"] in selected]

    def filtered_info_by_ticker(
        self, ticker, min_market_cap_vnd=None, include_unknown_market_cap=False
    ):
        row = self.info_by_ticker.get(ticker.upper())
        if row is None or min_market_cap_vnd is None:
            return row
        market_cap = row.get("market_cap_vnd")
        if market_cap is None:
            return row if include_unknown_market_cap else None
        return row if market_cap >= min_market_cap_vnd else None

    @cached_property
    def sorted_info(self):
        """Cache Rust-compatible field ordering for the immutable company catalog."""
        return sort_json_value_keys(self.info)

    @cached_property
    def info_by_ticker(self):
        return {row["ticker"].upper(): row for row in self.sorted_info}

    @cached_property
    def info_json(self):
        """Render the large static info response once instead of per request."""
        return RustJSONResponse(self.sorted_info).body

    @cached_property
    def default_filtered_info_json(self):
        """Cache the API's default 99%-coverage metadata response once."""
        return RustJSONResponse(
            self.filtered_info(DEFAULT_VN_MIN_MARKET_CAP_VND)
        ).body

    def sector(self, source, symbol):
        for sector, symbols in sorted(self.groups_by_source[source].items()):
            if sector != "ALL_STOCKS" and symbol in symbols:
                return sector
        return None

    def initialize(self, repo):
        for source, groups in self.groups_by_source.items():
            symbols = set(s for values in groups.values() for s in values)
            if source == "vn":
                symbols = set(self.vn_listed_symbols) | {
                    row["symbol"] for row in self.vn_index_rows
                }
            for symbol in symbols:
                repo.register(source, symbol, self.names_by_source[source].get(symbol))
