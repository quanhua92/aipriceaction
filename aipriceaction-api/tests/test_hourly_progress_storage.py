import asyncio
import json
from argparse import Namespace
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, cutoff, date_bounds
from aipriceaction_api.storage import Repository
from scripts import publish_vn_hourly_progress as progress


@pytest.mark.parametrize("failure", [None, "count", "values", "backup"])
def test_verification_copy_cleanup_preserves_operational_rollback(tmp_path, monkeypatch, failure):
    repo = Repository(tmp_path / "live.sqlite3")
    repo.initialize()
    candle = Candle("vn", "FPT", "1h", date_bounds("2024-04-17") + 7200, 10.0, 11.0, 9.0, 10.5, 100)
    repo.put([candle])
    rollback = tmp_path / "operational-rollback.sqlite3"
    repo.backup(rollback)
    rollback_bytes = rollback.read_bytes()
    original_rows = repo.read("vn", "FPT", "1h")
    paths = []
    original_backup = repo.backup

    def backup(path):
        paths.append(path)
        if failure == "backup":
            path.write_bytes(b"partial")
            path.with_name(path.name + "-wal").write_bytes(b"partial WAL")
            raise OSError("Injected backup failure")
        original_backup(path)
        if failure == "values":
            copied = Repository(path)
            copied.put([replace(candle, volume=101)])

    monkeypatch.setattr(repo, "backup", backup)
    if failure:
        with pytest.raises((AssertionError, OSError)):
            progress.verify_temporary_backup(
                repo, [{"symbol": "FPT"}], 2 if failure == "count" else 1
            )
    else:
        result = progress.verify_temporary_backup(repo, [{"symbol": "FPT"}], 1)
        assert result["backup_quick_check"] == "ok"
        assert result["after_backup_bytes"] > 0
        assert len(result["after_backup_sha256"]) == 64
        assert not result["after_backup_retained"]
        assert result["temporary_backup_removed"]
        assert "after_backup" not in result
    assert len(paths) == 1
    assert not paths[0].parent.exists()
    assert rollback.read_bytes() == rollback_bytes
    assert repo.read("vn", "FPT", "1h") == original_rows
    assert not (tmp_path / "after.sqlite3").exists()


@pytest.mark.parametrize("execute", [False, True])
@pytest.mark.parametrize("expired_staging", [False, True])
def test_empty_hourly_batch_creates_only_report_without_backup_or_provider(
    tmp_path, monkeypatch, execute, expired_staging
):
    watchlist = tmp_path / "watchlist.json"
    watchlist.write_text('{"vn":["FPT"]}')
    settings = replace(
        Settings(),
        database=tmp_path / "live.sqlite3",
        watchlist=watchlist,
        s3_endpoint="http://127.0.0.1:9100",
    )
    repo = Repository(settings.database)
    repo.initialize()
    if expired_staging:
        floor = cutoff(settings.hourly_years)
        repo.queue("vn", "FPT", "1h", "bootstrap", floor - 86400, "dnse")
        job = repo.claim_job("setup")
        expired = Candle(
            "vn",
            "FPT",
            "1h",
            floor - 86400 + 7200,
            10.0,
            11.0,
            9.0,
            10.5,
            100,
            "dnse",
            job["revision"],
        )
        repo.stage(job, [expired], expired.time, "dnse")
        repo.put([replace(expired, time=floor + 7200)])
    original = settings.database.read_bytes()
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))

    def forbidden(*args, **kwargs):
        raise AssertionError("Empty batch created a backup or provider")

    monkeypatch.setattr(Repository, "backup", forbidden)
    monkeypatch.setattr(progress, "Providers", forbidden)
    monkeypatch.setattr(progress, "RecordingTransport", forbidden)
    output = tmp_path / "output"
    result = asyncio.run(
        progress.run(Namespace(allow_direct=False, execute=execute, symbol=None, output=output))
    )
    assert result["no_candidates"]
    assert result["dry_run"] == (not execute)
    assert result["captures"] == []
    assert [path.name for path in output.iterdir()] == ["report.json"]
    assert settings.database.read_bytes() == original


@pytest.mark.asyncio
async def test_real_local_publication_retains_rollback_but_not_verification_copy(
    tmp_path, monkeypatch
):
    watchlist = tmp_path / "watchlist.json"
    watchlist.write_text('{"vn":["FPT"]}')
    settings = replace(
        Settings(),
        database=tmp_path / "live.sqlite3",
        watchlist=watchlist,
        archive_backend="filesystem",
        object_dir=tmp_path / "objects",
        cache_dir=tmp_path / "cache",
        s3_endpoint="http://127.0.0.1:9100",
    )
    repo = Repository(settings.database)
    repo.initialize()
    first = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=80
    )
    days = [first + timedelta(days=index) for index in range(60)]
    days = [day for day in days if day.weekday() < 5][:40]
    repo.queue("vn", "FPT", "1h", "bootstrap", int(first.timestamp()), "dnse")
    job = repo.claim_job("setup")
    rows = [
        Candle(
            "vn",
            "FPT",
            "1h",
            int(day.timestamp()) + hour * 3600,
            100.0,
            101.0,
            99.0,
            100.0,
            10,
            "dnse",
            job["revision"],
        )
        for day in days
        for hour in (2, 3, 4, 6, 7)
    ]
    repo.stage(job, rows, rows[0].time, "dnse")
    repo.put(rows[-150:])
    original = repo.read("vn", "FPT", "1h")
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))

    class Provider:
        def __init__(self, config, transport):
            self.settings = config
            self.closed = False

        async def page(self, source, symbol, interval, **kwargs):
            from aipriceaction_api.providers import Page

            assert (source, symbol, interval, kwargs["provider"]) == ("vn", "FPT", "1h", "dnse")
            return Page(original, "dnse")

        async def close(self):
            self.closed = True

    monkeypatch.setattr(progress, "Providers", Provider)
    output = tmp_path / "output"
    result = await progress.run(
        Namespace(allow_direct=False, execute=True, symbol=None, output=output)
    )
    assert result["preservation_verified"]
    assert result["appended_rows"] == 50
    assert result["total_candles"] == 200
    assert result["temporary_backup_removed"]
    assert not result["after_backup_retained"]
    assert not (output / "after.sqlite3").exists()
    rollback = Repository(output / "before.sqlite3")
    assert rollback.read("vn", "FPT", "1h") == original
    assert repo.read("vn", "FPT", "1h")[-150:] == original
    assert json.loads((output / "report.json").read_text())["temporary_backup_removed"]
