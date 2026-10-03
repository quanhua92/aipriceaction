import asyncio
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from aipriceaction_api.app import create_app
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, parse_time
from aipriceaction_api.providers import Page
from aipriceaction_api.storage import Repository
from aipriceaction_api.workers import Worker


@pytest.fixture
def system(tmp_path):
    settings = replace(
        Settings(),
        database=tmp_path / "db",
        object_dir=tmp_path / "objects",
        cache_dir=tmp_path / "cache",
        archive_backend="filesystem",
    )
    repo = Repository(settings.database)
    repo.initialize()
    return repo, settings


def bars():
    first = parse_time("2026-01-05")
    return [
        Candle("crypto", "BTCUSDT", "1m", first + i * 60, 100, 101, 99, 100, 1000, "binance")
        for i in range(2)
    ]


class Provider:
    def __init__(self, rows, error=None):
        self.rows, self.error = rows, error
        self.called = False

    async def page(self, *args, **kwargs):
        self.called = True
        if self.error:
            raise self.error
        return Page(self.rows, self.rows[0].provider)


@pytest.mark.asyncio
async def test_provisional_tail_requires_completed_provider_recheck(system, monkeypatch):
    repo, settings = system
    rows = bars()
    repo.put(rows)
    worker = Worker(repo, settings, providers=Provider(rows))
    clock = [rows[0].time + 90]
    monkeypatch.setattr("aipriceaction_api.workers.time.time", lambda: clock[0])
    assert repo.status()["series"][0]["latest_verification"] == "unverified"
    entry = {"source": "crypto", "symbol": "BTCUSDT"}
    assert await worker.sync(entry, "1m") == 2
    check = repo.status()["series"][0]
    assert check["verification_current"]
    assert check["completed_rows"] == check["provisional_rows"] == 1
    assert check["latest_verification"] == "provisional_at_check"
    clock[0] += 60
    # The clock passing alone does not claim a completed provider recheck.
    assert repo.status()["series"][0]["latest_verification"] == "provisional_at_check"
    assert await worker.sync(entry, "1m") == 2
    check = repo.status()["series"][0]
    assert check["latest_verification"] == "completed_recheck"
    assert check["completed_rows"] == 2 and check["provisional_rows"] == 0
    assert check["completed_start"] == rows[0].time
    assert check["completed_end"] == rows[-1].time


@pytest.mark.asyncio
async def test_failed_update_preserves_success_and_dated_error(system, monkeypatch):
    repo, settings = system
    rows = bars()
    monkeypatch.setattr("aipriceaction_api.workers.time.time", lambda: rows[-1].time + 120)
    repo.put(rows)
    worker = Worker(repo, settings, providers=Provider(rows))
    entry = {"source": "crypto", "symbol": "BTCUSDT"}
    await worker.sync(entry, "1m")
    success = repo.status()["series"][0]
    worker.providers = Provider(rows, DataError("Provider unavailable"))
    assert await worker.sync(entry, "1m") == 0
    failed = repo.status()["series"][0]
    assert failed["outcome"] == "failed" and failed["error"] == "Provider unavailable"
    assert failed["successful_at_ns"] == success["successful_at_ns"]
    assert failed["attempted_at_ns"] > success["attempted_at_ns"]
    assert failed["verification_current"]  # Dated earlier proof; not a fresh success.
    worker.providers = Provider(rows)
    await worker.sync(entry, "1m")
    assert repo.status()["series"][0]["error"] is None
    assert not any(r["kind"] == "provider_failure" for r in repo.findings())


@pytest.mark.asyncio
async def test_timeout_records_failure_and_releases_live_lease(system):
    repo, settings = system
    rows = bars()
    repo.put(rows)

    class Slow:
        async def page(self, *args, **kwargs):
            await asyncio.sleep(1)

    worker = Worker(repo, settings, providers=Slow())
    worker.deadline = 0.01
    assert await worker.sync({"source": "crypto", "symbol": "BTCUSDT"}, "1m") == 0
    check = repo.status()["series"][0]
    assert check["outcome"] == "failed" and "timed out" in check["error"]
    assert check["successful_at_ns"] is None
    assert repo.live_claim("crypto", "BTCUSDT", "1m", "replacement")


def test_verification_atomicity_and_later_import_invalidates_proof(system):
    repo, _ = system
    rows = bars()
    repo.put(rows)
    first = repo.start_source_check("crypto", "BTCUSDT", "1m")
    second = repo.start_source_check("crypto", "BTCUSDT", "1m")
    changed = [replace(r, close=100.5) for r in rows]
    with pytest.raises(DataError, match="superseded"):
        repo.put(
            changed, verification={"attempt_ns": first, "completed_before": rows[-1].time + 60}
        )
    assert repo.read("crypto", "BTCUSDT", "1m")[-1].close == 100
    repo.fail_source_check("crypto", "BTCUSDT", "1m", first, "Late failure")
    assert repo.status()["series"][0]["outcome"] == "running"
    repo.put(rows, verification={"attempt_ns": second, "completed_before": rows[-1].time + 60})
    assert repo.status()["series"][0]["verification_current"]
    repo.put(changed)
    assert not repo.status()["series"][0]["verification_current"]
    assert repo.status()["series"][0]["latest_verification"] == "unverified"


@pytest.mark.asyncio
async def test_provider_revision_change_is_pending_not_success(system):
    repo, settings = system
    rows = bars()
    repo.put(rows)
    incoming = [replace(r, provider="other-provider") for r in rows]
    worker = Worker(repo, settings, providers=Provider(incoming))
    assert await worker.sync({"source": "crypto", "symbol": "BTCUSDT"}, "1m") == 0
    check = repo.status()["series"][0]
    assert check["outcome"] == "repair_queued" and check["successful_at_ns"] is None
    assert repo.read("crypto", "BTCUSDT", "1m")[-1].provider == "binance"


@pytest.mark.asyncio
async def test_unverified_vn_snapshot_does_not_attempt_silent_provider_switch(system):
    repo, settings = system
    rows = [replace(r, source="vn", symbol="GEG", provider="legacy-api") for r in bars()]
    repo.put(rows)
    provider = Provider(rows)
    worker = Worker(repo, settings, providers=provider)
    assert await worker.sync({"source": "vn", "symbol": "GEG"}, "1m") == 0
    assert not provider.called and repo.status()["jobs"] == []
    check = repo.status()["series"][0]
    assert "verified provider handoff" in check["error"]
    assert check["outcome"] == "handoff_required"
    assert check["latest_verification"] == "unverified"


def test_schema_upgrade_and_backup_preserve_checks(system, tmp_path):
    repo, _ = system
    rows = bars()
    repo.put(rows)
    with repo.connect() as con:
        con.execute("DROP TABLE source_checks")
        con.execute("PRAGMA user_version=1")
    repo.initialize()
    assert len(repo.read("crypto", "BTCUSDT", "1m")) == 2
    attempt = repo.start_source_check("crypto", "BTCUSDT", "1m")
    repo.put(rows, verification={"attempt_ns": attempt, "completed_before": rows[-1].time + 60})
    backup = tmp_path / "backup.sqlite3"
    repo.backup(backup)
    restored = Repository(backup)
    restored.initialize()
    assert restored.status()["series"] == repo.status()["series"]
    with restored.connect() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2
        con.execute("PRAGMA user_version=3")
    with pytest.raises(DataError, match="newer"):
        restored.initialize()


def test_health_exposes_snapshot_dates_without_claiming_live_verification(system):
    repo, settings = system
    repo.put(bars())
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")
    assert response.status_code == 200
    check = response.json()["storage"]["series"][0]
    assert check["last_ingest_at"] and check["latest_candle_at"].startswith("2026-01-05")
    assert check["last_provider_success_at"] is None and not check["verification_current"]
    assert check["latest_verification"] == "unverified"
