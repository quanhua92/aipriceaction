import json
import time
from dataclasses import replace

import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.domain import DataError
from aipriceaction_api.history import History
from aipriceaction_api.storage import Repository
from aipriceaction_api.vci_volume import proof_from_capture, validate_volume_proof
from aipriceaction_api.volume_corrections import make_record, publish_corrections
from tests.test_volume_correction_receipts import system as system


def candidates(system):
    repo, archive, settings, rows, proof, raw = system
    body = json.loads(raw)
    body[0]["symbol"] = "VCB"
    second_raw = json.dumps(body).encode()
    second_rows = [replace(row, symbol="VCB") for row in rows]
    repo.put(second_rows)
    second_rows = repo.read("vn", "VCB", "1m")
    witnesses = [row | {"symbol": "VCB"} for row in proof["daily_witnesses"]]
    second = proof_from_capture(
        second_raw, witnesses, validate_volume_proof(proof).time, time.time_ns()
    )
    repo.publish_archive(archive.prepare(second_rows))
    return [(proof, raw), (second, second_raw)], second_rows


def test_batch_is_atomic_preserves_raw_prices_and_restores_from_one_manifest(
    system, tmp_path, monkeypatch
):
    repo, archive, settings, rows, proof, raw = system
    selected, second_rows = candidates(system)
    calls = []
    manifest = archive.manifest

    def publish(objects):
        calls.append(len(repo.volume_corrections()))
        return manifest(objects)

    monkeypatch.setattr(archive, "manifest", publish)
    records = publish_corrections(repo, archive, selected)
    assert calls == [2]
    assert repo.read("vn", proof["symbol"], "1m") == rows
    assert repo.read("vn", "VCB", "1m") == second_rows
    restored = Repository(tmp_path / "restored.sqlite3")
    restored.initialize()
    cold = Archive(restored, replace(settings, database=restored.path))
    assert cold.restore_index() == 2
    assert {row["id"] for row in restored.volume_corrections()} == {row["id"] for row in records}
    for candidate, original in zip(selected, [rows, second_rows], strict=True):
        target = validate_volume_proof(candidate[0])
        expected = [
            replace(row, volume=target.volume) if row.time == target.time else row
            for row in original
        ]
        assert History(restored, cold, settings).read("vn", target.symbol, "1m") == expected
    assert publish_corrections(repo, archive, selected) == records
    assert calls == [2, 2]


def test_race_in_later_day_rolls_back_entire_batch(system, monkeypatch):
    repo, archive, settings, rows, proof, raw = system
    selected, second_rows = candidates(system)
    original = repo.record_volume_corrections

    def race(pairs):
        repo.put([replace(second_rows[0], volume=second_rows[0].volume + 1)])
        original(pairs)

    monkeypatch.setattr(repo, "record_volume_corrections", race)
    with pytest.raises(DataError, match="snapshot changed"):
        publish_corrections(repo, archive, selected)
    assert repo.volume_corrections() == []
    assert repo.live_claim("vn", "__ARCHIVE_WRITER__", "1D", "check")
    assert repo.live_claim("vn", proof["symbol"], "1m", "check")
    assert repo.live_claim("vn", "VCB", "1m", "check")


def test_later_busy_series_releases_all_acquired_leases_without_publication(system):
    repo, archive, settings, rows, proof, raw = system
    selected, second_rows = candidates(system)
    assert repo.live_claim("vn", "VCB", "1m", "other")
    with pytest.raises(DataError, match="series is busy"):
        publish_corrections(repo, archive, selected)
    assert repo.volume_corrections() == []
    assert repo.live_claim("vn", proof["symbol"], "1m", "check")
    assert repo.live_claim("vn", "__ARCHIVE_WRITER__", "1D", "check")


def test_manifest_failure_keeps_whole_verified_batch_and_retry_repairs_pointer(system, monkeypatch):
    repo, archive, settings, rows, proof, raw = system
    selected, second_rows = candidates(system)
    manifest = archive.manifest

    def fail(objects):
        raise DataError("Manifest pointer failed")

    monkeypatch.setattr(archive, "manifest", fail)
    with pytest.raises(DataError, match="pointer failed"):
        publish_corrections(repo, archive, selected)
    records = repo.volume_corrections()
    assert len(records) == 2
    monkeypatch.setattr(archive, "manifest", manifest)
    result = publish_corrections(repo, archive, selected)
    assert {row["id"] for row in result} == {row["id"] for row in records}
    assert len(repo.volume_corrections()) == 2


def test_changed_native_capture_rejects_batch_before_evidence_writes(system, monkeypatch):
    repo, archive, settings, rows, proof, raw = system
    selected, second_rows = candidates(system)
    monkeypatch.setattr(
        archive,
        "prepare",
        lambda *args: pytest.fail("No evidence writes before native proof validation"),
    )
    with pytest.raises(DataError, match="capture checksum differs"):
        publish_corrections(repo, archive, [selected[0], (selected[1][0], b"[]")])
    assert repo.volume_corrections() == []


def test_duplicate_target_rejects_batch_before_leases(system):
    repo, archive, settings, rows, proof, raw = system
    with pytest.raises(DataError, match="repeats a target"):
        publish_corrections(repo, archive, [(proof, raw), (proof, raw)])
    assert repo.volume_corrections() == []


def test_original_evidence_must_match_every_expected_snapshot(system):
    repo, archive, settings, rows, proof, raw = system
    selected, second_rows = candidates(system)
    records = [
        make_record(original, candidate[0], archive.prepare(original), "capture")
        for original, candidate in zip([rows, second_rows], selected, strict=True)
    ]
    changed = [
        replace(row, volume=row.volume + 1) if index == 0 else row
        for index, row in enumerate(second_rows)
    ]
    with pytest.raises(DataError, match="original evidence"):
        repo.record_volume_corrections([(records[0], rows), (records[1], changed)])
    assert repo.volume_corrections() == []


def test_two_different_correction_targets_cannot_double_correct_same_day(system):
    repo, archive, settings, rows, proof, raw = system
    body = json.loads(raw)
    body[0]["t"] = [stamp + 60 for stamp in body[0]["t"]]
    shifted_raw = json.dumps(body).encode()
    shifted = proof_from_capture(
        shifted_raw,
        proof["daily_witnesses"],
        validate_volume_proof(proof).time + 60,
        time.time_ns(),
    )
    with pytest.raises(DataError, match="repeats a source day"):
        publish_corrections(repo, archive, [(proof, raw), (shifted, shifted_raw)])
    assert repo.volume_corrections() == []
