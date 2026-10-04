from dataclasses import replace

import pytest

from aipriceaction_api.domain import Candle, DataError
from scripts.activate_verified_vci_minutes import daily_check


@pytest.fixture
def session():
    first = Candle("vn", "TPB", "1m", 1761700500, 100, 110, 90, 105, 10, "vci")
    last = replace(first, time=1761700560, open=105, high=120, low=100, close=115, volume=20)
    daily = replace(first, interval="1D", time=1761696000, high=120, close=115, volume=30)
    return [first, last], daily


def test_daily_check_groups_intraday_and_preserves_independent_gates(session):
    rows, daily = session
    # A daily feed's rounding/disagreement is recorded without changing minute
    # prices. Its volume must still agree exactly with the full minute session.
    peer = replace(daily, close=123)
    checks = daily_check(rows, [daily], {"vndirect": [peer]})
    assert checks[0]["dates"] == 1
    assert checks[0]["checked_prices"]
    assert checks[1]["checked_volume"]
    assert checks[1]["max_price_difference_vnd"] == 8


def test_daily_check_rejects_price_basis_disagreement(session):
    rows, daily = session
    with pytest.raises(DataError):
        daily_check(rows, [replace(daily, close=117)], {"peer": [daily]})


def test_daily_check_rejects_volume_disagreement(session):
    rows, daily = session
    with pytest.raises(DataError):
        daily_check(rows, [daily], {"peer": [replace(daily, volume=31)]})


@pytest.mark.parametrize("missing", ["price", "volume"])
def test_daily_check_rejects_missing_reference_date(session, missing):
    rows, daily = session
    with pytest.raises(DataError):
        daily_check(
            rows,
            [] if missing == "price" else [daily],
            {"peer": [] if missing == "volume" else [daily]},
        )
