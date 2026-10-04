import gzip
import json
import sqlite3
from pathlib import Path

import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError
from aipriceaction_api.storage import Repository
from aipriceaction_api.volume_corrections import make_record
from scripts import audit_legacy_volume_projections as module
from tests.test_vci_volume_projection import fixture


def setup(tmp_path):
    proof, rows = fixture()
    repo = Repository(tmp_path / "live.sqlite3")
    repo.initialize()
    repo.put(rows)
    with repo.connect() as con:
        con.execute("UPDATE candles SET updated_at=?", (proof["verified_at_ns"] - 1,))
    return proof, repo


def review(repo, proofs):
    with sqlite3.connect(repo.path.resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN")
        return module.audit(con, proofs, proofs[0]["day"], proofs[0]["day"] + 86400)


def test_catalog_review_preserves_price_basis_and_database(tmp_path, monkeypatch):
    proof, repo = setup(tmp_path)
    before = repo.path.read_bytes()
    records = review(repo, [proof])
    assert records[0]["status"] == "projection_available"
    assert records[0]["candidate_day_volume"] == proof["daily_witnesses"][0]["volume"]
    assert records[0]["original_checksum"] != records[0]["candidate_checksum"]
    assert repo.path.read_bytes() == before
    path = tmp_path / "proofs.json"
    path.write_text(json.dumps([proof]))
    monkeypatch.setattr(
        module.Settings, "from_env", classmethod(lambda cls: Settings(database=repo.path))
    )
    output = tmp_path / "report"
    from datetime import UTC, datetime
    from types import SimpleNamespace

    day = datetime.fromtimestamp(proof["day"], UTC).strftime("%Y-%m-%d")
    report = module.run(SimpleNamespace(proofs=path, start_date=day, end_date=day, output=output))
    assert not report["publication_license"]
    assert not report["remote_requests"]
    assert repo.path.read_bytes() == before
    assert [f.name for f in output.iterdir()] == ["report.json"]


@pytest.mark.parametrize("defect", ["missing", "volume", "repairing", "revision"])
def test_catalog_keeps_frozen_observation_and_basis_refusals(tmp_path, defect):
    proof, repo = setup(tmp_path)
    with repo.connect() as con:
        if defect == "missing":
            con.execute("DELETE FROM candles WHERE time=?", (proof["source_rows"][0]["time"],))
        elif defect == "volume":
            con.execute(
                "UPDATE candles SET volume=volume+1 WHERE time=?",
                (proof["source_rows"][0]["time"],),
            )
        elif defect == "repairing":
            con.execute("UPDATE series SET status='repairing'")
        else:
            con.execute("UPDATE series SET revision='changed'")
    before = repo.path.read_bytes()
    record = review(repo, [proof])[0]
    assert record["status"] == "projection_blocked"
    assert "error" in record
    assert repo.path.read_bytes() == before


def test_duplicate_catalog_targets_are_rejected_and_old_dates_are_excluded(tmp_path):
    proof, repo = setup(tmp_path)
    with pytest.raises(DataError, match="Duplicate"):
        review(repo, [proof, proof])
    with repo.connect() as con:
        report = module.audit(con, [proof], proof["day"] + 86400, proof["day"] + 2 * 86400)
    assert report[0]["status"] == "outside_requested_window"


def test_peer_proof_is_not_misrepresented_as_legacy_projection_license(tmp_path):
    proof, repo = setup(tmp_path)
    peer = json.loads(
        gzip.decompress(
            (Path(__file__).parent / "fixtures/vci_gas_peer_volume_proof.json.gz").read_bytes()
        )
    )
    report = review(repo, [peer])[0]
    assert report["status"] == "projection_blocked"
    assert "one cumulative-volume correction" in report["error"]


def test_replayed_existing_receipt_is_distinguished_from_new_candidate(tmp_path):
    proof, repo = setup(tmp_path)
    rows = repo.read("vn", proof["symbol"], "1m", proof["day"], proof["day"] + 86399)
    record = make_record(rows, proof, {"original": "fixture"}, "fixture-capture")
    repo.record_volume_correction(record, rows)
    assert review(repo, [proof])[0]["status"] == "already_applied"
