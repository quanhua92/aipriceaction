import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from aipriceaction_api.archive import Archive, FileStore
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, date_bounds
from aipriceaction_api.storage import Repository
from scripts import rehearse_captured_vci_replacement as rehearsal


class Client:
    def __init__(self, root):
        self.root, self.deleted = root, []

    def list_objects_v2(self, Bucket, Prefix, MaxKeys):
        keys = sorted(
            str(path.relative_to(self.root))
            for path in self.root.rglob("*")
            if path.is_file() and str(path.relative_to(self.root)).startswith(Prefix)
        )
        return {"Contents": [{"Key": key} for key in keys[:MaxKeys]]}

    def delete_objects(self, Bucket, Delete):
        for obj in Delete["Objects"]:
            self.deleted.append(obj["Key"])
            (self.root / obj["Key"]).unlink()
        return {}

    def get_bucket_versioning(self, Bucket):
        return {}


def test_versioned_bucket_is_refused_before_retaining_test_versions():
    client = SimpleNamespace(get_bucket_versioning=lambda **_: {"Status": "Enabled"})
    store = SimpleNamespace(client=client, settings=SimpleNamespace(s3_bucket="local"))
    with pytest.raises(DataError, match="unversioned local bucket"):
        rehearsal.require_unversioned_bucket(store)


def test_cleanup_cannot_delete_a_canonical_namespace(tmp_path):
    root = tmp_path / "objects"
    root.mkdir()
    store = SimpleNamespace(client=Client(root), settings=SimpleNamespace(s3_bucket="local"))
    canonical = root / "archive-v2/LATEST.json"
    canonical.parent.mkdir()
    canonical.write_text("original")
    prefix = "archive-v2/candidates/captured-rehearsal-" + "a" * 32
    file = root / prefix / "test.json"
    file.parent.mkdir(parents=True)
    file.write_text("temporary")
    with pytest.raises(DataError):
        rehearsal.cleanup_prefix(store, "archive-v2", "archive-v2")
    assert rehearsal.cleanup_prefix(store, prefix, "archive-v2") == 1
    assert canonical.read_text() == "original" and not file.exists()


@pytest.mark.parametrize("failure", [None, "volume", "price", "changed_rows", "source_error"])
def test_full_native_daily_replay_is_required_for_every_candidate_session(tmp_path, failure):
    stamp = date_bounds("2025-09-03")
    minute = Candle("vn", "FPT", "1m", stamp + 3 * 3600, 10000, 11000, 9000, 10000, 100, "vci")
    retained = replace(
        minute,
        time=stamp,
        interval="1D",
        close=10200 if failure == "price" else 10000,
        provider="vps",
    )
    for feed in ("vps", "vndirect", "dnse"):
        folder = tmp_path / feed
        folder.mkdir()
        volume = 200 if failure == "volume" else 100
        payload = {
            "symbol": "FPT",
            "s": "ok",
            "t": [stamp],
            "o": [10],
            "h": [11],
            "l": [9],
            "c": [10],
            "v": [volume],
        }
        raw = json.dumps(payload).encode()
        capture = folder / "capture.json"
        capture.write_bytes(raw)
        rows = [
            {
                "time": stamp,
                "open": 10000,
                "high": 11000,
                "low": 9000,
                "close": 10000,
                "volume": volume + 1 if failure == "changed_rows" else volume,
            }
        ]
        record = {
            "start_date": "2025-09-03",
            "end_date": "2025-09-03",
            "rows": rows,
            "captures": [
                {
                    "path": str(capture),
                    "bytes": len(raw),
                    "status": 200,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            ],
        }
        if failure == "source_error":
            record["error"] = "earlier invalid native source response"
        (folder / "FPT-1D.json").write_text(json.dumps(record))
    if failure:
        with pytest.raises(DataError):
            asyncio.run(rehearsal.daily_checks(Settings(), tmp_path, "FPT", [minute], [retained]))
    else:
        result = asyncio.run(
            rehearsal.daily_checks(Settings(), tmp_path, "FPT", [minute], [retained])
        )
        assert result["observed_days"] == 1
        assert result["maximum_retained_daily_price_difference_vnd"] == 0
        assert result["native_volume_witnesses"][0]["matching_native_feeds"] == [
            "vps",
            "vndirect",
            "dnse",
        ]


@pytest.mark.parametrize("failure", [False, True])
def test_success_and_failure_remove_every_owned_database_and_object(tmp_path, monkeypatch, failure):
    settings = replace(
        Settings(),
        database=tmp_path / "live.sqlite3",
        archive_backend="s3",
        s3_endpoint="http://127.0.0.1:9100",
        s3_prefix="archive-v2",
    )
    main = Repository(settings.database)
    main.initialize()
    stamps = [date_bounds(day) + 3 * 3600 for day in ["2025-09-02", "2025-09-03", "2025-09-05"]]
    main.put([Candle("vn", "FPT", "1m", t, 10, 11, 9, 10, 100, "vps", "old") for t in stamps[1:]])
    before = settings.database.read_bytes()
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    extension = tmp_path / "extension"
    extension.mkdir()
    proofs = tmp_path / "proofs.json"
    proofs.write_text("[]")
    native = json.dumps(
        [
            {
                "symbol": "FPT",
                "t": stamps,
                "o": [10] * 3,
                "h": [11] * 3,
                "l": [9] * 3,
                "c": [10] * 3,
                "v": [100] * 3,
                "accumulatedVolume": [100] * 3,
            }
        ]
    ).encode()
    capture = tmp_path / "native.json"
    capture.write_bytes(native)
    record = {
        "start_date": "2025-09-03",
        "end_date": "2025-09-05",
        "window": {
            "pages": [{"before": date_bounds("2025-09-06"), "cursor": stamps[0], "rows": 2}]
        },
        "captures": [
            {
                "path": str(capture),
                "bytes": len(native),
                "sha256": hashlib.sha256(native).hexdigest(),
                "status": 200,
            }
        ],
    }
    (extension / "FPT-record.json").write_text(json.dumps(record))
    (extension / "report.json").write_text(
        json.dumps(
            {
                "completed": True,
                "canonical_publication": False,
                "proofs_sha256": hashlib.sha256(proofs.read_bytes()).hexdigest(),
                "series": [
                    {
                        "symbol": "FPT",
                        "candidate_ready": True,
                        "accepted_rows": 2,
                        "required_start_date": "2025-09-03",
                    }
                ],
            }
        )
    )
    objects = tmp_path / "objects"
    objects.mkdir()
    store = FileStore(objects)
    store.settings = settings
    store.client = Client(objects)
    sentinel = objects / "archive-v2/LATEST.json"
    sentinel.parent.mkdir()
    sentinel.write_text("canonical")
    monkeypatch.setattr(rehearsal, "S3Store", lambda _: store)
    monkeypatch.setattr(
        rehearsal,
        "Archive",
        lambda repo, settings, store=None: Archive(repo, settings, store=store or globals_store),
    )
    globals_store = store
    monkeypatch.setattr(rehearsal, "cutoff", lambda _: date_bounds("2025-09-04"))
    directories = []
    temporary_directory = rehearsal.tempfile.TemporaryDirectory

    def temporary(**kwargs):
        kwargs.setdefault("dir", tmp_path)
        result = temporary_directory(**kwargs)
        directories.append(Path(result.name))
        return result

    monkeypatch.setattr(rehearsal.tempfile, "TemporaryDirectory", temporary)

    async def checked(*_):
        return {"fixture": "daily-policy checked separately"}

    monkeypatch.setattr(rehearsal, "daily_checks", checked)
    if failure:
        publish = rehearsal.publish

        def fail_after_publish(*args, **kwargs):
            publish(*args, **kwargs)
            raise DataError("Injected failure after isolated publication")

        monkeypatch.setattr(rehearsal, "publish", fail_after_publish)
    args = SimpleNamespace(
        extension=extension, proofs=proofs, daily=tmp_path, output=tmp_path / "result"
    )
    if failure:
        with pytest.raises(DataError, match="Injected failure"):
            asyncio.run(rehearsal.run(args))
    else:
        result = asyncio.run(rehearsal.run(args))
        assert result["passed"] and len(result["series"][0]["query_cases"]) == 18
        assert result["restored_archive_objects"] == 1
    report = json.loads((args.output / "report.json").read_text())
    assert report["temporary_storage_removed"] and report["temporary_s3_prefix_removed"]
    assert report["temporary_s3_objects_deleted"] > 0
    assert directories and not any(path.exists() for path in directories)
    assert not list(args.output.rglob("*.sqlite3*"))
    assert sentinel.read_text() == "canonical"
    assert settings.database.read_bytes() == before
    assert all(key.startswith(report["temporary_s3_prefix"] + "/") for key in store.client.deleted)
