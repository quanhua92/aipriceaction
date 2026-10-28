import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, ProviderMigrationReviewRequired
from aipriceaction_api.history_worker import HistoryWorker
from aipriceaction_api.providers import Page, Providers
from aipriceaction_api.storage import Repository
from aipriceaction_api.workers import Worker


@pytest.fixture
def repo(tmp_path):
    result = Repository(tmp_path / "db")
    result.initialize()
    return result


def seed(repo, symbol, interval="1D"):
    stamp = int(time.time()) // 86400 * 86400
    repo.register("vn", symbol, enabled=True)
    row = Candle("vn", symbol, interval, stamp, 100, 101, 99, 100, 10, "dnse", "old")
    repo.put([row])
    return repo.read("vn", symbol, interval)[0]


def seed_completed_history(repo, row):
    previous = []
    for days in range(1, 15):
        stamp = row.time - days * 86400
        if datetime.fromtimestamp(stamp, UTC).weekday() < 5:
            previous.append(replace(row, time=stamp))
        if len(previous) == 3:
            break
    repo.put(previous)
    return repo.read("vn", row.symbol, "1D")


@pytest.mark.asyncio
async def test_disabled_explicit_provider_never_reaches_network():
    providers = Providers(replace(Settings(), vn_providers=("vps", "vndirect")))
    calls = []

    async def page(provider, *args):
        calls.append(provider)
        return Page([], provider, no_data=True)

    providers.vn_page = page
    with pytest.raises(DataError, match="dnse is disabled"):
        await providers.page("vn", "FPT", "1D", provider="dnse")
    assert calls == []
    assert (await providers.page("vn", "FPT", "1D", provider="vps")).provider == "vps"
    assert calls == ["vps"]
    await providers.close()


def test_migration_limit_is_atomic_across_competing_workers(repo):
    symbols = [f"S{i}" for i in range(8)]
    rows = {symbol: seed(repo, symbol) for symbol in symbols}

    def queue(symbol):
        return repo.queue_provider_migration(
            "vn", symbol, "1D", repo.state("vn", symbol, "1D"),
            rows[symbol].time - 100, "vps", 2,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        queued = list(pool.map(queue, symbols))
    assert sum(job is not None for job in queued) == 2
    assert repo.provider_migration_counts() == {"active": 2}
    status = repo.provider_migration_status(("vps", "vndirect"))
    assert status["counts"] == {"active": 2}
    assert sum(row["count"] for row in status["pinned_to_disabled_provider"]) == len(symbols)
    assert len(status["migrations"]) == 2
    assert sum(repo.state("vn", symbol, "1D")["status"] == "repairing" for symbol in symbols) == 2
    for symbol, row in rows.items():
        assert repo.read("vn", symbol, "1D") == [row]


def test_in_progress_cold_partition_is_not_discarded(repo):
    row = seed(repo, "FPT")
    repo.configure_history_job("vn", "FPT", "1D", row.time, row.time - 1000, "dnse", "old")
    with repo.connect() as con:
        con.execute(
            "UPDATE history_jobs SET batch_start=?,batch_end=?,batch_cursor=?,batch_status='fetching' WHERE source='vn' AND symbol='FPT' AND interval='1D'",
            (row.time - 500, row.time, row.time - 100),
        )
    before = repo.history_job("vn", "FPT", "1D")
    assert repo.queue_provider_migration(
        "vn", "FPT", "1D", repo.state("vn", "FPT", "1D"), row.time - 100, "vps", 1
    ) is None
    assert repo.history_job("vn", "FPT", "1D") == before
    assert repo.read("vn", "FPT", "1D") == [row]


@pytest.mark.parametrize("blocker", ["pending_archive", "active_rebase"])
def test_migration_waits_for_other_durable_repairs(repo, blocker):
    row = seed(repo, "FPT")
    with repo.connect() as con:
        if blocker == "pending_archive":
            con.execute(
                "INSERT INTO archives(id,source,symbol,interval,start,end,row_count,object_key,checksum,revision,provider,status,created_at) VALUES ('obj','vn','FPT','1D',?,?,1,'object','checksum','old','dnse','pending_repair',?)",
                (row.time, row.time, row.time),
            )
        else:
            con.execute(
                "INSERT INTO adjustment_rebases(source,symbol,status,detected_at,evidence,updated_at) VALUES ('vn','FPT','daily_pending',?,'{}',?)",
                (row.time, row.time),
            )
    assert repo.queue_provider_migration(
        "vn", "FPT", "1D", repo.state("vn", "FPT", "1D"), row.time - 100, "vps", 1
    ) is None
    assert repo.read("vn", "FPT", "1D") == [row]


def test_disabled_jobs_are_paused_but_unpinned_and_other_markets_run(repo):
    repo.queue("vn", "DNSE", "1D", "bootstrap", 100, "dnse")
    unpinned = repo.queue("vn", "NEW", "1D", "bootstrap", 100)
    assert repo.claim_job("one", excluded_vn_providers={"dnse"})["id"] == unpinned
    assert repo.claim_job("two", excluded_vn_providers={"dnse"}) is None


def test_migration_preserves_old_bootstrap_pages(repo):
    row = seed(repo, "FPT")
    bootstrap_id = repo.queue("vn", "FPT", "1D", "bootstrap", row.time - 200, "dnse")
    bootstrap = repo.claim_job("bootstrap")
    saved = replace(row, revision=bootstrap["revision"])
    repo.stage(bootstrap, [saved], saved.time, "dnse")
    job_id = repo.queue_provider_migration(
        "vn", "FPT", "1D", repo.state("vn", "FPT", "1D"), row.time - 100, "vps", 1
    )
    assert job_id
    with repo.connect() as con:
        assert con.execute("SELECT status FROM jobs WHERE id=?", (bootstrap_id,)).fetchone()[0] == "cancelled"
        assert con.execute("SELECT COUNT(*) FROM staging WHERE job_id=?", (bootstrap_id,)).fetchone()[0] == 1
    assert repo.read("vn", "FPT", "1D") == [row]


def test_migration_preserves_in_progress_disabled_repair_pages(repo):
    row = seed(repo, "FPT")
    old_job_id = repo.queue("vn", "FPT", "1D", "repair", row.time - 200, "dnse")
    old_job = repo.claim_job("old-worker")
    repo.stage(old_job, [replace(row, revision=old_job["revision"])], row.time, "dnse")
    job_id = repo.queue_provider_migration(
        "vn", "FPT", "1D", repo.state("vn", "FPT", "1D"), row.time - 100, "vps", 1
    )
    assert job_id and job_id != old_job_id
    with repo.connect() as con:
        old = con.execute("SELECT kind,status FROM jobs WHERE id=?", (old_job_id,)).fetchone()
        assert tuple(old) == (f"paused_provider_repair:{old_job_id}", "cancelled")
        assert con.execute("SELECT COUNT(*) FROM staging WHERE job_id=?", (old_job_id,)).fetchone()[0] == 1
    assert repo.read("vn", "FPT", "1D") == [row]


def test_migration_does_not_interrupt_an_active_repair_lease(repo):
    row = seed(repo, "FPT")
    old_job_id = repo.queue("vn", "FPT", "1D", "repair", row.time - 200, "dnse")
    repo.claim_job("old-worker")
    assert repo.queue_provider_migration(
        "vn", "FPT", "1D", repo.state("vn", "FPT", "1D"), row.time - 100, "vps", 1
    ) is None
    with repo.connect() as con:
        old = con.execute("SELECT kind,status FROM jobs WHERE id=?", (old_job_id,)).fetchone()
        assert tuple(old) == ("repair", "running")


def test_migration_exhaustion_releases_slot_and_preserves_staging(repo):
    row = seed(repo, "FPT")
    other = seed(repo, "VCB")
    job_id = repo.queue_provider_migration(
        "vn", "FPT", "1D", repo.state("vn", "FPT", "1D"), row.time - 100, "vps", 1
    )
    job = repo.claim_job("worker")
    repo.stage(job, [replace(row, provider="vps", revision=job["revision"])], row.time, "vps")
    for attempt in range(10):
        with repo.connect() as con:
            con.execute("UPDATE jobs SET retry_at=0 WHERE id=?", (job_id,))
        job = repo.claim_job("worker")
        assert repo.fail_job(job, "unavailable") is (attempt == 9)
    assert repo.provider_migration_counts() == {"exhausted": 1}
    assert repo.state("vn", "FPT", "1D")["status"] == "ready"
    assert repo.read("vn", "FPT", "1D") == [row]
    with repo.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM staging WHERE job_id=?", (job_id,)).fetchone()[0] == 1
    assert repo.queue_provider_migration(
        "vn", "FPT", "1D", repo.state("vn", "FPT", "1D"), row.time - 100, "vps", 1
    ) is None
    assert repo.queue_provider_migration(
        "vn", "VCB", "1D", repo.state("vn", "VCB", "1D"), other.time - 100, "vps", 1
    )


def test_complete_migration_changes_basis_only_after_coverage_check(repo):
    row = seed(repo, "FPT")
    old = seed_completed_history(repo, row)
    floor = old[0].time - 100
    repo.queue_provider_migration(
        "vn", "FPT", "1D", repo.state("vn", "FPT", "1D"), floor, "vps", 1
    )
    job = repo.claim_job("worker")
    staged = [replace(value, provider="vps", revision=job["revision"]) for value in old]
    repo.stage(job, staged, floor, "vps")
    job = repo.claim_job("worker")
    repo.finish_job(job)
    assert repo.state("vn", "FPT", "1D")["provider"] == "vps"
    assert repo.provider_migration_counts() == {"complete": 1}
    assert repo.read("vn", "FPT", "1D")[0].provider == "vps"


@pytest.mark.parametrize("field", ["price", "volume"])
def test_mismatching_provider_is_parked_without_overwriting_candles(repo, field):
    row = seed(repo, "FPT")
    old = seed_completed_history(repo, row)
    floor = old[0].time - 100
    job_id = repo.queue_provider_migration(
        "vn", "FPT", "1D", repo.state("vn", "FPT", "1D"), floor, "vps", 1
    )
    job = repo.claim_job("worker")
    staged = [replace(value, provider="vps", revision=job["revision"]) for value in old]
    staged[0] = replace(staged[0], volume=20) if field == "volume" else replace(
        staged[0], open=99.5
    )
    repo.stage(job, staged, floor, "vps")
    job = repo.claim_job("worker")
    with pytest.raises(ProviderMigrationReviewRequired, match="OHLCV differs") as error:
        repo.finish_job(job)
    assert repo.review_provider_migration(job, str(error.value))
    assert repo.provider_migration_counts() == {"review": 1}
    assert repo.read("vn", "FPT", "1D") == old
    assert repo.state("vn", "FPT", "1D")["status"] == "ready"
    with repo.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM staging WHERE job_id=?", (job_id,)).fetchone()[0] == len(old)
    assert repo.queue_provider_migration(
        "vn", "FPT", "1D", repo.state("vn", "FPT", "1D"), floor, "vps", 1
    ) is None


@pytest.mark.asyncio
async def test_live_sync_does_not_use_disabled_pinned_provider(repo):
    row = seed(repo, "FPT")

    class NeverFetch:
        async def page(self, *args, **kwargs):
            pytest.fail("Disabled provider must not be contacted")

    worker = Worker(repo, replace(Settings(), vn_providers=("vps",)), providers=NeverFetch())
    assert await worker._sync({"source": "vn", "symbol": "FPT"}, "1D") == 0
    assert repo.read("vn", "FPT", "1D") == [row]


def test_bootstrap_does_not_repeatedly_replace_allowed_vndirect_series(repo):
    stamp = int(time.time()) // 86400 * 86400
    row = Candle("vn", "ECI", "1D", stamp, 100, 101, 99, 100, 10, "vndirect", "old")
    repo.put([row])
    worker = Worker(repo, replace(Settings(), vn_providers=("vps", "vndirect")))
    worker.configuration = [{"source": "vn", "symbol": "ECI", "intervals": ["1D"]}]
    assert worker.bootstrap() == []
    assert worker.bootstrap() == []
    assert repo.state("vn", "ECI", "1D")["status"] == "ready"


def test_scheduler_prefers_daily_and_does_not_exceed_limit(repo, tmp_path):
    rows = {symbol: seed(repo, symbol) for symbol in ("FPT", "VCB", "HPG")}
    settings = replace(
        Settings(), vn_providers=("vps",), vn_provider_migration_concurrency=1,
        archive_backend="filesystem", object_dir=tmp_path / "objects", cache_dir=tmp_path / "cache",
    )
    worker = HistoryWorker(repo, settings)
    worker.configuration = [
        {"source": "vn", "symbol": symbol, "intervals": ["1D"]} for symbol in rows
    ]
    worker._schedule_disabled_provider_migrations()
    assert repo.provider_migration_counts() == {"active": 1}
    assert repo.state("vn", "FPT", "1D")["status"] == "repairing"
    worker._schedule_disabled_provider_migrations()
    assert repo.provider_migration_counts() == {"active": 1}
    asyncio.run(worker.providers.close())


@pytest.mark.asyncio
async def test_unverified_index_intraday_fallback_is_not_ingested(repo, tmp_path):
    row = seed(repo, "VNINDEX", "1m")
    settings = replace(
        Settings(), vn_providers=("vps", "vndirect"), archive_backend="filesystem",
        object_dir=tmp_path / "objects", cache_dir=tmp_path / "cache",
    )

    class NeverFetch:
        async def page(self, *args, **kwargs):
            pytest.fail("Unverified index fallback must not be fetched")

    entry = {"source": "vn", "symbol": "VNINDEX", "intervals": ["1D", "1h", "1m"]}
    worker = Worker(repo, settings, providers=NeverFetch())
    worker.configuration = [entry]
    assert await worker._sync(entry, "1m") == 0
    worker.bootstrap()
    with repo.connect() as con:
        assert not con.execute("SELECT 1 FROM jobs WHERE symbol='VNINDEX' AND interval IN ('1h','1m')").fetchone()
    history = HistoryWorker(repo, settings)
    history.configuration = [entry]
    history._schedule_disabled_provider_migrations()
    assert repo.provider_migration_counts() == {}
    assert repo.read("vn", "VNINDEX", "1m") == [row]
    await history.providers.close()
