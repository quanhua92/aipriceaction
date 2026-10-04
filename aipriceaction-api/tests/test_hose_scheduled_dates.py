import hashlib
import json
import sqlite3
from argparse import Namespace
from datetime import date
from pathlib import Path

import pytest

from aipriceaction_api.domain import DataError, parse_time
from scripts.check_hose_scheduled_dates import compare_dates, dates_between, load_calendar, run

CALENDAR = Path(__file__).parents[1] / "calendars/hose-2025.json"


@pytest.fixture
def evidence(tmp_path):
    calendar = json.loads(CALENDAR.read_text())
    # A deliberately synthetic PDF tests byte binding, not transcription. The
    # signed primary source was inspected visually and its actual hash is pinned
    # in the tracked declaration; unit tests cannot certify the transcription.
    raw = b"%PDF synthetic source-integrity fixture"
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(raw)
    calendar["source"]["bytes"] = len(raw)
    calendar["source"]["sha256"] = hashlib.sha256(raw).hexdigest()
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps(calendar))
    return path, pdf


def test_announced_2025_schedule_has_249_dates_and_excludes_makeup_saturday(evidence):
    calendar, closed = load_calendar(*evidence)
    first, last = date(2025, 1, 1), date(2025, 12, 31)
    observed = [
        parse_time(day.isoformat())
        for day in dates_between(first, last)
        if day.weekday() < 5 and day not in closed
    ]
    result = compare_dates(calendar, closed, first, last, observed)
    assert result["scheduled_dates"] == result["observed_dates"] == 249
    assert result["scheduled_date_coverage_passed"] is True
    assert result["ohlcv_accuracy_proven"] is False
    assert result["actual_session_completeness_proven"] is False
    assert date(2025, 4, 26) in closed
    assert date(2025, 5, 2) in closed
    assert all(date(2025, 1, day) in closed for day in range(27, 32))


def test_missing_dates_are_found_without_using_observed_provider_calendar(evidence):
    calendar, closed = load_calendar(*evidence)
    result = compare_dates(calendar, closed, date(2025, 1, 1), date(2025, 1, 3), [])
    assert result["missing_scheduled_dates"] == ["2025-01-02", "2025-01-03"]
    assert result["scheduled_date_coverage_passed"] is False


def test_holiday_weekend_and_shifted_daily_dates_remain_visible(evidence):
    calendar, closed = load_calendar(*evidence)
    stamps = [parse_time("2025-04-26"), parse_time("2025-04-30"), parse_time("2025-04-29") + 7200]
    result = compare_dates(calendar, closed, date(2025, 4, 26), date(2025, 4, 30), stamps)
    assert result["unexpected_dates"] == ["2025-04-26", "2025-04-30"]
    assert result["missing_scheduled_dates"] == ["2025-04-28"]
    assert result["non_midnight_daily_timestamps"] == [parse_time("2025-04-29") + 7200]
    assert result["scheduled_date_coverage_passed"] is False


@pytest.mark.parametrize(
    "first,last",
    [("2024-12-31", "2025-01-03"), ("2025-12-31", "2026-01-02"), ("2025-01-03", "2025-01-02")],
)
def test_unknown_years_and_reversed_windows_do_not_infer_sessions(evidence, first, last):
    calendar, closed = load_calendar(*evidence)
    with pytest.raises(DataError, match="outside verified calendar year"):
        compare_dates(calendar, closed, date.fromisoformat(first), date.fromisoformat(last), [])


def test_changed_source_pdf_is_rejected(evidence):
    path, pdf = evidence
    pdf.write_bytes(pdf.read_bytes() + b"changed")
    with pytest.raises(DataError, match="source PDF changed"):
        load_calendar(path, pdf)


@pytest.mark.parametrize("change", ["duplicate", "wrong_year", "working_weekend", "extra_symbol"])
def test_invalid_calendar_scope_is_rejected(evidence, change):
    path, pdf = evidence
    calendar = json.loads(path.read_text())
    if change == "duplicate":
        calendar["closed_ranges"].append(calendar["closed_ranges"][0])
    elif change == "wrong_year":
        calendar["closed_ranges"].append(["2026-01-01", "2026-01-01"])
    elif change == "working_weekend":
        calendar["explicit_closed_weekends"] = ["2025-04-28"]
    else:
        calendar["symbols"].append("FPT")
    path.write_text(json.dumps(calendar))
    with pytest.raises(DataError):
        load_calendar(path, pdf)


def test_real_sqlite_audit_is_read_only_and_retains_missing_holiday_findings(evidence, tmp_path):
    path, pdf = evidence
    database = tmp_path / "prices.sqlite3"
    with sqlite3.connect(database) as con:
        con.execute("CREATE TABLE candles(source TEXT,symbol TEXT,interval TEXT,time INTEGER)")
        con.executemany(
            "INSERT INTO candles VALUES ('vn',?,'1D',?)",
            [("VNINDEX", parse_time("2025-01-02")), ("VN30", parse_time("2025-01-01"))],
        )
    before = database.read_bytes()
    args = Namespace(
        calendar=path,
        source_pdf=pdf,
        database=database,
        symbol=None,
        start_date="2025-01-01",
        end_date="2025-01-03",
        output=tmp_path / "audit.json",
    )
    result = run(args)
    assert result["canonical_publication"] is False
    assert result["series"][0]["missing_scheduled_dates"] == ["2025-01-03"]
    assert result["series"][1]["unexpected_dates"] == ["2025-01-01"]
    assert database.read_bytes() == before
    with pytest.raises(DataError, match="Preserve the previous audit"):
        run(args)
    args.output = tmp_path / "another.json"
    args.symbol = ["FPT"]
    with pytest.raises(DataError, match="indices explicitly licensed"):
        run(args)


@pytest.fixture
def amended_evidence(tmp_path):
    calendar = json.loads((CALENDAR.parent / "hose-2024.json").read_text())
    paths = []
    for i, source in enumerate([calendar["source"], calendar["amendments"][0]["source"]]):
        raw = b"%PDF synthetic annual/amendment integrity fixture " + str(i).encode()
        path = tmp_path / f"source-{i}.pdf"
        path.write_bytes(raw)
        source["bytes"] = len(raw)
        source["sha256"] = hashlib.sha256(raw).hexdigest()
        paths.append(path)
    declaration = tmp_path / "amended-calendar.json"
    declaration.write_text(json.dumps(calendar))
    return declaration, paths


def test_2024_amendment_removes_april_29_without_adding_makeup_saturday(amended_evidence):
    declaration, paths = amended_evidence
    calendar, closed = load_calendar(declaration, paths[0], paths[1:])
    observed = [parse_time("2024-04-26"), parse_time("2024-05-02"), parse_time("2024-05-03")]
    result = compare_dates(calendar, closed, date(2024, 4, 26), date(2024, 5, 4), observed)
    assert result["scheduled_dates"] == 3
    assert result["scheduled_date_coverage_passed"] is True
    assert date(2024, 4, 29) in closed
    assert date(2024, 5, 4) in closed


@pytest.mark.parametrize("failure", ["missing", "extra", "swapped", "changed"])
def test_amendment_source_is_required_and_exactly_bound(amended_evidence, failure):
    declaration, paths = amended_evidence
    base, amendments = paths[0], paths[1:]
    if failure == "missing":
        amendments = []
    elif failure == "extra":
        amendments = [paths[1], paths[1]]
    elif failure == "swapped":
        base, amendments = paths[1], [paths[0]]
    else:
        paths[1].write_bytes(paths[1].read_bytes() + b"altered")
    with pytest.raises(DataError, match="amendment source|source PDF changed"):
        load_calendar(declaration, base, amendments)


def test_jpeg_notice_integrity_requires_exact_image_bytes(evidence):
    path, image = evidence
    calendar = json.loads((CALENDAR.parent / "hose-2023.json").read_text())
    raw = b"\xff\xd8\xff synthetic notice image \xff\xd9"
    image.write_bytes(raw)
    calendar["source"]["bytes"] = len(raw)
    calendar["source"]["sha256"] = hashlib.sha256(raw).hexdigest()
    path.write_text(json.dumps(calendar))
    _, closed = load_calendar(path, image)
    assert date(2023, 1, 2) in closed
    assert date(2023, 9, 4) in closed
    assert date(2023, 10, 4) not in closed
    image.write_bytes(raw[:-2])
    with pytest.raises(DataError, match="source image changed"):
        load_calendar(path, image)


def test_2026_amendment_and_both_makeup_weekends_are_excluded(evidence, tmp_path):
    path, base = evidence
    calendar = json.loads((CALENDAR.parent / "hose-2026.json").read_text())
    amendment = tmp_path / "amendment.pdf"
    amendment.write_bytes(b"%PDF synthetic 2026 amendment")
    for source, file in [
        (calendar["source"], base),
        (calendar["amendments"][0]["source"], amendment),
    ]:
        source["bytes"] = len(file.read_bytes())
        source["sha256"] = hashlib.sha256(file.read_bytes()).hexdigest()
    path.write_text(json.dumps(calendar))
    _, closed = load_calendar(path, base, [amendment])
    assert all(
        day in closed
        for day in (date(2026, 1, 2), date(2026, 1, 10), date(2026, 8, 22), date(2026, 8, 31))
    )


@pytest.mark.parametrize("interval", ["1h", "1m"])
def test_intraday_date_presence_does_not_certify_candle_completeness(evidence, tmp_path, interval):
    path, pdf = evidence
    database = tmp_path / "intraday.sqlite3"
    with sqlite3.connect(database) as con:
        con.execute("CREATE TABLE candles(source TEXT,symbol TEXT,interval TEXT,time INTEGER)")
        con.execute(
            "INSERT INTO candles VALUES ('vn','VNINDEX',?,?)",
            (interval, parse_time("2025-01-02") + 7200),
        )
        # A different interval cannot hide the missing January 3 partition.
        con.execute(
            "INSERT INTO candles VALUES ('vn','VNINDEX','1D',?)", (parse_time("2025-01-03"),)
        )
    args = Namespace(
        calendar=path,
        source_pdf=pdf,
        database=database,
        symbol=["VNINDEX"],
        interval=interval,
        start_date="2025-01-02",
        end_date="2025-01-03",
        output=tmp_path / "intraday.json",
    )
    result = run(args)
    series = result["series"][0]
    assert series["observed_rows"] == series["observed_dates"] == 1
    assert series["missing_scheduled_dates"] == ["2025-01-03"]
    assert series["intraday_timestamp_completeness_proven"] is False
    assert series["ohlcv_accuracy_proven"] is False
    assert "non_midnight_daily_timestamps" not in series
    args.end_date = "2025-01-02"
    args.output = tmp_path / "one-date.json"
    one_date = run(args)["series"][0]
    assert one_date["scheduled_date_coverage_passed"] is True
    assert one_date["intraday_timestamp_completeness_proven"] is False
