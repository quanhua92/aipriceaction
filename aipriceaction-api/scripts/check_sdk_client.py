"""Rehearse the installed Python SDK against a local replacement (read-only)."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd
import requests
from aipriceaction import AIPriceAction


def check(api_url, symbol, report, source=None, intervals=("1D", "1m", "15m")):
    if urlsplit(api_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Use a loopback replacement API for this rehearsal")
    client = AIPriceAction(
        cache_dir=str(report.parent / "sdk-rehearsal-cache"),
        live_url=api_url,
        utc_offset=0,
    )
    results = []
    fields = ["time", "open", "high", "low", "close", "volume"] + [
        f"ma{period}" for period in (10, 20, 50, 100, 200)
    ]
    for interval in intervals:
        for ema in (False, True):
            frame = client.get_ohlcv(symbol, interval=interval, limit=20, ema=ema, source=source)
            response = requests.get(
                api_url.rstrip("/") + "/tickers",
                params={
                    "symbol": symbol,
                    "interval": interval,
                    "limit": 20,
                    "ma": "true",
                    "ema": str(ema).lower(),
                    **({"mode": "yahoo" if source == "sjc" else source} if source else {}),
                },
                timeout=30,
            )
            response.raise_for_status()
            rows = response.json().get(symbol, [])
            assert len(frame) == len(rows) == 20, (interval, ema, len(frame), len(rows))
            differences = {
                field: sum(
                    not (pd.isna(a) and b.get(field) is None) and a != b.get(field)
                    for a, b in zip(frame[field].tolist(), rows, strict=True)
                )
                for field in fields
            }
            result = {
                "symbol": symbol,
                "interval": interval,
                "ema": ema,
                "rows": len(frame),
                "source": frame.attrs.get("data_source"),
                "differences": differences,
            }
            assert not any(differences.values()), result
            assert result["source"] == "api", result
            results.append(result)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:3001")
    parser.add_argument("--symbol", default="FPT")
    parser.add_argument("--source", choices=("vn", "crypto", "yahoo", "sjc"))
    parser.add_argument("--interval", action="append", help="Sample intervals; may repeat")
    parser.add_argument("--report", type=Path, default=Path("data/sdk-parity.json"))
    args = parser.parse_args()
    check(
        args.api_url,
        args.symbol.upper(),
        args.report,
        args.source,
        args.interval or ("1D", "1m", "15m"),
    )
