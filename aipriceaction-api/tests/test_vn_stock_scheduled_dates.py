import hashlib
import json
import sqlite3
from argparse import Namespace
from datetime import date
from pathlib import Path

import pytest

from aipriceaction_api.domain import DataError, parse_time
from scripts.check_hose_scheduled_dates import load_calendar, load_reference_calendar
from scripts.check_vn_stock_scheduled_dates import run, shared_schedule
from scripts.stock_transfer_evidence import load_transfers


@pytest.fixture
def args(tmp_path):
    declarations = Path(__file__).parents[1] / "calendars"
    files = {}
    for exchange in ("hose", "hnx"):
        calendar = json.loads((declarations / f"{exchange}-2026.json").read_text())
        paths = []
        for i, declaration in enumerate([calendar, *calendar["amendments"]]):
            raw = f"%PDF synthetic {exchange} source {i}".encode()
            path = tmp_path / f"{exchange}-{i}.pdf"
            path.write_bytes(raw)
            declaration["source"]["bytes"] = len(raw)
            declaration["source"]["sha256"] = hashlib.sha256(raw).hexdigest()
            paths.append(path)
        path = tmp_path / f"{exchange}.json"
        path.write_text(json.dumps(calendar))
        files[f"{exchange}_calendar"] = path
        files[f"{exchange}_source"] = paths[0]
        files[f"{exchange}_amendment"] = paths[1:]
    database = tmp_path / "prices.sqlite3"
    with sqlite3.connect(database) as con:
        con.execute("CREATE TABLE candles (source TEXT, symbol TEXT, interval TEXT, time INTEGER)")
        # FPT has an unexpected holiday row, a shifted daily row, and an absent
        # scheduled date. VPL's absence cannot be licensed as a missing session.
        con.executemany(
            "INSERT INTO candles VALUES ('vn','FPT','1D',?)",
            [(parse_time("2026-01-02"),), (parse_time("2026-01-05") + 7200,)],
        )
        con.execute(
            "INSERT INTO candles VALUES ('vn','FPT','1m',?)", (parse_time("2026-01-05") + 7200,)
        )
    return Namespace(
        **files,
        database=database,
        symbol=["FPT", "VPL"],
        interval="1D",
        start_date="2026-01-01",
        end_date="2026-01-06",
        output=tmp_path / "review.json",
    )


def test_stock_candidates_do_not_license_missing_sessions_or_prices(args):
    before = args.database.read_bytes()
    report = run(args)
    assert args.database.read_bytes() == before
    fpt, vpl = report["series"]
    assert report["scheduled_dates"] == 2
    assert fpt["absent_shared_schedule_dates"] == ["2026-01-06"]
    assert fpt["unexpected_dates"] == ["2026-01-02"]
    assert fpt["non_midnight_daily_timestamps"] == [parse_time("2026-01-05") + 7200]
    assert vpl["observed_rows"] == 0
    assert vpl["absent_shared_schedule_dates"] == ["2026-01-05", "2026-01-06"]
    assert report["ohlcv_accuracy_proven"] is False
    assert report["actual_session_completeness_proven"] is False
    assert report["canonical_publication"] is False
    assert not any("passed" in k for k in report)


def test_single_minute_only_establishes_date_presence(args):
    args.interval = "1m"
    report = run(args)
    fpt = report["series"][0]
    assert fpt["observed_rows"] == fpt["observed_dates"] == 1
    assert fpt["absent_shared_schedule_dates"] == ["2026-01-06"]
    assert "non_midnight_daily_timestamps" not in fpt
    assert report["actual_session_completeness_proven"] is False


@pytest.mark.parametrize("exchange", ["hose", "hnx"])
def test_each_amendment_is_required_and_hash_bound(args, exchange):
    paths = getattr(args, f"{exchange}_amendment")
    setattr(args, f"{exchange}_amendment", [])
    with pytest.raises(DataError, match="every declared"):
        run(args)
    setattr(args, f"{exchange}_amendment", paths)
    paths[0].write_bytes(paths[0].read_bytes() + b"altered")
    with pytest.raises(DataError, match="source PDF changed"):
        run(args)
    assert not args.output.exists()


def test_different_exchange_schedules_require_venue_evidence(args):
    calendar = json.loads(args.hnx_calendar.read_text())
    calendar["closed_ranges"].append(["2026-01-06", "2026-01-06"])
    args.hnx_calendar.write_text(json.dumps(calendar))
    with pytest.raises(DataError, match="schedules differ"):
        run(args)
    assert not args.output.exists()


def test_hnx_reference_does_not_relax_hose_index_scope(args):
    with pytest.raises(DataError, match="Unsupported announced calendar scope"):
        load_calendar(args.hnx_calendar, args.hnx_source, args.hnx_amendment)
    calendar, closed = load_reference_calendar(
        args.hnx_calendar, args.hnx_source, args.hnx_amendment
    )
    assert calendar["symbols"] == []
    assert date(2026, 1, 2) in closed


@pytest.mark.parametrize("symbols", [[], ["FPT", "FPT"], ["VNINDEX"], ["FPT' OR 1=1"]])
def test_unknown_or_invalid_stock_requests_reject(args, symbols):
    args.symbol = symbols
    with pytest.raises(DataError, match="unique three-letter"):
        run(args)


def test_existing_output_is_preserved(args):
    args.output.write_bytes(b"original")
    with pytest.raises(DataError, match="Preserve"):
        run(args)
    assert args.output.read_bytes() == b"original"


def test_mixed_year_or_swapped_exchange_pair_rejects(args):
    hose = load_reference_calendar(args.hose_calendar, args.hose_source, args.hose_amendment)
    hnx = load_reference_calendar(args.hnx_calendar, args.hnx_source, args.hnx_amendment)
    with pytest.raises(DataError, match="outside both verified"):
        shared_schedule(hose, hnx, date(2025, 12, 31), date(2026, 1, 6))
    with pytest.raises(DataError, match="declared order"):
        shared_schedule(hnx, hose, date(2026, 1, 1), date(2026, 1, 6))


@pytest.fixture
def transfer_args(args):
    declaration = json.loads(
        (Path(__file__).parents[1] / "calendars/stock-transfers.json").read_text()
    )
    event = declaration["events"][0]
    # Synthetic single-event fixture is independent of the live reviewed catalog.
    declaration["events"] = [event]
    event.update(symbol="FPT", last_trading_date="2026-01-05", first_trading_date="2026-01-07")
    sources = []
    for i, source in enumerate(event["sources"]):
        raw = f"%PDF synthetic transfer witness {i}".encode()
        path = args.output.parent / f"transfer-{i}.pdf"
        path.write_bytes(raw)
        source["bytes"] = len(raw)
        source["sha256"] = hashlib.sha256(raw).hexdigest()
        sources.append(path)
    args.transfers = args.output.parent / "transfers.json"
    args.transfers.write_text(json.dumps(declaration))
    args.transfer_source = sources
    return args


def test_transfer_annotations_preserve_original_candidates_and_boundary_sessions(transfer_args):
    before = transfer_args.database.read_bytes()
    r = run(transfer_args)
    fpt, vpl = r["series"]
    assert fpt["absent_shared_schedule_dates"] == ["2026-01-06"]
    assert fpt["explained_transfer_dates"] == ["2026-01-06"]
    assert fpt["remaining_absent_dates"] == []
    assert fpt["observed_transfer_dates"] == []
    assert vpl["explained_transfer_dates"] == []
    assert vpl["remaining_absent_dates"] == ["2026-01-05", "2026-01-06"]
    assert r["canonical_publication"] is False
    assert r["actual_session_completeness_proven"] is False
    assert transfer_args.database.read_bytes() == before
    _, dates = load_transfers(transfer_args.transfers, transfer_args.transfer_source)
    assert date(2026, 1, 5) not in dates["FPT"]
    assert date(2026, 1, 7) not in dates["FPT"]


@pytest.mark.parametrize("day", ["2026-01-03", "2026-01-06"])
def test_observed_transfer_rows_remain_visible_as_conflicts(transfer_args, day):
    c = json.loads(transfer_args.transfers.read_text())
    c["events"][0]["last_trading_date"] = "2026-01-02"
    transfer_args.transfers.write_text(json.dumps(c))
    with sqlite3.connect(transfer_args.database) as con:
        con.execute("INSERT INTO candles VALUES ('vn','FPT','1D',?)", (parse_time(day),))
    before = transfer_args.database.read_bytes()
    r = run(transfer_args)
    fpt = r["series"][0]
    assert fpt["observed_transfer_dates"] == sorted(["2026-01-05", day])
    assert fpt["explained_transfer_dates"] == ([] if day == "2026-01-06" else ["2026-01-06"])
    assert fpt["observed_rows"] == 3
    assert transfer_args.database.read_bytes() == before


@pytest.mark.parametrize("change", ["missing", "swapped", "altered", "extra"])
def test_transfer_source_pair_is_mandatory_and_bound(transfer_args, change):
    if change == "missing":
        transfer_args.transfer_source.pop()
    elif change == "swapped":
        transfer_args.transfer_source.reverse()
    elif change == "altered":
        p = transfer_args.transfer_source[0]
        p.write_bytes(p.read_bytes() + b"changed")
    else:
        transfer_args.transfer_source.append(transfer_args.transfer_source[0])
    with pytest.raises(DataError, match="source PDF changed|both source files"):
        run(transfer_args)
    assert not transfer_args.output.exists()


@pytest.mark.parametrize("change", ["reversed", "too_long", "wrong_venue", "overlap", "wrong_role"])
def test_invalid_transfer_scopes_reject(transfer_args, change):
    c = json.loads(transfer_args.transfers.read_text())
    event = c["events"][0]
    if change == "reversed":
        event["last_trading_date"] = "2026-01-08"
    elif change == "too_long":
        event["first_trading_date"] = "2026-06-01"
    elif change == "wrong_venue":
        event["from_venue"] = "HOSE"
    elif change == "wrong_role":
        event["sources"][0]["role"] = "first_trading_day"
    else:
        c["events"].append(event.copy())
        transfer_args.transfer_source *= 2
    transfer_args.transfers.write_text(json.dumps(c))
    with pytest.raises(DataError, match="Invalid stock transfer|Unsupported reviewed|Overlapping"):
        run(transfer_args)


def test_orphan_transfer_sources_do_not_change_default_review(args):
    args.transfer_source = [args.hose_source]
    with pytest.raises(DataError, match="require a declared"):
        run(args)
