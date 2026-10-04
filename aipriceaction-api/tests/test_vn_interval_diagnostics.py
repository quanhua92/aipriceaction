import gzip
import json
from pathlib import Path

import pytest

from scripts.diagnose_vn_feed_audit import diagnose


@pytest.mark.parametrize("symbol", ("HCM", "HAG"))
def test_captured_hourly_feed_omits_late_session_observation(symbol):
    raw = gzip.decompress(
        (Path(__file__).parent / "fixtures/vn_hourly_closing_observations.json.gz").read_bytes()
    )
    feeds = json.loads(raw)[symbol]
    result = diagnose(feeds["vndirect"]["1m"], feeds["vndirect"]["1h"])
    excluded = [
        issue for issue in result["issues"] if issue["kind"] == "late_session_observations_excluded"
    ]
    assert any(issue["time"] == 1790924400 for issue in excluded)
    issue = next(issue for issue in excluded if issue["time"] == 1790924400)
    assert [row["time"] for row in issue["late_observations"]] == [1790927100]
    assert issue["native_hour"]["close"] != issue["minute_aggregate"]["close"]
    for feed in ("vps", "dnse", "legacy"):
        result = diagnose(feeds[feed]["1m"], feeds[feed]["1h"])
        assert not result["issues"]


def test_absent_minutes_do_not_certify_native_hour():
    hour = dict(time=0, open=10, high=10, low=10, close=10, volume=10)
    result = diagnose([], [hour])
    assert result["counts"]["hour_without_observed_minutes"] == 1
    assert "native_hour_matches_observed_minutes" not in result["counts"]


def test_missing_native_hour_is_separate_from_price_disagreement():
    minute = dict(time=0, open=10, high=10, low=10, close=10, volume=10)
    result = diagnose([minute], [])
    assert result["minute_buckets_without_native_hour"] == [0]
    assert result["issues"] == []


def test_inconsistent_native_hour_is_not_assumed_to_exclude_auction():
    minute = dict(time=7 * 3600, open=10, high=10, low=10, close=10, volume=10)
    late = dict(minute, time=minute["time"] + 45 * 60, volume=20)
    hour = dict(minute, volume=12)
    result = diagnose([minute, late], [hour])
    assert result["counts"]["other_minute_hour_disagreement"] == 1
    assert "late_session_observations_excluded" not in result["counts"]
