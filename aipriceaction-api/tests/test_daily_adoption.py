import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from aipriceaction_api.adoption import checksum
from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.daily_adoption import adopt_daily_snapshot
from aipriceaction_api.domain import Candle, DataError, completed_vn_sessions, parse_time
from aipriceaction_api.history import History
from aipriceaction_api.providers import Page
from aipriceaction_api.storage import Repository
from aipriceaction_api.workers import Worker


@pytest.fixture
def system(tmp_path):
    settings = replace(
        Settings(),
        database=tmp_path / "db",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        archive_backend="filesystem",
    )
    repo = Repository(settings.database)
    repo.initialize()
    days = [completed_vn_sessions() - i * 86400 for i in range(1, 121)]
    days = sorted(t for t in days if datetime.fromtimestamp(t, UTC).weekday() < 5)[-80:]
    repo.put(
        [
            Candle("vn", "FPT", "1D", day, 100, 110, 90, 100, 1000, "legacy-api", "captured")
            for day in days
        ]
    )
    archive = Archive(repo, settings)
    old = replace(repo.read("vn", "FPT", "1D")[0], time=parse_time("2022-01-04"))
    archive.publish([old])
    return repo, archive, History(repo, archive, settings), settings


class Provider:
    settings = SimpleNamespace(vn_providers=("vps", "vndirect", "dnse"))

    def __init__(self, repo, mutate=None):
        self.rows = [
            replace(r, provider="vps", revision="initial")
            for r in repo.read("vn", "FPT", "1D", limit=40)
        ]
        self.mutate = mutate

    async def page(self, source, symbol, interval, **kwargs):
        assert (source, symbol, interval) == ("vn", "FPT", "1D")
        if self.mutate:
            self.mutate()
        return Page(self.rows, "vps")


@pytest.mark.asyncio
async def test_daily_dry_run_preserves_snapshot_and_execute_allows_worker_append(system):
    repo, archive, history, settings = system
    before = repo.read("vn", "FPT", "1D")
    epoch = repo.epoch()
    report = await adopt_daily_snapshot(repo, Provider(repo), "FPT", "vps")
    assert report["dry_run"] and repo.epoch() == epoch and repo.adoptions() == []
    job = repo.queue("vn", "FPT", "1D", "bootstrap", before[0].time)
    await adopt_daily_snapshot(repo, Provider(repo), "FPT", "vps", True)
    assert repo.read("vn", "FPT", "1D") == before
    assert repo.state("vn", "FPT", "1D")["provider"] == "vps"
    with repo.connect() as con:
        assert (
            con.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0] == "cancelled"
        )
    assert repo.mark_archive_repairs("vn", "FPT", "1D") == 0
    provider = Provider(repo)
    provider.rows = provider.rows[-39:] + [
        replace(provider.rows[-1], time=provider.rows[-1].time + 86400)
    ]
    assert (
        await Worker(repo, settings, provider, archive).sync(
            {"source": "vn", "symbol": "FPT"}, "1D"
        )
        == 40
    )
    assert len(repo.read("vn", "FPT", "1D")) == len(before) + 1
    assert len(history.read("vn", "FPT", "1D")) == len(before) + 2
    assert history.query("vn", "FPT", "1D", limit=1)[0]["close"] == 100


@pytest.mark.asyncio
async def test_unverified_daily_snapshot_stays_frozen_without_provider_requests(system):
    repo, archive, _, settings = system
    provider = Provider(repo, mutate=lambda: pytest.fail("No provider should be called"))
    before = repo.read("vn", "FPT", "1D")
    assert (
        await Worker(repo, settings, provider, archive).sync(
            {"source": "vn", "symbol": "FPT"}, "1D"
        )
        == 0
    )
    assert repo.read("vn", "FPT", "1D") == before
    assert repo.state("vn", "FPT", "1D")["provider"] == "legacy-api"
    with repo.connect() as con:
        assert con.execute("SELECT outcome FROM source_checks").fetchone()[0] == "handoff_required"
        assert con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["price", "volume", "short", "missing", "provider", "future"])
async def test_daily_disagreements_or_incomplete_tail_reject_without_changes(system, mutation):
    repo, _, _, _ = system
    provider = Provider(repo)
    if mutation == "price":
        provider.rows[0] = replace(provider.rows[0], close=101)
    elif mutation == "volume":
        provider.rows[0] = replace(provider.rows[0], volume=1001)
    elif mutation == "short":
        provider.rows = provider.rows[:-1]
    elif mutation == "missing":
        provider.rows[0] = replace(provider.rows[0], time=provider.rows[0].time - 86400)
    elif mutation == "provider":
        provider.rows[0] = replace(provider.rows[0], provider="dnse")
    else:
        provider.rows[-1] = replace(provider.rows[-1], time=completed_vn_sessions())
    before = repo.read("vn", "FPT", "1D")
    epoch = repo.epoch()
    with pytest.raises(DataError):
        await adopt_daily_snapshot(repo, provider, "FPT", "vps", True)
    assert repo.read("vn", "FPT", "1D") == before
    assert repo.epoch() == epoch and repo.adoptions() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["candle", "archive", "worker", "job"])
async def test_daily_mutation_or_active_writers_prevent_adoption(system, mutation):
    repo, archive, _, _ = system
    row = repo.read("vn", "FPT", "1D")[0]

    def change():
        if mutation == "candle":
            repo.put([replace(row, close=101)])
        elif mutation == "archive":
            archive.publish([replace(row, time=parse_time("2022-01-05"))])
        elif mutation == "worker":
            assert repo.live_claim("vn", "FPT", "1D", "other")
        else:
            repo.queue("vn", "FPT", "1D", "bootstrap", row.time)
            assert repo.claim_job("other")

    provider = Provider(repo, mutate=change)
    with pytest.raises(DataError):
        await adopt_daily_snapshot(repo, provider, "FPT", "vps", True)
    assert repo.state("vn", "FPT", "1D")["provider"] == "legacy-api" and repo.adoptions() == []


@pytest.mark.asyncio
async def test_daily_certificate_restore_and_tamper_rejection(system, tmp_path):
    repo, archive, _, settings = system
    await adopt_daily_snapshot(repo, Provider(repo), "FPT", "vps", True)
    archive.publish_metadata()
    fresh = Repository(tmp_path / "restored")
    fresh.initialize()
    assert Archive(fresh, settings).restore_index() == 1
    assert fresh.adoptions() == repo.adoptions()
    assert len(History(fresh, Archive(fresh, settings), settings).read("vn", "FPT", "1D")) == 1
    record = repo.adoptions()[0]
    evidence = json.loads(record["evidence"])
    evidence["provider_candles"][0]["close"] = 101
    # Even recalculating the digest must not license a changed price.
    evidence["overlap_checksum"] = checksum([Candle(**r) for r in evidence["provider_candles"]])
    record["evidence"] = json.dumps(evidence)
    with pytest.raises(DataError, match="adoption evidence"):
        fresh.restore_adoptions([record])
    assert fresh.adoptions() == repo.adoptions()


@pytest.mark.asyncio
async def test_daily_adjustment_after_handoff_queues_repair_without_erasing_snapshot(system):
    repo, archive, _, settings = system
    await adopt_daily_snapshot(repo, Provider(repo), "FPT", "vps", True)
    before = repo.read("vn", "FPT", "1D")
    provider = Provider(repo)
    provider.rows = [replace(r, open=90, high=91, low=89, close=90) for r in provider.rows]
    assert (
        await Worker(repo, settings, provider, archive).sync(
            {"source": "vn", "symbol": "FPT"}, "1D"
        )
        == 0
    )
    assert repo.state("vn", "FPT", "1D")["status"] == "repairing"
    assert repo.read("vn", "FPT", "1D") == before


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["foreign", "pending"])
async def test_daily_snapshot_cannot_license_an_unverified_archive_basis(system, kind):
    repo, archive, _, _ = system
    if kind == "foreign":
        old = repo.read("vn", "FPT", "1D")[0]
        archive.publish(
            [replace(old, time=parse_time("2021-01-04"), provider="dnse", revision="other")]
        )
    else:
        with repo.connect() as con:
            con.execute("UPDATE archives SET status='pending_repair'")
    before = repo.read("vn", "FPT", "1D")
    with pytest.raises(DataError, match="adoption evidence"):
        await adopt_daily_snapshot(repo, Provider(repo), "FPT", "vps", True)
    assert repo.adoptions() == [] and repo.read("vn", "FPT", "1D") == before


@pytest.mark.asyncio
async def test_daily_archived_snapshot_mutation_after_adoption_is_rejected(system):
    repo, archive, _, _ = system
    result = await adopt_daily_snapshot(repo, Provider(repo), "FPT", "vps", True)
    old = repo.read("vn", "FPT", "1D")[0]
    with pytest.raises(DataError, match="changed after adoption"):
        archive.publish(
            [
                replace(
                    old,
                    time=parse_time("2021-01-04"),
                    updated_at=result["evidence"]["verified_at_ns"] + 1,
                )
            ]
        )
    assert len(repo.archives()) == 1
