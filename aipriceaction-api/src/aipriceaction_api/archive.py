"""Immutable Parquet objects, checksummed manifests, bounded local read cache.

Use boto3 for explicit object transfers and DuckDB for local Parquet queries.
No runtime extension download or bucket-wide query glob is required.
"""

import csv
import hashlib
import json
import os
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import duckdb

from .domain import Candle, DataError, cutoff
from .storage import COLUMNS


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
        self.lock = threading.RLock()

    def _path(self, obj, refresh=False):
        root = self.settings.cache_dir
        root.mkdir(parents=True, exist_ok=True)
        path = root / (obj["checksum"] + ".parquet")
        if refresh or not path.exists() or sha256(path) != obj["checksum"]:
            with tempfile.NamedTemporaryFile(dir=root, suffix=".tmp", delete=False) as file:
                temp = Path(file.name)
            try:
                self.store.download(obj["object_key"], temp)
                if sha256(temp) != obj["checksum"]:
                    raise DataError("Archive checksum mismatch")
                if temp.stat().st_size > self.settings.cache_bytes:
                    raise DataError("Archive object exceeds cache budget; repartition it")
                os.replace(temp, path)
            finally:
                temp.unlink(missing_ok=True)
        path.touch()
        return path

    def _evict(self):
        files = sorted(self.settings.cache_dir.glob("*.parquet"), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        for path in files:
            if total <= self.settings.cache_bytes:
                break
            total -= path.stat().st_size
            path.unlink(missing_ok=True)

    def read(self, obj, start=None, end=None, limit=None, refresh=False, forward=False):
        with self.lock:
            path = self._path(obj, refresh)
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
                with duckdb.connect(config={"threads": 1, "memory_limit": "256MB"}) as con:
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
                != (obj["source"], obj["symbol"], obj["interval"], obj["revision"], obj["provider"])
                for c in result
            ):
                raise DataError("Archive identity/revision mismatch")
            self._evict()
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

    def manifest(self, objects):
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
            self.manifest(self.repo.archives())
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
            self.manifest(self.repo.archives())
        finally:
            self.repo.live_release("vn", "__ARCHIVE_WRITER__", "1D", owner)

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
            self.manifest(self.repo.archives())
            return candidate
        finally:
            self.repo.live_release("vn", "__ARCHIVE_WRITER__", "1D", owner)

    def eligible(self, source=None, symbols=None, interval=None, now=None):
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
