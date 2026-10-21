from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Event
from time import time

import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.calculations import (
    enhance,
    enhance_latest_many,
    enhance_many_selected,
    enhance_selected,
    moving_average,
)
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


@pytest.mark.parametrize("ema", [False, True])
def test_enhance_latest_many_can_limit_requested_periods(ema):
    contexts = {}
    closes = {}
    all_rows = {}
    for length in (20, 75, 250, 601):
        symbol = f"S{length}"
        rows = [candle(symbol, index) for index in range(length)]
        all_rows[symbol] = rows
        contexts[symbol] = rows[-2:]
        closes[symbol] = [row.close for row in rows]

    actual = enhance_latest_many(
        contexts,
        closes,
        "1D",
        ma=True,
        ema=ema,
        periods=(20, 100),
    )
    for symbol, rows in all_rows.items():
        expected = enhance(rows, "1D", ma=True, ema=ema)[-1]
        selected = actual[symbol][0]
        requested_keys = {"ma20", "ma20_score", "ma100", "ma100_score"}
        for key, value in expected.items():
            if key.startswith("ma") and key not in requested_keys:
                assert key not in selected
            else:
                assert selected[key] == value


@pytest.mark.parametrize("ema", [False, True])
def test_enhance_many_selected_matches_scalar_histories_across_batches(ema):
    contexts = {
        f"B{index}": [candle(f"B{index}", position) for position in range(601)]
        for index in range(19)
    }
    contexts.update(
        {
            "SHORT": [candle("SHORT", position) for position in range(17)],
            "EMPTY": [],
        }
    )
    selected = {
        symbol: range(max(0, len(rows) - 6), len(rows)) for symbol, rows in contexts.items()
    }

    actual = enhance_many_selected(contexts, selected, "1D", ma=True, ema=ema)

    for symbol, rows in contexts.items():
        if rows:
            indices = list(selected[symbol])
            expected = enhance_selected(rows, "1D", indices, ma=True, ema=ema)
            assert actual[symbol] == expected
        else:
            assert actual[symbol] == []


@pytest.mark.parametrize("ema", [False, True])
def test_sparse_numpy_trails_match_ordered_scalar_moving_averages(ema):
    symbol = "SPARSE"
    rows = [candle(symbol, index) for index in range(601)]
    indices = list(range(596, 601))
    actual = enhance_many_selected({symbol: rows}, {symbol: indices}, "1D", ma=True, ema=ema)[
        symbol
    ]
    references = {
        period: moving_average([row.close for row in rows], period, ema)
        for period in (10, 20, 50, 100, 200)
    }

    for index, result in zip(indices, actual, strict=True):
        for period, values in references.items():
            expected = values[index]
            if expected > 0:
                assert result[f"ma{period}"] == expected
                assert (
                    result[f"ma{period}_score"] == ((rows[index].close - expected) / expected) * 100
                )


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
        assert tail.closes.tolist() == [row.close for row in expected]
        assert tail.oldest_time == expected[0].time
        assert not tail.mixed_revision
        assert not tail.mixed_provider
        assert loaded.local[symbol] == expected[-2:]
    assert "MISSING" not in loaded.compact_tails
    assert loaded.local["MISSING"] == []


def test_compact_tail_batch_preserves_quality_flags_inside_selected_window(system):
    repo, history = system
    rows = [candle("FPT", index) for index in range(5)]
    rows.extend(candle("VCB", index) for index in range(5))
    repo.put(rows)
    with repo.connect() as con:
        con.execute(
            "UPDATE candles SET provider=? WHERE source=? AND symbol=? AND interval=? AND time=?",
            ("outside-window", "vn", "FPT", rows[1].interval, rows[1].time),
        )
        con.execute(
            "UPDATE candles SET revision=? WHERE source=? AND symbol=? AND interval=? AND time=?",
            ("r2", "vn", "FPT", rows[4].interval, rows[4].time),
        )
        con.execute(
            "UPDATE candles SET provider=? WHERE source=? AND symbol=? AND interval=? AND time=?",
            ("r2-provider", "vn", "VCB", rows[-1].interval, rows[-1].time),
        )

    loaded = history.data_loader.load_many("vn", ["FPT", "VCB"], "1D", limit=3, compact_tail=True)

    assert loaded.compact_tails["FPT"].mixed_revision
    assert not loaded.compact_tails["FPT"].mixed_provider
    assert not loaded.compact_tails["VCB"].mixed_revision
    assert loaded.compact_tails["VCB"].mixed_provider
    assert loaded.local["FPT"] == repo.read("vn", "FPT", "1D", limit=2)
    assert loaded.local["VCB"] == repo.read("vn", "VCB", "1D", limit=2)


def test_compact_tail_batch_handles_zero_limit_without_latest_rows(system):
    repo, history = system
    repo.put([candle("FPT", index) for index in range(3)])

    loaded = history.data_loader.load_many(
        "vn", ["FPT", "MISSING"], "1D", limit=0, compact_tail=True
    )

    assert loaded.compact_tails == {}
    assert loaded.local == {"FPT": [], "MISSING": []}


def test_compact_tail_batch_crosses_sqlite_symbol_chunk_boundary(system):
    repo, history = system
    symbols = [f"T{index:03}" for index in range(401)]
    repo.put([candle(symbol, index) for symbol in symbols for index in range(2)])

    loaded = history.data_loader.load_many(
        "vn", [*symbols[:200], "MISSING", *symbols[200:]], "1D", limit=2, compact_tail=True
    )

    assert len(loaded.compact_tails) == len(symbols)
    assert loaded.compact_tails[symbols[0]].closes.tolist() == [
        candle(symbols[0], i).close for i in range(2)
    ]
    assert loaded.compact_tails[symbols[-1]].closes.tolist() == [
        candle(symbols[-1], i).close for i in range(2)
    ]
    assert loaded.local[symbols[-1]] == repo.read("vn", symbols[-1], "1D", limit=2)
    assert "MISSING" not in loaded.compact_tails
    assert loaded.local["MISSING"] == []


def test_repository_connections_enable_large_sqlite_read_cache(system):
    repo, _history = system
    with repo.connect() as con:
        assert con.execute("PRAGMA cache_size").fetchone()[0] == -32768
        assert con.execute("PRAGMA mmap_size").fetchone()[0] >= 256 * 1024 * 1024


def test_loader_lru_refreshes_changed_series_and_keeps_epoch_cache(system, monkeypatch):
    repo, history = system
    repo.put([candle("FPT", index) for index in range(3)])
    loader = history.data_loader
    now = [100.0]
    real_read_tails = loader._read_compact_tails
    reads = 0

    monkeypatch.setattr("aipriceaction_api.data_loading.time.monotonic", lambda: now[0])

    def count_reads(*args):
        nonlocal reads
        reads += 1
        return real_read_tails(*args)

    monkeypatch.setattr(loader, "_read_compact_tails", count_reads)
    first = loader.load_many("vn", ["FPT"], "1D", limit=3, compact_tail=True)
    first.compact_tails["FPT"].closes[0] = -1
    second = loader.load_many("vn", ["FPT"], "1D", limit=3, compact_tail=True)

    assert reads == 1
    assert second.compact_tails["FPT"].closes.tolist() == [
        candle("FPT", index).close for index in range(3)
    ]

    with repo.connect() as con:
        repo.bump(con)
    third = loader.load_many("vn", ["FPT"], "1D", limit=3, compact_tail=True)

    assert reads == 1
    assert third.compact_tails["FPT"].closes.tolist() == [
        candle("FPT", index).close for index in range(3)
    ]

    repo.put([candle("FPT", 3)])
    fourth = loader.load_many("vn", ["FPT"], "1D", limit=3, compact_tail=True)

    assert reads == 2
    assert fourth.compact_tails["FPT"].closes.tolist() == [
        candle("FPT", index).close for index in range(1, 4)
    ]

    now[0] += loader._CACHE_TTL_SECONDS + 0.01
    fifth = loader.load_many("vn", ["FPT"], "1D", limit=3, compact_tail=True)

    assert reads == 3
    assert fifth.compact_tails["FPT"].closes.tolist() == [
        candle("FPT", index).close for index in range(1, 4)
    ]


def test_wide_loader_cache_reloads_only_series_with_new_version(system, monkeypatch):
    repo, history = system
    repo.put([candle(symbol, index) for symbol in ("FPT", "VCB") for index in range(4)])
    loader = history.data_loader
    real_load = loader._load_many
    loaded_symbols = []

    def count_loads(source, symbols, interval, **kwargs):
        loaded_symbols.append(tuple(symbols))
        return real_load(source, symbols, interval, **kwargs)

    monkeypatch.setattr(loader, "_load_many", count_loads)
    loader.load_many("vn", ["FPT", "VCB"], "1D", limit=4, compact_tail=True)
    repo.put([candle("FPT", 4)])

    refreshed = loader.load_many("vn", ["FPT", "VCB"], "1D", limit=4, compact_tail=True)
    assert loaded_symbols == [("FPT", "VCB"), ("FPT",)]
    assert refreshed.compact_tails["FPT"].closes.tolist() == [
        candle("FPT", index).close for index in range(1, 5)
    ]
    assert refreshed.compact_tails["VCB"].closes.tolist() == [
        candle("VCB", index).close for index in range(4)
    ]


def test_loader_reuses_wider_compact_tail_for_smaller_symbol_subset(system, monkeypatch):
    repo, history = system
    repo.put(
        [
            candle(symbol, index)
            for symbol, length in (("FPT", 17), ("VCB", 8), ("VIC", 5))
            for index in range(length)
        ]
    )
    loader = history.data_loader
    real_load = loader._load_many
    loads = 0

    def count_loads(*args, **kwargs):
        nonlocal loads
        loads += 1
        return real_load(*args, **kwargs)

    monkeypatch.setattr(loader, "_load_many", count_loads)
    loader.load_many("vn", ["FPT", "VCB", "VIC"], "1D", limit=11, compact_tail=True)
    subset = loader.load_many("vn", ["VCB"], "1D", limit=3, compact_tail=True)

    expected = repo.read("vn", "VCB", "1D", limit=3)
    assert loads == 1
    assert subset.local["VCB"] == expected[-2:]
    assert subset.compact_tails["VCB"].closes.tolist() == [row.close for row in expected]
    assert subset.compact_tails["VCB"].times.tolist() == [row.time for row in expected]
    assert subset.compact_tails["VCB"].oldest_time == expected[0].time
    assert set(subset.local) == {"VCB"}


def test_loader_reuses_compact_latest_rows_for_noncompact_two_bar_request(system, monkeypatch):
    repo, history = system
    repo.put([candle("FPT", index) for index in range(6)])
    loader = history.data_loader
    real_load = loader._load_many
    loads = 0

    def count_loads(*args, **kwargs):
        nonlocal loads
        loads += 1
        return real_load(*args, **kwargs)

    monkeypatch.setattr(loader, "_load_many", count_loads)
    loader.load_many("vn", ["FPT"], "1D", limit=6, compact_tail=True)
    latest = loader.load_many("vn", ["FPT"], "1D", limit=2)

    assert loads == 1
    assert latest.local["FPT"] == repo.read("vn", "FPT", "1D", limit=2)


def test_empty_startup_warmup_does_not_hide_first_market_rows(system):
    repo, history = system
    loader = history.data_loader

    empty = loader.load_many("vn", ["FPT"], "1D", limit=601, compact_tail=True)
    assert empty.compact_tails == {}
    assert not loader._cache

    repo.put([candle("FPT", index) for index in range(3)])
    populated = loader.load_many("vn", ["FPT"], "1D", limit=601, compact_tail=True)

    assert populated.compact_tails["FPT"].closes.tolist() == [
        candle("FPT", index).close for index in range(3)
    ]


def test_compatible_loader_hits_do_not_extend_original_cache_ttl(system, monkeypatch):
    repo, history = system
    repo.put([candle(symbol, index) for symbol in ("FPT", "VCB") for index in range(4)])
    loader = history.data_loader
    now = [100.0]
    real_load = loader._load_many
    loads = 0

    monkeypatch.setattr("aipriceaction_api.data_loading.time.monotonic", lambda: now[0])

    def count_loads(*args, **kwargs):
        nonlocal loads
        loads += 1
        return real_load(*args, **kwargs)

    monkeypatch.setattr(loader, "_load_many", count_loads)
    loader.load_many("vn", ["FPT", "VCB"], "1D", limit=4, compact_tail=True)
    now[0] = 159.0
    loader.load_many("vn", ["FPT"], "1D", limit=2, compact_tail=True)
    repo.put([candle("FPT", 4)])
    now[0] = 160.01
    refreshed = loader.load_many("vn", ["FPT"], "1D", limit=2, compact_tail=True)

    assert loads == 2
    assert refreshed.compact_tails["FPT"].closes.tolist() == [
        candle("FPT", index).close for index in (3, 4)
    ]


def test_loader_cache_copy_isolates_nested_history_evidence(system):
    _repo, history = system
    loader = history.data_loader
    from aipriceaction_api.data_loading import LoadedHistory

    original = LoadedHistory(gaps={"FPT": [{"evidence": {"sources": ["capture"]}}]})
    copied = loader._copy_loaded(original)
    copied.gaps["FPT"][0]["evidence"]["sources"].append("mutated")

    assert original.gaps["FPT"][0]["evidence"]["sources"] == ["capture"]
    assert loader._estimated_size(original) > 0


def test_readonly_loader_reuses_cached_result_without_copying(system):
    repo, history = system
    rows = [candle("FPT", index) for index in range(4)]
    repo.put(rows)
    loader = history.data_loader

    # Prime the LRU, then query the exact same read through the internal
    # read-only path used by History and the analysis endpoints.
    loader.load_many("vn", ["FPT"], "1D", limit=4)
    first = loader.load_many("vn", ["FPT"], "1D", limit=4, copy_result=False)
    second = loader.load_many("vn", ["FPT"], "1D", limit=4, copy_result=False)

    assert first is second
    assert first.local["FPT"] == repo.read("vn", "FPT", "1D", limit=4)

    isolated = loader.load_many("vn", ["FPT"], "1D", limit=4)
    assert isolated is not first
    isolated.local["FPT"].clear()
    assert len(loader.load_many("vn", ["FPT"], "1D", limit=4).local["FPT"]) == 4


def test_read_many_returns_detached_candle_list_for_local_fast_path(system):
    repo, history = system
    rows = [candle("FPT", index) for index in range(4)]
    repo.put(rows)

    first = history.read_many("vn", ["FPT"], "1D", limit=3)["FPT"]
    first.clear()
    second = history.read_many("vn", ["FPT"], "1D", limit=3)["FPT"]

    assert second == repo.read("vn", "FPT", "1D", limit=3)


def test_loader_coalesces_identical_concurrent_reads(system, monkeypatch):
    _repo, history = system
    loader = history.data_loader
    entered = Event()
    joined = Event()
    release = Event()
    reads = 0
    real_read_tails = loader._read_compact_tails

    class TrackingFuture(Future):
        def result(self, *args, **kwargs):
            joined.set()
            return super().result(*args, **kwargs)

    def count_reads(*args):
        nonlocal reads
        reads += 1
        entered.set()
        assert release.wait(5)
        return real_read_tails(*args)

    monkeypatch.setattr("aipriceaction_api.data_loading.Future", TrackingFuture)
    monkeypatch.setattr(loader, "_read_compact_tails", count_reads)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(loader.load_many, "vn", ["FPT"], "1D", limit=3, compact_tail=True)
        assert entered.wait(5)
        second = pool.submit(loader.load_many, "vn", ["FPT"], "1D", limit=3, compact_tail=True)
        assert joined.wait(5)
        release.set()
        assert first.result(timeout=5).local == second.result(timeout=5).local

    assert reads == 1


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
def test_query_many_can_calculate_only_requested_ma_periods(system, ema):
    repo, history = system
    repo.put(
        [candle(symbol, index) for symbol in ("FPT", "VCB") for index in range(260)]
    )

    full = history.query_many(
        "vn",
        ["FPT", "VCB"],
        "1D",
        limit=1,
        ma=True,
        ema=ema,
        strict_source_intervals=True,
    )
    selected = history.query_many(
        "vn",
        ["FPT", "VCB"],
        "1D",
        limit=1,
        ma=True,
        ema=ema,
        strict_source_intervals=True,
        periods=(20,),
    )

    for symbol in ("FPT", "VCB"):
        expected = {
            key: value
            for key, value in full[symbol][0].items()
            if not key.startswith("ma") or key in {"ma20", "ma20_score"}
        }
        assert selected[symbol] == [expected]


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
@pytest.mark.parametrize(
    ("column", "value"), [("revision", "older-revision"), ("provider", "older-provider")]
)
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
