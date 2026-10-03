"""Compare the replacement with bounded public legacy API snapshots, read-only."""

import json
import math
import tempfile
from dataclasses import replace
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from aipriceaction_api.app import create_app
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, cutoff, parse_time


def main():
    root = Path(tempfile.mkdtemp(prefix="aipa-legacy-contract-"))
    settings = replace(
        Settings(),
        database=root / "db.sqlite3",
        cache_dir=root / "cache",
        object_dir=root / "objects",
        archive_backend="filesystem",
    )
    app = create_app(settings)
    report = {"database": str(settings.database), "objects": str(settings.object_dir), "checks": []}
    with (
        httpx.Client(base_url="https://api.aipriceaction.com", timeout=45) as legacy,
        TestClient(app) as local,
    ):
        for symbol, iv, start, end, count in [
            ("FPT", "1D", None, None, 10000),
            ("VCB", "1D", None, None, 1000),
            ("VNINDEX", "1D", None, None, 1000),
            ("FPT", "1m", None, None, 1000),
            ("VCB", "1m", None, None, 1000),
            ("VCB", "1h", None, None, 1000),
            ("VCB", "1h", "2025-04-03", "2025-04-14", 200),
            ("VIC", "1h", "2025-05-14", "2025-10-02", 10000),
            ("VIC", "1m", "2025-01-02", "2025-01-03", 1000),
            ("BTCUSDT", "1D", None, None, 1000),
            ("CL=F", "1D", None, None, 40),
            ("SJC-GOLD", "1D", None, None, 40),
        ]:
            source = (
                "crypto"
                if symbol == "BTCUSDT"
                else "yahoo"
                if symbol == "CL=F"
                else "sjc"
                if symbol == "SJC-GOLD"
                else "vn"
            )
            params = {
                "symbol": symbol,
                "interval": iv,
                "limit": count,
                "ma": "false",
                "cache": "false",
            }
            params["mode"] = "yahoo" if source == "sjc" else source
            if start:
                params.update(start_date=start, end_date=end)
            response = legacy.get("/tickers", params=params)
            response.raise_for_status()
            raw = response.json().get(symbol, [])
            if not raw:
                raise RuntimeError(f"Legacy API returned no fixture for {symbol} {iv} {start}")
            bars = []
            invalid = None
            for r in raw:
                bar = Candle(
                    source,
                    symbol,
                    iv,
                    parse_time(r["time"]),
                    *(r[k] for k in ("open", "high", "low", "close", "volume")),
                    "legacy-contract",
                    "legacy-contract",
                )
                try:
                    bars.append(bar.validate())
                except DataError as exc:
                    invalid = {
                        "symbol": symbol,
                        "interval": iv,
                        "rejected_invalid": bar.record(),
                        "reason": str(exc),
                    }
                    break
            if invalid:
                report["checks"].append(invalid)
                continue
            app.state.repo.put(bars)
            if start and iv == "1m":
                app.state.archive.publish(app.state.repo.read("vn", symbol, iv), prune=True)
            if symbol == "FPT" and iv == "1D":
                older = app.state.repo.read("vn", symbol, iv, end=cutoff(3) - 1)
                if older:
                    app.state.archive.publish(older, prune=True)
            actual = local.get("/tickers", params=params)
            assert actual.status_code == 200, actual.text
            actual = actual.json()[symbol]
            assert len(actual) == len(raw)
            for old, new in zip(raw, actual, strict=True):
                assert old["time"] == new["time"]
                for key in ("open", "high", "low", "close", "volume"):
                    assert old[key] == new[key], (symbol, iv, key)
            report["checks"].append(
                {
                    "symbol": symbol,
                    "interval": iv,
                    "start": start,
                    "rows": len(raw),
                    "identical_ohlcv": True,
                }
            )
        # Compare legacy SMA/EMA and aggregated outputs on identical input candles.
        for iv in ("1D", "1W", "2W", "1M"):
            for ema in (False, True):
                params = {
                    "symbol": "FPT",
                    "interval": iv,
                    "limit": 5,
                    "ema": str(ema).lower(),
                    "cache": "false",
                }
                old = legacy.get("/tickers", params=params)
                old.raise_for_status()
                expected = old.json()["FPT"]
                actual = local.get("/tickers", params=params).json()["FPT"]
                deltas = {}
                for a, b in zip(expected, actual, strict=True):
                    assert a["time"] == b["time"]
                    for key in ("open", "high", "low", "close", "volume"):
                        assert a[key] == b[key], (iv, ema, key)
                    for key in a.keys() & b.keys() - {"time", "symbol"}:
                        if isinstance(a[key], (int, float)):
                            deltas[key] = max(deltas.get(key, 0), abs(a[key] - b[key]))
                report["checks"].append(
                    {
                        "interval": iv,
                        "ema": ema,
                        "max_numeric_deltas": deltas,
                        "relative_tolerance_1e_3": all(
                            math.isclose(a[k], b[k], rel_tol=1e-3, abs_tol=1e-6)
                            for a, b in zip(expected, actual, strict=True)
                            for k in a.keys() & b.keys() - {"time", "symbol"}
                            if isinstance(a[k], (int, float))
                        ),
                    }
                )
    output = settings.database.parent / "report.json"
    output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"Report: {output}")


if __name__ == "__main__":
    main()
