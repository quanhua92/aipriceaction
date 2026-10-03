import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.cli import main
from aipriceaction_api.config import ROOT, Settings
from aipriceaction_api.domain import Candle, DataError, parse_time
from aipriceaction_api.history import History
from aipriceaction_api.importing import import_csv
from aipriceaction_api.storage import Repository


def test_duplicate_imports_and_concurrent_updates_preserve_identity(tmp_path):
    repo = Repository(tmp_path / "db")
    repo.initialize()
    csv = tmp_path / "candles.csv"
    csv.write_text("symbol,time,open,high,low,close,volume\nTEST,2026-01-01,100,101,99,100,1000\n")
    assert import_csv(repo, csv, "vn", "TEST", "1D") == 1
    assert import_csv(repo, csv, "vn", "TEST", "1D") == 1
    row = repo.read("vn", "TEST", "1D")[0]

    def concurrent(_):
        repo.put([row])
        return repo.read("vn", "TEST", "1D", limit=1)[0].close

    with ThreadPoolExecutor(max_workers=6) as executor:
        assert list(executor.map(concurrent, range(48))) == [100] * 48
    assert len(repo.read("vn", "TEST", "1D")) == 1


def test_configuration_relative_paths_stay_inside_new_project(monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", "./data/other.sqlite3")
    assert Settings.from_env().database == ROOT / "data/other.sqlite3"


def test_operational_cli_rejects_aggregate_intervals(tmp_path, capsys):
    assert (
        main(
            [
                "--database",
                str(tmp_path / "db"),
                "reconcile",
                "--source",
                "vn",
                "--symbol",
                "TEST",
                "--interval",
                "1M",
            ]
        )
        == 1
    )
    assert "native 1D" in capsys.readouterr().out


def test_publish_index_retries_failed_gap_metadata_without_changing_candles(
    tmp_path, monkeypatch, capsys
):
    settings = replace(
        Settings(),
        database=tmp_path / "db",
        archive_backend="filesystem",
        object_dir=tmp_path / "objects",
        cache_dir=tmp_path / "cache",
    )
    repo = Repository(settings.database)
    repo.initialize()
    repo.put([Candle("vn", "FPT", "1D", parse_time("2026-01-02"), 100, 101, 99, 100, 1000)])
    original = repo.read("vn", "FPT", "1D")
    state = repo.state("vn", "FPT", "1D")
    archive = Archive(repo, settings)
    archive.publish_metadata()
    pointer_key = f"{settings.s3_prefix}/LATEST.json"
    previous_pointer = archive.store.read(pointer_key)
    repo.record_history_gap(
        "vn",
        "FPT",
        "1D",
        parse_time("2019-01-01"),
        parse_time("2020-01-01") - 1,
        "Original CSV invalid",
        {"year": 2019},
    )
    put = archive.store.put

    def unavailable_pointer(key, path):
        if key == pointer_key:
            raise DataError("Object store temporarily unavailable")
        return put(key, path)

    monkeypatch.setattr(archive.store, "put", unavailable_pointer)
    with pytest.raises(DataError, match="temporarily unavailable"):
        archive.publish_metadata()
    assert archive.store.read(pointer_key) == previous_pointer
    for name, value in (
        ("ARCHIVE_BACKEND", "filesystem"),
        ("ARCHIVE_OBJECT_PATH", str(settings.object_dir)),
        ("ARCHIVE_CACHE_PATH", str(settings.cache_dir)),
    ):
        monkeypatch.setenv(name, value)
    assert main(["--database", str(settings.database), "publish-index"]) == 0
    assert json.loads(capsys.readouterr().out) == {"published_index": True}
    fresh = Repository(tmp_path / "index")
    fresh.initialize()
    assert Archive(fresh, settings).restore_index() == 0
    assert fresh.history_gaps() == repo.history_gaps()
    assert repo.read("vn", "FPT", "1D") == original
    assert repo.state("vn", "FPT", "1D") == state
    assert repo.archives() == [] and repo.status()["jobs"] == []
    assert not list(settings.object_dir.glob("**/*.parquet"))


def test_publish_index_cannot_overwrite_an_active_archive_writer(tmp_path, monkeypatch, capsys):
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
    archive.publish_metadata()
    key = f"{settings.s3_prefix}/LATEST.json"
    before = archive.store.read(key)
    assert repo.live_claim("vn", "__ARCHIVE_WRITER__", "1D", "other-writer", lease=3600)
    monkeypatch.setenv("ARCHIVE_BACKEND", "filesystem")
    monkeypatch.setenv("ARCHIVE_OBJECT_PATH", str(settings.object_dir))
    monkeypatch.setenv("ARCHIVE_CACHE_PATH", str(settings.cache_dir))
    assert main(["--database", str(settings.database), "publish-index"]) == 1
    assert "Another archive writer is active" in capsys.readouterr().out
    assert archive.store.read(key) == before
    repo.live_release("vn", "__ARCHIVE_WRITER__", "1D", "other-writer")


def test_reconcile_uses_verified_watchlist_history_start(tmp_path, monkeypatch, capsys):
    watchlist = tmp_path / "watchlist.json"
    watchlist.write_text('{"vn":[{"symbol":"VPL","history_start":"2025-05-13"}]}')
    monkeypatch.setenv("WATCHLIST_PATH", str(watchlist))
    database = tmp_path / "db"
    assert (
        main(
            [
                "--database",
                str(database),
                "reconcile",
                "--source",
                "vn",
                "--symbol",
                "VPL",
                "--interval",
                "1D",
                "--provider",
                "vps",
            ]
        )
        == 0
    )
    job = Repository(database).claim_job("test")
    assert job["floor"] == parse_time("2025-05-13")


def test_invalid_legacy_candles_are_rejected_without_partial_import(tmp_path):
    repo = Repository(tmp_path / "db")
    repo.initialize()
    csv = tmp_path / "bad.csv"
    csv.write_text(
        "time,open,high,low,close,volume\n2026-01-01,100,101,99,100,1000\n2026-01-02,100,101,99,110,1000\n"
    )
    with pytest.raises(DataError, match="OHLC range"):
        import_csv(repo, csv, "vn", "TEST", "1D")
    assert repo.read("vn", "TEST", "1D") == []


def test_sjc_quote_and_negative_futures_have_explicit_validation_rules():
    bar = Candle("sjc", "SJC-GOLD", "1D", parse_time("2026-01-01"), 98, 101, 99, 100, 1)
    assert bar.validate() is bar
    future = replace(bar, source="yahoo", symbol="CL=F", open=-30, high=-20, low=-40, close=-35)
    assert future.validate() is future
    with pytest.raises(DataError):
        replace(future, source="vn", symbol="TEST").validate()


@pytest.mark.parametrize("close", (98, 102))
def test_daily_futures_close_can_be_independent_of_traded_range(close):
    bar = Candle("yahoo", "GC=F", "1D", parse_time("2010-11-01"), 100, 101, 99, close, 40)
    assert bar.validate() is bar
    for changes in (
        {"interval": "1m"},
        {"interval": "1h"},
        {"symbol": "AAPL"},
        {"source": "vn"},
        {"source": "crypto"},
        {"source": "sjc", "symbol": "SJC-GOLD"},
        {"open": 102},
        {"high": 98},
        {"volume": -1},
        {"close": float("nan")},
    ):
        with pytest.raises(DataError):
            replace(bar, **changes).validate()


def test_futures_daily_archive_and_weekly_response_preserve_quote(tmp_path):
    settings = Settings(
        database=tmp_path / "db",
        archive_backend="filesystem",
        object_dir=tmp_path / "objects",
        cache_dir=tmp_path / "cache",
    )
    repo = Repository(settings.database)
    repo.initialize()
    # Captured Yahoo/legacy fields for 2010-11-01: preserve close below low.
    bar = Candle(
        "yahoo",
        "GC=F",
        "1D",
        parse_time("2010-11-01"),
        1360.300048828125,
        1360.300048828125,
        1350.699951171875,
        1350.199951171875,
        40,
        "yahoo",
        "settlement-quote",
    )
    repo.put([bar])
    stored = repo.read("yahoo", "GC=F", "1D")
    archive = Archive(repo, settings)
    obj = archive.publish(stored, prune=True)
    assert not repo.read("yahoo", "GC=F", "1D")
    assert archive.read(obj, refresh=True) == stored
    result = History(repo, archive, settings).query("yahoo", "GC=F", "1W", limit=1, ma=False)
    assert len(result) == 1
    assert {field: result[0][field] for field in ("open", "high", "low", "close", "volume")} == {
        field: getattr(bar, field) for field in ("open", "high", "low", "close", "volume")
    }
