import hashlib
import json
import os
import time
from dataclasses import replace

import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError
from aipriceaction_api.storage import Repository

DAY = 86400


@pytest.fixture
def env(tmp_path):
    settings = replace(
        Settings(),
        database=tmp_path / "db",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        archive_backend="filesystem",
    )
    repo = Repository(settings.database)
    repo.initialize()
    return Archive(repo, settings), settings


def mkey(settings, name):
    digest = hashlib.sha256(name.encode()).hexdigest()
    return f"{settings.s3_prefix}/manifests/{digest}.json"


def add(archive, key, age_seconds, content=b"{}"):
    """Create an object whose mtime is age_seconds in the past."""
    path = archive.store.path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    t = time.time() - age_seconds
    os.utime(path, (t, t))
    return key


def make(archive, settings, ages, latest=None):
    """ages: {name: age_seconds}. latest: name LATEST.json points to."""
    keys = {n: add(archive, mkey(settings, n), a, n.encode()) for n, a in ages.items()}
    if latest is not None:
        add(
            archive,
            f"{settings.s3_prefix}/LATEST.json",
            0,
            json.dumps({"key": keys[latest], "checksum": hashlib.sha256(latest.encode()).hexdigest()}).encode(),
        )
    return keys


def exists(archive, key):
    return archive.store.path(key).exists()


def test_dry_run_deletes_nothing(env):
    archive, s = env
    keys = make(archive, s, {"a": 10 * DAY, "b": 9 * DAY, "c": 8 * DAY}, latest="c")
    result = archive.gc_manifests(keep=1, min_age_seconds=0)
    assert result["dry_run"] is True
    assert set(result["deleted"]) == {keys["a"], keys["b"]}
    assert all(exists(archive, k) for k in keys.values())


def test_latest_target_survives_when_oldest_and_keep_zero(env):
    archive, s = env
    keys = make(archive, s, {"old": 10 * DAY, "new1": 2 * DAY, "new2": DAY}, latest="old")
    result = archive.gc_manifests(keep=0, min_age_seconds=0, dry_run=False)
    assert keys["old"] in result["kept"]
    assert keys["old"] not in result["deleted"]
    assert exists(archive, keys["old"])
    assert not exists(archive, keys["new1"])
    assert not exists(archive, keys["new2"])


def test_keep_n_newest_by_mtime(env):
    archive, s = env
    keys = make(
        archive, s, {"a": 5 * DAY, "b": 4 * DAY, "c": 3 * DAY, "d": 2 * DAY, "e": DAY}, latest="e"
    )
    result = archive.gc_manifests(keep=3, min_age_seconds=0, dry_run=False)
    assert set(result["kept"]) == {keys["c"], keys["d"], keys["e"]}
    assert set(result["deleted"]) == {keys["a"], keys["b"]}
    assert not exists(archive, keys["a"]) and not exists(archive, keys["b"])
    assert all(exists(archive, keys[n]) for n in "cde")


def test_min_age_respected(env):
    archive, s = env
    keys = make(
        archive, s, {"old": 10 * DAY, "mid": 3 * DAY, "young": 60, "latest": 30}, latest="latest"
    )
    result = archive.gc_manifests(keep=0, min_age_seconds=5 * DAY, dry_run=False)
    assert result["deleted"] == [keys["old"]]
    assert not exists(archive, keys["old"])
    for n in ("mid", "young", "latest"):
        assert exists(archive, keys[n])


def test_unrelated_keys_untouched(env):
    archive, s = env
    keys = make(archive, s, {"a": 10 * DAY, "b": 9 * DAY, "c": DAY}, latest="c")
    others = [
        add(archive, f"{s.s3_prefix}/parquet/vn/FPT/1m/part.parquet", 100 * DAY),
        add(archive, f"{s.s3_prefix}/other/readme.txt", 100 * DAY),
    ]
    latest_key = f"{s.s3_prefix}/LATEST.json"
    result = archive.gc_manifests(keep=0, min_age_seconds=0, dry_run=False)
    assert set(result["deleted"]) == {keys["a"], keys["b"]}
    assert all(exists(archive, k) for k in others)
    assert exists(archive, latest_key)
    assert latest_key not in result["deleted"]
    assert not set(others) & set(result["deleted"])


def test_empty_manifests_dir(env):
    archive, s = env
    result = archive.gc_manifests(keep=2, min_age_seconds=0, dry_run=False)
    assert result["kept"] == [] and result["deleted"] == []
    assert result["dry_run"] is False
    add(archive, f"{s.s3_prefix}/LATEST.json", 0, b"{}")
    assert archive.gc_manifests(keep=2, min_age_seconds=0)["deleted"] == []


def test_store_list_and_delete(env):
    archive, s = env
    keys = make(archive, s, {"a": DAY, "b": 2 * DAY})
    listed = dict(archive.store.list(f"{s.s3_prefix}/manifests/"))
    assert set(listed) == set(keys.values())
    assert abs(listed[keys["a"]] - (time.time() - DAY)) < 60
    archive.store.delete(keys["a"])
    assert not exists(archive, keys["a"])


def test_missing_latest_fails_closed(env):
    archive, settings = env
    keys = make(archive, settings, {"a": 5 * DAY, "b": 6 * DAY})
    with pytest.raises(DataError):
        archive.gc_manifests(keep=0, min_age_seconds=0, dry_run=False)
    assert all(exists(archive, k) for k in keys.values())


def test_bad_latest_checksum_fails_closed(env):
    archive, settings = env
    keys = make(archive, settings, {"a": 5 * DAY, "b": 6 * DAY})
    add(
        archive,
        f"{settings.s3_prefix}/LATEST.json",
        0,
        json.dumps({"key": keys["a"], "checksum": "0" * 64}).encode(),
    )
    with pytest.raises(DataError):
        archive.gc_manifests(keep=0, min_age_seconds=0, dry_run=False)
    assert all(exists(archive, k) for k in keys.values())
