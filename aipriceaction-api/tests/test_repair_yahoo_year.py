import hashlib
import io
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from botocore.exceptions import ClientError

from aipriceaction_api.archive import Archive, S3Store
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError
from aipriceaction_api.providers import Page
from aipriceaction_api.public_csv import plan_candles
from aipriceaction_api.repair_yahoo_year import repair
from aipriceaction_api.storage import Repository


class S3:
    def __init__(self):
        self.objects = {}
        self.puts = []
        self.race = None

    @staticmethod
    def etag(body):
        return '"' + hashlib.sha256(body).hexdigest() + '"'

    def get_object(self, *, Bucket, Key, IfMatch=None):
        obj = self.objects[(Bucket, Key)]
        etag = self.etag(obj["Body"])
        if IfMatch and IfMatch != etag:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}, "ResponseMetadata": {"HTTPStatusCode": 412}}, "GetObject")
        return {"Body": io.BytesIO(obj["Body"]), "ETag": etag}

    def head_object(self, *, Bucket, Key):
        obj = self.objects[(Bucket, Key)]
        return {
            "ETag": self.etag(obj["Body"]), "ContentLength": len(obj["Body"]),
            "ContentType": obj["ContentType"], "Metadata": obj.get("Metadata", {}),
            "CacheControl": obj.get("CacheControl"),
        }

    def put_object(self, **kwargs):
        identity = (kwargs["Bucket"], kwargs["Key"])
        existing = self.objects.get(identity)
        if kwargs.get("IfNoneMatch") == "*" and existing:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}, "ResponseMetadata": {"HTTPStatusCode": 412}}, "PutObject")
        if kwargs.get("IfMatch") and (not existing or self.etag(existing["Body"]) != kwargs["IfMatch"]):
            raise ClientError({"Error": {"Code": "PreconditionFailed"}, "ResponseMetadata": {"HTTPStatusCode": 412}}, "PutObject")
        self.objects[identity] = kwargs
        self.puts.append(kwargs["Key"])
        if self.race and kwargs["Key"].endswith(".json"):
            self.race()


class Provider:
    def __init__(self, rows):
        self.rows = rows

    async def page(self, *args, **kwargs):
        return Page(self.rows, "yahoo")


@pytest.fixture
def system(tmp_path):
    settings = replace(Settings(), database=tmp_path / "db", archive_backend="filesystem",
                       object_dir=tmp_path / "objects", cache_dir=tmp_path / "cache", s3_bucket="bucket")
    repo = Repository(settings.database)
    repo.initialize()
    archive = Archive(repo, settings)
    archive.store = S3Store(settings)
    s3 = S3()
    archive.store._client = s3
    start = int(datetime(2023, 1, 1, tzinfo=UTC).timestamp())
    rows = [Candle("yahoo", "AVGO", "1D", start + i * 86400, 100, 102, 99, 101, 1000, "yahoo", "current") for i in range(5)]
    repo.put(rows[:3])
    original = plan_candles([replace(row, open=95, high=97, low=94, close=96) for row in rows[:4]], "yahoo", "AVGO", "1D", year=2023)
    s3.objects[("bucket", original.key)] = {"Body": original.body, "ContentType": "text/csv", "Metadata": {}}
    return repo, archive, s3, rows, original


@pytest.mark.asyncio
async def test_verified_year_preserves_original_and_does_not_change_sqlite(system):
    repo, archive, s3, rows, original = system
    before = repo.read("yahoo", "AVGO", "1D")
    dry = await repair(repo, archive, Provider(rows), "AVGO", 2023)
    assert dry["status"] == "verified_dry_run" and dry["evidence"]["canonical_rows_checked"] == 3
    assert dry["evidence"]["changed_existing_rows"] == 4
    assert not s3.puts
    result = await repair(repo, archive, Provider(rows), "AVGO", 2023, apply=True)
    assert result["status"] == "applied"
    assert s3.objects[("bucket", result["evidence"]["original_backup_key"])]["Body"] == original.body
    assert s3.objects[("bucket", original.key)]["Body"] == plan_candles(rows, "yahoo", "AVGO", "1D", year=2023).body
    assert repo.read("yahoo", "AVGO", "1D") == before
    with repo.connect() as con:
        assert con.execute("SELECT 1 FROM quality WHERE kind='public_csv_year_verified_repair'").fetchone()


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["canonical", "missing_old_date"])
async def test_year_repair_refuses_mismatched_data(system, defect):
    repo, archive, s3, rows, _ = system
    if defect == "canonical":
        rows[0] = replace(rows[0], close=100)
    else:
        rows = rows[:3]
    with pytest.raises(DataError):
        await repair(repo, archive, Provider(rows), "AVGO", 2023, apply=True)
    assert not s3.puts


@pytest.mark.asyncio
async def test_year_repair_does_not_overwrite_a_concurrent_s3_writer(system):
    repo, archive, s3, rows, original = system
    newer = b"another writer's data"
    s3.race = lambda: s3.objects[("bucket", original.key)].update(Body=newer)
    with pytest.raises(DataError, match="changed since it was verified"):
        await repair(repo, archive, Provider(rows), "AVGO", 2023, apply=True)
    assert s3.objects[("bucket", original.key)]["Body"] == newer
    assert repo.live_claim("yahoo", "AVGO", "1D", "next")
    repo.live_release("yahoo", "AVGO", "1D", "next")
