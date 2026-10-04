import asyncio
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, date_bounds
from aipriceaction_api.storage import Repository
from scripts.extend_vci_replacement_candidates import expand_floor, run
from scripts.replay_vci_candidate_pages import replay_record


def inputs(tmp_path, contradiction=False):
    stamps = [
        date_bounds("2025-09-02") + 3 * 3600,
        date_bounds("2025-09-03") + 3 * 3600,
        date_bounds("2025-09-03") + 3 * 3600 + 60,
        date_bounds("2025-10-06") + 3 * 3600,
    ]
    payload = [
        {
            "symbol": "FPT",
            "t": stamps,
            "o": [10] * 4,
            "h": [11] * 4,
            "l": [9] * 4,
            "c": [10] * 4,
            "v": [100] * 4,
            "accumulatedVolume": [100, 100, 250 if contradiction else 200, 100],
        }
    ]
    raw = json.dumps(payload).encode()
    path = tmp_path / "native.json"
    path.write_bytes(raw)
    before = date_bounds("2025-10-07")
    record = {
        "start_date": "2025-10-04",
        "end_date": "2025-10-06",
        "window": {"pages": [{"before": before, "cursor": stamps[0], "rows": 1}]},
        "captures": [
            {
                "path": str(path),
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "status": 200,
            }
        ],
    }
    return replace(Settings(), vci_history_fallback=True), record, path


def test_expansion_reuses_verified_raw_bytes_and_preserves_all_old_rows(tmp_path):
    settings, original, path = inputs(tmp_path)
    before = path.read_bytes()
    expanded = asyncio.run(expand_floor(settings, "FPT", original, "2025-09-03"))
    result = asyncio.run(replay_record(settings, "FPT", expanded, retain_rows=True))
    assert expanded["captures"] == original["captures"]
    assert expanded["window"]["pages"][0]["rows"] == 3
    assert result["captured_pages_passed"] and result["accepted_rows"] == 3
    assert result["rows"][-1].time == date_bounds("2025-10-06") + 3 * 3600
    assert path.read_bytes() == before
    assert len(list(tmp_path.iterdir())) == 1


def test_older_contradiction_is_retained_as_a_replayable_refusal(tmp_path):
    settings, original, _ = inputs(tmp_path, contradiction=True)
    assert asyncio.run(replay_record(settings, "FPT", original))["captured_pages_passed"]
    expanded = asyncio.run(expand_floor(settings, "FPT", original, "2025-09-03"))
    assert "contradicts cumulative" in expanded["error"]
    assert expanded["window"]["pages"] == []
    assert len(expanded["captures"]) == 1
    assert not asyncio.run(replay_record(settings, "FPT", expanded))["captured_pages_passed"]


@pytest.mark.parametrize("change", ["floor", "capture", "checkpoint", "rejected"])
def test_changed_evidence_or_scope_is_rejected(tmp_path, change):
    settings, original, path = inputs(tmp_path)
    floor = "2025-09-03"
    if change == "floor":
        floor = "2025-10-05"
    elif change == "capture":
        path.write_bytes(path.read_bytes() + b" ")
    elif change == "checkpoint":
        original["window"]["pages"][0]["rows"] = 2
    else:
        original["error"] = "unresolved source refusal"
    with pytest.raises(DataError):
        asyncio.run(expand_floor(settings, "FPT", original, floor))


def test_eligible_candidate_extends_without_database_copies_or_network(tmp_path, monkeypatch):
    settings, record, _ = inputs(tmp_path)
    settings = replace(settings, database=tmp_path / "live.sqlite3")
    repo = Repository(settings.database)
    repo.initialize()
    repo.put([Candle("vn", "FPT", "1m", date_bounds("2025-09-03") + 3 * 3600, 10, 11, 9, 10, 100)])
    original_database = settings.database.read_bytes()
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    review, basis = tmp_path / "review", tmp_path / "basis"
    review.mkdir()
    basis.mkdir()
    proofs = tmp_path / "proofs.json"
    proofs.write_text("[]")
    source_record = json.dumps(record).encode()
    (review / "FPT-record.json").write_bytes(source_record)
    raw_review = json.dumps(
        {"completed": True, "proofs_sha256": hashlib.sha256(proofs.read_bytes()).hexdigest()}
    ).encode()
    (review / "report.json").write_bytes(raw_review)
    basis_report = {
        "completed": True,
        "review_sha256": hashlib.sha256(raw_review).hexdigest(),
        "proofs_sha256": hashlib.sha256(proofs.read_bytes()).hexdigest(),
        "series": [
            {
                "symbol": "FPT",
                "record_sha256": hashlib.sha256(source_record).hexdigest(),
                "days": [{}],
                "summary": {
                    "native_volume_witness_exceptions": [],
                    "daily_comparisons": {
                        "sqlite_daily": {
                            "vci_minutes": {"observed_days": 1, "price_within_1_vnd_days": 1}
                        }
                    },
                },
            }
        ],
    }
    (basis / "report.json").write_text(json.dumps(basis_report))
    args = SimpleNamespace(
        review=review, basis=basis, proofs=proofs, output=tmp_path / "result", max_pages=2
    )
    result = asyncio.run(run(args))
    assert result["completed"] and not result["canonical_publication"]
    assert result["series"][0]["candidate_ready"]
    assert result["series"][0]["accepted_rows"] == 3
    assert not list(args.output.rglob("*.sqlite3*"))
    assert settings.database.read_bytes() == original_database
