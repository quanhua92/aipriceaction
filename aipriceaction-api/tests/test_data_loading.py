from dataclasses import replace
from datetime import UTC, datetime
from time import time

import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.calculations import enhance, enhance_latest_many, enhance_selected
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError
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


@pytest.mark.parametrize("ema", [False, True])
def test_enhance_latest_many_matches_scalar_calculation(ema):
    contexts = {}
    closes = {}
    all_rows = {}
    for length in (1, 4, 9, 10, 49, 50, 99, 100, 199, 200, 201, 601):
        symbol = f"T{length}"
        rows = [candle(symbol, index) for index in range(length)]
        all_rows[symbol] = rows
        contexts[symbol] = rows[-2:]
        closes[symbol] = [row.close for row in rows]

    actual = enhance_latest_many(contexts, closes, "1D", ma=True, ema=ema)
    for symbol, rows in all_rows.items():
        assert actual[symbol] == [enhance(rows, "1D", ma=True, ema=ema)[-1]]


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


def test_compact_tail_batches_warmup_closes_and_latest_candles(system):
    repo, history = system
    rows = [
        candle(symbol, index)
        for symbol, length in (("FPT", 17), ("VCB", 8))
        for index in range(length)
    ]
    repo.put(rows)

    loaded = history.data_loader.load_many(
        "vn",
        ["FPT", "VCB", "MISSING"],
        "1D",
        limit=11,
        compact_tail=True,
    )

    for symbol in ("FPT", "VCB"):
        expected = repo.read("vn", symbol, "1D", limit=11)
        tail = loaded.compact_tails[symbol]
        assert tail.closes == [row.close for row in expected]
        assert tail.oldest_time == expected[0].time
        assert not tail.mixed_revision
        assert not tail.mixed_provider
        assert loaded.local[symbol] == expected[-2:]
    assert "MISSING" not in loaded.compact_tails
    assert loaded.local["MISSING"] == []


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


@pytest.mark.parametrize("ema", [False, True])
def test_compact_latest_ma_matches_single_queries_and_archive_fallback(system, ema):
    repo, history = system
    repo.put([candle("FPT", index) for index in range(700)])
    repo.put([candle("VCB", index) for index in range(601)])
    history.archive.publish(repo.read("vn", "FPT", "1D", limit=650, forward=True), prune=True)

    batched = history.query_many(
        "vn",
        ["FPT", "VCB", "MISSING"],
        "1D",
        limit=1,
        ma=True,
        ema=ema,
        strict_source_intervals=True,
        ignore_pending_repairs=True,
        return_exceptions=True,
    )
    for symbol in ("FPT", "VCB", "MISSING"):
        assert batched[symbol] == history.query(
            "vn",
            symbol,
            "1D",
            limit=1,
            ma=True,
            ema=ema,
            strict_source_intervals=True,
            ignore_pending_repairs=True,
        )


@pytest.mark.parametrize("ema", [False, True])
@pytest.mark.parametrize("ignore_pending_repairs", [False, True])
def test_compact_latest_ma_preserves_gaps_after_latest(system, ema, ignore_pending_repairs):
    repo, history = system
    rows = [candle("FPT", index) for index in range(250)]
    repo.put(rows)
    repo.record_history_gap(
        "vn", "FPT", "1D", rows[-1].time + 1, int(time()), "Unverified recent history", {}
    )

    with pytest.raises(DataError) as expected:
        history.query(
            "vn",
            "FPT",
            "1D",
            limit=1,
            ma=True,
            ema=ema,
            redis=False,
            strict_source_intervals=True,
            ignore_pending_repairs=ignore_pending_repairs,
        )
    actual = history.query_many(
        "vn",
        ["FPT"],
        "1D",
        limit=1,
        ma=True,
        ema=ema,
        redis=False,
        strict_source_intervals=True,
        ignore_pending_repairs=ignore_pending_repairs,
        return_exceptions=True,
    )["FPT"]
    assert isinstance(actual, DataError)
    assert str(actual) == str(expected.value)


@pytest.mark.parametrize("ema", [False, True])
def test_compact_latest_ma_preserves_adaptive_warmup_around_gaps(system, ema):
    repo, history = system
    rows = [candle("FPT", index) for index in range(250)]
    repo.put(rows)
    repo.record_history_gap(
        "vn", "FPT", "1D", rows[150].time, rows[150].time, "Unverified warmup history", {}
    )

    expected = history.query(
        "vn",
        "FPT",
        "1D",
        limit=1,
        ma=True,
        ema=ema,
        redis=False,
        strict_source_intervals=True,
        ignore_pending_repairs=True,
    )
    actual = history.query_many(
        "vn",
        ["FPT"],
        "1D",
        limit=1,
        ma=True,
        ema=ema,
        redis=False,
        strict_source_intervals=True,
        ignore_pending_repairs=True,
        return_exceptions=True,
    )["FPT"]
    assert actual == expected


@pytest.mark.parametrize("ema", [False, True])
@pytest.mark.parametrize(("column", "value"), [("revision", "older-revision"), ("provider", "older-provider")])
def test_compact_latest_ma_preserves_adaptive_warmup_across_basis_outliers(
    system, ema, column, value
):
    repo, history = system
    rows = [candle("FPT", index) for index in range(250)]
    repo.put(rows)
    with repo.connect() as con:
        con.execute(
            f"UPDATE candles SET {column}=? WHERE source='vn' AND symbol='FPT' AND interval='1D' AND time=?",
            (value, rows[150].time),
        )

    expected = history.query(
        "vn",
        "FPT",
        "1D",
        limit=1,
        ma=True,
        ema=ema,
        redis=False,
        strict_source_intervals=True,
        ignore_pending_repairs=True,
    )
    actual = history.query_many(
        "vn",
        ["FPT"],
        "1D",
        limit=1,
        ma=True,
        ema=ema,
        redis=False,
        strict_source_intervals=True,
        ignore_pending_repairs=True,
        return_exceptions=True,
    )["FPT"]
    assert actual == expected
