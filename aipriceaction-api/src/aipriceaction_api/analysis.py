from datetime import UTC, datetime

from .calculations import enhance, jdk, volume_profile
from .domain import INDEXES, PERIODS, DataError, date_bounds


def envelope(kind, data, date="latest", total=None):
    return {
        "analysis_date": date,
        "analysis_type": kind,
        "total_analyzed": total if total is not None else len(data),
        "data": data,
    }


class Analysis:
    def __init__(self, history, catalog):
        self.history, self.catalog = history, catalog

    def universe(self, mode):
        sources = (
            ("vn", "yahoo", "sjc", "crypto")
            if mode == "all"
            else ("yahoo", "sjc")
            if mode == "yahoo"
            else (mode,)
        )
        return self.history.repo.tickers(sources)

    def latest(self, mode, date=None, ema=False):
        result = []
        for t in self.universe(mode):
            if t["symbol"] in INDEXES:
                continue
            rows = self.history.query(
                t["source"], t["symbol"], "1D", end=date_bounds(date, True), limit=1, ema=ema
            )
            if rows:
                result.append((t["source"], rows[-1]))
        return result

    def performers(
        self,
        mode="vn",
        date=None,
        ema=False,
        sort_by="close_changed",
        direction="desc",
        limit=10,
        min_volume=10000,
        sector=None,
    ):
        allowed = {
            "close_changed",
            "volume",
            "volume_changed",
            "total_money_changed",
            *(f"ma{p}_score" for p in PERIODS),
        }
        if sort_by not in allowed:
            sort_by = "close_changed"  # Rust default on unknown metrics
        result = []
        keys = (
            "close_changed",
            "volume_changed",
            "total_money_changed",
            *(f"ma{p}" for p in PERIODS),
            *(f"ma{p}_score" for p in PERIODS),
        )
        for source, row in self.latest(mode, date, ema):
            if source == "vn" and row["volume"] < min_volume:
                continue
            group = self.catalog.sector(source, row["symbol"])
            if sector and group != sector:
                continue
            out = (
                {k: row[k] for k in ("symbol", "close", "volume")}
                | {k: row.get(k) for k in keys}
                | {"sector": group}
            )
            if mode == "all":
                out["source"] = source
            result.append(out)
        # Keep Rust's ordering for missing metrics (last descending, first ascending).
        desc = sorted(
            result, key=lambda r: (r.get(sort_by) is not None, r.get(sort_by) or 0), reverse=True
        )
        asc = list(reversed(desc))
        n = max(1, min(100, limit))
        top, worst = (asc[:n], desc[:n]) if direction == "asc" else (desc[:n], asc[:n])
        return envelope(
            "top_performers",
            {"performers": top, "worst_performers": worst, "hourly": None},
            date or "latest",
            len(top) + len(worst),
        )

    def sectors(
        self,
        mode="vn",
        date=None,
        ema=False,
        ma_period=20,
        min_score=0,
        above_threshold_only=False,
        top_per_sector=10,
    ):
        if ma_period not in PERIODS:
            raise DataError("Invalid MA period. Must be one of: 10, 20, 50, 100, 200", 400)
        latest = {(src, row["symbol"]): row for src, row in self.latest(mode, date, ema)}
        sources = (
            ("vn", "yahoo", "sjc", "crypto")
            if mode == "all"
            else ("yahoo", "sjc")
            if mode == "yahoo"
            else (mode,)
        )
        sectors = []
        total = 0
        for src in sources:
            for name, symbols in self.catalog.groups_by_source[src].items():
                stocks = []
                above = 0
                for symbol in symbols:
                    row = latest.get((src, symbol))
                    if not row or f"ma{ma_period}" not in row:
                        continue
                    score = row[f"ma{ma_period}_score"]
                    if score >= min_score:
                        above += 1
                    elif above_threshold_only:
                        continue
                    stock = {k: row[k] for k in ("symbol", "close", "volume")} | {
                        "ma_value": row[f"ma{ma_period}"],
                        "ma_score": score,
                        "close_changed": row.get("close_changed"),
                        "volume_changed": row.get("volume_changed"),
                    }
                    if mode == "all":
                        stock["source"] = src
                    stocks.append(stock)
                if stocks:
                    total += len(stocks)
                    sectors.append(
                        {
                            "sector_name": name,
                            "total_stocks": len(stocks),
                            "stocks_above_threshold": above,
                            "average_score": sum(r["ma_score"] for r in stocks) / len(stocks),
                            "top_stocks": sorted(stocks, key=lambda r: r["ma_score"], reverse=True)[
                                : max(0, min(50, top_per_sector))
                            ],
                        }
                    )
        return envelope(
            "ma_scores_by_sector",
            {
                "sectors": sorted(sectors, key=lambda r: r["average_score"], reverse=True),
                "ma_period": ma_period,
                "threshold": min_score,
            },
            date or "latest",
            total,
        )

    def profile(
        self,
        symbol,
        mode="vn",
        date=None,
        start_date=None,
        end_date=None,
        bins=50,
        value_area_pct=70,
    ):
        start = date or start_date
        end = date or end_date or start_date
        if not start:
            raise DataError(
                "Either 'date' or 'start_date' parameter is required (YYYY-MM-DD format)", 400
            )
        lo, hi = date_bounds(start), date_bounds(end, True)
        if hi < lo:
            raise DataError("end_date must be >= start_date", 400)
        source = "crypto" if mode == "crypto" else "yahoo" if mode == "yahoo" else "vn"
        rows = self.history.read(source, symbol, "1m", lo, hi)
        data = volume_profile(rows, symbol, source, bins, value_area_pct)
        return envelope(
            "volume_profile", data, start if start == end else f"{start} to {end}", len(rows)
        )

    def rrg(
        self,
        mode="vn",
        date=None,
        ema=False,
        algorithm="jdk",
        benchmark=None,
        period=10,
        trails=10,
        min_volume=100000,
    ):
        if algorithm not in ("jdk", "mascore"):
            raise DataError("Invalid RRG algorithm", 400)
        period = max(4, min(50, period))
        trails = 0 if trails == 0 else max(1, min(120, trails))
        end = date_bounds(date, True)
        benchmark = (
            benchmark
            or ("BTCUSDT" if mode == "crypto" else "^GSPC" if mode == "yahoo" else "VNINDEX")
        ).upper()
        bench = None
        if algorithm == "jdk":
            for src in ("vn", "crypto", "yahoo"):
                candidate = self.history.read(
                    src, benchmark, "1D", end=end, limit=max(252, 4 * period + trails)
                )
                if candidate:
                    bench = {r.time: r.close for r in candidate if r.close}
                    break
            if not bench:
                raise DataError(f"Benchmark '{benchmark}' not found", 400)
        result = []
        for ticker in self.universe(mode):
            symbol, source = ticker["symbol"], ticker["source"]
            if symbol in INDEXES or symbol == benchmark and algorithm == "jdk":
                continue
            rows = self.history.read(
                source, symbol, "1D", end=end, limit=max(800 if ema else 300, 4 * period + trails)
            )
            if not rows or rows[-1].volume < min_volume:
                continue
            if algorithm == "mascore":
                enhanced = enhance(rows, "1D", True, ema)
                x, y = enhanced[-1].get("ma20_score"), enhanced[-1].get("ma100_score")
                if x is None or y is None:
                    continue
                points = [
                    {
                        "date": r["time"],
                        "rs_ratio": r.get("ma20_score", x),
                        "rs_momentum": r.get("ma100_score", y),
                    }
                    for r in enhanced
                ]
                raw = 0.0
            else:
                aligned = [r for r in rows if r.time in bench]
                computed = jdk([r.close for r in aligned], [bench[r.time] for r in aligned], period)
                if not computed:
                    continue
                relevant = aligned[-len(computed) :]
                points = [
                    {
                        "date": datetime.fromtimestamp(r.time, UTC).strftime("%Y-%m-%d"),
                        "rs_ratio": x,
                        "rs_momentum": y,
                    }
                    for r, (x, y) in zip(relevant, computed, strict=True)
                ]
                raw = aligned[-1].close / bench[aligned[-1].time]
            last = points[-1]
            item = {
                "symbol": symbol,
                "rs_ratio": last["rs_ratio"],
                "rs_momentum": last["rs_momentum"],
                "raw_rs": raw,
                "close": rows[-1].close,
                "volume": rows[-1].volume,
                "sector": self.catalog.sector(source, symbol),
            }
            if mode == "all":
                item["source"] = source
            if trails:
                item["trails"] = points[-trails:]
            result.append(item)
        data = {"algorithm": algorithm, "tickers": sorted(result, key=lambda r: r["symbol"])}
        if algorithm == "jdk":
            data.update(benchmark=benchmark, period=period)
        return envelope(
            "rrg",
            data,
            date
            or ("latest" if algorithm == "mascore" else datetime.now(UTC).strftime("%Y-%m-%d")),
            len(result),
        )
