import gzip
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError
from aipriceaction_api.history import History
from aipriceaction_api.storage import Repository
from aipriceaction_api.vci_volume import proof_from_capture, validate_volume_proof
from aipriceaction_api.volume_corrections import make_record, publish_correction


@pytest.fixture
def system(tmp_path):
    saved = json.loads(
        gzip.decompress(
            (Path(__file__).parent / "fixtures/vci_fpt_volume_proof.json.gz").read_bytes()
        )
    )
    body = {"symbol": saved["symbol"]}
    for key, field in (
        ("t", "time"),
        ("o", "open"),
        ("h", "high"),
        ("l", "low"),
        ("c", "close"),
        ("v", "volume"),
        ("accumulatedVolume", "cumulative_volume"),
    ):
        body[key] = [row[field] for row in saved["source_rows"]]
    raw = json.dumps([body]).encode()
    settings = Settings(
        database=tmp_path / "live.sqlite3",
        archive_backend="filesystem",
        object_dir=tmp_path / "objects",
        cache_dir=tmp_path / "cache",
    )
    repo = Repository(settings.database)
    repo.initialize()
    rows = [
        replace(
            Candle(**{k: v for k, v in row.items() if k != "cumulative_volume"}),
            provider="legacy-api",
            revision="frozen",
            open=row["open"] * 2,
            high=row["high"] * 2,
            low=row["low"] * 2,
            close=row["close"] * 2,
        )
        for row in saved["source_rows"]
    ]
    repo.put(rows)
    rows = repo.read("vn", saved["symbol"], "1m")
    proof = proof_from_capture(
        raw, saved["daily_witnesses"], validate_volume_proof(saved).time, time.time_ns()
    )
    archive = Archive(repo, settings)
    repo.publish_archive(archive.prepare(rows))
    return repo, archive, settings, rows, proof, raw


def test_receipt_serves_corrected_volume_without_mutating_snapshot_and_survives_restore(
    system, tmp_path
):
    repo, archive, settings, rows, proof, raw = system
    record = publish_correction(repo, archive, proof, raw)
    assert publish_correction(repo, archive, proof, raw) == record
    assert repo.volume_corrections() == [record]
    target = validate_volume_proof(proof)
    assert repo.read("vn", target.symbol, "1m") == rows
    result = History(repo, archive, settings).read("vn", target.symbol, "1m")
    assert result == [
        replace(row, volume=target.volume) if row.time == target.time else row for row in rows
    ]
    restored = Repository(tmp_path / "restored.sqlite3")
    restored.initialize()
    cold = Archive(restored, replace(settings, database=restored.path))
    assert cold.restore_index() == 1
    assert restored.volume_corrections() == [record]
    assert History(restored, cold, settings).read("vn", target.symbol, "1m") == result


def test_racing_snapshot_change_rejects_receipt(system):
    repo, archive, settings, rows, proof, raw = system
    record = make_record(rows, proof, archive.prepare(rows), "capture.json")
    repo.put([replace(rows[0], volume=rows[0].volume + 1)])
    with pytest.raises(DataError, match="snapshot changed"):
        repo.record_volume_correction(record, rows)
    assert repo.volume_corrections() == []


def test_changed_original_target_cannot_keep_using_old_correction(system):
    repo, archive, settings, rows, proof, raw = system
    publish_correction(repo, archive, proof, raw)
    target = next(row for row in rows if row.time == validate_volume_proof(proof).time)
    repo.put([replace(target, volume=target.volume + 1)])
    with pytest.raises(DataError, match="changed since"):
        History(repo, archive, settings).read("vn", target.symbol, "1m")


def test_tampered_native_capture_blocks_restore_before_receipt_insertion(system, tmp_path):
    repo, archive, settings, rows, proof, raw = system
    record = publish_correction(repo, archive, proof, raw)
    key = json.loads(record["evidence"])["capture_key"]
    archive.store.path(key).write_bytes(b"[]")
    restored = Repository(tmp_path / "restored.sqlite3")
    restored.initialize()
    cold = Archive(restored, replace(settings, database=restored.path))
    with pytest.raises(DataError, match="native capture differs"):
        cold.restore_index()
    assert restored.volume_corrections() == []
    assert restored.archives() == []


def test_busy_series_prevents_publication_and_releases_archive_lease(system):
    repo, archive, settings, rows, proof, raw = system
    assert repo.live_claim("vn", proof["symbol"], "1m", "other")
    with pytest.raises(DataError, match="series is busy"):
        publish_correction(repo, archive, proof, raw)
    assert repo.volume_corrections() == []
    assert repo.live_claim("vn", "__ARCHIVE_WRITER__", "1D", "check")
