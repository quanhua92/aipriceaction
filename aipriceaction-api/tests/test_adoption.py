from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from aipriceaction_api.adoption import adopt_snapshot
from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
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
    start = int(
        (
            datetime.now(UTC).replace(hour=2, minute=15, second=0, microsecond=0)
            - timedelta(days=14)
        ).timestamp()
    )
    rows = [
        Candle(
            "vn",
            "FPT",
            "1m",
            start + day * 86400 + minute * 60,
            100,
            101,
            99,
            100,
            100,
            "legacy-api",
            "captured",
        )
        for day in range(1, 6)
        for minute in range(220)
    ]
    repo.put(rows)
    archive = Archive(repo, settings)
    return repo, archive, History(repo, archive, settings), settings


class Provider:
    settings = SimpleNamespace(vn_providers=("vps", "vndirect", "dnse"))

    def __init__(self, repo, mutate=None, rows=None):
        self.rows = (
            rows
            if rows is not None
            else [
                replace(r, provider="vps", revision="initial") for r in repo.read("vn", "FPT", "1m")
            ]
        )
        self.mutate = mutate

    async def page(self, *args, **kwargs):
        if self.mutate:
            self.mutate()
        return Page(self.rows, "vps")


@pytest.mark.asyncio
async def test_dry_run_then_adoption_preserves_prices_provenance_and_allows_verified_append(system):
    repo, _, history, settings = system
    before = repo.read("vn", "FPT", "1m")
    epoch = repo.epoch()
    provider = Provider(repo)
    report = await adopt_snapshot(repo, provider, "FPT", "vps")
    assert report["dry_run"] and report["evidence"]["matched_rows"] == 1100
    assert repo.epoch() == epoch and repo.adoptions() == []
    job = repo.queue("vn", "FPT", "1m", "bootstrap", before[0].time)
    await adopt_snapshot(repo, provider, "FPT", "vps", True)
    assert repo.state("vn", "FPT", "1m")["provider"] == "vps"
    assert repo.read("vn", "FPT", "1m") == before
    assert repo.status()["jobs"] == []
    with repo.connect() as con:
        assert (
            con.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0] == "cancelled"
        )
    new = replace(before[-1], time=before[-1].time + 60, provider="vps", revision="initial")
    updates = Provider(
        repo, rows=[replace(r, provider="vps", revision="initial") for r in before[-39:]] + [new]
    )
    worker = Worker(repo, settings, updates)
    assert await worker.sync({"source": "vn", "symbol": "FPT"}, "1m") == 40
    assert len(history.read("vn", "FPT", "1m")) == 1101
    assert history.query("vn", "FPT", "15m", limit=1)[0]["close"] == 100
    assert repo.state("vn", "FPT", "1m")["status"] == "ready"
    with pytest.raises(DataError, match="staged recovery"):
        repo.put([replace(new, provider="dnse", revision="captured")])


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("close", 100.1), ("volume", 101)])
async def test_any_completed_price_or_volume_disagreement_rejects_adoption(system, field, value):
    repo, _, _, _ = system
    provider = Provider(repo)
    provider.rows[0] = replace(provider.rows[0], **{field: value})
    with pytest.raises(DataError, match="disagrees"):
        await adopt_snapshot(repo, provider, "FPT", "vps", True)
    assert repo.state("vn", "FPT", "1m")["provider"] == "legacy-api"
    assert repo.adoptions() == []


@pytest.mark.asyncio
async def test_sparse_or_truncated_provider_overlap_cannot_license_append(system):
    repo, _, _, _ = system
    provider = Provider(repo)
    provider.rows = provider.rows[:-1]
    with pytest.raises(DataError, match="published tail"):
        await adopt_snapshot(repo, provider, "FPT", "vps", True)
    provider.rows = provider.rows[:999]
    with pytest.raises(DataError, match="1000 exact"):
        await adopt_snapshot(repo, provider, "FPT", "vps", True)
    assert repo.adoptions() == []


@pytest.mark.asyncio
async def test_concurrent_snapshot_correction_prevents_stale_adoption(system):
    repo, _, _, _ = system
    row = repo.read("vn", "FPT", "1m")[0]
    provider = Provider(repo, mutate=lambda: repo.put([replace(row, close=100.5)]))
    with pytest.raises(DataError, match="changed during"):
        await adopt_snapshot(repo, provider, "FPT", "vps", True)
    assert repo.read("vn", "FPT", "1m")[0].close == 100.5
    assert repo.state("vn", "FPT", "1m")["provider"] == "legacy-api"


@pytest.mark.asyncio
async def test_active_repair_lease_prevents_adoption(system):
    repo, _, _, _ = system
    repo.queue("vn", "FPT", "1m", "bootstrap", parse_time("2024-01-01"))
    repo.claim_job("other")
    with pytest.raises(DataError, match="job is active"):
        await adopt_snapshot(repo, Provider(repo), "FPT", "vps", True)
    assert repo.adoptions() == []


@pytest.mark.asyncio
async def test_adoption_evidence_survives_archive_index_restore(system, tmp_path):
    repo, archive, history, settings = system
    await adopt_snapshot(repo, Provider(repo), "FPT", "vps", True)
    old = repo.read("vn", "FPT", "1m")
    new = replace(old[-1], time=old[-1].time + 60, provider="vps")
    repo.put([new])
    expected = history.query("vn", "FPT", "15m", limit=1)
    archive.publish(repo.read("vn", "FPT", "1m", end=old[-1].time), prune=True)
    archive.publish(repo.read("vn", "FPT", "1m"), prune=True)
    fresh = Repository(tmp_path / "recovered")
    fresh.initialize()
    restored = Archive(fresh, settings)
    assert restored.restore_index() == 2
    assert len(fresh.adoptions()) == 1
    assert History(fresh, restored, settings).query("vn", "FPT", "15m", limit=1) == expected


def test_same_revision_without_adoption_cannot_mix_providers(system):
    repo, archive, history, _ = system
    row = repo.read("vn", "FPT", "1m")[-1]
    archive.publish([replace(row, time=row.time + 60, provider="dnse")])
    with pytest.raises(DataError, match="Unverified providers"):
        history.read("vn", "FPT", "1m", limit=2)


def test_vn_finality_includes_closed_sessions_and_excludes_partial_sessions():
    assert completed_vn_sessions(datetime(2026, 10, 2, 7, 45, tzinfo=UTC)) == parse_time(
        "2026-10-02"
    )
    assert completed_vn_sessions(datetime(2026, 10, 2, 8, 14, tzinfo=UTC)) == parse_time(
        "2026-10-02"
    )
    assert completed_vn_sessions(datetime(2026, 10, 2, 8, 15, tzinfo=UTC)) == parse_time(
        "2026-10-03"
    )
    assert completed_vn_sessions(datetime(2026, 10, 3, 8, 15, tzinfo=UTC)) == parse_time(
        "2026-10-03"
    )


@pytest.mark.asyncio
async def test_adopted_snapshot_price_revisions_still_queue_recovery(system):
    repo, archive, _, settings = system
    await adopt_snapshot(repo, Provider(repo), "FPT", "vps", True)
    previous = repo.read("vn", "FPT", "1m")
    changed = [
        replace(r, provider="vps", revision="initial", open=90, high=91, low=89, close=90)
        for r in previous[-40:]
    ]
    worker = Worker(repo, settings, Provider(repo, rows=changed), archive)
    assert await worker.sync({"source": "vn", "symbol": "FPT"}, "1m") == 0
    assert repo.state("vn", "FPT", "1m")["status"] == "repairing"
    assert repo.read("vn", "FPT", "1m") == previous


@pytest.mark.asyncio
async def test_new_snapshot_cannot_be_silently_inserted_under_an_adopted_revision(system):
    repo, archive, _, _ = system
    report = await adopt_snapshot(repo, Provider(repo), "FPT", "vps", True)
    row = repo.read("vn", "FPT", "1m")[0]
    with pytest.raises(DataError, match="changed after adoption"):
        archive.publish([replace(row, updated_at=report["evidence"]["verified_at_ns"] + 1)])
    assert repo.archives() == []


@pytest.mark.asyncio
async def test_archive_only_reconciliation_does_not_rebuild_verified_snapshot_partitions(system):
    repo, archive, _, _ = system
    await adopt_snapshot(repo, Provider(repo), "FPT", "vps", True)
    archive.publish(repo.read("vn", "FPT", "1m"))
    assert repo.mark_archive_repairs("vn", "FPT", "1m") == 0
    assert repo.archives()[0]["status"] == "published"


@pytest.mark.asyncio
async def test_rejected_adoption_evidence_does_not_change_restored_index(system, tmp_path):
    repo, archive, _, settings = system
    await adopt_snapshot(repo, Provider(repo), "FPT", "vps", True)
    archive.publish(repo.read("vn", "FPT", "1m"))
    record = repo.adoptions()[0]
    record["evidence"] = "{}"
    # A checksum-valid manifest with a semantically invalid certificate must fail.
    import hashlib
    import json
    import tempfile
    from pathlib import Path

    raw = json.dumps({"version": 1, "objects": repo.archives(), "adoptions": [record]}).encode()
    digest = hashlib.sha256(raw).hexdigest()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "manifest"
        path.write_bytes(raw)
        key = f"{settings.s3_prefix}/manifests/{digest}.json"
        archive.store.put(key, path)
        path.write_text(json.dumps({"key": key, "checksum": digest}))
        archive.store.put(f"{settings.s3_prefix}/LATEST.json", path)
    fresh = Repository(tmp_path / "fresh")
    fresh.initialize()
    with pytest.raises(DataError, match="adoption evidence"):
        Archive(fresh, settings).restore_index()
    assert fresh.archives() == [] and fresh.adoptions() == []


class GlobalProvider:
    settings = SimpleNamespace(vn_providers=("vps", "vndirect", "dnse"))

    def __init__(self, rows, mutate=None):
        self.rows = [replace(r, provider="yahoo", revision="initial") for r in rows]
        self.mutate = mutate

    async def page(self, source, symbol, interval, **kwargs):
        assert source == "yahoo" and symbol == "^GSPC" and interval == "1m"
        if self.mutate:
            self.mutate()
        return Page(self.rows, "yahoo")


def global_snapshot(repo):
    rows = [
        replace(r, source="yahoo", symbol="^GSPC", volume=0) for r in repo.read("vn", "FPT", "1m")
    ]
    repo.put(rows)
    return repo.read("yahoo", "^GSPC", "1m")


@pytest.mark.asyncio
async def test_global_unverified_snapshot_stays_frozen_without_automatic_provider_repair(system):
    repo, _, _, settings = system
    before = global_snapshot(repo)
    assert (
        await Worker(repo, settings, GlobalProvider(before)).sync(
            {"source": "yahoo", "symbol": "^GSPC"}, "1m"
        )
        == 0
    )
    assert repo.read("yahoo", "^GSPC", "1m") == before
    assert repo.state("yahoo", "^GSPC", "1m")["provider"] == "legacy-api"
    with repo.connect() as con:
        assert (
            con.execute(
                "SELECT outcome FROM source_checks WHERE source='yahoo' AND symbol='^GSPC' AND interval='1m'"
            ).fetchone()[0]
            == "handoff_required"
        )
        assert not con.execute(
            "SELECT 1 FROM jobs WHERE source='yahoo' AND symbol='^GSPC' AND interval='1m'"
        ).fetchone()


@pytest.mark.asyncio
async def test_global_exact_handoff_preserves_snapshot_allows_updates_and_restores(
    system, tmp_path
):
    repo, archive, history, settings = system
    before = global_snapshot(repo)
    vn_before = repo.read("vn", "FPT", "1m")
    result = await adopt_snapshot(repo, GlobalProvider(before), "^GSPC", "yahoo", source="yahoo")
    assert result["dry_run"] and repo.adoptions() == []
    job = repo.queue("yahoo", "^GSPC", "1m", "bootstrap", before[0].time)
    await adopt_snapshot(repo, GlobalProvider(before), "^GSPC", "yahoo", True, source="yahoo")
    assert repo.read("yahoo", "^GSPC", "1m") == before
    assert repo.read("vn", "FPT", "1m") == vn_before
    assert repo.state("yahoo", "^GSPC", "1m")["provider"] == "yahoo"
    with repo.connect() as con:
        assert (
            con.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0] == "cancelled"
        )
    extra = replace(before[-1], time=before[-1].time + 60)
    provider = GlobalProvider(before[-39:] + [extra])
    assert (
        await Worker(repo, settings, provider).sync({"source": "yahoo", "symbol": "^GSPC"}, "1m")
        == 40
    )
    assert len(history.read("yahoo", "^GSPC", "1m")) == len(before) + 1
    assert repo.adoptions()[0]["source"] == "yahoo"
    combined = repo.read("yahoo", "^GSPC", "1m")
    for provider_name in ("legacy-api", "yahoo"):
        archive.publish([r for r in combined if r.provider == provider_name], prune=True)
    restored = Repository(tmp_path / "global-restored")
    restored.initialize()
    assert Archive(restored, settings).restore_index() == 2
    assert restored.adoptions() == repo.adoptions()
    assert History(restored, Archive(restored, settings), settings).query(
        "yahoo", "^GSPC", "15m", limit=1
    ) == history.query("yahoo", "^GSPC", "15m", limit=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("leased", [False, True])
async def test_global_handoff_rejects_concurrent_correction_or_active_job(system, leased):
    repo, _, _, _ = system
    rows = global_snapshot(repo)

    def mutate():
        repo.put([replace(rows[0], close=100.5)])

    if leased:
        repo.queue("yahoo", "^GSPC", "1m", "bootstrap", rows[0].time)
        repo.claim_job("other", allowed=[("yahoo", "^GSPC", "1m")])
        mutate = None
    with pytest.raises(DataError, match="changed during|job is active"):
        await adopt_snapshot(
            repo, GlobalProvider(rows, mutate), "^GSPC", "yahoo", True, source="yahoo"
        )
    assert repo.state("yahoo", "^GSPC", "1m")["provider"] == "legacy-api"
    assert repo.adoptions() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("option", ["complete_sessions", "corroborate"])
async def test_global_handoff_cannot_use_vn_specific_correction_proofs(system, option):
    repo, _, _, _ = system
    rows = global_snapshot(repo)
    with pytest.raises(DataError, match="require VN"):
        await adopt_snapshot(
            repo,
            GlobalProvider(rows),
            "^GSPC",
            "yahoo",
            True,
            source="yahoo",
            **{option: True if option == "complete_sessions" else "vps"},
        )
    assert repo.adoptions() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["wrong_provider", "unclosed_overlap", "future_finality"])
async def test_global_receipt_rejects_wrong_market_or_invalid_utc_finality(system, invalid):
    import json

    repo, _, _, _ = system
    rows = global_snapshot(repo)
    await adopt_snapshot(repo, GlobalProvider(rows), "^GSPC", "yahoo", True, source="yahoo")
    record = repo.adoptions()[0]
    evidence = json.loads(record["evidence"])
    if invalid == "wrong_provider":
        record["provider"] = "vps"
    elif invalid == "unclosed_overlap":
        evidence["completed_before"] = evidence["overlap_end"]
    else:
        evidence["completed_before"] = evidence["verified_at_ns"] // 60_000_000_000 * 60 + 60
    record["evidence"] = json.dumps(evidence)
    with pytest.raises(DataError, match="adoption evidence"):
        repo.validate_adoption(record)
