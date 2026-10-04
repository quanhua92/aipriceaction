import pytest

from aipriceaction_api.domain import DataError
from scripts.ohlcv_disagreements import diagnose, price_class
from scripts.review_vci_combined_captures import summarize_diagnostics


def row(stamp=0, price=100, volume=100):
    return {
        "time": stamp,
        "open": price,
        "high": price + 1,
        "low": price - 1,
        "close": price,
        "volume": volume,
    }


def test_rounding_is_identified_without_a_price_adoption_license():
    source = row(price=100.123456)
    local = {
        key: round(value, 2) if key in ("open", "high", "low", "close") else value
        for key, value in source.items()
    }
    result = diagnose({0: local}, {0: source})
    assert result["price_classes"] == {"two_decimal_rounding": 1}
    assert sum(result["price_difference_bands"].values()) == 1
    assert not result["publication_license"]
    assert local["close"] == 100.12


def test_integer_rounding_and_consistent_ratio_are_distinct():
    source = row(price=100.123456)
    assert price_class(row(), source) == "integer_rounding"
    scaled = source | {field: source[field] * 0.9 for field in ("open", "high", "low", "close")}
    assert price_class(source, scaled) == "uniform_price_ratio"
    scaled["high"] *= 1.01
    assert price_class(source, scaled) == "nonuniform_price_difference"


def test_mismatched_times_cannot_turn_partial_day_totals_into_volume_truth():
    local = {0: row(volume=100), 60: row(stamp=60, volume=300)}
    source = {0: row(volume=120), 120: row(stamp=120, volume=500)}
    result = diagnose(local, source)
    day = result["volume_days"][0]
    assert result["volume_disagreements"] == 1
    assert not day["timestamps_match"]
    assert day["sqlite_volume_total"] == 400
    assert day["source_volume_total"] == 620
    assert day["shared_volume_net_difference"] == 20
    assert day["sqlite_only_rows"] == day["source_only_rows"] == 1
    assert not result["publication_license"]


def test_opposing_volume_errors_remain_visible_when_day_totals_match():
    local = {0: row(volume=100), 60: row(stamp=60, volume=300)}
    source = {0: row(volume=120), 60: row(stamp=60, volume=280)}
    result = diagnose(local, source)
    day = result["volume_days"][0]
    assert day["timestamps_match"]
    assert result["volume_disagreements"] == 2
    assert day["shared_volume_net_difference"] == 0
    assert day["sqlite_volume_total"] == day["source_volume_total"]


def test_examples_are_bounded_while_counts_cover_all_differences():
    local = {stamp: row(stamp=stamp) for stamp in range(0, 60 * 50, 60)}
    source = {stamp: row(stamp=stamp, price=200, volume=110) for stamp in local}
    result = diagnose(local, source, samples=3)
    assert sum(result["price_classes"].values()) == 50
    assert sum(result["price_difference_bands"].values()) == 50
    assert len(result["price_examples"]["nonuniform_price_difference"]) == 3
    assert result["volume_disagreements"] == 50
    assert len(result["volume_days"][0]["samples"]) == 3
    with pytest.raises(ValueError):
        diagnose(local, source, samples=21)


def test_equal_observations_and_empty_peers_do_not_create_disagreements():
    result = diagnose({0: row()}, {0: row()})
    assert result["price_classes"] == {}
    assert result["volume_disagreements"] == 0
    assert result["maximum_symmetric_price_difference_pct"] == 0
    assert diagnose({0: row()}, {})["shared_rows"] == 0


@pytest.mark.parametrize("defect", ["price_counts", "bands", "volume_counts", "day_counts"])
def test_aggregate_rejects_classifications_that_do_not_reconcile_with_exact_counts(defect):
    detail = diagnose({0: row()}, {0: row(price=200, volume=200)})
    counts = {"price_disagreements": 1, "volume_disagreements": 1}
    if defect == "price_counts":
        counts["price_disagreements"] = 2
    elif defect == "bands":
        detail["price_difference_bands"] = {}
    elif defect == "volume_counts":
        counts["volume_disagreements"] = 2
    else:
        detail["volume_days"] = []
    series = [
        {"sqlite_comparison": {"providers": {"vci": {"counts": counts, "diagnostics": detail}}}}
    ]
    with pytest.raises(DataError, match="does not reconcile"):
        summarize_diagnostics(series)
