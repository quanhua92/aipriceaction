from dataclasses import replace

import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, parse_time
from aipriceaction_api.storage import Repository
from scripts.audit_archive_ohlcv import audit


def setup(tmp_path):
    settings = replace(
        Settings(),
        database=tmp_path / "live.sqlite3",
        object_dir=tmp_path / "objects",
        cache_dir=tmp_path / "runtime-cache",
        archive_backend="filesystem",
    )
    repo = Repository(settings.database)
    repo.initialize()
    rows = [
        Candle("vn", "FPT", "1D", parse_time(f"2020-01-0{n}"), 10, 11, 9, 10, 100) for n in (1, 2)
    ]
    repo.put(rows)
    obj = Archive(repo, settings).publish(repo.read("vn", "FPT", "1D"))
    return settings, repo, obj


def test_full_cold_audit_preserves_objects_and_runtime_cache(tmp_path):
    settings, repo, obj = setup(tmp_path)
    image = repo.path.read_bytes()
    raw = (settings.object_dir / obj["object_key"]).read_bytes()
    cached = list(settings.cache_dir.iterdir())
    result = audit(settings)
    assert result["structural_validity_passed"] and result["verified_rows"] == 2
    assert result["temporary_cache_removed"] and result["perfect_data_proven"] is False
    assert repo.path.read_bytes() == image
    assert (settings.object_dir / obj["object_key"]).read_bytes() == raw
    assert list(settings.cache_dir.iterdir()) == cached


def test_pending_repair_remains_visible_even_when_structure_passes(tmp_path):
    settings, repo, _ = setup(tmp_path)
    with repo.connect() as con:
        con.execute("UPDATE archives SET status='pending_repair'")
    result = audit(settings)
    assert result["structural_validity_passed"]
    assert result["pending_repair_objects"] == 1
    assert result["objects"][0]["status"] == "pending_repair"


@pytest.mark.parametrize("corruption", ["checksum", "count", "identity", "bounds"])
def test_cold_audit_reports_corrupt_bytes_and_index_metadata(tmp_path, corruption):
    settings, repo, obj = setup(tmp_path)
    if corruption == "checksum":
        (settings.object_dir / obj["object_key"]).write_bytes(b"corrupt")
    else:
        with repo.connect() as con:
            if corruption == "count":
                con.execute("UPDATE archives SET row_count=row_count+1")
            if corruption == "identity":
                con.execute("UPDATE archives SET provider='different'")
            if corruption == "bounds":
                con.execute("UPDATE archives SET start=start+86400")
    result = audit(settings)
    assert result["failed_objects"] == 1 and not result["structural_validity_passed"]
    assert result["objects"][0]["passed"] is False
    assert result["temporary_cache_removed"]
