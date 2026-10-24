import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.cli import _export_public_candles
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle
from aipriceaction_api.history import History
from aipriceaction_api.history_worker import HistoryWorker, _partition
from aipriceaction_api.providers import Page
from aipriceaction_api.storage import Repository


class OnePageProvider:
    def __init__(self, rows):
        self.rows = rows
        self.provider_attempt_counts = {}
        self.provider_route_attempt_counts = {}

    async def page(self, source, symbol, interval, *, before, count, provider, **kwargs):
        return Page(self.rows, provider, cursor=self.rows[0].time)


@pytest.fixture
def history_system(tmp_path):
    settings = replace(
        Settings(),
        database=tmp_path / "db.sqlite3",
        archive_backend="filesystem",
        object_dir=tmp_path / "objects",
        cache_dir=tmp_path / "cache",
        public_export_enabled=False,
    )
    repo = Repository(settings.database)
    repo.initialize()
    archive = Archive(repo, settings)
    return repo, archive, settings


def test_history_worker_checkpoints_parquet_without_storing_cold_candles(history_system):
    repo, archive, settings = history_system
    start = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp())
    end = start + 2 * 86400
    revision = "history-revision"
    with repo.connect() as con:
        con.execute(
            "INSERT INTO series(source,symbol,interval,provider,revision,status) "
            "VALUES ('crypto','BTCUSDT','1D','binance',?,'ready')",
            (revision,),
        )
    job = repo.configure_history_job(
        "crypto", "BTCUSDT", "1D", end, start - 86400, "binance", revision
    )
    rows = [
        Candle(
            "crypto",
            "BTCUSDT",
            "1D",
            stamp,
            100,
            101,
            99,
            100,
            10,
            "binance",
            revision,
        )
        for stamp in (start, start + 86400)
    ]
    providers = OnePageProvider(rows)
    worker = HistoryWorker(repo, settings, providers=providers, archive=archive)

    asyncio.run(worker._fetch_page(job, start, end, end))

    saved = repo.history_job("crypto", "BTCUSDT", "1D")
    assert saved["batch_cursor"] == start
    assert len(repo.history_pending_objects("crypto", "BTCUSDT", "1D", start)) == 1
    assert repo.read("crypto", "BTCUSDT", "1D") == []
    with repo.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM staging").fetchone()[0] == 0

    asyncio.run(worker._publish_parquet_batch(saved))
    saved = repo.history_job("crypto", "BTCUSDT", "1D")
    assert saved["batch_status"] == "csv"
    assert History(repo, archive, settings).read(
        "crypto", "BTCUSDT", "1D", start=start, end=end - 1, revision=revision
    ) == rows
    assert repo.read("crypto", "BTCUSDT", "1D") == []

    asyncio.run(worker._publish_next_csv(saved))
    assert repo.history_job("crypto", "BTCUSDT", "1D")["cursor"] == start


def test_history_partition_walks_back_from_the_exclusive_cutoff():
    cutoff = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp())
    start, end, label = _partition(cutoff, "1D")
    assert label == "2023"
    assert end == cutoff
    assert start < end


def test_vci_minute_fallback_is_a_supported_active_history_provider(history_system):
    repo, archive, settings = history_system
    settings = replace(
        settings,
        vn_providers=("vps", "dnse"),
        vci_history_fallback=True,
    )
    worker = HistoryWorker(repo, settings, providers=OnePageProvider([]), archive=archive)

    assert worker._expected_provider("vn", "FPT", "1m", "vci")


def test_history_gate_resume_clears_stale_retry_delay(history_system):
    repo, _archive, _settings = history_system
    repo.configure_history_job("crypto", "BTCUSDT", "1D", 100, 10, "binance", "rev")
    repo.fail_history_job("crypto", "BTCUSDT", "1D", "temporary", 9999999999)
    repo.set_history_job_status("crypto", "BTCUSDT", "1D", "waiting_series")
    repo.set_history_job_status("crypto", "BTCUSDT", "1D", "pending")
    job = repo.history_job("crypto", "BTCUSDT", "1D")
    assert job["status"] == "pending"
    assert job["retry_at"] == 0
    assert job["attempts"] == 0


@pytest.mark.asyncio
async def test_history_publish_failure_is_checkpointed_for_retry(history_system, monkeypatch):
    repo, archive, settings = history_system
    with repo.connect() as con:
        con.execute(
            "INSERT INTO series(source,symbol,interval,provider,revision,status) "
            "VALUES ('crypto','BTCUSDT','1D','binance','rev','ready')"
        )
    repo.configure_history_job("crypto", "BTCUSDT", "1D", 100, 10, "binance", "rev")
    repo.checkpoint_history_page(
        repo.history_job("crypto", "BTCUSDT", "1D"),
        batch_start=10,
        batch_end=100,
        batch_cursor=10,
        objects=[],
        row_count=0,
    )
    repo.mark_history_batch_csv("crypto", "BTCUSDT", "1D", [{"kind": "day", "value": "2020-01-01"}])
    worker = HistoryWorker(repo, settings, providers=OnePageProvider([]), archive=archive)
    monkeypatch.setattr(worker, "_ensure_jobs", lambda: None)

    async def fail(_job):
        raise OSError("temporary object store failure")

    monkeypatch.setattr(worker, "_publish_next_csv", fail)
    assert await worker.step()
    job = repo.history_job("crypto", "BTCUSDT", "1D")
    assert job["status"] == "retry"
    assert job["batch_status"] == "csv"
    assert job["csv_cursor"] == 0


@pytest.mark.asyncio
async def test_empty_cold_partition_is_recorded_and_cursor_continues(history_system):
    repo, archive, settings = history_system
    job = repo.configure_history_job("crypto", "BTCUSDT", "1D", 100, 0, "binance", "rev")
    repo.checkpoint_history_page(
        job,
        batch_start=10,
        batch_end=100,
        batch_cursor=10,
        objects=[],
        row_count=0,
    )
    worker = HistoryWorker(repo, settings, providers=OnePageProvider([]), archive=archive)

    await worker._publish_parquet_batch(repo.history_job("crypto", "BTCUSDT", "1D"))

    saved = repo.history_job("crypto", "BTCUSDT", "1D")
    assert saved["cursor"] == 10
    assert saved["batch_start"] is None
    with repo.connect() as con:
        assert con.execute(
            "SELECT 1 FROM quality WHERE source='crypto' AND symbol='BTCUSDT' "
            "AND interval='1D' AND kind='history_coverage_gap' AND resolved=0"
        ).fetchone()


@pytest.mark.asyncio
async def test_history_overlap_accepts_verified_snapshot_provider(history_system, monkeypatch):
    repo, archive, settings = history_system
    revision = "active-revision"
    timestamp = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp())
    with repo.connect() as con:
        con.execute(
            "INSERT INTO series(source,symbol,interval,provider,revision,status) "
            "VALUES ('crypto','BTCUSDT','1D','binance',?,'ready')",
            (revision,),
        )
        con.execute(
            "INSERT INTO candles(source,symbol,interval,time,open,high,low,close,volume,provider,revision,updated_at) "
            "VALUES ('crypto','BTCUSDT','1D',?,100,101,99,100,10,'legacy',?,1)",
            (timestamp, revision),
        )
    monkeypatch.setattr(repo, "snapshot_provider", lambda _state: "legacy")
    worker = HistoryWorker(repo, settings, providers=OnePageProvider([]), archive=archive)
    incoming = Candle(
        "crypto", "BTCUSDT", "1D", timestamp, 100, 101, 99, 100, 10, "binance", revision
    )
    job = {
        "source": "crypto",
        "symbol": "BTCUSDT",
        "interval": "1D",
        "provider": "binance",
        "revision": revision,
    }

    assert await worker._prepare_rows(job, [incoming]) == ([], 0)


def test_public_csv_cli_export_builds_the_snapshot_inside_its_lock(history_system):
    repo, archive, settings = history_system
    day = datetime(2024, 1, 1, tzinfo=UTC).date()
    stamp = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp())
    repo.put(
        [Candle("crypto", "BTCUSDT", "1D", stamp, 100, 101, 99, 100, 10, "binance", "rev")]
    )

    result = _export_public_candles(
        archive,
        settings,
        repo,
        "crypto",
        "BTCUSDT",
        "1D",
        day=day,
        execute=False,
    )

    assert result["dry_run"] is True
    assert result["key"] == "ohlcv/crypto/BTCUSDT/1D/BTCUSDT-1D-2024-01-01.csv"
    assert result["rows"] == 1
