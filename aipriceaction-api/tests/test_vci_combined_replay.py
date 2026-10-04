import asyncio
import hashlib
import json
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from aipriceaction_api.domain import DataError
from aipriceaction_api.storage import Repository
from scripts.replay_vci_candidate_pages import replay_record
from scripts.review_vci_combined_captures import combined_record
from tests.test_vci_candidate_replay import inputs


def test_successful_captures_replay_with_exact_counts_and_observed_dates(tmp_path):
    proof, settings, original, path = inputs(tmp_path, True)
    baseline = asyncio.run(replay_record(settings, proof["symbol"], original))
    successful = original | {"window": {"pages": baseline["pages"]}}
    del successful["error"]
    result = asyncio.run(replay_record(settings, proof["symbol"], successful, retain_rows=True))
    assert result["captured_pages_passed"]
    assert len(result["rows"]) == result["accepted_rows"] == len(proof["source_rows"])
    assert sum(result["rows_by_date"].values()) == result["accepted_rows"]
    assert not result["requested_year_proven"]
    successful["window"]["pages"][0]["rows"] += 1
    changed = asyncio.run(replay_record(settings, proof["symbol"], successful))
    assert not changed["captured_pages_passed"]
    assert "row count" in changed["error"]


def test_combination_keeps_references_and_rejects_changed_seam(tmp_path):
    proof, settings, original, path = inputs(tmp_path, True)
    baseline = asyncio.run(replay_record(settings, proof["symbol"], original))
    continued = {
        "symbol": proof["symbol"],
        "seam_verified": True,
        "captured_rows": baseline["accepted_rows"],
        "pages": [],
        "stop": "requested_boundary",
        "captures": original["captures"],
    }
    combined = asyncio.run(combined_record(settings, proof["symbol"], original, continued))
    assert "error" not in combined
    assert combined["captures"] == original["captures"]
    assert combined["window"]["pages"] == baseline["pages"]
    raw = json.loads(path.read_bytes())
    raw[0]["o"][0] *= 0.99
    changed_raw = json.dumps(raw).encode()
    fresh = tmp_path / "fresh.json"
    fresh.write_bytes(changed_raw)
    continued["captures"] = [
        {
            "path": str(fresh),
            "status": 200,
            "bytes": len(changed_raw),
            "sha256": hashlib.sha256(changed_raw).hexdigest(),
        }
    ]
    with pytest.raises(DataError, match="seam no longer matches"):
        asyncio.run(combined_record(settings, proof["symbol"], original, continued))


def test_combination_requires_complete_capture_accounting(tmp_path):
    proof, settings, original, path = inputs(tmp_path, True)
    baseline = asyncio.run(replay_record(settings, proof["symbol"], original))
    continued = {
        "symbol": proof["symbol"],
        "seam_verified": True,
        "captured_rows": baseline["accepted_rows"],
        "pages": [],
        "stop": "provider_error",
        "captures": original["captures"],
        "error": "failed older page",
    }
    with pytest.raises(DataError, match="capture/page count differs"):
        asyncio.run(combined_record(settings, proof["symbol"], original, continued))


def test_replay_rejects_disconnected_page_chain_before_parsing(tmp_path):
    proof, settings, original, path = inputs(tmp_path, True)
    baseline = asyncio.run(replay_record(settings, proof["symbol"], original))
    record = original | {
        "window": {
            "pages": baseline["pages"]
            + [
                {
                    "before": baseline["next_cursor"] - 60,
                    "cursor": baseline["next_cursor"] - 120,
                    "rows": 0,
                }
            ]
        },
        "captures": original["captures"] * 2,
    }
    del record["error"]
    with pytest.raises(DataError, match="skips the preceding"):
        asyncio.run(replay_record(settings, proof["symbol"], record))


def test_combined_review_uses_corrected_captures_and_readonly_sqlite_without_copies(
    tmp_path, monkeypatch
):
    from scripts import review_vci_combined_captures as module

    proof, settings, original, raw_path = inputs(tmp_path, True)
    symbol, day = proof["symbol"], original["start_date"]
    replay = asyncio.run(replay_record(settings, symbol, original, retain_rows=True))
    settings = replace(settings, database=tmp_path / "live.sqlite3")
    repo = Repository(settings.database)
    repo.initialize()
    # Deliberately store one different volume in SQLite so review must expose it.
    rows = replay["rows"]
    target = next(row for row in rows if row.time == proof["source_rows"][0]["time"])
    repo.put(
        [replace(row, volume=row.volume + 1) if row.time == target.time else row for row in rows]
    )
    before = settings.database.read_bytes()
    audit = tmp_path / "audit"
    batch = audit / "batch-000"
    (batch / "vci").mkdir(parents=True)
    (batch / "vci" / f"{symbol}-1m.json").write_text(json.dumps(original))
    comparison = {
        "symbols": [symbol],
        "feeds": ["vps", "vndirect", "dnse", "vci"],
        "intervals": ["1m"],
        "daily_start": day,
        "intraday_start": day,
        "end_date": day,
    }
    (batch / "report.json").write_text(json.dumps(comparison))
    for feed in comparison["feeds"][:3]:
        (batch / feed).mkdir()
        (batch / feed / f"{symbol}-1m.json").write_text(
            json.dumps({"rows": [asdict(row) for row in rows]})
        )
    (audit / "summary.json").write_text(
        json.dumps(
            {
                "completed": True,
                "symbols": [symbol],
                "batches": [{"path": str(batch), "symbols": [symbol]}],
            }
        )
    )
    (audit / "request.json").write_text(
        json.dumps({"symbols": [symbol], "start_date": day, "end_date": day})
    )
    continuation = tmp_path / "continuation"
    continuation.mkdir()
    (continuation / "report.json").write_text(json.dumps({"completed": True, "series": []}))
    calendar = tmp_path / "calendar.json"
    calendar.write_text("{}")
    monkeypatch.setattr(module.Settings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(module, "expected_dates", lambda *args: {day})
    output = tmp_path / "review"
    result = asyncio.run(
        module.run(
            SimpleNamespace(
                audit=audit,
                continuation=continuation,
                proofs=settings.vci_volume_proofs,
                calendar_catalog=calendar,
                output=output,
            )
        )
    )
    assert result["completed"] and not result["perfect_data_proven"]
    assert not result["remote_requests"] and not result["canonical_publication"]
    checked = result["series"][0]
    assert checked["missing_reference_date_candidates"] == []
    assert checked["sqlite_comparison"]["unanimous_provider_conflicts"]["count"] == 1
    assert checked["sqlite_comparison"]["providers"]["vci"]["counts"]["volume_disagreements"] == 1
    assert settings.database.read_bytes() == before
    assert not list(output.rglob("*.sqlite*"))
    assert json.loads((output / f"{symbol}-record.json").read_text())["rows"] == []
    # A subsequent review can use saved references after the original normalized
    # record is gone; it must still bind its continuation to the exact base report.
    (batch / "vci" / f"{symbol}-1m.json").unlink()
    round2 = tmp_path / "round2"
    round2.mkdir()
    next_report = {
        "completed": True,
        "series": [],
        "combined_records": str(output),
        "combined_review_sha256": hashlib.sha256((output / "report.json").read_bytes()).hexdigest(),
    }
    (round2 / "report.json").write_text(json.dumps(next_report))
    next_args = SimpleNamespace(
        audit=audit,
        continuation=round2,
        base_records=output,
        proofs=settings.vci_volume_proofs,
        calendar_catalog=calendar,
        output=tmp_path / "review2",
    )
    reviewed = asyncio.run(module.run(next_args))
    assert reviewed["accepted_rows"] == result["accepted_rows"]
    assert reviewed["series"][0]["sqlite_comparison"] == checked["sqlite_comparison"]
    assert settings.database.read_bytes() == before
    next_report["combined_review_sha256"] = "tampered"
    (round2 / "report.json").write_text(json.dumps(next_report))
    next_args.output = tmp_path / "rejected"
    with pytest.raises(DataError, match="different base combined review"):
        asyncio.run(module.run(next_args))
    assert not next_args.output.exists()
