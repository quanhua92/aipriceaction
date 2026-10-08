from datetime import UTC, datetime

from aipriceaction_api.domain import Candle
from aipriceaction_api.storage import Repository
from scripts.check_vn_interval_consistency import audit


def stamp(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp())


def build(
    tmp_path,
    *,
    stale_hour=False,
    missing_hour=False,
    bad_hour=False,
    bad_close=False,
    late_minute=False,
):
    path = tmp_path / "db.sqlite3"
    repo = Repository(path)
    repo.initialize()
    day = stamp("2026-10-08")
    minutes = [
        Candle("vn", "VCB", "1m", day + 2 * 3600 + 15 * 60, 100, 102, 99, 101, 10),
        Candle(
            "vn",
            "VCB",
            "1m",
            day + (8 * 3600 if late_minute else 7 * 3600 + 45 * 60),
            101,
            104,
            100,
            103,
            20,
        ),
    ]
    hour_day = day - 86400 if stale_hour else day
    hours = [
        Candle("vn", "VCB", "1h", hour_day + 2 * 3600, 100, 102, 99, 101, 10),
        Candle("vn", "VCB", "1h", hour_day + 7 * 3600, 101, 104, 100, 102 if bad_hour else 103, 20),
    ]
    daily = Candle("vn", "VCB", "1D", day, 100, 104, 99, 102 if bad_close else 103, 30)
    repo.put([*minutes, *([] if missing_hour else hours), daily])
    return path


def test_latest_vn_intervals_align_and_reconcile(tmp_path):
    report = audit(build(tmp_path), ["VCB"])
    row = report["series"][0]
    assert row["latest_dates"] == {"1D": "2026-10-08", "1h": "2026-10-08", "1m": "2026-10-08"}
    assert row["missing_native_intervals"] == []
    assert row["missing_served_intervals"] == []
    assert row["latest_dates_aligned"]
    assert row["minute_session"] == {
        "date": "2026-10-08",
        "first": "02:15:00",
        "last": "07:45:00",
        "rows": 2,
        "outside_regular_session": 0,
        "regular_boundary": True,
    }
    assert row["daily_vs_minutes"] == []
    assert row["daily_vs_hours"] == []
    assert row["hour_basis"] == "recent-minute-overlay"
    assert row["native_hourly_vs_minutes"] == []


def test_latest_vn_interval_audit_exposes_date_boundary_and_ohlcv_drift(tmp_path):
    report = audit(build(tmp_path, stale_hour=True, bad_close=True, late_minute=True), ["VCB"])
    row = report["series"][0]
    assert not row["latest_dates_aligned"]
    assert not row["minute_session"]["regular_boundary"]
    assert row["daily_vs_minutes"] == ["close"]
    assert row["daily_vs_hours"] is None
    assert row["hour_basis"] == "native-missing-latest-session"
    assert row["native_hourly_vs_minutes"] == []


def test_latest_vn_interval_audit_models_public_hour_fallback(tmp_path):
    report = audit(build(tmp_path, missing_hour=True), ["VCB"])
    row = report["series"][0]
    assert row["missing_native_intervals"] == ["1h"]
    assert row["missing_served_intervals"] == []
    assert row["latest_dates_aligned"]
    assert row["daily_vs_hours"] == []
    assert row["hour_basis"] == "recent-minute-overlay"
    assert row["native_hourly_vs_minutes"] == []


def test_latest_vn_interval_audit_exposes_hour_bucket_drift(tmp_path):
    row = audit(build(tmp_path, bad_hour=True), ["VCB"])["series"][0]
    assert row["native_hourly_vs_minutes"] == [
        {"time": "07:00:00", "kind": "ohlcv", "fields": ["close"]}
    ]
    assert row["daily_vs_hours"] == ["close"]
