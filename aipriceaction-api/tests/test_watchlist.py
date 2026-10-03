from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from threading import Event

import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, parse_time
from aipriceaction_api.storage import Repository
from aipriceaction_api.workers import Worker


def test_starting_worker_never_exposes_partially_disabled_watchlist(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "db")
    repo.initialize()
    for symbol in ["FPT", "VCB", "OLD"]:
        repo.register("vn", symbol, name=symbol + " company", enabled=True)
    repo.schedule("vn", "FPT", "1m", 12345)
    original = repo.tickers(enabled=True)
    repo.put([Candle("vn", "OLD", "1D", parse_time("2024-01-01"), 100, 101, 99, 100, 1000)])
    watchlist = tmp_path / "watchlist.json"
    watchlist.write_text('{"vn":["FPT","VCB","TCB"]}')
    worker = Worker(repo, replace(Settings(), watchlist=watchlist))

    entered, release = Event(), Event()
    original_connect = repo.connect

    def trace(statement):
        if statement.startswith("INSERT INTO tickers") and not entered.is_set():
            entered.set()
            assert release.wait(5)

    @contextmanager
    def traced_connect():
        with original_connect() as con:
            con.set_trace_callback(trace)
            yield con

    monkeypatch.setattr(repo, "connect", traced_connect)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(worker.load_watchlist)
        try:
            assert entered.wait(5)
            # The writer is between disabling and enabling rows. A separate
            # WAL reader must still see the complete previously committed set.
            assert repo.tickers(enabled=True) == original
        finally:
            release.set()
        pending.result(timeout=5)
    assert {r["symbol"] for r in repo.tickers(enabled=True)} == {"FPT", "VCB", "TCB"}
    row = next(r for r in repo.tickers() if r["symbol"] == "FPT")
    assert row["name"] == "FPT company" and row["next_1m"] == 12345
    assert len(repo.read("vn", "OLD", "1D")) == 1


@pytest.mark.asyncio
async def test_running_worker_skips_removed_ticker_and_its_pending_job(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "db")
    repo.initialize()
    repo.register("vn", "FPT", enabled=True)
    repo.queue("vn", "FPT", "1D", "bootstrap", parse_time("2024-01-01"))
    worker = Worker(repo, Settings())
    worker.configuration = [{"source": "vn", "symbol": "FPT", "intervals": ["1D"]}]
    repo.activate_watchlist([])  # Another worker loaded a real removal.

    async def no_fetch(*args):
        pytest.fail("Removed tickers must not be fetched or reclaimed")

    monkeypatch.setattr(worker, "sync", no_fetch)
    monkeypatch.setattr(worker, "repair_page", no_fetch)
    assert await worker.cycle() == 0
    assert repo.status()["jobs"][0]["status"] == "pending"
