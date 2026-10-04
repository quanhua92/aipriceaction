from scripts.compare_vn_feeds import compare


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
