import gzip
import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, parse_time
from aipriceaction_api.history import History
from aipriceaction_api.storage import Repository


@pytest.mark.parametrize("ema", [False, True])
def test_captured_legacy_indicators_survive_archive_boundary(tmp_path, ema):
    with gzip.open(Path(__file__).parent / "fixtures/legacy_fpt.json.gz", "rt") as file:
        fixture = json.load(file)
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
    candles = [
        Candle(
            "vn",
            "FPT",
            "1D",
            parse_time(row["time"]),
            *(row[k] for k in ("open", "high", "low", "close", "volume")),
            "legacy-fixture",
        )
        for row in fixture["rows"]
    ]
    repo.put(candles)
    archive.publish(repo.read("vn", "FPT", "1D", end=candles[399].time), prune=True)
    actual = History(repo, archive, settings).query("vn", "FPT", "1D", limit=5, ema=ema)
    expected = fixture["expected"]["ema" if ema else "sma"]
    assert len(actual) == len(expected)
    for old, new in zip(expected, actual, strict=True):
        assert set(old) == set(new)
        for key in old:
            if isinstance(old[key], (int, float)):
                assert math.isclose(old[key], new[key], rel_tol=1e-8, abs_tol=1e-7), (
                    key,
                    old[key],
                    new[key],
                )
            else:
                assert old[key] == new[key]
