from dataclasses import replace

import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, parse_time
from aipriceaction_api.history import History
from aipriceaction_api.storage import Repository


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
    archive = Archive(repo, settings)
    return repo, archive, History(repo, archive, settings)


def rows(count, start=0, revision="native", provider="import", close=100):
    first = parse_time("2019-01-01")
    return [
        Candle(
            "vn",
            "FPT",
            "1D",
            first + i * 86400,
            close,
            close + 1,
            close - 1,
            close,
            1000,
            provider,
            revision,
            1,
        )
        for i in range(start, start + count)
    ]


def setup(system, count=650):
    repo, archive, history = system
    cold = rows(count)
    original = archive.publish(cold)
    repo.put(rows(30, count))
    frozen = rows(count, revision="public-frozen", provider="legacy-api", close=200)
    obj = archive.publish(frozen, historical_snapshot=True)
    return repo, archive, history, cold, frozen, original, obj


def test_expired_bounded_queries_use_one_frozen_revision_and_pin_full_indicator_context(system):
    repo, _, history, cold, frozen, _, _ = setup(system)
    original = repo.read("vn", "FPT", "1D")
    historical = history.query("vn", "FPT", "1D", end=cold[-1].time, limit=20, ema=True)
    assert len(historical) == 20
    assert all(r["close"] == 200 and r["ma200"] == pytest.approx(200) for r in historical)
    recent = history.query("vn", "FPT", "1D", limit=20, ema=True)
    assert all(r["close"] == 100 and r["ma200"] == pytest.approx(100) for r in recent)
    assert history.read("vn", "FPT", "1D", end=cold[-1].time, revision="native") == cold
    assert history.read("vn", "FPT", "1D", end=cold[-1].time) == frozen
    assert repo.read("vn", "FPT", "1D") == original


def test_open_ended_and_cross_retention_primary_reads_preserve_original_dates_and_basis(system):
    repo, _, history, cold, _, _, _ = setup(system)
    original = cold + repo.read("vn", "FPT", "1D")
    assert history.read("vn", "FPT", "1D") == original
    assert history.read("vn", "FPT", "1D", start=cold[0].time, end=original[-1].time) == original


def test_complete_frozen_snapshot_can_cover_a_valid_pending_primary_archive(system):
    repo, _, history, cold, frozen, original, _ = setup(system)
    with repo.connect() as con:
        con.execute("UPDATE archives SET status='pending_repair' WHERE id=?", (original["id"],))
    assert history.read("vn", "FPT", "1D", end=cold[-1].time, limit=10000) == frozen
    result = history.query("vn", "FPT", "1D", end=cold[-1].time, limit=10000)
    assert len(result) == len(frozen)
    assert all(row["close"] == 200 for row in result)
    assert (
        next(o for o in repo.archives() if o["id"] == original["id"])["status"] == "pending_repair"
    )
    with pytest.raises(DataError, match="repair pending"):
        history.read("vn", "FPT", "1D")


def test_newly_imported_older_snapshot_does_not_displace_a_later_bounded_tail(system):
    repo, archive, history, cold, _, _, current = setup(system)
    older = archive.publish(
        rows(30, revision="older-public", provider="legacy-api", close=300),
        historical_snapshot=True,
    )
    with repo.connect() as con:
        con.execute("UPDATE archives SET created_at=100 WHERE id=?", (current["id"],))
        con.execute("UPDATE archives SET created_at=200 WHERE id=?", (older["id"],))
    result = history.query("vn", "FPT", "1D", end=cold[-1].time, limit=20, ema=True)
    assert len(result) == 20
    assert all(row["close"] == 200 for row in result)
    assert result[-1]["time"] == "2020-10-11"
    earlier = history.query("vn", "FPT", "1D", end=rows(30)[-1].time, limit=20, ma=False)
    assert len(earlier) == 20
    assert all(row["close"] == 300 for row in earlier)


def test_frozen_snapshot_cannot_hide_a_missing_pending_primary_timestamp(system):
    repo, archive, history = system
    cold = rows(30)
    original = archive.publish(cold)
    with repo.connect() as con:
        con.execute("UPDATE archives SET status='pending_repair' WHERE id=?", (original["id"],))
    frozen = rows(29, revision="public-frozen", provider="legacy-api", close=200)
    archive.publish(frozen, historical_snapshot=True)
    with pytest.raises(DataError, match="repair pending"):
        history.read("vn", "FPT", "1D", end=cold[-1].time, limit=10000)


@pytest.mark.parametrize("start", [None, "2019-01-01"])
def test_snapshot_context_row_does_not_displace_a_longer_primary_request(system, start):
    repo, archive, history = system
    cold = rows(650)
    archive.publish(cold)
    repo.put(rows(30, start=650))
    archive.publish(
        rows(1, start=649, revision="context-only", provider="legacy-api", close=200),
        historical_snapshot=True,
    )
    result = history.query(
        "vn", "FPT", "1D", start=parse_time(start) if start else None, end=cold[-1].time, limit=20
    )
    assert len(result) == 20
    assert all(row["close"] == 100 for row in result)


def test_frozen_snapshot_preserves_pending_guard_when_archive_cannot_be_verified(
    system, monkeypatch
):
    _, archive, history, cold, _, original, _ = setup(system)
    with history.repo.connect() as con:
        con.execute("UPDATE archives SET status='pending_repair' WHERE id=?", (original["id"],))
    read = archive.read

    def broken(obj, *args, **kwargs):
        if obj["id"] == original["id"]:
            raise DataError("Invalid Parquet archive")
        return read(obj, *args, **kwargs)

    monkeypatch.setattr(archive, "read", broken)
    with pytest.raises(DataError, match="repair pending"):
        history.read("vn", "FPT", "1D", end=cold[-1].time, limit=10000)


def test_incompatible_older_warmup_does_not_block_or_pollute_native_candles(system):
    repo, archive, history, cold, _, _, _ = setup(system, count=20)
    archive.publish(rows(1, start=-1, revision="older-basis"))
    original = repo.read("vn", "FPT", "1D")
    result = history.query("vn", "FPT", "1D", limit=1, ema=True)
    assert result[0]["close"] == 100
    assert result[0]["ma200"] == pytest.approx(100)
    with pytest.raises(DataError, match="Incompatible adjustment revisions"):
        history.read("vn", "FPT", "1D")
    assert repo.read("vn", "FPT", "1D") == original


@pytest.mark.parametrize(
    "changes",
    [
        {"prune": True},
        {"require_current": True},
        {"replaces": {}},
        {"recent": True},
        {"provider": "vps"},
        {"revision": "native"},
    ],
)
def test_public_historical_publication_rejects_live_primary_or_destructive_options(system, changes):
    repo, archive, _ = system
    repo.put(rows(1))
    data = rows(1, revision="public-frozen", provider=changes.get("provider", "legacy-api"))
    if changes.get("revision"):
        data = [replace(r, revision=changes["revision"]) for r in data]
    if changes.get("recent"):
        data = [replace(r, time=parse_time("2099-01-01")) for r in data]
    kwargs = {k: v for k, v in changes.items() if k in ("prune", "require_current", "replaces")}
    with pytest.raises(DataError, match="Historical snapshots"):
        archive.publish(data, historical_snapshot=True, **kwargs)
    assert not repo.archives()


def test_snapshot_status_and_candles_survive_manifest_restoration(system, tmp_path):
    _, archive, _, cold, frozen, _, _ = setup(system)
    fresh = Repository(tmp_path / "restored")
    fresh.initialize()
    restored = Archive(fresh, archive.settings)
    assert restored.restore_index() == 2
    snapshots = [obj for obj in fresh.archives() if obj["status"] == "historical_snapshot"]
    assert len(snapshots) == 1
    assert (
        History(fresh, restored, archive.settings).read("vn", "FPT", "1D", end=cold[-1].time)
        == frozen
    )


def test_manifest_cannot_restore_recent_candles_disguised_as_a_historical_snapshot(
    system, tmp_path
):
    repo, archive, _ = system
    future = [
        replace(r, time=parse_time("2099-01-01"))
        for r in rows(1, revision="frozen", provider="legacy-api")
    ]
    obj = archive.prepare(future) | {"status": "historical_snapshot"}
    repo.publish_archive(obj)
    archive.manifest(repo.archives())
    fresh = Repository(tmp_path / "forged-restoration")
    fresh.initialize()
    with pytest.raises(DataError, match="outside retention"):
        Archive(fresh, archive.settings).restore_index()
    assert not fresh.archives()
