"""Local-only persistence/Parquet proof; run write, restart RustFS, then read."""

import argparse
import json
import tempfile
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle
from aipriceaction_api.history import History
from aipriceaction_api.storage import Repository


def setup(state):
    settings = replace(
        Settings.from_env(),
        database=Path(state["root"]) / "db.sqlite3",
        cache_dir=Path(state["root"]) / "cache",
        archive_backend="s3",
        s3_prefix=state["prefix"],
    )
    if urlparse(settings.s3_endpoint or "").hostname not in ("localhost", "127.0.0.1", "::1"):
        raise SystemExit("This validation command requires a loopback S3 endpoint")
    repo = Repository(settings.database)
    repo.initialize()
    archive = Archive(repo, settings)
    return repo, archive, History(repo, archive, settings)


def rows():
    origin = datetime(2020, 1, 1, tzinfo=UTC)
    return [
        Candle(
            "vn",
            "TEST",
            "1D",
            int((origin + timedelta(days=i)).timestamp()),
            100 + i,
            101 + i,
            99 + i,
            100 + i,
            1000 + i,
            "fixture",
        )
        for i in range(360)
    ]


def write(path):
    state = {
        "root": tempfile.mkdtemp(prefix="aipa-rustfs-"),
        "prefix": "validation/" + uuid.uuid4().hex,
    }
    repo, archive, history = setup(state)
    archive.store.initialize()
    repo.put(rows())
    state["expected"] = history.query("vn", "TEST", "1D", limit=5)
    snapshot = repo.read("vn", "TEST", "1D", end=rows()[239].time)
    state["object"] = archive.publish(snapshot, prune=True)
    assert len(repo.read("vn", "TEST", "1D")) == 120
    assert history.query("vn", "TEST", "1D", limit=5) == state["expected"]
    path.write_text(json.dumps(state))
    print(
        json.dumps(
            {
                "phase": "write",
                "object_rows": 240,
                "local_rows": 120,
                "verified": True,
                "state": str(path),
            }
        )
    )


def read(path):
    state = json.loads(path.read_text())
    repo, archive, history = setup(state)
    obj = state["object"]
    cached = archive.settings.cache_dir / (obj["checksum"] + ".parquet")
    cached.unlink(missing_ok=True)
    response = archive.store.client.get_object(
        Bucket=archive.settings.s3_bucket, Key=obj["object_key"], Range="bytes=0-3"
    )
    with response["Body"] as body:
        assert body.read() == b"PAR1"
    assert response["ResponseMetadata"]["HTTPStatusCode"] == 206
    begun = time.perf_counter()
    cold = history.query("vn", "TEST", "1D", start=rows()[229].time, limit=120)
    cold_ms = (time.perf_counter() - begun) * 1000
    begun = time.perf_counter()
    warm = history.query("vn", "TEST", "1D", start=rows()[229].time, limit=120)
    warm_ms = (time.perf_counter() - begun) * 1000
    assert cold == warm
    assert len(cold) == 120
    assert history.query("vn", "TEST", "1D", limit=5) == state["expected"]
    fresh = Repository(Path(state["root"]) / (uuid.uuid4().hex + ".sqlite3"))
    fresh.initialize()
    recovery = Archive(fresh, archive.settings)
    assert recovery.restore_index() == 1
    assert len(History(fresh, recovery, archive.settings).read("vn", "TEST", "1D")) == 240
    print(
        json.dumps(
            {
                "phase": "read",
                "persisted": True,
                "range_status": 206,
                "restored_rows": 240,
                "boundary_rows": len(cold),
                "cold_ms": round(cold_ms, 2),
                "warm_ms": round(warm_ms, 2),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("write", "read"))
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    (write if args.phase == "write" else read)(args.state)
