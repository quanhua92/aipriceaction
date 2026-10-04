import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, date_bounds
from aipriceaction_api.storage import Repository
from scripts import review_vn_recent_disagreements
from scripts.compare_vn_feeds import NATIVE_FEEDS
from scripts.review_vn_recent_disagreements import run


def setup_audit(tmp_path, monkeypatch):
    database = tmp_path / "live.sqlite3"
    settings = replace(Settings(), database=database)
    repository = Repository(database)
    repository.initialize()
    stamp = date_bounds("2026-09-28") + 3 * 3600
    rows = [
        dict(time=stamp, open=100, high=101, low=99, close=100, volume=100),
        dict(time=stamp + 60, open=100, high=101, low=99, close=100, volume=300),
        dict(time=stamp - 86400, open=100, high=101, low=99, close=100, volume=900),
    ]
    repository.put([Candle("vn", "FPT", "1m", **row) for row in rows])
    before = database.read_bytes()
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    audit = tmp_path / "audit"
    batch = audit / "batch-000"
    batch.mkdir(parents=True)
    summary = {
        "completed": True,
        "symbols": ["FPT"],
        "batches": [{"path": str(batch), "symbols": ["FPT"]}],
    }
    (audit / "summary.json").write_text(json.dumps(summary))
    (audit / "request.json").write_text(
        json.dumps({"symbols": ["FPT"], "start_date": "2026-09-01", "end_date": "2026-10-02"})
    )
    (batch / "report.json").write_text(
        json.dumps(
            {
                "symbols": ["FPT"],
                "feeds": list(NATIVE_FEEDS),
                "intervals": ["1m"],
                "intraday_start": "2026-09-01",
                "end_date": "2026-10-02",
            }
        )
    )
    for feed in NATIVE_FEEDS:
        folder = batch / feed
        folder.mkdir()
        values = [dict(row) for row in rows]
        if feed == "vci":
            values[0]["volume"] = 120
            values[1]["volume"] = 280
        (folder / "FPT-1m.json").write_text(
            json.dumps({"rows": values, "error": "older page rejected" if feed == "vci" else None})
        )
    args = SimpleNamespace(
        audit=audit, output=tmp_path / "review", start_date="2026-09-28", end_date="2026-10-02"
    )
    return args, batch, database, before


def test_all_four_sources_are_scoped_and_volume_redistribution_is_preserved(tmp_path, monkeypatch):
    args, _, database, before = setup_audit(tmp_path, monkeypatch)
    result = run(args)
    assert result["checked_symbols"] == 1
    assert set(result["sqlite_totals"]) == set(NATIVE_FEEDS)
    assert len(result["provider_pair_totals"]) == 6
    assert all(row["shared_rows"] == 2 for row in result["sqlite_totals"].values())
    volume = result["sqlite_totals"]["vci"]
    assert volume["volume_disagreements"] == 2
    assert volume["matching_timestamp_volume_days"] == volume["equal_total_volume_days"] == 1
    assert result["provider_pair_totals"]["vps:vci"]["volume_disagreements"] == 2
    observations = result["series"][0]["observations"]["vci"]
    assert observations["original_collection_error"] == "older page rejected"
    assert len(observations["sha256"]) == 64
    assert database.read_bytes() == before
    assert not result["remote_requests"] and not result["canonical_publication"]
    assert not list(args.output.rglob("*.sqlite3*"))


@pytest.mark.parametrize("failure", ["window", "duplicate", "feed", "missing"])
def test_misdeclared_observations_do_not_produce_a_report(tmp_path, monkeypatch, failure):
    args, batch, _, _ = setup_audit(tmp_path, monkeypatch)
    if failure == "window":
        args.start_date = "2026-09-01"
    elif failure == "duplicate":
        path = batch / "vci/FPT-1m.json"
        record = json.loads(path.read_text())
        record["rows"].append(record["rows"][0])
        path.write_text(json.dumps(record))
    elif failure == "feed":
        path = batch / "report.json"
        report = json.loads(path.read_text())
        report["feeds"] = ["vps"]
        path.write_text(json.dumps(report))
    else:
        path = args.audit / "summary.json"
        report = json.loads(path.read_text())
        report["batches"] = []
        path.write_text(json.dumps(report))
    with pytest.raises(DataError):
        run(args)
    assert not args.output.exists()


@pytest.mark.parametrize("changed_proof", [False, True])
def test_empty_vci_observations_use_only_bound_combined_captures(
    tmp_path, monkeypatch, changed_proof
):
    args, batch, _, _ = setup_audit(tmp_path, monkeypatch)
    original = batch / "vci/FPT-1m.json"
    original.write_text(json.dumps({"rows": [], "error": "original rejection"}))
    args.proofs = tmp_path / "proofs.json"
    args.proofs.write_text("[]")
    args.combined_records = tmp_path / "combined"
    args.combined_records.mkdir()
    report = {
        "completed": True,
        "series": [{"symbol": "FPT"}],
        "proofs_sha256": hashlib.sha256(args.proofs.read_bytes()).hexdigest(),
        "audit_sha256": hashlib.sha256((args.audit / "summary.json").read_bytes()).hexdigest(),
    }
    (args.combined_records / "report.json").write_text(json.dumps(report))
    record = {"start_date": "2026-09-01", "end_date": "2026-10-02"}
    (args.combined_records / "FPT-record.json").write_text(json.dumps(record))
    if changed_proof:
        args.proofs.write_text("[1]")
    calls = []

    async def replay(settings, symbol, combined_record, retain_rows):
        calls.append(symbol)
        assert retain_rows and settings.vci_volume_proofs == args.proofs
        assert combined_record == record
        stamp = date_bounds(args.start_date) + 3 * 3600
        return {
            "rows": [Candle("vn", "FPT", "1m", stamp, 100, 101, 99, 100, 120)],
            "captured_pages_passed": False,
            "error": "older combined capture remains rejected",
            "accepted_rows": 1,
        }

    monkeypatch.setattr(review_vn_recent_disagreements, "replay_record", replay)
    if changed_proof:
        with pytest.raises(DataError, match="proof catalog differs"):
            run(args)
        assert not calls and not args.output.exists()
    else:
        result = run(args)
        assert calls == ["FPT"]
        assert result["sqlite_totals"]["vci"]["shared_rows"] == 1
        observed = result["series"][0]["observations"]["vci"]
        assert observed["original_collection_error"] == "original rejection"
        assert not observed["combined_capture_replay"]["captured_pages_passed"]
        assert observed["combined_capture_replay"]["replay_error"]
        assert result["combined_vci_evidence"]["proofs_sha256"] == report["proofs_sha256"]
        assert not result["perfect_data_proven"]
