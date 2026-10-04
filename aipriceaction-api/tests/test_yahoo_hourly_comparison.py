import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest

from aipriceaction_api.domain import Candle


@pytest.fixture
def compare_native(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root))
    spec = importlib.util.spec_from_file_location(
        "hourly_check", root / "scripts/check_yahoo_hourly.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.native_comparison


def row(volume=100):
    return Candle("yahoo", "NVDA", "1h", 1735819200, 100, 101, 99, 100, volume)


def test_native_diagnostic_preserves_exact_float_noise_and_material_volume(compare_native):
    original = row()
    incoming = replace(original, open=100 + 1e-10, volume=101)
    result = compare_native([original], [incoming])
    assert result["changed"][0]["fields"] == {
        "open": [100, incoming.open],
        "volume": [100, 101],
    }
    assert result["material_changed"][0]["fields"] == {"volume": [100, 101]}
    assert incoming.open != original.open  # Neither record was rounded or modified.


def test_native_comparison_uses_absolute_price_tolerance(compare_native):
    original = replace(row(), open=1e9, high=1e9 + 1, low=1e9 - 1, close=1e9)
    incoming = replace(original, close=original.close + 0.01)
    result = compare_native([original], [incoming])
    assert result["material_changed"][0]["fields"] == {"close": [original.close, incoming.close]}


def test_missing_flat_zero_volume_observation_still_counts_as_missing(compare_native):
    original = replace(row(volume=0), open=100, high=100, low=100, close=100)
    result = compare_native([original], [])
    assert result["missing"] == [original.time]
    assert result["missing_flat_zero_volume_observations"] == [original.time]
    assert result["material_changed"] == []
