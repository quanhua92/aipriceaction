import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, date_bounds
from aipriceaction_api.providers import Page
from aipriceaction_api.storage import Repository
from scripts import activate_verified_vci_minutes as activation
from scripts.captured_vci_activation_inputs import fresh_controls, http_controls


@pytest.mark.parametrize("failure", [None, "price", "volume", "missing", "provider"])
def test_fresh_controls_require_every_exact_original_candle(failure):
    first = date_bounds("2025-10-06") + 3 * 3600
    rows = [
        Candle("vn", "FPT", "1m", first + i * 60, 10, 11, 9, 10, 100, "vci") for i in range(2000)
    ]

    class Feed:
        async def page(self, source, symbol, interval, before, **kwargs):
            selected = [row for row in rows if row.time < before][-1000:]
            if failure == "price":
                selected[0] = replace(selected[0], close=10.5)
            elif failure == "volume":
                selected[0] = replace(selected[0], volume=101)
            elif failure == "missing":
                selected.pop(0)
            return Page(selected, "vps" if failure == "provider" else "vci")

    if failure:
        with pytest.raises(DataError):
            asyncio.run(fresh_controls(Feed(), rows))
    else:
        result = asyncio.run(fresh_controls(Feed(), rows))
        assert [row["control"] for row in result] == ["oldest", "middle"]
        assert all(row["rows"] == 1000 for row in result)


def test_actual_http_raw_sma_ema_routes_use_the_scoped_candidate(tmp_path, monkeypatch):
    settings = replace(
        Settings(),
        database=tmp_path / "candidate.sqlite3",
        archive_backend="filesystem",
        object_dir=tmp_path / "objects",
        cache_dir=tmp_path / "cache",
    )
    repo = Repository(settings.database)
    repo.initialize()
    floor = date_bounds("2025-10-04")
    monkeypatch.setattr("scripts.captured_vci_activation_inputs.cutoff", lambda _: floor)
    rows = [
        Candle("vn", "FPT", "1m", date_bounds(day) + 3 * 3600 + i * 60, 10, 11, 9, 10, 100, "vci")
        for day in ("2025-10-03", "2025-10-06")
        for i in range(40)
    ]
    repo.put(rows)
    checks = http_controls(settings, "FPT")
    assert len(checks) == 6 and all(row["rows"] for row in checks)
    assert not list(tmp_path.rglob("*golden*"))


def test_activation_failure_cleans_temporary_workspace(tmp_path, monkeypatch):
    seen = []

    async def fail(args, temporary):
        seen.append(temporary)
        (temporary / "candidate.sqlite3").write_bytes(b"temporary")
        raise DataError("injected preflight failure")

    monkeypatch.setattr(activation, "_run", fail)
    with pytest.raises(DataError):
        asyncio.run(activation.run(SimpleNamespace()))
    assert seen and not seen[0].exists()
