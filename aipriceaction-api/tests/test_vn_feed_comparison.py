from scripts.compare_vn_feeds import compare, date_coverage


def candle(stamp=0, close=10.0, volume=100):
    return dict(time=stamp, open=10.0, high=11.0, low=9.0, close=close, volume=volume)


def test_legacy_can_be_the_outlier():
    report = compare(
        {
            "vps": [candle()],
            "vndirect": [candle()],
            "dnse": [candle()],
            "legacy": [candle(close=10.5)],
        }
    )
    assert report["three_agree_outliers"] == {"legacy": 1}
    assert report["counts"] == {"four_feed_disagreement": 1}
    assert report["pairs"]["vps:legacy"]["price_disagreements"] == [0]
    assert report["pairs"]["vps:legacy"]["price_difference_ge_1pct"] == [0]


def test_two_against_two_does_not_choose_a_winner():
    report = compare(
        {
            "vps": [candle()],
            "legacy": [candle()],
            "vndirect": [candle(volume=101)],
            "dnse": [candle(volume=101)],
        }
    )
    assert report["three_agree_outliers"] == {}
    assert report["counts"] == {"four_feed_disagreement": 1}
    assert report["pairs"]["vps:vndirect"]["price_disagreements"] == []
    assert report["pairs"]["vps:vndirect"]["volume_disagreements"] == [0]
    assert report["pairs"]["vps:vndirect"]["price_difference_ge_1pct"] == []


def test_unavailable_feed_cannot_be_counted_as_consensus():
    report = compare({"vps": [candle()], "vndirect": [candle()], "dnse": [candle()]})
    assert report["counts"] == {"incomplete_four_feed_coverage": 1}
    assert report["three_agree_outliers"] == {}


def test_timestamp_coverage_is_independent_of_value_agreement():
    report = compare(
        {
            "vps": [candle(), candle(60)],
            "vndirect": [candle()],
            "dnse": [candle()],
            "legacy": [candle()],
        }
    )
    assert report["counts"] == {"four_feed_agreement": 1, "incomplete_four_feed_coverage": 1}
    assert report["pairs"]["vps:legacy"]["only_left"] == [60]


def test_float_representation_noise_is_not_a_provider_disagreement():
    report = compare(
        {
            "vps": [candle()],
            "vndirect": [candle(close=10.0000000001)],
            "dnse": [candle()],
            "legacy": [candle()],
        }
    )
    assert report["counts"] == {"four_feed_agreement": 1}


def test_empty_feeds_do_not_certify_any_candle():
    report = compare({"vps": [], "vndirect": [], "dnse": [], "legacy": []})
    assert report["counts"] == {}
    assert report["three_agree_outliers"] == {}


def test_cursor_completion_cannot_hide_missing_days_or_partial_sessions():
    report = date_coverage(
        {
            "vci": [candle(0), candle(60), candle(86400), candle(2 * 86400)],
            "vndirect": [candle(0), candle(2 * 86400)],
            "dnse": [],
        }
    )
    assert report["basis"] == "union_of_observed_provider_dates_not_exchange_calendar"
    assert report["observed_dates"] == 3
    assert report["providers"]["vndirect"]["missing_observed_dates"] == ["1970-01-02"]
    assert report["providers"]["vndirect"]["fewer_rows_than_largest_provider"] == {
        "1970-01-01": {"rows": 1, "largest_observed": 2}
    }
    assert len(report["providers"]["dnse"]["missing_observed_dates"]) == 3
