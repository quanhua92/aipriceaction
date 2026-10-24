"""Immutable Parquet objects, checksummed manifests, bounded local read cache.

Use boto3 for explicit object transfers and DuckDB for local Parquet queries.
No runtime extension download or bucket-wide query glob is required.
"""

import csv
import fcntl
import hashlib
import json
import logging
import os
import stat
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import duckdb

from .domain import Candle, DataError, cutoff
from .storage import COLUMNS

log = logging.getLogger(__name__)

_LOCAL_FILE_LOCKS = {}
_LOCAL_FILE_LOCKS_GUARD = threading.Lock()


@contextmanager
def _local_file_lock(path, blocking):
    """Serialize same-process flock users without keeping one lock per object forever."""
    key = os.path.abspath(path)
    with _LOCAL_FILE_LOCKS_GUARD:
        entry = _LOCAL_FILE_LOCKS.get(key)
        if entry is None:
            lock, users = threading.Lock(), 0
        else:
            lock, users = entry
        _LOCAL_FILE_LOCKS[key] = (lock, users + 1)
    acquired = False
    try:
        acquired = lock.acquire(blocking=blocking)
        yield acquired
    finally:
        if acquired:
            lock.release()
        with _LOCAL_FILE_LOCKS_GUARD:
            current_lock, users = _LOCAL_FILE_LOCKS[key]
            if users == 1:
                del _LOCAL_FILE_LOCKS[key]
            else:
                _LOCAL_FILE_LOCKS[key] = (current_lock, users - 1)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FileStore:
    def __init__(self, root):
        self.root = Path(root)

    def path(self, key):
        p = PurePosixPath(key)
        if p.is_absolute() or ".." in p.parts or not p.parts:
            raise DataError("Invalid object key")
        return self.root.joinpath(*p.parts)

    def put(self, key, path):
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
        import shutil

        try:
            shutil.copyfile(path, temp)
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)

    def download(self, key, path):
        import shutil

        try:
            shutil.copyfile(self.path(key), path)
        except FileNotFoundError as exc:
            raise DataError(f"Archive object missing: {key}") from exc

    def read(self, key):
        try:
            return self.path(key).read_bytes()
        except FileNotFoundError as exc:
            raise DataError(f"Archive object missing: {key}") from exc

    def initialize(self):
        self.root.mkdir(parents=True, exist_ok=True)


class S3Store:
    def __init__(self, settings):
        self.settings = settings
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            s = self.settings
            self._client = boto3.client(
                "s3",
                endpoint_url=s.s3_endpoint,
                region_name=s.s3_region,
                aws_access_key_id=s.s3_access_key,
                aws_secret_access_key=s.s3_secret_key,
                config=Config(
                    connect_timeout=5,
                    read_timeout=30,
                    retries={"max_attempts": 3},
                    s3={"addressing_style": "path" if s.s3_path_style else "auto"},
                ),
            )
        return self._client

    def initialize(self):
        from botocore.exceptions import ClientError

        try:
            self.client.head_bucket(Bucket=self.settings.s3_bucket)
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in ("404", "NoSuchBucket", "NotFound"):
                raise DataError("Cannot access configured archive bucket") from exc
            kwargs = {"Bucket": self.settings.s3_bucket}
            if self.settings.s3_region != "us-east-1":
                kwargs["CreateBucketConfiguration"] = {
                    "LocationConstraint": self.settings.s3_region
                }
            self.client.create_bucket(**kwargs)

    def put(self, key, path):
        try:
            self.client.upload_file(
                str(path),
                self.settings.s3_bucket,
                key,
                ExtraArgs={"Metadata": {"sha256": sha256(path)}},
            )
        except Exception as exc:
            raise DataError(f"Archive upload failed: {key}") from exc

    def download(self, key, path):
        try:
            self.client.download_file(self.settings.s3_bucket, key, str(path))
        except Exception as exc:
            raise DataError(f"Archive download failed: {key}") from exc

    def read(self, key):
        try:
            response = self.client.get_object(Bucket=self.settings.s3_bucket, Key=key)
            with response["Body"] as body:
                return body.read()
        except Exception as exc:
            raise DataError(f"Archive manifest read failed: {key}") from exc


class Archive:
    def __init__(self, repo, settings, store=None):
        self.repo, self.settings = repo, settings
        self.store = store or (
            FileStore(settings.object_dir)
            if settings.archive_backend == "filesystem"
            else S3Store(settings)
        )
        self._duckdb_local = threading.local()
        self._cache_eviction_guard = threading.Lock()
        self._cache_needs_check = True

    @contextmanager
    def read_session(self):
        """Reuse one DuckDB connection across a synchronous history batch."""
        existing = getattr(self._duckdb_local, "connection", None)
        if existing is not None:
            yield existing
            return

        connection = duckdb.connect(config={"threads": 1, "memory_limit": "256MB"})
        self._duckdb_local.connection = connection
        try:
            yield connection
        finally:
            del self._duckdb_local.connection
            connection.close()

    @contextmanager
    def _read_connection(self):
        """Use the active batch connection or own a one-object connection."""
        current = getattr(self._duckdb_local, "connection", None)
        if current is not None:
            yield current
            return
        with duckdb.connect(config={"threads": 1, "memory_limit": "256MB"}) as connection:
            yield connection

    @contextmanager
    def _cache_lock(self, name, blocking=True):
        """Take a stable local and cross-process lock in the shared cache directory.

        Lock files are deliberately persistent. Unlinking one can split waiters
        across old and new inodes, while flock itself releases on process exit.
        """
        lock_dir = self.settings.cache_dir / ".locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / f"{name}.lock"
        with _local_file_lock(str(lock_path), blocking) as local_acquired:
            if not local_acquired:
                yield False
                return
            flags = os.O_CREAT | os.O_RDWR
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(lock_path, flags, 0o600)
            acquired = False
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise DataError("Invalid archive cache lock")
                operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
                try:
                    fcntl.flock(descriptor, operation)
                except BlockingIOError:
                    yield False
                    return
                acquired = True
                yield True
            finally:
                if acquired:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    @contextmanager
    def public_csv_lock(self, key):
        """Serialize public CSV writers with a bounded set of cross-process locks.

        A ten-year export can touch millions of object keys; fixed lock stripes
        keep the shared cache directory bounded while allowing unrelated
        objects to publish concurrently in most cases.
        """
        stripe = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:4], "big") % 64
        name = f"public-csv-stripe-{stripe:02d}"
        with self._cache_lock(name) as acquired:
            if not acquired:
                raise DataError("Could not lock public CSV publication")
            yield

    @staticmethod
    def _validate_checksum(checksum):
        if (
            not isinstance(checksum, str)
            or len(checksum) != 64
            or any(character not in "0123456789abcdef" for character in checksum)
        ):
            raise DataError("Invalid archive checksum")
        return checksum

    def _path(self, obj, refresh=False):
        root = self.settings.cache_dir
        root.mkdir(parents=True, exist_ok=True)
        checksum = self._validate_checksum(obj["checksum"])
        path = root / (checksum + ".parquet")
        changed = False
        if path.is_symlink():
            path.unlink()
        if refresh or not path.is_file() or sha256(path) != checksum:
            with tempfile.NamedTemporaryFile(dir=root, suffix=".tmp", delete=False) as file:
                temp = Path(file.name)
            try:
                self.store.download(obj["object_key"], temp)
                if sha256(temp) != checksum:
                    raise DataError("Archive checksum mismatch")
                if temp.stat().st_size > self.settings.cache_bytes:
                    raise DataError("Archive object exceeds cache budget; repartition it")
                # Publish before replacing the cache path so a crash or a
                # failed timestamp update cannot hide a new object from other
                # long-lived Archive processes. Keep the global eviction lock
                # through replace so another process cannot clear the marker
                # after publication but before the new path becomes visible.
                with self._cache_lock("eviction") as acquired:
                    if not acquired:
                        raise DataError("Could not lock archive cache eviction")
                    self._write_eviction_marker()
                    os.replace(temp, path)
                changed = True
            finally:
                temp.unlink(missing_ok=True)
        path.touch()
        return path, changed

    @property
    def _eviction_marker(self):
        return self.settings.cache_dir / ".locks" / "eviction-needed"

    def _write_eviction_marker(self):
        """Create the shared marker while holding the global eviction lock."""
        marker = self._eviction_marker
        flags = os.O_CREAT | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(marker, flags, 0o600)
        os.close(descriptor)

    def _evict(self):
        with self._cache_lock("eviction") as acquired:
            if not acquired:
                return True
            files = []
            for path in self.settings.cache_dir.glob("*.parquet"):
                if path.is_symlink() or not path.is_file():
                    continue
                try:
                    info = path.stat()
                except FileNotFoundError:
                    continue
                files.append((path, info.st_mtime_ns, info.st_size))
            files.sort(key=lambda item: item[1])
            total = sum(size for _, _, size in files)
            for path, scanned_mtime, scanned_size in files:
                if total <= self.settings.cache_bytes:
                    break
                try:
                    lock_name = self._validate_checksum(path.stem)
                except DataError:
                    # Old or stray .parquet files are not addressable by an
                    # archive checksum, but still count against the cache.
                    lock_name = "orphan-" + hashlib.sha256(path.name.encode()).hexdigest()
                # Never wait for an active reader while holding the eviction
                # lock. A later successful read will retry eviction.
                with self._cache_lock(lock_name, blocking=False) as object_acquired:
                    if not object_acquired:
                        continue
                    try:
                        current = path.stat()
                    except FileNotFoundError:
                        continue
                    # A read may have refreshed this object since the scan.
                    if (
                        current.st_mtime_ns != scanned_mtime
                        or current.st_size != scanned_size
                        or path.is_symlink()
                        or not path.is_file()
                    ):
                        continue
                    path.unlink(missing_ok=True)
                    total -= current.st_size
            needs_check = total > self.settings.cache_bytes
            marker = self._eviction_marker
            if needs_check:
                self._write_eviction_marker()
            else:
                marker.unlink(missing_ok=True)
            return needs_check

    def _evict_if_needed(self, cache_changed):
        with self._cache_eviction_guard:
            if cache_changed:
                self._cache_needs_check = True
            local_needs_check = self._cache_needs_check
        if not local_needs_check:
            # Serialize the marker check with marker publication/removal. This
            # lets a process that just released its last cache pin observe a
            # retry another process published while it held that pin.
            with self._cache_lock("eviction") as acquired:
                if not acquired:
                    with self._cache_eviction_guard:
                        self._cache_needs_check = True
                    return
                if not self._eviction_marker.exists():
                    return
        with self._cache_eviction_guard:
            self._cache_needs_check = False
        try:
            needs_check = self._evict()
        except Exception:
            with self._cache_eviction_guard:
                self._cache_needs_check = True
            raise
        if needs_check:
            with self._cache_eviction_guard:
                self._cache_needs_check = True

    def read(self, obj, start=None, end=None, limit=None, refresh=False, forward=False):
        checksum = self._validate_checksum(obj.get("checksum"))
        cache_changed = False
        try:
            with self._cache_lock(checksum) as acquired:
                if not acquired:
                    raise DataError("Could not lock archive cache object")
                path, cache_changed = self._path(obj, refresh)
                where, args = [], [str(path)]
                if start is not None:
                    where.append("time>=?")
                    args.append(start)
                if end is not None:
                    where.append("time<=?")
                    args.append(end)
                query = (
                    "SELECT * FROM read_parquet(?)"
                    + (" WHERE " + " AND ".join(where) if where else "")
                    + (" ORDER BY time ASC" if forward else " ORDER BY time DESC")
                )
                if limit is not None:
                    query += " LIMIT ?"
                    args.append(limit)
                try:
                    with self._read_connection() as con:
                        cursor = con.execute(query, args)
                        columns = [c[0] for c in cursor.description]
                        fetched = cursor.fetchall()
                        result = [
                            Candle(**dict(zip(columns, row, strict=True))).validate()
                            for row in (fetched if forward else reversed(fetched))
                        ]
                except DataError:
                    raise
                except Exception as exc:
                    raise DataError("Invalid Parquet archive") from exc
                if any(
                    (c.source, c.symbol, c.interval, c.revision, c.provider)
                    != (
                        obj["source"],
                        obj["symbol"],
                        obj["interval"],
                        obj["revision"],
                        obj["provider"],
                    )
                    for c in result
                ):
                    raise DataError("Archive identity/revision mismatch")
        except BaseException:
            connection = getattr(self._duckdb_local, "connection", None)
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
            # Keep the original read error if eviction also fails. The shared
            # marker remains available for the next cache operation to retry.
            try:
                self._evict_if_needed(cache_changed)
            except Exception:
                pass
            raise
        self._evict_if_needed(cache_changed)
        return result

    def _write(self, candles, path):
        # Bulk-load through a local typed CSV rather than executing one INSERT
        # per candle. QUOTE_NOTNULL preserves empty strings separately from
        # SQL NULL; repr-based float serialization round-trips IEEE doubles.
        with (
            tempfile.TemporaryDirectory(dir=Path(path).parent) as tmp,
            duckdb.connect(config={"threads": 1, "memory_limit": "256MB"}) as con,
        ):
            source = Path(tmp) / "bars.csv"
            with source.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, quoting=csv.QUOTE_NOTNULL)
                writer.writerow(COLUMNS)
                for candle in candles:
                    record = candle.record()
                    writer.writerow(tuple(record[key] for key in COLUMNS))
            con.execute(
                "CREATE TABLE bars(source VARCHAR,symbol VARCHAR,interval VARCHAR,time BIGINT,open DOUBLE,high DOUBLE,low DOUBLE,close DOUBLE,volume BIGINT,provider VARCHAR,revision VARCHAR,updated_at BIGINT)"
            )
            con.execute(
                "COPY bars FROM ? (FORMAT CSV, HEADER true, ALLOW_QUOTED_NULLS false)",
                [str(source)],
            )
            con.execute(
                "COPY (SELECT * FROM bars ORDER BY time) TO ? (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 16384)",
                [str(path)],
            )

    def prepare(self, candles):
        candles = sorted(candles, key=lambda c: c.time)
        if not candles:
            raise DataError("Cannot archive an empty series")
        first = candles[0]
        self.repo.validate_basis(candles)
        identity = (first.source, first.symbol, first.interval, first.provider, first.revision)
        for c in candles:
            c.validate()
            if (c.source, c.symbol, c.interval, c.provider, c.revision) != identity:
                raise DataError("Cannot archive mixed series revisions")
        if len({c.time for c in candles}) != len(candles):
            raise DataError("Duplicate timestamps in archive")
        self.settings.cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.settings.cache_dir) as tmp:
            path = Path(tmp) / "bars.parquet"
            self._write(candles, path)
            digest = sha256(path)
            # Symbol is encoded so e.g. CL=F, ^GSPC and slash-containing user
            # metadata never escape the intended prefix.
            from urllib.parse import quote

            key = f"{self.settings.s3_prefix}/ohlcv/{first.source}/{quote(first.symbol, safe='')}/{first.interval}/{first.time}-{candles[-1].time}-{digest}.parquet"
            obj = {
                "id": digest,
                "source": first.source,
                "symbol": first.symbol,
                "interval": first.interval,
                "start": first.time,
                "end": candles[-1].time,
                "row_count": len(candles),
                "object_key": key,
                "checksum": digest,
                "revision": first.revision,
                "provider": first.provider,
                "status": "published",
                "schema_version": 1,
                "created_at": int(time.time()),
            }
            self.store.put(key, path)
            restored = self.read(obj, refresh=True)
            if restored != candles:
                raise DataError("Archive verification differs from exported snapshot")
        return obj

    def _write_manifest(self, objects):
        data = json.dumps(
            {
                "version": 1,
                "objects": objects,
                "adoptions": self.repo.adoptions(),
                "recoveries": self.repo.recoveries(),
                "volume_corrections": self.repo.volume_corrections(),
                "history_gaps": self.repo.history_gaps(),
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        digest = hashlib.sha256(data).hexdigest()
        key = f"{self.settings.s3_prefix}/manifests/{digest}.json"
        self.settings.cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.settings.cache_dir) as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_bytes(data)
            self.store.put(key, path)
            if self.store.read(key) != data:
                raise DataError("Archive manifest verification failed")
            path.write_text(json.dumps({"key": key, "checksum": digest}))
            self.store.put(f"{self.settings.s3_prefix}/LATEST.json", path)
            if self.store.read(f"{self.settings.s3_prefix}/LATEST.json") != path.read_bytes():
                raise DataError("Archive manifest pointer verification failed")

    def manifest(self, objects):
        """Publish an explicitly supplied manifest snapshot under the shared lock."""
        with self._cache_lock("manifest") as acquired:
            if not acquired:
                raise DataError("Could not lock archive manifest publication")
            self._write_manifest(objects)

    def manifest_current(self):
        """Read the latest SQLite index only after taking the cross-process lock."""
        with self._cache_lock("manifest") as acquired:
            if not acquired:
                raise DataError("Could not lock archive manifest publication")
            objects, generation = self.repo.archive_manifest_snapshot()
            self._write_manifest(objects)
            self.repo.mark_archive_manifest_published(generation)

    def publish_prepared(self, objects, *, require_current=True):
        """Index previously uploaded Parquet objects and publish one manifest."""
        objects = list(objects)
        if not objects:
            return []
        owner = uuid.uuid4().hex
        if not self.repo.live_claim("vn", "__ARCHIVE_WRITER__", "1D", owner, lease=3600):
            raise DataError("Another archive writer is active")
        try:
            self.repo.publish_archives(objects, require_current=require_current)
            self.manifest_current()
            return objects
        finally:
            self.repo.live_release("vn", "__ARCHIVE_WRITER__", "1D", owner)

    def validate_historical_snapshot(self, candles):
        if not candles:
            raise DataError("Historical snapshots require frozen public candles")
        first = candles[0]
        years = {
            "1m": self.settings.minute_years,
            "1h": self.settings.hourly_years,
            "1D": self.settings.daily_years,
        }[first.interval]
        if not first.revision or any(
            row.provider != "legacy-api" or row.updated_at <= 0 or row.time >= cutoff(years)
            for row in candles
        ):
            raise DataError(
                "Historical snapshots require a separate frozen public revision wholly outside retention"
            )

    def publish(
        self,
        candles,
        prune=False,
        replaces=None,
        *,
        require_current=False,
        historical_snapshot=False,
    ):
        if historical_snapshot:
            if not candles or prune or replaces is not None or require_current:
                raise DataError("Historical snapshots must preserve primary records and objects")
            self.validate_historical_snapshot(candles)
            first = candles[0]
            state = self.repo.state(first.source, first.symbol, first.interval)
            if state and first.revision == state["revision"]:
                raise DataError(
                    "Historical snapshots require a separate frozen public revision wholly outside retention"
                )
        owner = uuid.uuid4().hex
        if not self.repo.live_claim("vn", "__ARCHIVE_WRITER__", "1D", owner, lease=3600):
            raise DataError("Another archive writer is active")
        try:
            if historical_snapshot:
                # Recheck under the writer lease so simultaneous imports cannot
                # combine different public captures in one frozen revision.
                self.validate_historical_capture(candles)
            obj = self.prepare(candles)
            if historical_snapshot:
                obj["status"] = "historical_snapshot"
            # Validate/commit the local index before advertising it remotely.
            # A concurrent series repair must not leak a rejected replacement
            # through LATEST. Failed manifest publication keeps all local rows.
            if replaces:
                self.repo.replace_archive(replaces, obj)
            else:
                self.repo.publish_archive(obj, require_current=require_current)
            self.manifest_current()
            if prune:
                self.repo.publish_archive(obj, candles, True)
            return obj
        finally:
            self.repo.live_release("vn", "__ARCHIVE_WRITER__", "1D", owner)

    def validate_historical_capture(self, candles):
        first = candles[0]
        incoming = {row.time: row for row in candles}
        versions = {row.updated_at for row in candles}
        for obj in self.repo.archives(first.source, first.symbol, first.interval):
            if obj["revision"] != first.revision:
                continue
            if obj["status"] != "historical_snapshot":
                raise DataError(
                    "Historical snapshot revision already belongs to primary history", 400
                )
            previous = self.read(obj)
            if {row.updated_at for row in previous} != versions:
                raise DataError("Use a new revision for a different public capture", 400)
            if any(row.time in incoming and row != incoming[row.time] for row in previous):
                raise DataError("Conflicting candles in the same frozen public revision", 400)

    def publish_metadata(self):
        """Persist observations without uploading a candle object or pruning."""
        owner = uuid.uuid4().hex
        if not self.repo.live_claim("vn", "__ARCHIVE_WRITER__", "1D", owner, lease=3600):
            raise DataError("Another archive writer is active; metadata remains recorded locally")
        try:
            self.manifest_current()
            if not self.repo.archive_manifest_is_dirty():
                self.repo.resolve_archive_manifest_findings()
        finally:
            self.repo.live_release("vn", "__ARCHIVE_WRITER__", "1D", owner)

    def finish_live_repair(self, job):
        """Activate a live repair and refresh its archive status under one writer lease."""
        owner = uuid.uuid4().hex
        if not self.repo.live_claim("vn", "__ARCHIVE_WRITER__", "1D", owner, lease=3600):
            raise DataError("Another archive writer is active; repair activation will retry")
        try:
            self.repo.finish_job(job)
            if self.repo.archive_manifest_is_dirty():
                try:
                    self.manifest_current()
                    if not self.repo.archive_manifest_is_dirty():
                        self.repo.resolve_archive_manifest_findings()
                except Exception as exc:
                    self.repo.finding(
                        job["source"],
                        job["symbol"],
                        job["interval"],
                        "archive_manifest_pending",
                        "Archive manifest publication after live repair failed",
                    )
                    log.warning(
                        "archive manifest update pending after live repair source=%s "
                        "symbol=%s interval=%s error=%s",
                        job["source"],
                        job["symbol"],
                        job["interval"],
                        type(exc).__name__,
                    )
        finally:
            self.repo.live_release("vn", "__ARCHIVE_WRITER__", "1D", owner)

    def publish_repair_batch(self, batch_id, owner):
        """Apply one durable repair receipt and advertise it with one manifest.

        The receipt remains indexed if S3 publication fails. Retrying rebuilds
        the same manifest from SQLite and does not fetch or rewrite candles.
        """
        archive_owner = uuid.uuid4().hex
        if not self.repo.live_claim("vn", "__ARCHIVE_WRITER__", "1D", archive_owner, lease=3600):
            raise DataError("Another archive writer is active")
        try:
            batch = self.repo.apply_archive_repair_batch(batch_id, owner)
            if batch["status"] == "indexed":
                self.manifest_current()
                self.repo.mark_archive_repair_manifest_published(batch_id, owner)
            elif batch["status"] != "manifest_published":
                raise DataError("Archive repair batch was not indexed")
            return batch["payload"]
        finally:
            self.repo.live_release("vn", "__ARCHIVE_WRITER__", "1D", archive_owner)

    def restore_index(self):
        pointer = json.loads(self.store.read(f"{self.settings.s3_prefix}/LATEST.json"))
        raw = self.store.read(pointer["key"])
        if hashlib.sha256(raw).hexdigest() != pointer["checksum"]:
            raise DataError("Archive manifest checksum mismatch")
        manifest = json.loads(raw)
        if manifest["version"] != 1:
            raise DataError("Unsupported archive manifest version")
        gaps = manifest.get("history_gaps", [])
        if type(gaps) is not list:
            raise DataError("Unavailable-history records must be a list")
        for record in gaps:
            self.repo.validate_history_gap(record)
        for record in manifest.get("adoptions", []):
            self.repo.validate_adoption(record)
        for obj in manifest["objects"]:
            if obj.get("schema_version") != 1:
                raise DataError("Unsupported archive schema version")
            rows = self.read(obj, refresh=True)
            if obj.get("status") == "historical_snapshot":
                self.validate_historical_snapshot(rows)
            if (
                not rows
                or len(rows) != obj["row_count"]
                or rows[0].time != obj["start"]
                or rows[-1].time != obj["end"]
            ):
                raise DataError("Archive manifest coverage differs from verified object")
            for record in manifest.get("adoptions", []):
                if all(
                    obj[key] == record[key] for key in ("source", "symbol", "interval", "revision")
                ):
                    if obj["provider"] not in (record["snapshot_provider"], record["provider"]):
                        raise DataError("Archive provider is outside its snapshot adoption")
                    verified = json.loads(record["evidence"])["verified_at_ns"]
                    if (
                        record["snapshot_provider"] != record["provider"]
                        and obj["provider"] == record["snapshot_provider"]
                        and any(not 0 < row.updated_at <= verified for row in rows)
                    ):
                        raise DataError("Archive snapshot was changed after adoption")
        from .recovery import validate_recovery

        for record in manifest.get("recoveries", []):
            validate_recovery(self, record)
        from .volume_corrections import validate_record

        corrections = manifest.get("volume_corrections", [])
        if type(corrections) is not list:
            raise DataError("Volume correction records must be a list")
        for record in corrections:
            validate_record(record, self)
        self.repo.restore_adoptions(manifest.get("adoptions", []))
        self.repo.restore_recoveries(manifest.get("recoveries", []))
        self.repo.restore_volume_corrections(corrections)
        self.repo.restore_history_gaps(gaps)
        for obj in manifest["objects"]:
            self.repo.publish_archive(obj)
        return len(manifest["objects"])

    def compaction_groups(self):
        groups = {}
        for obj in self.repo.archives():
            if obj["status"] != "published":
                continue
            fmt = "%Y" if obj["interval"] == "1D" else "%Y-%m"
            period = datetime.fromtimestamp(obj["start"], UTC).strftime(fmt)
            if datetime.fromtimestamp(obj["end"], UTC).strftime(fmt) != period:
                continue  # Preserve imported objects that span several periods.
            key = tuple(obj[k] for k in ("source", "symbol", "interval", "provider", "revision"))
            groups.setdefault((*key, period), []).append(obj)
        return [
            objects
            for objects in groups.values()
            if 2 <= len(objects) <= 256
            and sum(o["row_count"] for o in objects) <= self.settings.archive_max_rows
        ]

    def compact(self, objects):
        owner = uuid.uuid4().hex
        if not self.repo.live_claim("vn", "__ARCHIVE_WRITER__", "1D", owner, lease=3600):
            raise DataError("Another archive writer is active")
        try:
            if len(objects) < 2 or len(objects) > 256:
                raise DataError("Compaction requires two to 256 fragments", 400)
            if sum(o["row_count"] for o in objects) > self.settings.archive_max_rows:
                raise DataError("Compaction exceeds its row budget", 400)
            first = objects[0]
            fmt = "%Y" if first["interval"] == "1D" else "%Y-%m"
            period = datetime.fromtimestamp(first["start"], UTC).strftime(fmt)
            if any(
                datetime.fromtimestamp(o[k], UTC).strftime(fmt) != period
                for o in objects
                for k in ("start", "end")
            ):
                raise DataError("Compaction must remain within one partition period", 400)
            merged = {}
            for obj in objects:
                rows = self.read(obj)
                if (
                    not rows
                    or len(rows) != obj["row_count"]
                    or rows[0].time != obj["start"]
                    or rows[-1].time != obj["end"]
                ):
                    raise DataError("Compaction fragment coverage differs from its index")
                for row in rows:
                    previous = merged.get(row.time)
                    if previous and previous.updated_at == row.updated_at and previous != row:
                        raise DataError("Conflicting equal-version archive fragments")
                    if previous is None or row.updated_at > previous.updated_at:
                        merged[row.time] = row
            candidate = self.prepare(list(merged.values()))
            self.repo.compact_archives(objects, candidate)
            self.manifest_current()
            return candidate
        finally:
            self.repo.live_release("vn", "__ARCHIVE_WRITER__", "1D", owner)

    def eligible(self, source=None, symbols=None, interval=None, now=None, max_groups=None):
        if max_groups is not None:
            if max_groups < 1:
                raise ValueError("max_groups must be positive")
            now = (now or datetime.now(UTC)).astimezone(UTC)
            floors = {
                iv: cutoff(years, now)
                for iv, years in (
                    ("1D", self.settings.daily_years),
                    ("1h", self.settings.hourly_years),
                    ("1m", self.settings.minute_years),
                )
                if interval is None or iv == interval
            }
            allowed = set(symbols or ())
            bounded = []
            for ticker in self.repo.tickers(sources=[source] if source else None):
                if allowed and ticker["symbol"] not in allowed:
                    continue
                for iv, floor in floors.items():
                    rows = self.repo.eligible_archive_group(
                        ticker["source"], ticker["symbol"], iv, floor
                    )
                    if rows:
                        bounded.append(rows)
                    if len(bounded) >= max_groups:
                        return bounded
            return bounded
        groups = []
        symbols = set(symbols or ())
        now = now or datetime.now(UTC)
        floors = {
            iv: cutoff(years, now)
            for iv, years in (
                ("1D", self.settings.daily_years),
                ("1h", self.settings.hourly_years),
                ("1m", self.settings.minute_years),
            )
            if interval is None or iv == interval
        }
        for ticker in self.repo.tickers(sources=[source] if source else None):
            if symbols and ticker["symbol"] not in symbols:
                continue
            for iv, floor in floors.items():
                rows = self.repo.read(ticker["source"], ticker["symbol"], iv, end=floor - 1)
                partitions = {}
                for row in rows:
                    dt = datetime.fromtimestamp(row.time, UTC)
                    period = dt.strftime("%Y" if iv == "1D" else "%Y-%m")
                    partitions.setdefault((period, row.provider, row.revision), []).append(row)
                groups.extend(partitions.values())
        return groups
