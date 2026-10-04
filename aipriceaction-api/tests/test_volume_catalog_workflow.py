import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError
from aipriceaction_api.vci_volume import validate_volume_proof
from aipriceaction_api.vci_volume_projection import project_legacy_volume
from aipriceaction_api.volume_corrections import publish_corrections
from scripts import check_volume_catalog_restore as verifier
from scripts import publish_legacy_volume_catalog as publisher
from scripts.check_legacy_volume_projection import checksum
from tests.test_volume_correction_batch import candidates
from tests.test_volume_correction_receipts import system as system


def inputs(system, tmp_path):
    repo, archive, settings, rows, proof, raw = system
    catalog = tmp_path / "proofs.json"
    catalog.write_text(json.dumps([proof]))
    target = validate_volume_proof(proof)
    staged = project_legacy_volume(rows, proof)
    audit = {
        "proofs_sha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
        "series": [
            {
                "symbol": target.symbol,
                "time": target.time,
                "status": "projection_available",
                "original_checksum": checksum(rows),
                "candidate_checksum": checksum(staged),
            }
        ],
    }
    captures = tmp_path / "captures"
    captures.mkdir()
    path = captures / f"native-{proof['source_capture_sha256']}.json"
    path.write_bytes(raw)
    return catalog, audit, captures, path


def test_batch_preview_is_bound_to_audit_and_does_not_publish(system, tmp_path, monkeypatch):
    repo, archive, settings, rows, proof, raw = system
    catalog, audit, captures, path = inputs(system, tmp_path)
    before = repo.path.read_bytes()
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(audit))
    monkeypatch.setattr(
        Settings, "from_env", classmethod(lambda cls: replace(settings, archive_backend="s3"))
    )
    result = publisher.run(
        SimpleNamespace(
            audit=audit_path,
            proofs=catalog,
            captures=captures,
            execute=False,
            output=tmp_path / "preview",
        )
    )
    assert not result["canonical_publication"]
    assert len(result["targets"]) == 1
    assert repo.path.read_bytes() == before
    assert repo.volume_corrections() == []


@pytest.mark.parametrize("defect", ["catalog", "capture", "missing", "snapshot"])
def test_batch_preparation_rejects_changed_evidence(system, tmp_path, defect):
    repo, archive, settings, rows, proof, raw = system
    catalog, audit, captures, path = inputs(system, tmp_path)
    if defect == "catalog":
        catalog.write_text("[]")
    elif defect == "capture":
        path.write_bytes(b"[]")
    elif defect == "missing":
        path.unlink()
    else:
        repo.put([replace(rows[0], volume=rows[0].volume + 1)])
    with pytest.raises(DataError):
        publisher.prepare(repo, audit, catalog, captures)
    assert repo.volume_corrections() == []


@pytest.mark.parametrize("failure", [False, True])
def test_batch_verification_cleans_temporary_index_on_success_and_http_failure(
    system, tmp_path, monkeypatch, failure
):
    repo, archive, settings, rows, proof, raw = system
    selected, second_rows = candidates(system)
    records = publish_corrections(repo, archive, selected)
    activation = tmp_path / "activation.json"
    activation.write_text(
        json.dumps({"canonical_publication": True, "receipt_ids": [row["id"] for row in records]})
    )
    monkeypatch.setattr(
        Settings, "from_env", classmethod(lambda cls: replace(settings, archive_backend="s3"))
    )
    monkeypatch.setattr(
        verifier,
        "Archive",
        lambda repo, config: Archive(repo, replace(config, archive_backend="filesystem")),
    )

    def http(*args):
        if failure:
            raise DataError("HTTP mismatch")
        return {"tested": True}

    monkeypatch.setattr(verifier, "verify_http", http)
    output = tmp_path / "verification"
    args = SimpleNamespace(activation=activation, output=output, base_url="http://127.0.0.1:3001")
    if failure:
        with pytest.raises(DataError, match="HTTP mismatch"):
            verifier.run(args)
    else:
        result = verifier.run(args)
        assert result["completed"] and result["temporary_storage_removed"]
        assert len(result["series"]) == 2
        assert not result["remote_writes"]
    assert [path.name for path in output.iterdir()] == ["report.json"]
    assert repo.read("vn", proof["symbol"], "1m") == rows
    assert repo.read("vn", "VCB", "1m") == second_rows
