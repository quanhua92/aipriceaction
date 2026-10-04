import json
import sqlite3
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


@pytest.mark.parametrize("runtime_state", ["corrupt", "newer", "missing"])
def test_restore_recovers_without_opening_runtime_database(tmp_path, monkeypatch, runtime_state):
    runtime = tmp_path / "runtime.sqlite3"
    if runtime_state == "corrupt":
        runtime.write_bytes(b"damaged runtime database")
    elif runtime_state == "newer":
        with sqlite3.connect(runtime) as con:
            con.execute("PRAGMA user_version=999")
    original_runtime = runtime.read_bytes() if runtime.exists() else None
    settings = replace(Settings(), database=runtime)
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    original = Repository(tmp_path / "original.sqlite3")
    original.initialize()
    candle = Candle("vn", "FPT", "1D", parse_time("2026-10-02"), 100, 101, 99, 100, 1000)
    original.put([candle])
    key = "12345678-1234-1234-1234-123456789abc"
    sync = original.sync(key, "test-secret", {"watchlist": ["FPT"]}, write=True)
    backup = tmp_path / "backup ?# snapshot.sqlite3"
    original.backup(backup)
    source_bytes = backup.read_bytes()
    destination = tmp_path / "recovery" / "restored.sqlite3"
    assert main(["restore", str(backup), "--destination", str(destination)]) == 0
    restored = Repository(destination)
    assert restored.read("vn", "FPT", "1D") == original.read("vn", "FPT", "1D")
    assert restored.sync(key, "test-secret") == sync
    assert restored.epoch() == original.epoch()
    assert backup.read_bytes() == source_bytes
    assert (runtime.read_bytes() if runtime.exists() else None) == original_runtime


@pytest.mark.parametrize("invalid_source", ["missing", "corrupt"])
def test_restore_invalid_backup_leaves_no_destination_or_runtime(
    tmp_path, monkeypatch, capsys, invalid_source
):
    runtime = tmp_path / "runtime.sqlite3"
    monkeypatch.setattr(
        Settings, "from_env", classmethod(lambda cls: replace(Settings(), database=runtime))
    )
    source = tmp_path / "source.sqlite3"
    if invalid_source == "corrupt":
        source.write_bytes(b"invalid backup")
    destination = tmp_path / "restored.sqlite3"
    assert main(["restore", str(source), "--destination", str(destination)]) == 1
    assert "error" in json.loads(capsys.readouterr().out)
    assert not runtime.exists() and not destination.exists()
    assert source.exists() == (invalid_source == "corrupt")


@pytest.mark.parametrize("target", ["existing", "runtime"])
def test_restore_refuses_existing_or_configured_destination(tmp_path, monkeypatch, target):
    runtime = tmp_path / "runtime.sqlite3"
    monkeypatch.setattr(
        Settings, "from_env", classmethod(lambda cls: replace(Settings(), database=runtime))
    )
    source = tmp_path / "backup.sqlite3"
    with sqlite3.connect(source) as con:
        con.execute("CREATE TABLE preserved(value TEXT)")
        con.execute("INSERT INTO preserved VALUES ('original')")
    original = source.read_bytes()
    destination = tmp_path / "existing.sqlite3" if target == "existing" else runtime
    if target == "existing":
        destination.write_bytes(b"existing data")
    assert main(["restore", str(source), "--destination", str(destination)]) == 1
    assert source.read_bytes() == original
    assert not runtime.exists()
    if target == "existing":
        assert destination.read_bytes() == b"existing data"


@pytest.mark.parametrize("execute", [False, True])
def test_scoped_archive_cli_preserves_other_markets_intervals_and_recent_rows(
    tmp_path, monkeypatch, capsys, execute
):
    settings = replace(
        Settings(),
        database=tmp_path / "db",
        archive_backend="filesystem",
        object_dir=tmp_path / "objects",
        cache_dir=tmp_path / "cache",
    )
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    floor = parse_time("2021-01-01")
    monkeypatch.setattr("aipriceaction_api.archive.cutoff", lambda years, now=None: floor)
    repo = Repository(settings.database)
    repo.initialize()
    identities = [
        ("crypto", "BTCUSDT", "1D"),
        ("crypto", "BTCUSDT", "1h"),
        ("crypto", "BTCUSDT", "1m"),
        ("crypto", "ETHUSDT", "1m"),
        ("vn", "FPT", "1m"),
    ]
    for source, symbol, iv in identities:
        repo.put(
            [
                Candle(source, symbol, iv, stamp, 100, 101, 99, 100, 1000)
                for stamp in (floor - 86400, floor)
            ]
        )
    original = {ident: repo.read(*ident) for ident in identities}
    archive = Archive(repo, settings)
    assert len(archive.eligible()) == 5
    args = [
        "archive",
        "--source",
        "crypto",
        "--symbol",
        "btcusdt",
        "--symbol",
        "ethusdt",
        "--interval",
        "1m",
    ]
    if execute:
        args += ["--execute", "--prune"]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    planned = result["published" if execute else "partitions"]
    assert {(row["source"], row["symbol"], row["interval"]) for row in planned} == {
        ("crypto", "BTCUSDT", "1m"),
        ("crypto", "ETHUSDT", "1m"),
    }
    history = History(repo, archive, settings)
    for ident in identities:
        selected = ident[0] == "crypto" and ident[2] == "1m"
        assert repo.read(*ident) == (
            original[ident][1:] if execute and selected else original[ident]
        )
        assert history.read(*ident) == original[ident]
        assert len(repo.archives(*ident)) == (1 if execute and selected else 0)


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
