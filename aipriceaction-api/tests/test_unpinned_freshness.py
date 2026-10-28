import time
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError
from aipriceaction_api.history import History
from aipriceaction_api.history_worker import HistoryWorker
from aipriceaction_api.providers import Page, Providers
from aipriceaction_api.storage import Repository
from aipriceaction_api.workers import Worker


@pytest.fixture
def system(tmp_path):
    settings = replace(
        Settings(), database=tmp_path / "db.sqlite3", archive_backend="filesystem",
        object_dir=tmp_path / "objects", cache_dir=tmp_path / "cache",
        public_export_enabled=False, vn_providers=("vps", "vndirect"),
    )
    repo = Repository(settings.database)
    repo.initialize()
    return repo, Archive(repo, settings), settings


def bar(stamp, provider="dnse", price=100, interval="1D"):
    return Candle("vn", "FPT", interval, stamp, price, price + 1, price - 1, price, 10, provider, "r1")


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_first", [False, True])
async def test_live_fallback_appends_without_rewriting_completed_history(system, monkeypatch, failed_first):
    repo, archive, settings = system
    today = int(time.time()) // 86400 * 86400
    repo.put([bar(today - 86400)])
    original = repo.read("vn", "FPT", "1D")[0]
    selected = "vndirect" if failed_first else "vps"
    calls = []

    class Provider:
        async def page(self, source, symbol, interval, *, provider, **kwargs):
            calls.append(provider)
            if failed_first and provider == "vps":
                raise DataError("VPS unavailable")
            return Page([bar(today - 86400, selected, 90), bar(today, selected, 101)], selected)

    worker = Worker(repo, settings, Provider(), archive)
    monkeypatch.setattr(worker, "floor", lambda *args: today - 86400)
    monkeypatch.setattr("aipriceaction_api.workers.completed_vn_sessions", lambda: today)

    async def skip_sentinel(*args):
        pass

    monkeypatch.setattr(worker, "sentinel", skip_sentinel)
    assert await worker.sync({"source": "vn", "symbol": "FPT"}, "1D") == 2
    rows = repo.read("vn", "FPT", "1D")
    assert rows[0] == original
    assert rows[1].time == today and rows[1].provider == selected and rows[1].close == 101
    assert calls == (["vps", "vndirect"] if failed_first else ["vps"])
    assert repo.state("vn", "FPT", "1D")["status"] == "ready"
    assert History(repo, archive, settings).read("vn", "FPT", "1D") == rows
    assert repo.status()["series"][0]["verification_current"]
    with repo.connect() as con:
        assert con.execute("SELECT provider,outcome FROM source_checks").fetchone()[:] == (selected, "succeeded")


def test_old_migration_is_retired_but_its_staged_evidence_remains(system):
    repo, _, _ = system
    today = int(time.time()) // 86400 * 86400
    repo.put([bar(today)])
    job_id = repo.queue_provider_migration(
        "vn", "FPT", "1D", repo.state("vn", "FPT", "1D"), today - 86400, "vps", 1
    )
    job = repo.claim_job("test")
    candidate = replace(bar(today, "vps"), revision=job["revision"])
    repo.stage(job, [candidate], today, "vps")

    assert repo.retire_vn_provider_migrations(("vps", "vndirect")) == 1
    assert repo.state("vn", "FPT", "1D")["status"] == "ready"
    with repo.connect() as con:
        assert con.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == "cancelled"
        assert con.execute("SELECT COUNT(*) FROM staging WHERE job_id=?", (job_id,)).fetchone()[0] == 1
    new_job = repo.queue("vn", "FPT", "1D", "repair", today - 86400, "vps")
    assert new_job != job_id
    with repo.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM staging WHERE job_id=?", (job_id,)).fetchone()[0] == 1
        assert con.execute("SELECT kind FROM jobs WHERE id=?", (job_id,)).fetchone()[0].startswith("retired:repair:")


@pytest.mark.asyncio
async def test_cold_checkpoint_can_resume_with_another_provider(system):
    repo, archive, settings = system
    start = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp())
    end = start + 3 * 86400
    repo.put([bar(end)])
    job = repo.configure_history_job("vn", "FPT", "1D", end, start, "dnse", "r1")
    old_object = archive.prepare([bar(start + 86400)])
    repo.checkpoint_history_page(
        job, batch_start=start, batch_end=end, batch_cursor=start + 86400,
        objects=[old_object], row_count=1,
    )

    class Provider:
        provider_attempt_counts = {}
        provider_route_attempt_counts = {}

        async def page(self, source, symbol, interval, **kwargs):
            assert kwargs["provider"] is None
            return Page([bar(start, "vps")], "vps", cursor=start)

    worker = HistoryWorker(repo, settings, Provider(), archive)
    job = repo.history_job("vn", "FPT", "1D")
    await worker._fetch_page(job, start, end, job["batch_cursor"])
    pending = repo.history_pending_objects("vn", "FPT", "1D", start)
    assert {obj["provider"] for obj in pending} == {"dnse", "vps"}
    await worker._publish_parquet_batch(repo.history_job("vn", "FPT", "1D"))
    cold = History(repo, archive, settings).read("vn", "FPT", "1D", start=start, end=end - 1)
    assert [(r.time, r.provider) for r in cold] == [(start, "vps"), (start + 86400, "dnse")]
    assert [r.time for r in repo.read("vn", "FPT", "1D")] == [end]


@pytest.mark.asyncio
@pytest.mark.parametrize(("interval", "step"), [("1h", 3600), ("1m", 60)])
async def test_yahoo_trailing_quote_does_not_replace_full_candle(system, interval, step):
    _, _, settings = system
    stamp = 1793181600
    candle = {"open": [110.55, 111.08], "high": [111.3, 111.08],
              "low": [110.51, 111.08], "close": [111.08, 111.08], "volume": [1278, 0]}
    payload = {"chart": {"result": [{"timestamp": [stamp, stamp + min(42, step - 1)],
                                      "indicators": {"quote": [candle]}}], "error": None}}
    providers = Providers(settings, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    try:
        page = await providers.yahoo_page("BZ=F", interval, stamp + step, 10)
    finally:
        await providers.close()
    assert len(page.rows) == 1
    assert (page.rows[0].open, page.rows[0].high, page.rows[0].volume) == (110.55, 111.3, 1278)
