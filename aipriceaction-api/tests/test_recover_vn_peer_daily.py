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
    "recover_vn_peer_daily", Path(__file__).parents[1] / "scripts/recover_vn_peer_daily.py"
)
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


@pytest.fixture
def case(tmp_path):
    settings = replace(Settings(), database=tmp_path / "db", cache_dir=tmp_path / "cache")
    repo = Repository(settings.database)
    repo.initialize()
    old = Candle("vn", "ARM", "1D", parse_time("2026-06-23"), 45798, 45798, 45798, 45798, 14, "vps", "old")
    repo.put([old])
    with repo.connect() as con:
        con.execute("UPDATE tickers SET enabled=1 WHERE source='vn' AND symbol='ARM'")
    old = repo.read("vn", "ARM", "1D")[0]
    day = date(2026, 7, 2)
    row = Candle("vn", "ARM", "1D", parse_time(day.isoformat()), 45800, 45800, 45800, 45800, 13, "vndirect")
    keys = {"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
    payload = {"data": [{"symbol": "ARM"} | {key: [getattr(row, field)] for key, field in keys.items()}]}
    return repo, settings, day, old, row, Page([row], "vndirect"), payload


@pytest.mark.parametrize("change", ["close", "volume", "missing", "duplicate", "wrong_symbol"])
def test_daily_trade_requires_exact_independent_positive_witness(case, change):
    _, _, day, _, _, page, payload = case
    assert script.verify_day("ARM", day, page, payload).volume == 13
    body = payload["data"][0]
    if change == "close":
        body["c"][0] += 1
    elif change == "volume":
        body["v"][0] = 0
    elif change == "missing":
        for key in ("t", "o", "h", "l", "c", "v"):
            body[key].clear()
    elif change == "duplicate":
        for key in ("t", "o", "h", "l", "c", "v"):
            body[key].append(body[key][0])
    else:
        body["symbol"] = "OTHER"
    with pytest.raises(DataError):
        script.verify_day("ARM", day, page, payload)


def test_append_preserves_existing_rows_and_queues_only_the_day(case):
    repo, _, day, old, row, _, _ = case
    state = repo.state("vn", "ARM", "1D")
    assert repo.live_claim("vn", "ARM", "1D", "owner")
    before = repo.market_epoch()
    script.commit_append(repo, "ARM", day, row, state, old, "owner", "proof/key")
    rows = repo.read("vn", "ARM", "1D")
    assert rows[0] == old and rows[1].provider == "vndirect" and rows[1].revision == state["revision"]
    assert repo.market_epoch() > before
    with repo.connect() as con:
        assert con.execute("SELECT period FROM public_csv_queue").fetchone()[0] == "day:2026-07-02"
        assert con.execute("SELECT detail FROM quality WHERE kind='verified_peer_daily_append'").fetchone()[0] == "proof/key"
    with pytest.raises(DataError, match="no candle was appended"):
        script.commit_append(repo, "ARM", day, row, state, old, "owner", "proof/key")
    assert len(repo.read("vn", "ARM", "1D")) == 2


def test_append_refuses_active_repair(case):
    repo, _, day, old, row, _, _ = case
    state = repo.state("vn", "ARM", "1D")
    repo.queue("vn", "ARM", "1D", "repair", parse_time("2025-01-01"), "vps")
    assert repo.live_claim("vn", "ARM", "1D", "owner")
    with pytest.raises(DataError, match="no candle was appended"):
        script.commit_append(repo, "ARM", day, row, state, old, "owner", "proof/key")
    assert len(repo.read("vn", "ARM", "1D")) == 1


def test_later_unmatched_provider_trade_remains_visible(case):
    repo, _, day, old, row, _, payload = case
    later = replace(row, time=parse_time("2026-10-27"), volume=39)
    unmatched = script.unmatched_newer_trades("ARM", row.time, Page([row, later], "vndirect"), payload)
    assert unmatched == [later]
    state = repo.state("vn", "ARM", "1D")
    assert repo.live_claim("vn", "ARM", "1D", "owner")
    script.commit_append(repo, "ARM", day, row, state, old, "owner", "proof/key", unmatched)
    with repo.connect() as con:
        finding = con.execute("SELECT detail,resolved FROM quality WHERE kind='unverified_newer_provider_trades'").fetchone()
    assert finding[1] == 0 and '"volume":39' in finding[0]


@pytest.mark.asyncio
async def test_recovery_never_overwrites_existing_public_csv(case):
    repo, settings, day, _, _, page, payload = case

    class Providers:
        async def request(self, provider, *args, **kwargs):
            return {"raw": "peer capture"} if provider == "vndirect" else payload

        async def vn_page(self, *args):
            await self.request("vndirect")
            return page

    class Existing:
        def head_object(self, **kwargs):
            return {"ETag": '"existing"'}

    store = S3Store(settings)
    store._client = Existing()
    with pytest.raises(DataError, match="already exists"):
        await script.recover(repo, Archive(repo, settings, store=store), Providers(), "ARM", day, apply=True)
    assert len(repo.read("vn", "ARM", "1D")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_csv", [False, True])
async def test_recovery_writes_evidence_and_retries_failed_publication(case, fail_csv):
    repo, settings, day, _, _, page, payload = case

    class Providers:
        async def request(self, provider, *args, **kwargs):
            return {"raw": "peer capture"} if provider == "vndirect" else payload

        async def vn_page(self, *args):
            await self.request("vndirect")
            return page

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
            return {"Body": io.BytesIO(self.objects[Key]["body"]), "ETag": '"etag"'}

    fake = Store()
    store = S3Store(settings)
    store._client = fake
    archive = Archive(repo, settings, store=store)
    result = await script.recover(repo, archive, Providers(), "ARM", day, apply=True)
    assert result["status"] == ("appended_csv_pending" if fail_csv else "appended_and_published")
    assert result["evidence_key"] in fake.objects
    assert len(repo.read("vn", "ARM", "1D")) == 2
    with repo.connect() as con:
        pending = [r[0] for r in con.execute("SELECT period FROM public_csv_queue")]
        receipt = con.execute("SELECT detail FROM quality WHERE kind='verified_peer_daily_append'").fetchone()
    assert receipt[0] == result["evidence_key"]
    assert pending == (["day:2026-07-02"] if fail_csv else [])
    assert ("ohlcv/vn/ARM/1D/ARM-1D-2026-07-02.csv" in fake.objects) is not fail_csv
