import csv
import gzip
import json
from functools import cached_property


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
                row[0].upper(): {
                    "ticker": row[0],
                    "organ_name": row[1],
                    "en_organ_name": row[2],
                    "exchange": row[3],
                    "type": row[4],
                }
                for row in reader
                if len(row) >= 5
            }

    @cached_property
    def names_by_source(self):
        valid = set(s for symbols in self.groups_by_source["vn"].values() for s in symbols)
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
        registered = {source: {} for source in sources}
        if repo is not None:
            for row in repo.discovered_tickers(sources):
                registered[row["source"]][row["symbol"]] = row["name"] or row["symbol"]
        for source in sources:
            names = registered[source] | self.names_by_source[source]
            for symbol, name in names.items():
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
            if symbol in symbols:
                return sector
        return None

    def initialize(self, repo):
        for source, groups in self.groups_by_source.items():
            for symbol in set(s for symbols in groups.values() for s in symbols):
                repo.register(source, symbol, self.names_by_source[source].get(symbol))
