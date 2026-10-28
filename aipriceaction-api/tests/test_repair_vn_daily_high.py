import hashlib
import io
import json
from dataclasses import replace
from datetime import date, datetime

import pytest
from botocore.exceptions import ClientError

from aipriceaction_api.archive import Archive, S3Store
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, parse_time
from aipriceaction_api.public_csv import PUBLIC_CSV_CACHE_CONTROL, _csv_values, plan_candles
from aipriceaction_api.storage import Repository
from scripts import repair_vn_daily_high as correction
from scripts.repair_vn_daily_high import plan_csv, verify_evidence


@pytest.fixture
def witnesses():
    session = date(2026, 10, 28)
    old = Candle(
        "vn",
        "CEO",
        "1D",
        parse_time(session.isoformat()),
        11500,
        11500,
        11000,
        11300,
        8126000,
        "dnse",
        "r1",
    )
    vps = replace(old, high=11600, provider="vps")
    vnd = replace(vps, provider="vndirect", volume=8147400)
    vci = replace(vnd, provider="vci")
    quote = {
        "SB": "CEO",
        "TD": "28/10/2026",
        "OP": 11500,
        "HI": 11600,
        "LO": 11000,
        "CP": 11300,
        "TT": 8126000,
    }
    return session, old, vps, vnd, vci, quote


def test_high_correction_requires_all_price_sources_and_preserves_volume(witnesses):
    session, old, vps, vnd, vci, quote = witnesses
    assert verify_evidence(old, vps, vnd, vci, quote, session)
    assert not verify_evidence(replace(old, high=11600), vps, vnd, vci, quote, session)


@pytest.mark.parametrize("defect", ["price", "volume", "other_field", "date", "symbol", "provider"])
def test_high_correction_refuses_disagreement_or_wrong_identity(witnesses, defect):
    session, old, vps, vnd, vci, quote = witnesses
    if defect == "price":
        vci = replace(vci, high=11500)
    elif defect == "volume":
        quote = quote | {"TT": 1}
    elif defect == "other_field":
        old = replace(old, close=11200)
    elif defect == "date":
        quote = quote | {"TD": "27/10/2026"}
    elif defect == "symbol":
        vnd = replace(vnd, symbol="FPT")
    elif defect == "provider":
        vci = replace(vci, provider="vps")
    with pytest.raises(DataError):
        verify_evidence(old, vps, vnd, vci, quote, session)


def test_csv_correction_changes_only_one_value_and_keeps_all_other_rows(witnesses):
    session, old, vps, _, _, _ = witnesses
    earlier = replace(old, time=parse_time("2026-10-27"), high=11700)
    item = plan_candles([earlier, old], "vn", "CEO", "1D", year=2026)
    updated = plan_csv(item.body, item.key, old, vps.high)
    before = _csv_values(item.body, item.key)
    after = _csv_values(updated.body, updated.key)
    assert before.keys() == after.keys()
    for stamp in before:
        expected = list(before[stamp])
        if stamp.date() == session:
            expected[1] = vps.high
        assert after[stamp] == tuple(expected)
    assert updated.rows == item.rows


def test_csv_correction_refuses_a_changed_or_missing_original(witnesses):
    session, old, vps, _, _, _ = witnesses
    item = plan_candles([replace(old, high=11600)], "vn", "CEO", "1D", day=session)
    with pytest.raises(DataError, match="does not match"):
        plan_csv(item.body, item.key, old, vps.high)
    other = plan_candles(
        [replace(old, time=parse_time("2026-10-27"))], "vn", "CEO", "1D", year=2026
    )
    with pytest.raises(DataError, match="does not match"):
        plan_csv(other.body, other.key, old, vps.high)


class MemoryS3:
    def __init__(self):
        self.objects = {}
        self.race_key = None

    @staticmethod
    def etag(body):
        return '"' + hashlib.sha256(body).hexdigest() + '"'

    def get_object(self, *, Bucket, Key, IfMatch=None):
        value = self.objects[(Bucket, Key)]
        if IfMatch and self.etag(value["Body"]) != IfMatch:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "GetObject")
        return {"Body": io.BytesIO(value["Body"]), "ETag": self.etag(value["Body"])}

    def head_object(self, *, Bucket, Key):
        value = self.objects[(Bucket, Key)]
        return {
            "ETag": self.etag(value["Body"]),
            "ContentLength": len(value["Body"]),
            "ContentType": value["ContentType"],
            "Metadata": value.get("Metadata", {}),
            "CacheControl": value.get("CacheControl"),
        }

    def put_object(self, **kwargs):
        key = (kwargs["Bucket"], kwargs["Key"])
        previous = self.objects.get(key)
        if self.race_key == key and kwargs.get("IfMatch"):
            previous = previous | {"Body": previous["Body"] + b"concurrent writer\n"}
            self.objects[key] = previous
            self.race_key = None
        if kwargs.get("IfNoneMatch") == "*" and previous is not None:
            raise ClientError(
                {
                    "Error": {"Code": "PreconditionFailed"},
                    "ResponseMetadata": {"HTTPStatusCode": 412},
                },
                "PutObject",
            )
        if kwargs.get("IfMatch") and (
            not previous or self.etag(previous["Body"]) != kwargs["IfMatch"]
        ):
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.objects[key] = kwargs


@pytest.fixture
def correction_system(tmp_path, witnesses, monkeypatch):
    session, old, vps, vnd, vci, quote = witnesses
    settings = replace(
        Settings(),
        database=tmp_path / "db",
        cache_dir=tmp_path / "cache",
        s3_bucket="fixture",
        archive_backend="s3",
    )
    repo = Repository(settings.database)
    repo.initialize()
    earlier = replace(old, time=parse_time("2026-10-27"), high=11700)
    repo.put([earlier, old])
    client = MemoryS3()
    store = S3Store(settings)
    store._client = client
    archive = Archive(repo, settings, store)
    for item in (
        plan_candles([old], "vn", "CEO", "1D", day=session),
        plan_candles([earlier, old], "vn", "CEO", "1D", year=2026),
    ):
        client.objects[("fixture", item.key)] = {
            "Body": item.body,
            "ContentType": "text/csv",
            "Metadata": {"content-hash": item.content_hash, "sha256": item.sha256},
            "CacheControl": PUBLIC_CSV_CACHE_CONTROL,
        }

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 28, 18, 0, tzinfo=tz)

    async def sources(*args):
        return vps, vnd, vci, quote

    monkeypatch.setattr(correction, "datetime", FrozenDatetime)
    monkeypatch.setattr(correction, "source_evidence", sources)
    return repo, archive, client, session, old


@pytest.mark.asyncio
async def test_apply_keeps_before_images_and_atomically_queues_publication(correction_system):
    repo, archive, client, session, old = correction_system
    original = {key: value["Body"] for key, value in client.objects.items()}
    epoch = repo.market_epoch()
    dry = await correction.repair(repo, archive, object(), "CEO", session)
    assert dry["status"] == "verified_dry_run" and len(client.objects) == 2
    assert repo.read("vn", "CEO", "1D")[-1].high == old.high

    result = await correction.repair(repo, archive, object(), "CEO", session, apply=True)
    assert result["status"] == "applied" and repo.market_epoch() > epoch
    proof = client.objects[("fixture", result["evidence"]["evidence_key"])]["Body"]
    assert json.loads(proof) == result["evidence"]
    row = repo.read("vn", "CEO", "1D")[-1]
    assert row.high == 11600 and row.provider == "vps" and row.volume == old.volume
    for proof in result["evidence"]["public_before_images"]:
        assert (
            client.objects[("fixture", proof["backup"])]["Body"]
            == original[("fixture", proof["key"])]
        )
        saved = client.objects[("fixture", proof["key"])]["Body"]
        assert (
            _csv_values(saved, proof["key"])[datetime.fromtimestamp(old.time, correction.UTC)][1]
            == 11600
        )
    with repo.connect() as con:
        assert con.execute("SELECT count(*) FROM public_csv_queue").fetchone()[0] == 0
        assert (
            con.execute(
                "SELECT count(*) FROM quality WHERE kind='verified_daily_high_correction'"
            ).fetchone()[0]
            == 1
        )
        assert con.execute("SELECT count(*) FROM live_leases").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_external_s3_race_preserves_changed_object_and_keeps_retry(correction_system):
    repo, archive, client, session, _ = correction_system
    day_key = next(key for key in client.objects if "/1D/" in key[1])
    client.race_key = day_key
    result = await correction.repair(repo, archive, object(), "CEO", session, apply=True)
    assert result["status"] == "applied_csv_pending"
    assert client.objects[day_key]["Body"].endswith(b"concurrent writer\n")
    assert repo.read("vn", "CEO", "1D")[-1].high == 11600
    with repo.connect() as con:
        rows = list(con.execute("SELECT period FROM public_csv_queue"))
        assert [row[0] for row in rows] == ["day:2026-10-28"]
