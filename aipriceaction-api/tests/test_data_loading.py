from dataclasses import replace
from datetime import UTC, datetime

import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.calculations import enhance, enhance_selected
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle
from aipriceaction_api.history import History
from aipriceaction_api.storage import Repository


def candle(symbol, index, *, revision="r1", provider="test"):
    stamp = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp()) + index * 86400
    close = 50.0 + index * 0.17 + (index % 7) * 0.13
    return Candle(
        "vn",
        symbol,
        "1D",
        stamp,
        close - 0.5,
        close + 1.0,
        close - 1.0,
        close,
        1000 + index,
        provider,
        revision,
        stamp,
    )


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
    return repo, History(repo, archive, settings)


@pytest.mark.parametrize("length", [0, 1, 4, 5, 9, 10, 49, 50, 199, 200, 201, 601])
@pytest.mark.parametrize("ma,ema", [(False, False), (True, False), (True, True)])
def test_enhance_selected_matches_full_result(length, ma, ema):
    rows = [candle("FPT", index) for index in range(length)]
    indices = sorted({index for index in (0, length // 2, length - 1) if 0 <= index < length})
    expected = [enhance(rows, "1D", ma, ema)[index] for index in indices]
    assert enhance_selected(rows, "1D", indices, ma, ema) == expected


def test_read_many_matches_single_reads_for_local_and_archived_series(system):
    repo, history = system
    rows = [candle(symbol, index) for symbol in ("FPT", "VCB") for index in range(8)]
    repo.put(rows)
    repo.put([candle("VIC", index) for index in range(4)])
    archive = history.archive.publish(repo.read("vn", "FPT", "1D", end=rows[3].time), prune=True)
    assert archive["symbol"] == "FPT"

    batched = history.read_many("vn", ["FPT", "VCB", "VIC", "MISSING"], "1D", limit=5)
    for symbol in ("FPT", "VCB", "VIC", "MISSING"):
        assert batched[symbol] == history.read("vn", symbol, "1D", limit=5)


@pytest.mark.parametrize("ma,ema", [(False, False), (True, False), (True, True)])
def test_query_many_matches_single_latest_queries(system, ma, ema):
    repo, history = system
    repo.put(
        [candle(symbol, index) for symbol in ("FPT", "VCB") for index in range(260)]
        + [candle("VIC", index) for index in range(30)]
    )

    batched = history.query_many(
        "vn",
        ["FPT", "VCB", "VIC", "MISSING"],
        "1D",
        limit=1,
        ma=ma,
        ema=ema,
        strict_source_intervals=True,
    )
    for symbol in ("FPT", "VCB", "VIC", "MISSING"):
        assert batched[symbol] == history.query(
            "vn",
            symbol,
            "1D",
            limit=1,
            ma=ma,
            ema=ema,
            strict_source_intervals=True,
        )
