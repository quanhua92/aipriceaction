from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pytest

from aipriceaction_api.calculations import enhance_latest_many
from aipriceaction_api.domain import Candle
from aipriceaction_api.indicator_cache import IndicatorSnapshot, LatestIndicatorCache


def candle(symbol, index, close=None):
    stamp = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp()) + index * 86400
    value = close if close is not None else 50.0 + index * 0.17 + (index % 7) * 0.13
    return Candle(
        "vn",
        symbol,
        "1D",
        stamp,
        value - 0.5,
        value + 1.0,
        value - 1.0,
        value,
        1000 + index,
        "test",
        "r1",
        stamp,
    )


def snapshot(symbol, rows, version):
    tail = SimpleNamespace(
        times=np.asarray([row.time for row in rows], dtype=np.int64),
        closes=np.asarray([row.close for row in rows], dtype=np.float64),
    )
    return IndicatorSnapshot.from_loaded("vn", symbol, "1D", version, tail, rows[-2:])


@pytest.mark.parametrize("ema", [False, True])
def test_version_marker_refreshes_only_changed_series(monkeypatch, ema):
    import aipriceaction_api.indicator_cache as module

    calls = []
    original = module.enhance_latest_many

    def counted(contexts, closes, interval, **kwargs):
        calls.append(tuple(contexts))
        return original(contexts, closes, interval, **kwargs)

    monkeypatch.setattr(module, "enhance_latest_many", counted)
    cache = LatestIndicatorCache()
    a_rows = [candle("AAA", i) for i in range(601)]
    b_rows = [candle("BBB", i) for i in range(601)]
    a_v1, b_v1 = snapshot("AAA", a_rows, 1), snapshot("BBB", b_rows, 1)

    initial = cache.resolve([a_v1, b_v1], ema=ema)
    assert calls == [("AAA", "BBB")]
    assert initial["AAA"] == enhance_latest_many(
        {"AAA": a_rows[-2:]},
        {"AAA": [row.close for row in a_rows]},
        "1D",
        ma=True,
        ema=ema,
    )["AAA"]

    # Same SQLite series versions are a hot cache hit: no indicator pass.
    assert cache.resolve([a_v1, b_v1], ema=ema) == initial
    assert calls == [("AAA", "BBB")]

    # A new candle advances only AAA's version. Its updated snapshot replaces
    # that entry while BBB remains a cache hit.
    a_rows.append(candle("AAA", 601))
    a_v2 = snapshot("AAA", a_rows, 2)
    updated = cache.resolve([a_v2, b_v1], ema=ema)
    assert calls[-1] == ("AAA",)
    assert len(calls) == 2
    assert updated["AAA"] == enhance_latest_many(
        {"AAA": a_rows[-2:]},
        {"AAA": [row.close for row in a_rows]},
        "1D",
        ma=True,
        ema=ema,
    )["AAA"]
    assert updated["BBB"] == initial["BBB"]

    # Historical corrections also advance the version and refresh from the
    # corrected tail, so the latest result cannot retain stale indicators.
    a_rows[500] = candle("AAA", 500, close=130.25)
    corrected = cache.resolve([snapshot("AAA", a_rows, 3)], ema=ema)
    assert calls[-1] == ("AAA",)
    assert corrected["AAA"] == enhance_latest_many(
        {"AAA": a_rows[-2:]},
        {"AAA": [row.close for row in a_rows]},
        "1D",
        ma=True,
        ema=ema,
    )["AAA"]
    assert len(cache) == 2
    assert cache.size_bytes <= cache.max_bytes


def test_cache_invalidation_is_scoped_to_series():
    cache = LatestIndicatorCache()
    rows_a = [candle("AAA", i) for i in range(20)]
    rows_b = [candle("BBB", i) for i in range(20)]
    cache.resolve([snapshot("AAA", rows_a, 1), snapshot("BBB", rows_b, 1)])

    assert cache.invalidate("vn", "AAA", "1D") == 1
    assert len(cache) == 1
    assert cache.invalidate("crypto") == 0
    assert len(cache) == 1
