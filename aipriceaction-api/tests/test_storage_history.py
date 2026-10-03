import math
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, cutoff, parse_time
from aipriceaction_api.history import History
from aipriceaction_api.storage import Repository


@pytest.mark.parametrize(
    "provider,revision",
    (("", ""), ('provider,"日本\r\n', "\\N"), ("NULL", 'revision,"Việt Nam"')),
)
def test_bulk_archive_preserves_precision_bigints_and_quoted_metadata(system, provider, revision):
    repo, archive, _ = system
    close = math.nextafter(100.0, math.inf)
    row = Candle(
        "vn",
        'TEST,"/Việt',
        "1D",
        parse_time("2020-01-01"),
        close,
        101.0,
        99.0,
        close,
        2**63 - 1,
        provider,
        revision,
        2**63 - 1,
    )
    repo.put([row])
    original = repo.read("vn", row.symbol, "1D")
    obj = archive.publish(original, prune=True)
    assert archive.read(obj, refresh=True) == original
    assert not repo.read("vn", row.symbol, "1D")
    assert not list(archive.settings.cache_dir.rglob("*.csv"))


@pytest.fixture
def system(tmp_path):
    settings = replace(
        Settings(),
        database=tmp_path / "db.sqlite3",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        archive_backend="filesystem",
    )
    repo = Repository(settings.database)
    repo.initialize()
    archive = Archive(repo, settings)
    return repo, archive, History(repo, archive, settings)


def bar(day, close=100, revision="initial"):
    return Candle(
        "vn",
        "FPT",
        "1D",
        parse_time(day),
        close,
        close + 1,
        close - 1,
        close,
        1000,
        "import",
        revision,
    )


def fragments(system):
    repo, archive, _ = system
    repo.put([bar("2020-01-01"), bar("2020-01-02")])
    first = archive.publish(repo.read("vn", "FPT", "1D"), prune=True)
    repo.put([bar("2020-01-02", 105), bar("2020-01-03", 106)])
    second = archive.publish(repo.read("vn", "FPT", "1D"), prune=True)
    return [first, second]


def minute_hour_fixture(repo):
    start = parse_time("2024-01-03T02:15:00")
    repo.put(
        [
            Candle("vn", "FPT", "1m", start + i * 60, 100 + i, 102 + i, 99 + i, 101 + i, 10)
            for i in range(120)
        ]
    )


@pytest.mark.parametrize("archived", [False, True])
def test_minute_only_hourly_queries_preserve_complete_boundary_buckets(system, archived):
    repo, archive, history = system
    minute_hour_fixture(repo)
    if archived:
        archive.publish(repo.read("vn", "FPT", "1m"), prune=True)
    latest = history.query("vn", "FPT", "1h", limit=2, ma=False)
    assert [row["time"] for row in latest] == ["2024-01-03T03:00:00", "2024-01-03T04:00:00"]
    assert [
        (row["open"], row["high"], row["low"], row["close"], row["volume"]) for row in latest
    ] == [(145, 206, 144, 205, 600), (205, 221, 204, 220, 150)]
    forward = history.query(
        "vn", "FPT", "1h", start=parse_time("2024-01-03T03:00:00"), limit=1, ma=False
    )
    assert forward == latest[:1]
    four = history.query("vn", "FPT", "4h", limit=1, ma=False)
    assert four[0]["time"] == "2024-01-03T02:00:00"
    assert tuple(four[0][field] for field in ("open", "high", "low", "close", "volume")) == (
        100,
        221,
        99,
        220,
        1200,
    )
    assert repo.state("vn", "FPT", "1h") is None
    assert repo.archives("vn", "FPT", "1h") == []


@pytest.mark.parametrize("archived", [False, True])
def test_existing_hourly_data_wins_over_minute_derivation(system, archived):
    repo, archive, history = system
    minute_hour_fixture(repo)
    repo.put(
        [Candle("vn", "FPT", "1h", parse_time("2024-01-03T02:00:00"), 500, 501, 499, 500, 100)]
    )
    if archived:
        archive.publish(repo.read("vn", "FPT", "1h"), prune=True)
    for interval in ("1h", "4h"):
        row = history.query("vn", "FPT", interval, limit=1, ma=False)[0]
        assert (row["close"], row["volume"]) == (500, 100)


def test_pending_hourly_archive_is_not_bypassed_with_minutes(system):
    repo, archive, history = system
    minute_hour_fixture(repo)
    obj = archive.publish(
        [Candle("vn", "FPT", "1h", parse_time("2024-01-03T02:00:00"), 500, 501, 499, 500, 100)]
    )
    assert repo.state("vn", "FPT", "1h") is None
    with repo.connect() as con:
        con.execute("UPDATE archives SET status='pending_repair' WHERE id=?", (obj["id"],))
    for interval in ("1h", "4h"):
        with pytest.raises(DataError, match="repair pending"):
            history.query("vn", "FPT", interval, limit=1, ma=False)


def test_compaction_preserves_corrected_values_provenance_and_original_objects(system, tmp_path):
    repo, archive, history = system
    originals = fragments(system)
    repo.put([bar("2020-01-04", 107)])
    recent = repo.read("vn", "FPT", "1D")
    expected = history.read("vn", "FPT", "1D")
    assert len(archive.compaction_groups()) == 1
    candidate = archive.compact(originals)
    assert candidate["row_count"] == 3 and len(repo.archives()) == 1
    assert history.read("vn", "FPT", "1D") == expected
    assert repo.read("vn", "FPT", "1D") == recent
    assert archive.compaction_groups() == []
    for old in originals:
        assert len(archive.read(old, refresh=True)) == 2
    fresh = Repository(tmp_path / "restored")
    fresh.initialize()
    assert Archive(fresh, archive.settings).restore_index() == 1
    assert (
        History(fresh, Archive(fresh, archive.settings), archive.settings).read("vn", "FPT", "1D")
        == expected[:-1]
    )


def test_compaction_rejects_a_concurrent_repair_before_advertising_candidate(system, monkeypatch):
    repo, archive, _ = system
    originals = fragments(system)
    key = f"{archive.settings.s3_prefix}/LATEST.json"
    pointer = archive.store.read(key)
    prepare = archive.prepare

    def race(rows):
        obj = prepare(rows)
        with repo.connect() as con:
            con.execute(
                "UPDATE archives SET status='pending_repair' WHERE id=?", (originals[0]["id"],)
            )
        return obj

    monkeypatch.setattr(archive, "prepare", race)
    with pytest.raises(DataError, match="changed during compaction"):
        archive.compact(originals)
    assert archive.store.read(key) == pointer
    assert len(repo.archives()) == 2


def test_compaction_manifest_failure_keeps_a_verified_readable_local_index(system, monkeypatch):
    repo, archive, history = system
    originals = fragments(system)
    expected = history.read("vn", "FPT", "1D")
    key = f"{archive.settings.s3_prefix}/LATEST.json"
    pointer = archive.store.read(key)
    publish_manifest = archive.manifest

    def unavailable(_):
        raise DataError("Manifest unavailable")

    monkeypatch.setattr(archive, "manifest", unavailable)
    with pytest.raises(DataError, match="Manifest unavailable"):
        archive.compact(originals)
    assert archive.store.read(key) == pointer
    assert len(repo.archives()) == 1 and archive.compaction_groups() == []
    assert history.read("vn", "FPT", "1D") == expected
    publish_manifest(repo.archives())
    assert archive.store.read(key) != pointer


def test_archived_and_recent_candles_match_continuous_history(system):
    repo, archive, history = system
    rows = [bar(f"2020-01-{d:02}", 100 + d) for d in range(1, 31)]
    repo.put(rows)
    before = history.query("vn", "FPT", "1D", start=parse_time("2020-01-21"), limit=10)
    snapshot = repo.read("vn", "FPT", "1D", end=parse_time("2020-01-20"))
    archive.publish(snapshot, prune=True)
    assert len(repo.read("vn", "FPT", "1D")) == 10
    assert history.query("vn", "FPT", "1D", start=parse_time("2020-01-21"), limit=10) == before
    assert history.read("vn", "FPT", "1D")[0].close == 101
    assert history.query("vn", "FPT", "1D", limit=1)[0]["ma20"] == 120.5


def test_failed_manifest_keeps_local_rows(system, monkeypatch):
    repo, archive, _ = system
    repo.put([bar("2020-01-01")])

    def fail(_):
        raise DataError("network unavailable")

    monkeypatch.setattr(archive, "manifest", fail)
    with pytest.raises(DataError):
        archive.publish(repo.read("vn", "FPT", "1D"), prune=True)
    assert len(repo.read("vn", "FPT", "1D")) == 1
    assert len(repo.archives()) == 1
    # The verified object/index survives locally; remote publication and prune
    # can be retried. Original local candles remain the authoritative overlay.
    assert archive.read(repo.archives()[0]) == repo.read("vn", "FPT", "1D")


def test_rejected_archive_replacement_is_not_advertised_in_remote_manifest(
    system, monkeypatch, tmp_path
):
    repo, archive, _ = system
    repo.put([bar("2025-01-02")])
    original = archive.publish([bar("2020-01-01")])
    pointer = archive.store.read(f"{archive.settings.s3_prefix}/LATEST.json")
    replace_archive = repo.replace_archive

    def race(old_id, obj):
        repo.queue("vn", "FPT", "1D", "repair", parse_time("2023-01-01"), "import")
        replace_archive(old_id, obj)

    monkeypatch.setattr(repo, "replace_archive", race)
    with pytest.raises(DataError, match="superseded"):
        archive.publish([bar("2020-01-01", 110)], replaces=original["id"])
    assert archive.store.read(f"{archive.settings.s3_prefix}/LATEST.json") == pointer
    fresh = Repository(tmp_path / "restored")
    fresh.initialize()
    restored = Archive(fresh, archive.settings)
    assert restored.restore_index() == 1
    assert restored.read(fresh.archives()[0])[0].close == 100


def test_recovered_archive_requires_the_current_ready_revision(system):
    repo, archive, _ = system
    repo.put([bar("2025-01-02")])
    repo.queue("vn", "FPT", "1D", "repair", parse_time("2023-01-01"), "import")
    with pytest.raises(DataError, match="superseded"):
        archive.publish([bar("2020-01-01")], require_current=True)
    assert repo.archives() == []
    assert not archive.store.path(f"{archive.settings.s3_prefix}/LATEST.json").exists()


def test_stale_archive_target_cannot_publish_a_second_replacement(system):
    repo, archive, _ = system
    repo.put([bar("2025-01-02")])
    original = archive.publish([bar("2020-01-02")])
    current = archive.publish([bar("2020-01-02", 110)], replaces=original["id"])
    pointer = archive.store.read(f"{archive.settings.s3_prefix}/LATEST.json")
    with pytest.raises(DataError, match="superseded"):
        archive.publish([bar("2020-01-02", 120)], replaces=original["id"])
    assert [r["id"] for r in repo.archives()] == [current["id"]]
    assert archive.store.read(f"{archive.settings.s3_prefix}/LATEST.json") == pointer
    # The same candidate can still finish a failed remote-manifest publication.
    retried = archive.publish([bar("2020-01-02", 110)], replaces=original["id"])
    assert retried["id"] == current["id"]


def test_archive_replacement_cannot_supersede_another_tickers_object(system):
    repo, archive, _ = system
    repo.put([bar("2025-01-02")])
    original = archive.publish([replace(bar("2020-01-02"), symbol="VCB")])
    with pytest.raises(DataError, match="another series"):
        archive.publish([bar("2020-01-02")], replaces=original["id"])
    assert [r["id"] for r in repo.archives()] == [original["id"]]


def test_retiring_superseded_archive_work_respects_active_leases(system):
    repo, archive, _ = system
    repo.put([bar("2025-01-02")])
    original = archive.publish([bar("2020-01-02")])
    job = repo.queue(
        "vn", "FPT", "1D", f"archive_repair:{original['id']}:initial", original["start"]
    )
    assert repo.retire_archive_jobs() == 0  # Target remains active.
    archive.publish([bar("2020-01-02", 110)], replaces=original["id"])
    with repo.connect() as con:
        con.execute("UPDATE jobs SET lease_until=? WHERE id=?", (int(time.time()) + 120, job))
    assert repo.retire_archive_jobs() == 0
    with repo.connect() as con:
        con.execute("UPDATE jobs SET lease_until=0 WHERE id=?", (job,))
    assert repo.retire_archive_jobs("vn", "VCB", "1D") == 0
    assert repo.retire_archive_jobs("vn", "FPT", "1D") == 1
    with repo.connect() as con:
        assert (
            con.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0] == "cancelled"
        )


def test_archive_marked_pending_during_manifest_publication_is_not_pruned(system, monkeypatch):
    repo, archive, _ = system
    repo.put([bar("2020-01-01")])
    manifest = archive.manifest

    def race(objects):
        manifest(objects)
        with repo.connect() as con:
            con.execute("UPDATE archives SET status='pending_repair'")

    monkeypatch.setattr(archive, "manifest", race)
    with pytest.raises(DataError, match="verified for pruning"):
        archive.publish(repo.read("vn", "FPT", "1D"), prune=True)
    assert repo.read("vn", "FPT", "1D")[0].close == 100


def test_reconciliation_after_export_is_not_pruned(system):
    repo, archive, _ = system
    repo.put([bar("2020-01-01")])
    snapshot = repo.read("vn", "FPT", "1D")
    obj = archive.prepare(snapshot)
    repo.put([bar("2020-01-01", 105)])
    repo.publish_archive(obj, snapshot, prune=True)
    assert repo.read("vn", "FPT", "1D")[0].close == 105


def test_archive_corruption_is_not_an_empty_success(system):
    repo, archive, history = system
    repo.put([bar("2020-01-01")])
    obj = archive.publish(repo.read("vn", "FPT", "1D"), prune=True)
    archive.store.path(obj["object_key"]).write_bytes(b"corrupt")
    (archive.settings.cache_dir / (obj["checksum"] + ".parquet")).unlink()
    with pytest.raises(DataError, match="checksum"):
        history.read("vn", "FPT", "1D")


def test_archive_manifest_restores_discovery(system, tmp_path):
    repo, archive, _ = system
    repo.put([bar("2020-01-01")])
    archive.publish(repo.read("vn", "FPT", "1D"), prune=True)
    fresh = Repository(tmp_path / "fresh.sqlite3")
    fresh.initialize()
    recovery = Archive(fresh, archive.settings)
    assert recovery.restore_index() == 1
    assert fresh.tickers()[0]["symbol"] == "FPT"
    assert History(fresh, recovery, archive.settings).read("vn", "FPT", "1D")[0].close == 100


def test_no_mixing_adjustment_revisions(system):
    repo, archive, history = system
    repo.put([bar("2020-01-01")])
    archive.publish(repo.read("vn", "FPT", "1D"), prune=True)
    with repo.connect() as con:
        con.execute("UPDATE series SET revision='adjusted'")
    repo.put([bar("2020-01-02", 90, "adjusted")])
    with pytest.raises(DataError, match="adjustment revisions"):
        history.read("vn", "FPT", "1D")
    # Latest-only queries shouldn't be blocked by an unrelated old revision.
    assert history.read("vn", "FPT", "1D", limit=1)[0].close == 90


def test_archive_only_reconciliation_preserves_recent_series_and_only_marks_old_basis(system):
    repo, archive, history = system
    repo.put([bar("2025-01-02")])
    archive.publish([bar("2020-01-01", revision="old-basis")])
    archive.publish([bar("2020-01-02")])
    epoch = repo.epoch()
    assert repo.mark_archive_repairs("vn", "FPT", "1D") == 1
    assert repo.mark_archive_repairs("vn", "FPT", "1D") == 0
    assert repo.epoch() == epoch + 1
    assert repo.state("vn", "FPT", "1D")["status"] == "ready"
    assert history.read("vn", "FPT", "1D", limit=1)[0].time == parse_time("2025-01-02")
    assert sorted(obj["status"] for obj in repo.archives()) == ["pending_repair", "published"]
    with pytest.raises(DataError, match="ready retained series"):
        repo.mark_archive_repairs("vn", "MISSING", "1D")


def test_forward_reads_include_earliest_archived_rows(system):
    repo, archive, history = system
    repo.put([bar(f"2020-01-{d:02}", 100 + d) for d in range(1, 31)])
    archive.publish(repo.read("vn", "FPT", "1D", end=parse_time("2020-01-20")), prune=True)
    rows = history.query("vn", "FPT", "1D", start=parse_time("2020-01-02"), limit=2)
    assert [r["time"] for r in rows] == ["2020-01-02", "2020-01-03"]
    assert rows[0]["close_changed"] == pytest.approx((102 / 101 - 1) * 100)


def test_invalid_manifest_pointer_prevents_pruning(system, monkeypatch):
    repo, archive, _ = system
    repo.put([bar("2020-01-01")])
    original = archive.store.put

    def corrupt_pointer(key, path):
        original(key, path)
        if key.endswith("LATEST.json"):
            archive.store.path(key).write_bytes(b"corrupt")

    monkeypatch.setattr(archive.store, "put", corrupt_pointer)
    with pytest.raises(DataError, match="pointer"):
        archive.publish(repo.read("vn", "FPT", "1D"), prune=True)
    assert len(repo.read("vn", "FPT", "1D")) == 1
    assert len(repo.archives()) == 1


def test_restore_checks_manifest_count_and_bounds(system, tmp_path):
    repo, archive, _ = system
    repo.put([bar("2020-01-01")])
    obj = archive.publish(repo.read("vn", "FPT", "1D"), prune=True)
    archive.manifest([obj | {"row_count": 2}])
    fresh = Repository(tmp_path / "fresh")
    fresh.initialize()
    with pytest.raises(DataError, match="coverage differs"):
        Archive(fresh, archive.settings).restore_index()
    assert fresh.archives() == []


def test_atomic_sync_and_secret_protection(system):
    repo, _, _ = system
    key = str(uuid.uuid4())
    first = repo.sync(key, "secret", {"watchlists": ["FPT"]}, True)
    with pytest.raises(DataError, match="Invalid secret"):
        repo.sync(key, "wrong", {"watchlists": []}, True)
    second = repo.sync(key, "secret", {"watchlists": ["VCB"]}, True)
    assert second["created_at"] == first["created_at"]
    assert repo.sync(key, "secret")["value"] == {"watchlists": ["VCB"]}


def test_leap_day_retention():
    assert cutoff(3, datetime(2024, 2, 29, 12, tzinfo=UTC)) == parse_time("2021-02-28")


def test_backups_preserve_recent_rows(system, tmp_path):
    repo, _, _ = system
    repo.put([bar("2020-01-01")])
    dest = tmp_path / "backup.sqlite3"
    repo.backup(dest)
    assert Repository(dest).read("vn", "FPT", "1D")[0].close == 100
    with pytest.raises(DataError):
        repo.backup(dest)
