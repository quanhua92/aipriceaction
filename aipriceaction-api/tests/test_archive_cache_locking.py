import hashlib
import multiprocessing
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from aipriceaction_api.archive import Archive, FileStore, sha256
from aipriceaction_api.domain import Candle, DataError


def _evict_cache_in_child(cache_dir, cache_bytes):
    settings = SimpleNamespace(cache_dir=Path(cache_dir), cache_bytes=cache_bytes)
    Archive(None, settings, store=object())._evict()


def _cache_settings(path, cache_bytes=1024 * 1024):
    return SimpleNamespace(cache_dir=Path(path), cache_bytes=cache_bytes)


class _CountingFileStore(FileStore):
    def __init__(self, root, counter, counter_lock):
        super().__init__(root)
        self.counter, self.counter_lock = counter, counter_lock

    def download(self, key, path):
        with self.counter_lock:
            self.counter.value += 1
        time.sleep(0.2)
        super().download(key, path)


def _read_archive_in_child(
    cache_dir, object_dir, cache_bytes, obj, barrier, results, counter, counter_lock
):
    settings = _cache_settings(cache_dir, cache_bytes)
    archive = Archive(
        None,
        settings,
        store=_CountingFileStore(object_dir, counter, counter_lock),
    )
    try:
        barrier.wait(timeout=10)
        rows = archive.read(obj)
        results.put(("ok", [row.symbol for row in rows]))
    except Exception as exc:
        results.put(("error", repr(exc)))


def _release_cache_pin_and_retry(cache_dir, cache_bytes, checksum, ready, release):
    archive = Archive(None, _cache_settings(cache_dir, cache_bytes), store=object())
    # Model a long-lived API/worker process that already completed its initial
    # scan and therefore has no process-local retry pending.
    archive._evict_if_needed(False)
    with archive._cache_lock(checksum) as acquired:
        if not acquired:
            raise RuntimeError("could not acquire test cache pin")
        ready.set()
        if not release.wait(timeout=10):
            raise RuntimeError("timed out waiting to release test cache pin")
    archive._evict_if_needed(False)


def test_reads_for_distinct_checksums_can_overlap(tmp_path, monkeypatch):
    settings = _cache_settings(tmp_path / "cache")
    settings.cache_dir.mkdir()
    archive = Archive(None, settings, store=object())
    rows_by_path = {}
    objects = []
    for symbol in ("AAA", "BBB"):
        raw = f"parquet-placeholder-{symbol}".encode()
        checksum = hashlib.sha256(raw).hexdigest()
        path = settings.cache_dir / f"{checksum}.parquet"
        path.write_bytes(raw)
        rows_by_path[str(path)] = (
            "vn",
            symbol,
            "1D",
            86400,
            10.0,
            11.0,
            9.0,
            10.0,
            100,
            "fixture",
            "revision",
            1,
        )
        objects.append(
            {
                "checksum": checksum,
                "source": "vn",
                "symbol": symbol,
                "interval": "1D",
                "revision": "revision",
                "provider": "fixture",
            }
        )

    barrier = threading.Barrier(2)
    eviction_calls = 0
    eviction_guard = threading.Lock()

    def count_eviction():
        nonlocal eviction_calls
        with eviction_guard:
            eviction_calls += 1
        return False

    monkeypatch.setattr(archive, "_evict", count_eviction)
    columns = (
        "source",
        "symbol",
        "interval",
        "time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "provider",
        "revision",
        "updated_at",
    )

    class Cursor:
        description = [(column,) for column in columns]

        def __init__(self, path):
            self.path = path

        def fetchall(self):
            return [rows_by_path[self.path]]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _query, args):
            barrier.wait(timeout=3)
            return Cursor(args[0])

    monkeypatch.setattr("aipriceaction_api.archive.duckdb.connect", lambda **_kwargs: Connection())
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(archive.read, obj) for obj in objects]
        rows = [future.result(timeout=5) for future in futures]

    assert [[row.symbol for row in result] for result in rows] == [["AAA"], ["BBB"]]
    assert eviction_calls == 1


def test_eviction_skips_an_object_during_archive_read_in_another_process(tmp_path, monkeypatch):
    import aipriceaction_api.archive as archive_module

    cache_dir = tmp_path / "cache"
    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    source = object_dir / "active.parquet"
    row = Candle("vn", "AAA", "1D", 86400, 10, 11, 9, 10, 100, "fixture", "revision", 1)
    writer = Archive(None, _cache_settings(cache_dir), store=object())
    writer._write([row], source)
    cache_bytes = source.stat().st_size
    settings = _cache_settings(cache_dir, cache_bytes=cache_bytes)
    archive = Archive(None, settings, store=FileStore(object_dir))
    checksum = sha256(source)
    obj = {
        "checksum": checksum,
        "object_key": "active.parquet",
        "source": "vn",
        "symbol": "AAA",
        "interval": "1D",
        "revision": "revision",
        "provider": "fixture",
    }
    cached = cache_dir / f"{checksum}.parquet"
    query_entered = threading.Event()
    query_release = threading.Event()
    original_connect = archive_module.duckdb.connect

    class BlockingConnection:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def execute(self, query, args):
            query_entered.set()
            if not query_release.wait(timeout=10):
                raise RuntimeError("test timed out waiting to release DuckDB")
            return self.connection.execute(query, args)

    monkeypatch.setattr(
        archive_module.duckdb,
        "connect",
        lambda **kwargs: BlockingConnection(original_connect(**kwargs)),
    )

    context = multiprocessing.get_context("spawn")
    with ThreadPoolExecutor(max_workers=1) as pool:
        read_future = pool.submit(archive.read, obj)
        worker = None
        try:
            assert query_entered.wait(timeout=5)
            other_raw = b"x"
            other_checksum = hashlib.sha256(other_raw).hexdigest()
            other = cache_dir / f"{other_checksum}.parquet"
            other.write_bytes(other_raw)
            newer_ns = time.time_ns() + 5_000_000_000
            os.utime(other, ns=(newer_ns, newer_ns))
            worker = context.Process(
                target=_evict_cache_in_child, args=(str(cache_dir), cache_bytes)
            )
            worker.start()
            worker.join(timeout=10)
            assert not worker.is_alive()
            assert worker.exitcode == 0
            assert cached.exists()
        finally:
            if worker is not None and worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
            query_release.set()
        assert [item.symbol for item in read_future.result(timeout=10)] == ["AAA"]

    assert cached.exists() and sha256(cached) == checksum


def test_two_processes_materialize_the_same_checksum_once(tmp_path):
    cache_dir = tmp_path / "cache"
    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    writer = Archive(None, _cache_settings(cache_dir), store=object())
    source = object_dir / "same.parquet"
    row = Candle("vn", "AAA", "1D", 86400, 10, 11, 9, 10, 100, "fixture", "revision", 1)
    writer._write([row], source)
    checksum = sha256(source)
    obj = {
        "checksum": checksum,
        "object_key": "same.parquet",
        "source": "vn",
        "symbol": "AAA",
        "interval": "1D",
        "revision": "revision",
        "provider": "fixture",
    }

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    results = context.Queue()
    downloads = context.Value("i", 0)
    downloads_lock = context.Lock()
    arguments = (
        str(cache_dir),
        str(object_dir),
        1024 * 1024,
        obj,
        barrier,
        results,
        downloads,
        downloads_lock,
    )
    workers = [context.Process(target=_read_archive_in_child, args=arguments) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=15)
            assert not worker.is_alive()
            assert worker.exitcode == 0
        assert [results.get(timeout=2) for _ in workers] == [("ok", ["AAA"])] * 2
        assert downloads.value == 1
        cached = cache_dir / f"{checksum}.parquet"
        assert sha256(cached) == checksum
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)


def test_invalid_checksum_is_rejected_before_cache_mutation(tmp_path):
    cache_dir = tmp_path / "cache"
    archive = Archive(None, _cache_settings(cache_dir), store=object())

    with pytest.raises(DataError, match="Invalid archive checksum"):
        archive.read({"checksum": "../outside", "object_key": "unused"})

    assert not cache_dir.exists()


def test_eviction_removes_stray_parquet_without_failing(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    stray = cache_dir / "legacy-name.parquet"
    stray.write_bytes(b"orphan")
    archive = Archive(None, _cache_settings(cache_dir, cache_bytes=1), store=object())

    assert archive._evict() is False
    assert not stray.exists()


def test_query_failure_releases_cross_process_cache_lock(tmp_path, monkeypatch):
    settings = _cache_settings(tmp_path / "cache")
    settings.cache_dir.mkdir()
    archive = Archive(None, settings, store=object())
    raw = b"parquet-placeholder"
    checksum = hashlib.sha256(raw).hexdigest()
    (settings.cache_dir / f"{checksum}.parquet").write_bytes(raw)
    obj = {
        "checksum": checksum,
        "source": "vn",
        "symbol": "AAA",
        "interval": "1D",
        "revision": "revision",
        "provider": "fixture",
    }

    def fail_connect(**_kwargs):
        raise RuntimeError("injected DuckDB failure")

    monkeypatch.setattr("aipriceaction_api.archive.duckdb.connect", fail_connect)
    with pytest.raises(DataError, match="Invalid Parquet archive"):
        archive.read(obj)

    with archive._cache_lock(checksum, blocking=False) as acquired:
        assert acquired


def test_failed_read_after_materialization_still_evicts(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    source = object_dir / "source.parquet"
    row = Candle("vn", "AAA", "1D", 86400, 10, 11, 9, 10, 100, "fixture", "revision", 1)
    settings = _cache_settings(cache_dir)
    writer = Archive(None, settings, store=object())
    writer._write([row], source)
    settings.cache_bytes = source.stat().st_size
    archive = Archive(None, settings, store=FileStore(object_dir))
    archive._evict_if_needed(False)

    old = cache_dir / "older.parquet"
    old.write_bytes(b"old")
    os.utime(old, ns=(1, 1))
    checksum = sha256(source)
    obj = {
        "checksum": checksum,
        "object_key": "source.parquet",
        "source": "vn",
        "symbol": "AAA",
        "interval": "1D",
        "revision": "revision",
        "provider": "fixture",
    }

    def fail_connect(**_kwargs):
        raise RuntimeError("injected DuckDB failure")

    monkeypatch.setattr("aipriceaction_api.archive.duckdb.connect", fail_connect)
    with pytest.raises(DataError, match="Invalid Parquet archive"):
        archive.read(obj)

    assert (cache_dir / f"{checksum}.parquet").is_file()
    assert not old.exists()
    assert not archive._eviction_marker.exists()


def test_marker_precedes_atomic_cache_replace(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    source = object_dir / "source.parquet"
    row = Candle("vn", "AAA", "1D", 86400, 10, 11, 9, 10, 100, "fixture", "revision", 1)
    settings = _cache_settings(cache_dir)
    writer = Archive(None, settings, store=object())
    writer._write([row], source)
    settings.cache_bytes = source.stat().st_size
    archive = Archive(None, settings, store=FileStore(object_dir))
    archive._evict_if_needed(False)

    old = cache_dir / "older.parquet"
    old.write_bytes(b"old")
    os.utime(old, ns=(1, 1))
    checksum = sha256(source)
    cached = cache_dir / f"{checksum}.parquet"
    obj = {
        "checksum": checksum,
        "object_key": "source.parquet",
        "source": "vn",
        "symbol": "AAA",
        "interval": "1D",
        "revision": "revision",
        "provider": "fixture",
    }
    original_touch = Path.touch

    def fail_cached_touch(path, *args, **kwargs):
        if path == cached:
            raise OSError("injected timestamp update failure")
        return original_touch(path, *args, **kwargs)

    monkeypatch.setattr(Path, "touch", fail_cached_touch)
    with pytest.raises(OSError, match="timestamp update"):
        archive.read(obj)

    assert cached.is_file()
    assert not old.exists()
    assert not archive._eviction_marker.exists()


def test_eviction_cannot_clear_marker_between_publish_and_replace(tmp_path, monkeypatch):
    from contextlib import contextmanager

    import aipriceaction_api.archive as archive_module

    cache_dir = tmp_path / "cache"
    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    source = object_dir / "source.parquet"
    row = Candle("vn", "AAA", "1D", 86400, 10, 11, 9, 10, 100, "fixture", "revision", 1)
    settings = _cache_settings(cache_dir)
    writer = Archive(None, settings, store=object())
    writer._write([row], source)
    settings.cache_bytes = source.stat().st_size
    archive = Archive(None, settings, store=FileStore(object_dir))
    evictor = Archive(None, settings, store=object())
    archive._evict_if_needed(False)
    evictor._evict_if_needed(False)

    old_bytes = b"old"
    old_checksum = hashlib.sha256(old_bytes).hexdigest()
    old = cache_dir / f"{old_checksum}.parquet"
    old.write_bytes(old_bytes)
    os.utime(old, ns=(1, 1))
    checksum = sha256(source)
    cached = cache_dir / f"{checksum}.parquet"
    obj = {
        "checksum": checksum,
        "object_key": "source.parquet",
        "source": "vn",
        "symbol": "AAA",
        "interval": "1D",
        "revision": "revision",
        "provider": "fixture",
    }

    replace_entered = threading.Event()
    allow_replace = threading.Event()
    touch_entered = threading.Event()
    allow_touch_failure = threading.Event()
    eviction_started = threading.Event()
    real_replace = os.replace
    original_touch = Path.touch

    def pause_replace(source_path, destination_path):
        if Path(destination_path) == cached:
            replace_entered.set()
            if not allow_replace.wait(timeout=10):
                raise RuntimeError("timed out waiting to replace test cache object")
        return real_replace(source_path, destination_path)

    def fail_cached_touch(path, *args, **kwargs):
        if path == cached:
            touch_entered.set()
            if not allow_touch_failure.wait(timeout=10):
                raise RuntimeError("timed out waiting to fail test timestamp update")
            raise OSError("injected timestamp update failure")
        return original_touch(path, *args, **kwargs)

    original_cache_lock = evictor._cache_lock

    @contextmanager
    def observe_eviction_lock(name, blocking=True):
        if name == "eviction":
            eviction_started.set()
        with original_cache_lock(name, blocking=blocking) as acquired:
            yield acquired

    monkeypatch.setattr(archive_module.os, "replace", pause_replace)
    monkeypatch.setattr(Path, "touch", fail_cached_touch)
    monkeypatch.setattr(evictor, "_cache_lock", observe_eviction_lock)
    # Simulate the writer process exiting immediately after the touch failure;
    # only the shared marker can make the next process retry eviction.
    monkeypatch.setattr(archive, "_evict_if_needed", lambda _changed: None)

    with evictor._cache_lock(old_checksum):
        with ThreadPoolExecutor(max_workers=2) as pool:
            read_future = pool.submit(archive.read, obj)
            assert replace_entered.wait(timeout=5)
            assert archive._eviction_marker.is_file()
            eviction_future = pool.submit(evictor._evict)
            assert eviction_started.wait(timeout=5)
            assert not eviction_future.done()

            allow_replace.set()
            assert touch_entered.wait(timeout=5)
            assert eviction_future.result(timeout=5) is True
            assert archive._eviction_marker.is_file()
            allow_touch_failure.set()
            with pytest.raises(OSError, match="timestamp update"):
                read_future.result(timeout=5)
            assert archive._eviction_marker.is_file()

    evictor._evict_if_needed(False)
    assert sum(path.stat().st_size for path in cache_dir.glob("*.parquet")) <= settings.cache_bytes
    assert not archive._eviction_marker.exists()


def test_eviction_retry_marker_is_shared_across_processes(tmp_path):
    cache_dir = tmp_path / "cache"
    raw = b"pinned cache object larger than the cache budget"
    checksum = hashlib.sha256(raw).hexdigest()
    ready = multiprocessing.get_context("spawn").Event()
    release = multiprocessing.get_context("spawn").Event()
    worker = multiprocessing.get_context("spawn").Process(
        target=_release_cache_pin_and_retry,
        args=(str(cache_dir), 1, checksum, ready, release),
    )
    worker.start()
    try:
        assert ready.wait(timeout=10)
        cached = cache_dir / f"{checksum}.parquet"
        cached.write_bytes(raw)
        archive = Archive(None, _cache_settings(cache_dir, cache_bytes=1), store=object())

        assert archive._evict() is True
        assert cached.exists()
        assert archive._eviction_marker.is_file()

        release.set()
        worker.join(timeout=10)
        assert not worker.is_alive()
        assert worker.exitcode == 0
        assert not cached.exists()
        assert not archive._eviction_marker.exists()
    finally:
        release.set()
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=5)
