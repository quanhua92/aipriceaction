import asyncio
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, parse_time
from aipriceaction_api.storage import Repository
from scripts import compare_vn_feeds, validate_ohlcv


@pytest.mark.parametrize("unanimous", [False, True])
def test_native_pipeline_reports_provider_and_served_data_disagreements(
    tmp_path, monkeypatch, unanimous
):
    watchlist = tmp_path / "watchlist.json"
    watchlist.write_text(json.dumps({"vn": [{"symbol": "FPT", "intervals": ["1m"]}]}))
    settings = replace(Settings(), database=tmp_path / "live.sqlite3", watchlist=watchlist)
    repo = Repository(settings.database)
    repo.initialize()
    stamp = parse_time("2020-01-02T03:00:00Z")
    repo.put([Candle("vn", "FPT", "1m", stamp, 10, 11, 9, 10, 100, "vps", "test")])
    before = settings.database.read_bytes()
    requested = []

    class Providers:
        def __init__(self, config, transport):
            assert config.vci_history_fallback
            self.transport = transport

        async def page(self, source, symbol, interval, before, count, start, provider):
            requested.append(provider)
            close = 10.5 if provider == "vci" or unanimous else 10
            return SimpleNamespace(
                rows=[
                    Candle(source, symbol, interval, stamp, 10, 11, 9, close, 100, provider, "test")
                ]
            )

        async def close(self):
            await self.transport.aclose()

    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(compare_vn_feeds, "Providers", Providers)
    output = tmp_path / "validation"
    result = asyncio.run(
        validate_ohlcv.run(
            SimpleNamespace(
                output=output,
                symbol=None,
                interval=["1m"],
                daily_start="2020-01-02",
                intraday_start="2020-01-02",
                end_date="2020-01-02",
            )
        )
    )
    assert set(requested) == {"vps", "vndirect", "dnse", "vci"}
    assert result["perfect_data_proven"] is False
    assert result["canonical_publication"] is False
    assert any(e["kind"] == "provider_values" for e in result["exceptions"]) is not unanimous
    assert (
        any(e["kind"] == "unanimous_provider_conflicts" for e in result["exceptions"]) is unanimous
    )
    assert any(
        e["kind"] == "sqlite_provider_difference" and e["provider"] == "vci"
        for e in result["exceptions"]
    )
    assert settings.database.read_bytes() == before
    assert not list(output.rglob("*.sqlite3*"))


def test_empty_and_failed_providers_do_not_certify_data():
    report = {
        "errors": [{"symbol": "FPT", "interval": "1m", "feed": "vci", "error": "Unavailable"}],
        "comparisons": [{"symbol": "FPT", "interval": "1m", "counts": {}}],
    }
    local = [{"symbol": "FPT", "interval": "1m", "sqlite_rows": 0, "providers": {}}]
    issues = validate_ohlcv.exceptions(report, local)
    assert {r["kind"] for r in issues} == {
        "provider_error",
        "no_provider_observations",
        "no_local_observations",
    }


def test_zero_price_comparison_does_not_crash():
    row = {"time": 0, "open": 0, "high": 0, "low": 0, "close": 0, "volume": 0}
    report = compare_vn_feeds.compare({p: [row] for p in compare_vn_feeds.NATIVE_FEEDS})
    assert report["counts"] == {"four_feed_agreement": 1}


@pytest.mark.parametrize("failed", [False, True])
def test_daily_pipeline_uses_legacy_reference_and_checks_the_correct_feed_set(
    tmp_path, monkeypatch, failed
):
    watchlist = tmp_path / "watchlist.json"
    watchlist.write_text(json.dumps({"vn": [{"symbol": "FPT", "intervals": ["1D"]}]}))
    settings = replace(Settings(), database=tmp_path / "live.sqlite3", watchlist=watchlist)
    repo = Repository(settings.database)
    repo.initialize()
    stamp = parse_time("2020-01-02")
    repo.put([Candle("vn", "FPT", "1D", stamp, 10, 11, 9, 10, 100)])
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))

    async def reference_comparison(args):
        assert args.native_providers is False
        args.output.mkdir()
        row = dict(time=stamp, open=10, high=11, low=9, close=10.5, volume=100)
        for feed in compare_vn_feeds.FEEDS:
            folder = args.output / feed
            folder.mkdir()
            if failed and feed == "vps":
                raw = json.dumps(
                    {
                        "symbol": "FPT",
                        "s": "ok",
                        "t": [stamp],
                        "o": [12],
                        "h": [11],
                        "l": [9],
                        "c": [10.5],
                        "v": [100],
                    }
                ).encode()
                capture = folder / "captured.json"
                capture.write_bytes(raw)
                record = {
                    "error": "Invalid OHLC range",
                    "captures": [
                        {
                            "path": str(capture),
                            "sha256": hashlib.sha256(raw).hexdigest(),
                            "status": 200,
                        }
                    ],
                }
            else:
                record = {"rows": [row]}
            (folder / "FPT-1D.json").write_text(json.dumps(record))
        comparison = compare_vn_feeds.compare(
            {feed: [row] for feed in compare_vn_feeds.FEEDS if not (failed and feed == "vps")}
        )
        result = {
            "feeds": compare_vn_feeds.FEEDS,
            "symbols": ["FPT"],
            "intervals": ["1D"],
            "daily_start": args.daily_start,
            "intraday_start": args.intraday_start,
            "end_date": args.end_date,
            "errors": (
                [{"symbol": "FPT", "interval": "1D", "feed": "vps", "error": "Invalid OHLC range"}]
                if failed
                else []
            ),
            "requests": 4,
            "comparisons": [{"symbol": "FPT", "interval": "1D", **comparison}],
        }
        (args.output / "report.json").write_text(json.dumps(result))
        return result

    monkeypatch.setattr(validate_ohlcv, "compare_feeds", reference_comparison)
    result = asyncio.run(
        validate_ohlcv.run(
            SimpleNamespace(
                output=tmp_path / "review",
                symbol=None,
                interval=["1D"],
                daily_start="2020-01-02",
                intraday_start="2020-01-02",
                end_date="2020-01-02",
            )
        )
    )
    assert result["comparison_mode"] == "legacy"
    assert result["providers"] == compare_vn_feeds.FEEDS
    assert (
        any(e["kind"] == "unanimous_provider_conflicts" for e in result["exceptions"]) is not failed
    )
    if failed:
        assert result["diagnostic_rejected_rows"] == 1
        assert any(e["kind"] == "provider_error" for e in result["exceptions"])
        replay = tmp_path / "review/valid-subsets/FPT-vps.json"
        assert json.loads(replay.read_text())["rows"] == []
    assert result["perfect_data_proven"] is False
