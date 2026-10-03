import json
import time
from dataclasses import replace

import pytest

from aipriceaction_api.cli import parser
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, parse_time
from aipriceaction_api.providers import Page
from aipriceaction_api.storage import Repository
from aipriceaction_api.workers import Worker


class Provider:
    def __init__(self, rows, failures=()):
        self.rows, self.failures, self.calls = rows, failures, []
        self.closed = False

    async def page(self, source, symbol, interval, *args, **kwargs):
        self.calls.append(symbol)
        if symbol in self.failures:
            raise DataError("Upstream unavailable")
        return Page(self.rows[symbol], self.rows[symbol][0].provider)

    async def close(self):
        self.closed = True


@pytest.fixture
def system(tmp_path):
    watchlist = tmp_path / "watchlist.json"
    watchlist.write_text(json.dumps({"crypto": ["BTCUSDT", "ETHUSDT"]}))
    settings = replace(Settings(), database=tmp_path / "db", watchlist=watchlist)
    repo = Repository(settings.database)
    repo.initialize()
    start = parse_time("2026-01-05")
    rows = {
        symbol: [
            Candle("crypto", symbol, "1m", start + i * 60, 100, 101, 99, 100, 10, "binance")
            for i in range(2)
        ]
        for symbol in ("BTCUSDT", "ETHUSDT")
    }
    repo.put([r for values in rows.values() for r in values])
    return repo, settings, rows


@pytest.mark.asyncio
async def test_refresh_ignores_cooldown_and_leaves_historical_jobs_alone(system):
    repo, settings, rows = system
    provider = Provider(rows)
    job = repo.queue("crypto", "ETHUSDT", "1h", "bootstrap", 0, "binance")
    repo.schedule("crypto", "BTCUSDT", "1m", int(time.time()) + 3600)
    result = await Worker(repo, settings, providers=provider).refresh("crypto", ["BTCUSDT"], "1m")
    assert len(result) == 1 and result[0]["rows"] == 2
    assert result[0]["outcome"] == "succeeded" and result[0]["completed_rows"] == 2
    assert provider.calls == ["BTCUSDT"] and provider.closed
    with repo.connect() as con:
        assert con.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0] == "pending"
        assert con.execute("SELECT count(*) FROM staging").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_refresh_does_not_claim_new_verification_when_another_worker_holds_lease(system):
    repo, settings, rows = system
    provider = Provider(rows)
    assert repo.live_claim("crypto", "BTCUSDT", "1m", "other")
    result = await Worker(repo, settings, providers=provider).refresh("crypto", ["BTCUSDT"], "1m")
    assert result == [
        dict(source="crypto", symbol="BTCUSDT", interval="1m", rows=0, outcome="skipped")
    ]
    assert provider.calls == [] and provider.closed
    with repo.connect() as con:
        assert not con.execute("SELECT * FROM source_checks").fetchall()


@pytest.mark.asyncio
async def test_refresh_keeps_failures_independent_and_preserves_published_rows(system):
    repo, settings, rows = system
    saved = repo.read("crypto", "BTCUSDT", "1m")
    provider = Provider(rows, failures={"BTCUSDT"})
    result = await Worker(repo, settings, providers=provider).refresh("crypto", None, "1m")
    assert [r["outcome"] for r in result] == ["failed", "succeeded"]
    assert result[0]["error"] == "Upstream unavailable"
    assert repo.read("crypto", "BTCUSDT", "1m") == saved
    assert provider.closed


@pytest.mark.asyncio
async def test_refresh_respects_frozen_minute_handoff_gate(system):
    repo, settings, rows = system
    settings.watchlist.write_text('{"vn":["FPT"]}')
    saved = [replace(r, source="vn", symbol="FPT", provider="legacy-api") for r in rows["BTCUSDT"]]
    repo.put(saved)
    before = repo.read("vn", "FPT", "1m")
    provider = Provider({"FPT": saved})
    result = await Worker(repo, settings, providers=provider).refresh("vn", ["FPT"], "1m")
    assert result[0]["outcome"] == "handoff_required" and result[0]["rows"] == 0
    assert provider.calls == [] and provider.closed
    assert repo.read("vn", "FPT", "1m") == before


@pytest.mark.asyncio
async def test_refresh_does_not_initialize_a_missing_series_or_resume_an_active_repair(system):
    repo, settings, rows = system
    repo.queue("crypto", "ETHUSDT", "1m", "repair", 0, "binance")
    provider = Provider(rows)
    result = await Worker(repo, settings, providers=provider).refresh("crypto", ["ETHUSDT"], "1m")
    assert result[0]["outcome"] == "not_ready" and provider.calls == []
    provider = Provider(rows)
    result = await Worker(repo, settings, providers=provider).refresh("crypto", None, "1D")
    assert len(result) == 2 and all(r["outcome"] == "not_ready" for r in result)
    with repo.connect() as con:
        assert con.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_refresh_closes_provider_when_filters_match_nothing(system):
    repo, settings, rows = system
    provider = Provider(rows)
    with pytest.raises(DataError, match="refresh filters"):
        await Worker(repo, settings, providers=provider).refresh("vn", None, "1m")
    assert provider.closed and provider.calls == []


def test_refresh_cli_requires_explicit_source_and_native_interval():
    args = parser().parse_args(["refresh", "--source", "vn", "--interval", "1m"])
    assert args.command == "refresh" and args.symbol is None
    for arguments in (["refresh"], ["refresh", "--source", "vn", "--interval", "15m"]):
        with pytest.raises(SystemExit):
            parser().parse_args(arguments)


@pytest.mark.asyncio
async def test_refresh_rejects_partial_watchlist_matches_before_fetching(system):
    repo, settings, rows = system
    provider = Provider(rows)
    with pytest.raises(DataError, match="outside the refresh watchlist"):
        await Worker(repo, settings, providers=provider).refresh(
            "crypto", ["BTCUSDT", "TYPO"], "1m"
        )
    assert provider.calls == [] and provider.closed
