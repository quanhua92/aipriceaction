import importlib.util
import io
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from aipriceaction_api.archive import Archive, S3Store
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, parse_time
from aipriceaction_api.providers import Page
from aipriceaction_api.storage import Repository

spec = importlib.util.spec_from_file_location(
    "recover_vn_peer_minute", Path(__file__).parents[1] / "scripts/recover_vn_peer_minute.py"
)
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


@pytest.fixture
def case(tmp_path):
    settings = replace(Settings(), database=tmp_path / "db", cache_dir=tmp_path / "cache")
    repo = Repository(settings.database)
    repo.initialize()
    day = date(2026, 10, 16)
    start = parse_time(day.isoformat())
    daily = Candle("vn", "FPT", "1D", start, 100, 102, 99, 101, 300, "vps")
    repo.put([daily])
    with repo.connect() as con:
        con.execute("UPDATE tickers SET enabled=1 WHERE source='vn' AND symbol='FPT'")
    daily = repo.read("vn", "FPT", "1D")[0]
    minute = [
        Candle("vn", "FPT", "1m", start + 7200, 100, 101, 99, 100, 100, "dnse"),
        Candle("vn", "FPT", "1m", start + 7260, 100, 102, 99, 101, 200, "dnse"),
    ]
    peer = Page(minute, "dnse")

    def payload(rows, cumulative=None):
        fields = {"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
        body = {"symbol": "FPT"} | {key: [getattr(row, field) for row in rows] for key, field in fields.items()}
        if cumulative is not None:
            body["accumulatedVolume"] = cumulative
        return {"data": [body]}

    return repo, settings, day, daily, peer, payload(minute, [100, 300]), payload([daily])


def test_verify_day_requires_exact_peer_and_complete_daily_aggregate(case):
    _, _, day, daily, peer, minute, witness = case
    rows = script.verify_day("FPT", day, peer, minute, witness, daily)
    assert [row.volume for row in rows] == [100, 200]
    assert all(row.provider == "vci" for row in rows)
    minute["data"][0]["accumulatedVolume"][-1] = 299
    with pytest.raises(DataError, match="cumulative volume"):
        script.verify_day("FPT", day, peer, minute, witness, daily)


@pytest.mark.parametrize("change", ["price", "volume", "missing_minute", "daily", "duplicate"])
def test_verify_day_rejects_unverified_or_incomplete_observations(case, change):
    _, _, day, daily, peer, minute, witness = case
    body = minute["data"][0]
    if change == "price":
        body["c"][-1] += 1
    elif change == "volume":
        body["v"][-1] += 1
    elif change == "missing_minute":
        for key in ("t", "o", "h", "l", "c", "v", "accumulatedVolume"):
            body[key].pop()
    elif change == "daily":
        witness["data"][0]["v"][0] += 1
    else:
        for key in ("t", "o", "h", "l", "c", "v", "accumulatedVolume"):
            body[key].append(body[key][-1])
    with pytest.raises(DataError):
        script.verify_day("FPT", day, peer, minute, witness, daily)


def test_seed_is_atomic_and_queues_public_csv_and_evidence(case):
    repo, _, day, daily, peer, minute, witness = case
    rows = script.verify_day("FPT", day, peer, minute, witness, daily)
    assert repo.live_claim("vn", "FPT", "1m", "owner")
    before_epoch = repo.market_epoch()
    script.commit_seed(repo, "FPT", day, rows, daily, "owner", "proof/key", "verified-revision")
    state = repo.state("vn", "FPT", "1m")
    assert state["provider"] == "vci" and state["revision"] == "verified-revision"
    assert len(repo.read("vn", "FPT", "1m")) == 2
    assert repo.market_epoch() > before_epoch
    with repo.connect() as con:
        assert con.execute("SELECT period FROM public_csv_queue").fetchone()[0] == "day:2026-10-16"
        evidence = con.execute("SELECT detail,resolved FROM quality WHERE kind='verified_peer_minute_seed'").fetchone()
        assert tuple(evidence) == ("proof/key", 1)
    with pytest.raises(DataError, match="no seed was written"):
        script.commit_seed(repo, "FPT", day, rows, daily, "owner", "proof/key", "other-revision")
    assert len(repo.read("vn", "FPT", "1m")) == 2


def test_seed_refuses_active_bootstrap_lease(case):
    repo, _, day, daily, peer, minute, witness = case
    rows = script.verify_day("FPT", day, peer, minute, witness, daily)
    repo.queue("vn", "FPT", "1m", "bootstrap", parse_time("2025-10-16"))
    assert repo.claim_job("bootstrap-owner")
    assert repo.live_claim("vn", "FPT", "1m", "owner")
    with pytest.raises(DataError, match="no seed was written"):
        script.commit_seed(repo, "FPT", day, rows, daily, "owner", "proof/key", "revision")
    assert repo.state("vn", "FPT", "1m") is None
    with repo.connect() as con:
        assert not con.execute("SELECT 1 FROM public_csv_queue").fetchone()


@pytest.mark.asyncio
async def test_recovery_never_overwrites_an_existing_public_csv(case):
    repo, settings, day, _, peer, minute, witness = case

    class Providers:
        async def request(self, provider, *args, **kwargs):
            if provider == "dnse":
                return {"raw": "peer capture"}
            return minute if kwargs["json_body"]["timeFrame"] == "ONE_MINUTE" else witness

        async def vn_page(self, *args):
            await self.request("dnse")
            return peer

    class Existing:
        def head_object(self, **kwargs):
            return {"ETag": '"existing"'}

    store = S3Store(settings)
    store._client = Existing()
    archive = Archive(repo, settings, store=store)
    providers = Providers()
    dry = await script.recover(repo, archive, providers, "FPT", day)
    assert dry["status"] == "verified_dry_run"
    with pytest.raises(DataError, match="already exists"):
        await script.recover(repo, archive, Providers(), "FPT", day, apply=True)
    assert repo.state("vn", "FPT", "1m") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_csv", [False, True])
async def test_recovery_publishes_proof_and_keeps_failed_csv_queued(case, fail_csv):
    repo, settings, day, _, peer, minute, witness = case

    class Providers:
        async def request(self, provider, *args, **kwargs):
            if provider == "dnse":
                return {"raw": "peer capture"}
            return minute if kwargs["json_body"]["timeFrame"] == "ONE_MINUTE" else witness

        async def vn_page(self, *args):
            await self.request("dnse")
            return peer

    class Store:
        def __init__(self):
            self.objects = {}

        def head_object(self, Bucket, Key):
            if Key not in self.objects:
                raise ClientError({"Error": {"Code": "NoSuchKey"}}, "HeadObject")
            obj = self.objects[Key]
            return {"ETag": '"etag"', "ContentLength": len(obj["body"]),
                    "ContentType": obj["content_type"], "CacheControl": obj.get("cache"),
                    "Metadata": obj.get("metadata", {})}

        def put_object(self, **kwargs):
            key = kwargs["Key"]
            if fail_csv and key.startswith("ohlcv/"):
                raise ClientError({"Error": {"Code": "Unavailable"}}, "PutObject")
            if key in self.objects:
                raise ClientError({"Error": {"Code": "PreconditionFailed"},
                                   "ResponseMetadata": {"HTTPStatusCode": 412}}, "PutObject")
            self.objects[key] = {"body": kwargs["Body"], "content_type": kwargs["ContentType"],
                                 "metadata": kwargs.get("Metadata", {}), "cache": kwargs.get("CacheControl")}

        def get_object(self, Bucket, Key, **kwargs):
            obj = self.objects[Key]
            return {"Body": io.BytesIO(obj["body"]), "ETag": '"etag"'}

    fake = Store()
    store = S3Store(settings)
    store._client = fake
    archive = Archive(repo, settings, store=store)
    result = await script.recover(repo, archive, Providers(), "FPT", day, apply=True)
    assert result["status"] == ("seeded_csv_pending" if fail_csv else "seeded_and_published")
    assert result["evidence_key"] in fake.objects
    assert len(repo.read("vn", "FPT", "1m")) == 2
    with repo.connect() as con:
        pending = con.execute("SELECT period FROM public_csv_queue").fetchall()
        receipt = con.execute("SELECT detail FROM quality WHERE kind='verified_peer_minute_seed'").fetchone()
    assert receipt[0] == result["evidence_key"]
    assert [r[0] for r in pending] == (["day:2026-10-16"] if fail_csv else [])
    assert ("ohlcv/vn/FPT/1m/FPT-1m-2026-10-16.csv" in fake.objects) is not fail_csv
