import asyncio
import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, parse_time
from aipriceaction_api.storage import Repository
from scripts import check_crypto_worker, check_native_refresh, check_retention_rollover


@pytest.mark.parametrize(
    "script", [check_crypto_worker, check_native_refresh, check_retention_rollover]
)
@pytest.mark.parametrize("fail", [False, True])
def test_test_databases_removed_but_reports_retained(tmp_path, monkeypatch, script, fail):
    args = SimpleNamespace(output=tmp_path / "reports")
    created = []

    def work(args, databases):
        args.output.mkdir()
        (args.output / "report.json").write_text('{"scope":"test"}')
        path = databases / "before.sqlite3"
        with sqlite3.connect(path) as con:
            con.execute("CREATE TABLE candles(time INTEGER)")
        (databases / "before.sqlite3-wal").write_bytes(b"temporary journal")
        created.append(databases)
        if fail:
            raise RuntimeError("failed validation")

    async def async_work(args, databases):
        work(args, databases)

    asynchronous = script is not check_retention_rollover
    monkeypatch.setattr(script, "_run", async_work if asynchronous else work)

    def invoke():
        return asyncio.run(script.run(args)) if asynchronous else script.run(args)

    if fail:
        with pytest.raises(RuntimeError, match="failed validation"):
            invoke()
    else:
        invoke()
    assert created and not created[0].exists()
    assert (args.output / "report.json").is_file()
    assert not list(args.output.rglob("*.sqlite3*"))


def test_real_rollover_preserves_original_and_leaves_only_report(tmp_path, monkeypatch):
    settings = replace(
        Settings(),
        database=tmp_path / "live.sqlite3",
        archive_backend="filesystem",
        object_dir=tmp_path / "canonical-objects",
        cache_dir=tmp_path / "cache",
    )
    repo = Repository(settings.database)
    repo.initialize()
    repo.put(
        [
            Candle(
                "crypto",
                "BTCUSDT",
                "1D",
                parse_time("2020-01-01"),
                100,
                101,
                99,
                100,
                10,
                "binance",
                "initial",
            )
        ]
    )
    before = repo.read("crypto", "BTCUSDT", "1D")
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    output = tmp_path / "review"
    check_retention_rollover.run(
        SimpleNamespace(
            output=output,
            source="crypto",
            cutoff_date="2026-10-04",
        )
    )
    report = json.loads((output / "report.json").read_text())
    assert report["passed"] and report["expired_rows"] == 1
    assert repo.read("crypto", "BTCUSDT", "1D") == before
    assert [p.name for p in output.iterdir()] == ["report.json"]
    assert not settings.object_dir.exists()
