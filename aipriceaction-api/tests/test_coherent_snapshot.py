import json
from dataclasses import replace

import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.coherent_snapshot import capture, publish
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, parse_time
from aipriceaction_api.history import History
from aipriceaction_api.storage import Repository

FLOOR = parse_time("2025-10-04")


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
    archive = Archive(repo, settings)
    old = [
        Candle("vn", "FPT", "1m", parse_time(day), 100, 110, 90, 100, 1000, "vps", "old")
        for day in ("2025-10-03T02:15:00", "2025-10-06T02:15:00")
    ]
    repo.put(old[1:])
    archive.publish(old[:1], require_current=True)
    replacement = [replace(row, provider="vci", revision="new", close=101) for row in old]
    replacement.append(replace(replacement[-1], time=replacement[-1].time + 60))
    return repo, archive, replacement


def test_activation_changes_hot_and_cold_together_and_keeps_readable_before_images(system):
    repo, archive, rows = system
    snapshot = capture(repo, archive, "FPT")
    plan = publish(repo, archive, snapshot, rows, FLOOR)
    assert not plan["published"] and repo.state("vn", "FPT", "1m") == snapshot.state
    result = publish(repo, archive, snapshot, rows, FLOOR, execute=True)
    assert result["published"] and result["manifest_published"]
    assert not result["provider_handoff_licensed"]
    assert repo.snapshot_adoption(repo.state("vn", "FPT", "1m")) is None
    history = History(repo, archive, archive.settings)
    assert history.read("vn", "FPT", "1m") == rows
    assert repo.read("vn", "FPT", "1m") == rows[1:]
    assert all(o["revision"] == "new" and o["status"] == "published" for o in repo.archives())
    assert archive.read(result["before_hot_images"][0], refresh=True) == snapshot.hot
    assert archive.read(result["replacement_hot_image"], refresh=True) == rows[1:]
    for obj in snapshot.archives:
        assert archive.read(obj, refresh=True) == snapshot.cold[obj["id"]]
    prepared = json.loads(archive.store.read(result["prepared_receipt_key"]))
    assert prepared["original_state"] == snapshot.state
    with repo.connect() as con:
        assert (
            con.execute(
                "SELECT status FROM archives WHERE id=?", (snapshot.archives[0]["id"],)
            ).fetchone()[0]
            == "superseded"
        )


@pytest.mark.parametrize(
    "defect", ("missing", "revision", "provider", "duplicates", "floor", "before_image")
)
def test_activation_rejects_partial_or_ambiguous_replacements(system, defect):
    repo, archive, rows = system
    snapshot = capture(repo, archive, "FPT")
    floor = FLOOR
    if defect == "missing":
        rows = rows[1:]
    elif defect == "revision":
        rows[0] = replace(rows[0], revision="old")
    elif defect == "provider":
        rows[0] = replace(rows[0], provider="vps")
    elif defect == "duplicates":
        rows.append(rows[0])
    elif defect == "floor":
        floor += 1
    else:
        snapshot.cold.clear()
    with pytest.raises(DataError):
        publish(repo, archive, snapshot, rows, floor, execute=True)
    assert repo.read("vn", "FPT", "1m") == snapshot.hot
    assert repo.state("vn", "FPT", "1m") == snapshot.state


@pytest.mark.parametrize("change", ("hot", "archive"))
def test_activation_refuses_concurrent_changes_without_overwriting_them(
    system, monkeypatch, change
):
    repo, archive, rows = system
    snapshot = capture(repo, archive, "FPT")
    prepare = archive.prepare
    changed = False

    def race(values):
        nonlocal changed
        obj = prepare(values)
        if not changed:
            changed = True
            if change == "hot":
                repo.put([replace(snapshot.hot[0], close=102)])
            else:
                with repo.connect() as con:
                    con.execute(
                        "UPDATE archives SET status='pending_repair' WHERE id=?",
                        (snapshot.archives[0]["id"],),
                    )
        return obj

    monkeypatch.setattr(archive, "prepare", race)
    with pytest.raises(DataError, match="changed during"):
        publish(repo, archive, snapshot, rows, FLOOR, execute=True)
    assert repo.state("vn", "FPT", "1m") == snapshot.state
    assert not any(o["revision"] == "new" for o in repo.archives())
    if change == "hot":
        assert repo.read("vn", "FPT", "1m")[0].close == 102


@pytest.mark.parametrize("worker", ("live", "job"))
def test_activation_refuses_active_workers(system, worker):
    repo, archive, rows = system
    if worker == "live":
        assert repo.live_claim("vn", "FPT", "1m", "test", lease=300)
    else:
        repo.queue("vn", "FPT", "1m", "bootstrap", FLOOR, "vps")
        assert repo.claim_job("test")
    snapshot = capture(repo, archive, "FPT")
    with pytest.raises(DataError, match="worker is active"):
        publish(repo, archive, snapshot, rows, FLOOR, execute=True)
    assert repo.read("vn", "FPT", "1m") == snapshot.hot


def test_activation_cancels_idle_jobs_without_deleting_staged_evidence(system):
    repo, archive, rows = system
    repo.queue("vn", "FPT", "1m", "bootstrap", FLOOR, "vps")
    job = repo.claim_job("test")
    repo.stage(
        job, [replace(rows[-1], provider="vps", revision=job["revision"])], rows[-1].time, "vps"
    )
    snapshot = capture(repo, archive, "FPT")
    publish(repo, archive, snapshot, rows, FLOOR, execute=True)
    with repo.connect() as con:
        assert (
            con.execute("SELECT status FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
            == "cancelled"
        )
        assert (
            con.execute("SELECT COUNT(*) FROM staging WHERE job_id=?", (job["id"],)).fetchone()[0]
            == 1
        )


def test_activation_does_not_bypass_history_markers(system):
    repo, archive, rows = system
    repo.record_history_gap("vn", "FPT", "1m", rows[0].time, rows[0].time, "unverified", {})
    snapshot = capture(repo, archive, "FPT")
    with pytest.raises(DataError, match="history markers"):
        publish(repo, archive, snapshot, rows, FLOOR, execute=True)
    assert repo.read("vn", "FPT", "1m") == snapshot.hot


def test_activation_invalidates_inflight_source_checks_and_clears_old_freshness(system):
    repo, archive, rows = system
    attempt = repo.start_source_check("vn", "FPT", "1m")
    snapshot = capture(repo, archive, "FPT")
    publish(repo, archive, snapshot, rows, FLOOR, execute=True)
    repo.fail_source_check("vn", "FPT", "1m", attempt, "stale failure")
    with repo.connect() as con:
        record = con.execute(
            "SELECT * FROM source_checks WHERE source='vn' AND symbol='FPT' AND interval='1m'"
        ).fetchone()
        assert record["outcome"] == "handoff_required"
        assert record["attempted_at_ns"] != attempt
        assert record["successful_at_ns"] is None and record["completed_rows"] is None
        assert (record["provider"], record["revision"]) == ("vci", "new")


def test_sql_failure_rolls_back_hot_rows_state_and_archive_pointers(system, monkeypatch):
    repo, archive, rows = system
    snapshot = capture(repo, archive, "FPT")

    def fail(con):
        raise DataError("injected SQL failure")

    monkeypatch.setattr(repo, "bump", fail)
    with pytest.raises(DataError, match="SQL failure"):
        publish(repo, archive, snapshot, rows, FLOOR, execute=True)
    assert repo.read("vn", "FPT", "1m") == snapshot.hot
    assert repo.state("vn", "FPT", "1m") == snapshot.state
    assert repo.archives() == snapshot.archives


def test_manifest_failure_reports_committed_state_without_replacing_it_again(system, monkeypatch):
    repo, archive, rows = system
    snapshot = capture(repo, archive, "FPT")

    def fail(objects):
        raise DataError("injected manifest failure")

    monkeypatch.setattr(archive, "manifest", fail)
    result = publish(repo, archive, snapshot, rows, FLOOR, execute=True)
    assert result["published"] and not result["manifest_published"]
    assert "manifest failure" in result["manifest_error"]
    assert History(repo, archive, archive.settings).read("vn", "FPT", "1m") == rows
