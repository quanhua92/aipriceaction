import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, cutoff
from aipriceaction_api.history import History
from aipriceaction_api.maintenance import DailyArchive
from aipriceaction_api.storage import Repository
from aipriceaction_api.workers import Worker


@pytest.fixture
def system(tmp_path):
    settings = replace(
        Settings(),
        database=tmp_path / "db",
        archive_backend="filesystem",
        object_dir=tmp_path / "objects",
        cache_dir=tmp_path / "cache",
    )
    repo = Repository(settings.database)
    repo.initialize()
    return repo, Archive(repo, settings), settings


def bar(symbol, stamp, iv="1m"):
    return Candle("crypto", symbol, iv, stamp, 100, 101, 99, 100, 1000, "binance", "native")


def test_daily_archive_scopes_rollover_and_restarts_without_duplicate_objects(system):
    repo, archive, settings = system
    now = datetime(2026, 10, 3, tzinfo=UTC)
    floor = cutoff(1, now)
    rows = [bar("BTCUSDT", floor + offset) for offset in (-86400 + 120, 120, 86400 + 120)]
    others = [bar("ETHUSDT", floor - 86400 + 120), bar("BTCUSDT", floor - 86400, "1D")]
    repo.put(rows + others)
    original = repo.read("crypto", "BTCUSDT", "1m")
    maintenance = DailyArchive(archive, "crypto", ["BTCUSDT"], "1m")
    assert maintenance.tick(now) == {"day": "2026-10-03", "complete": True, "objects": 1, "rows": 1}
    assert maintenance.tick(now + timedelta(hours=1)) is None
    restarted = DailyArchive(archive, "crypto", ["BTCUSDT"], "1m")
    assert restarted.tick(now + timedelta(hours=2))["objects"] == 0
    assert len(repo.archives()) == 1
    assert maintenance.tick(now + timedelta(days=1))["rows"] == 1
    assert len(repo.archives()) == 2
    assert repo.read("crypto", "BTCUSDT", "1m") == original[2:]
    assert History(repo, archive, settings).read("crypto", "BTCUSDT", "1m") == original
    for row in others:
        assert len(repo.read(row.source, row.symbol, row.interval)) == 1


def test_partial_archive_failure_retries_remaining_rows_after_cooldown(system, monkeypatch):
    repo, archive, settings = system
    now = datetime(2026, 10, 3, tzinfo=UTC)
    floor = cutoff(1, now)
    repo.put([bar(symbol, floor - 86400 + 120) for symbol in ("BTCUSDT", "ETHUSDT")])
    original = {symbol: repo.read("crypto", symbol, "1m") for symbol in ("BTCUSDT", "ETHUSDT")}
    put = archive.store.put
    parquet_writes = 0

    def fail_second_object(key, path):
        nonlocal parquet_writes
        if key.endswith(".parquet"):
            parquet_writes += 1
            if parquet_writes == 2:
                raise OSError("Object store temporarily unavailable")
        put(key, path)

    monkeypatch.setattr(archive.store, "put", fail_second_object)
    maintenance = DailyArchive(archive, "crypto", interval="1m")
    assert maintenance.tick(now) == {
        "day": "2026-10-03",
        "complete": False,
        "objects": 1,
        "rows": 1,
    }
    assert maintenance.completed_day is None
    assert maintenance.tick(now + timedelta(seconds=30)) is None
    assert parquet_writes == 2
    assert repo.read("crypto", "ETHUSDT", "1m") == original["ETHUSDT"]
    assert maintenance.tick(now + timedelta(seconds=61))["complete"]
    for symbol, rows in original.items():
        assert repo.read("crypto", symbol, "1m") == []
        assert History(repo, archive, settings).read("crypto", symbol, "1m") == rows


def test_daily_archive_preserves_concurrent_corrections(system, monkeypatch):
    repo, archive, _ = system
    now = datetime(2026, 10, 3, tzinfo=UTC)
    repo.put([bar("BTCUSDT", cutoff(1, now) - 86400 + 120)])
    snapshot = repo.read("crypto", "BTCUSDT", "1m")
    prepare = archive.prepare
    corrected = False

    def corrected_after_export(rows):
        nonlocal corrected
        obj = prepare(rows)
        if not corrected:
            repo.put([replace(snapshot[0], close=100.5)])
            corrected = True
        return obj

    monkeypatch.setattr(archive, "prepare", corrected_after_export)
    maintenance = DailyArchive(archive, "crypto")
    assert not maintenance.tick(now)["complete"]
    retained = repo.read("crypto", "BTCUSDT", "1m")
    assert retained[0].close == 100.5 and retained[0].updated_at != snapshot[0].updated_at
    assert archive.read(repo.archives()[0]) == snapshot
    assert maintenance.tick(now + timedelta(seconds=61))["complete"]
    assert repo.read("crypto", "BTCUSDT", "1m") == []
    assert len(repo.archives()) == 2


@pytest.mark.asyncio
async def test_worker_continues_ingestion_when_daily_archive_is_unavailable(
    system, monkeypatch, tmp_path
):
    repo, archive, settings = system
    watchlist = tmp_path / "watchlist.json"
    watchlist.write_text(json.dumps({"crypto": [{"symbol": "BTCUSDT", "intervals": ["1m"]}]}))
    settings = replace(settings, watchlist=watchlist)
    repo.put([bar("BTCUSDT", cutoff(1) - 86400 + 120)])
    archive.store.put = lambda key, path: (_ for _ in ()).throw(OSError("Unavailable"))
    closed, cycles = False, 0

    class Providers:
        async def close(self):
            nonlocal closed
            closed = True

    worker = Worker(repo, settings, Providers(), archive)

    async def cycle():
        nonlocal cycles
        cycles += 1
        return 0

    monkeypatch.setattr(worker, "cycle", cycle)
    await worker.run(cycles=2, source="crypto", interval="1m", archive_daily=True)
    assert cycles == 2 and closed
    assert len(repo.read("crypto", "BTCUSDT", "1m")) == 1
