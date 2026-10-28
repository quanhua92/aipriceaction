import json
import time

import pytest

from aipriceaction_api.domain import Candle
from aipriceaction_api.storage import Repository


@pytest.fixture
def receipt(tmp_path):
    repo = Repository(tmp_path / "db")
    repo.initialize()
    stamp = int(time.time()) // 86400 * 86400
    row = Candle("vn", "ECI", "1D", stamp, 100, 101, 99, 100, 10, "vps", "new")
    repo.put([row])
    obj = {
        "id": "replacement", "source": "vn", "symbol": "ECI", "interval": "1D",
        "start": stamp - 86400, "end": stamp - 86400, "row_count": 1,
        "object_key": "parquet/old", "checksum": "checksum", "revision": "old",
        "provider": "dnse", "status": "pending_repair", "created_at": stamp,
    }
    target = {"old": {"source": "vn", "symbol": "ECI", "interval": "1D", "row_count": 1},
              "provider": "dnse", "revision": "old", "replacement": obj}
    payload = json.dumps({"version": 1, "targets": [target]})
    with repo.connect() as con:
        con.execute(
            "INSERT INTO archives(id,source,symbol,interval,start,end,row_count,object_key,checksum,revision,provider,status,created_at) VALUES (:id,:source,:symbol,:interval,:start,:end,:row_count,:object_key,:checksum,:revision,:provider,:status,:created_at)", obj,
        )
        con.execute(
            "INSERT INTO archive_repair_batches(id,status,payload,lease_owner,lease_until,created_at,updated_at,error) VALUES (?,'manifest_published',?,?,?,?,?,?)",
            ("receipt", payload, "worker", int(time.time()) + 60, stamp, stamp, "CSV still pending"),
        )
    kind = "archive_repair:replacement:new"
    job_id = repo.queue("vn", "ECI", "1D", kind, stamp - 86400, "vps")
    return repo, obj, payload, job_id


def test_superseded_receipt_releases_queue_without_losing_objects_or_jobs(receipt):
    repo, obj, payload, job_id = receipt
    assert repo.retire_superseded_archive_repair_batch("receipt", "worker")
    with repo.connect() as con:
        batch = con.execute("SELECT * FROM archive_repair_batches WHERE id='receipt'").fetchone()
        assert batch["status"] == "failed"
        assert batch["payload"] == payload
        assert "CSV still pending" in batch["error"]
        assert con.execute("SELECT status FROM archives WHERE id=?", (obj["id"],)).fetchone()[0] == "pending_repair"
        assert con.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == "pending"


def test_new_archive_repair_does_not_select_a_disabled_provider(receipt):
    repo, _, _, _ = receipt
    with repo.connect() as con:
        con.execute("UPDATE archive_repair_batches SET status='failed' WHERE id='receipt'")
    assert repo.ensure_archive_repair_batch(excluded_vn_providers={"vps"}) is None
    assert repo.ensure_archive_repair_batch()["payload"]["targets"][0]["provider"] == "vps"


@pytest.mark.parametrize("changed", ["dirty_manifest", "same_revision", "missing_job", "published_object", "expired_lease", "indexed_receipt", "mixed_targets"])
def test_receipt_is_not_retired_without_complete_supersession_proof(receipt, changed):
    repo, obj, payload, job_id = receipt
    with repo.connect() as con:
        if changed == "dirty_manifest":
            con.execute("UPDATE archive_manifest_state SET generation=generation+1")
        elif changed == "same_revision":
            con.execute("UPDATE series SET provider='dnse',revision='old' WHERE symbol='ECI'")
        elif changed == "missing_job":
            con.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job_id,))
        elif changed == "published_object":
            con.execute("UPDATE archives SET status='published' WHERE id=?", (obj["id"],))
        elif changed == "expired_lease":
            con.execute("UPDATE archive_repair_batches SET lease_until=0 WHERE id='receipt'")
        elif changed == "indexed_receipt":
            con.execute("UPDATE archive_repair_batches SET status='indexed' WHERE id='receipt'")
        else:
            decoded = json.loads(payload)
            decoded["targets"].append(replace_target := dict(decoded["targets"][0]))
            replace_target["revision"] = "new"
            con.execute("UPDATE archive_repair_batches SET payload=? WHERE id='receipt'", (json.dumps(decoded),))
    assert not repo.retire_superseded_archive_repair_batch("receipt", "worker")
    with repo.connect() as con:
        assert con.execute("SELECT status FROM archive_repair_batches WHERE id='receipt'").fetchone()[0] != "failed"
