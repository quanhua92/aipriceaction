import gzip
import json
from dataclasses import replace
from pathlib import Path

import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.domain import Candle, DataError
from aipriceaction_api.vci_volume import validate_volume_proof
from aipriceaction_api.vci_volume_projection import project_legacy_volume
from scripts.check_legacy_volume_projection import rehearse


def fixture():
    proof = json.loads(
        gzip.decompress(
            (Path(__file__).parent / "fixtures/vci_fpt_volume_proof.json.gz").read_bytes()
        )
    )
    rows = []
    for raw in proof["source_rows"]:
        value = {k: v for k, v in raw.items() if k != "cumulative_volume"}
        row = Candle(**value)
        rows.append(
            replace(
                row,
                open=row.open * 2,
                high=row.high * 2,
                low=row.low * 2,
                close=row.close * 2,
                provider="legacy-api",
                revision="frozen-legacy",
                updated_at=proof["verified_at_ns"] - 1,
            )
        )
    return proof, rows


def test_projection_preserves_legacy_price_basis_and_provenance():
    proof, rows = fixture()
    target = validate_volume_proof(proof)
    result = project_legacy_volume(rows, proof)
    assert len(result) == len(rows)
    assert sum(row.volume for row in result) == proof["daily_witnesses"][0]["volume"]
    for before, after in zip(rows, result, strict=True):
        if before.time == target.time:
            assert after == replace(before, volume=target.volume)
        else:
            assert after == before


def test_parquet_rehearsal_preserves_original_and_cleans_temporary_storage(tmp_path):
    proof, rows = fixture()
    before = list(rows)
    report = rehearse(rows, proof, tmp_path)
    assert report["rollback_round_trip_verified"]
    assert report["temporary_storage_removed"]
    assert report["original_checksum"] != report["candidate_checksum"]
    assert rows == before
    assert list(tmp_path.iterdir()) == []


def test_failed_archive_rehearsal_also_cleans_temporary_storage(tmp_path, monkeypatch):
    proof, rows = fixture()

    def fail(*args, **kwargs):
        raise DataError("Archive verification failed")

    monkeypatch.setattr(Archive, "read", fail)
    with pytest.raises(DataError, match="Archive verification failed"):
        rehearse(rows, proof, tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "defect",
    ["missing", "duplicate", "order", "volume", "provider", "symbol", "revision", "newer", "proof"],
)
def test_projection_rejects_incomplete_revised_or_uncorroborated_observations(defect):
    proof, rows = fixture()
    if defect == "missing":
        rows.pop()
    elif defect == "duplicate":
        rows.append(rows[-1])
    elif defect == "order":
        rows.reverse()
    elif defect == "proof":
        proof["daily_witnesses"][0]["volume"] += 1
    else:
        changes = {
            "volume": {"volume": rows[0].volume + 1},
            "provider": {"provider": "vps"},
            "symbol": {"symbol": "MWG"},
            "revision": {"revision": "other"},
            "newer": {"updated_at": proof["verified_at_ns"] + 1},
        }
        rows[0] = replace(rows[0], **changes[defect])
    with pytest.raises(DataError):
        project_legacy_volume(rows, proof)
