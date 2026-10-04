import copy
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from aipriceaction_api.adoption import adopt_snapshot, checksum
from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError
from aipriceaction_api.history import History
from aipriceaction_api.providers import Page
from aipriceaction_api.storage import Repository
from aipriceaction_api.workers import Worker


@pytest.fixture
def sparse(tmp_path):
    settings = replace(
        Settings(),
        database=tmp_path / "db",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        archive_backend="filesystem",
    )
    repo = Repository(settings.database)
    repo.initialize()
    monday = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=30
    )
    monday -= timedelta(days=monday.weekday())
    rows, daily = [], []
    for day in range(5):
        start = int((monday + timedelta(days=day)).timestamp())
        candles = [
            Candle(
                "vn",
                "GEG",
                "1m",
                start + 2 * 3600 + 15 * 60,
                100,
                105,
                98,
                101,
                100,
                "legacy-api",
                "captured",
            ),
            Candle(
                "vn",
                "GEG",
                "1m",
                start + 2 * 3600 + 16 * 60,
                101,
                104,
                99,
                102,
                200,
                "legacy-api",
                "captured",
            ),
            Candle(
                "vn",
                "GEG",
                "1m",
                start + 7 * 3600 + 45 * 60,
                102,
                106,
                100,
                104,
                300,
                "legacy-api",
                "captured",
            ),
        ]
        rows.extend(candles)
        daily.append(
            Candle("vn", "GEG", "1D", start, 100, 106, 98, 104, 600, "vps", "daily-current")
        )
    repo.put(rows)
    repo.put(daily)
    return repo, Archive(repo, settings), settings


class Provider:
    def __init__(self, repo, settings, mutate=None):
        self.settings = settings
        self.minute = [
            replace(r, provider="vps", revision="initial", updated_at=0)
            for r in repo.read("vn", "GEG", "1m")
        ]
        self.daily = [
            replace(r, revision="initial", updated_at=0) for r in repo.read("vn", "GEG", "1D")
        ]
        self.mutate = mutate

    async def page(self, source, symbol, interval, **kwargs):
        if interval == "1D" and self.mutate:
            self.mutate()
        return Page(self.minute if interval == "1m" else self.daily, "vps")


class CorroboratingProvider(Provider):
    def __init__(self, repo, settings):
        super().__init__(repo, settings)
        self.minute[0] = replace(self.minute[0], close=102, volume=90)
        self.minute[1] = replace(self.minute[1], volume=210)
        self.witness = [replace(r, provider="dnse") for r in self.minute]
        self.on_witness = None

    async def page(self, source, symbol, interval, **kwargs):
        if kwargs.get("provider") == "dnse":
            if self.on_witness:
                self.on_witness()
            return Page(self.witness, "dnse")
        return await super().page(source, symbol, interval, **kwargs)


@pytest.mark.asyncio
async def test_corroborated_corrections_are_atomic_and_keep_unchanged_provenance(sparse):
    repo, _, settings = sparse
    original = repo.read("vn", "GEG", "1m")
    provider = CorroboratingProvider(repo, settings)
    with pytest.raises(DataError, match="disagrees"):
        await adopt_snapshot(repo, provider, "GEG", "vps", True, complete_sessions=True)
    report = await adopt_snapshot(
        repo, provider, "GEG", "vps", complete_sessions=True, corroborate="dnse"
    )
    assert report["dry_run"] and report["evidence"]["corrected_rows"] == 2
    assert repo.read("vn", "GEG", "1m") == original and repo.adoptions() == []
    result = await adopt_snapshot(
        repo, provider, "GEG", "vps", True, complete_sessions=True, corroborate="dnse"
    )
    current = repo.read("vn", "GEG", "1m")
    assert current[2:] == original[2:]
    assert [r.time for r in current] == [r.time for r in original]
    assert current[0].close == 102 and current[0].volume == 90 and current[1].volume == 210
    assert all(r.provider == "vps" and r.revision == original[0].revision for r in current[:2])
    assert result["evidence"]["kind"] == "corroborated_complete_sessions"
    assert (
        result["evidence"]["complete_session_proof"]["snapshot_minute"][0] == original[0].record()
    )
    repo.validate_adoption(repo.adoptions()[0])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["disagree", "missing", "identity", "aggregate", "daily", "concurrent"]
)
async def test_uncorroborated_or_inconsistent_minute_corrections_preserve_snapshot(sparse, failure):
    repo, _, settings = sparse
    provider = CorroboratingProvider(repo, settings)
    original = repo.read("vn", "GEG", "1m")
    if failure == "disagree":
        provider.witness[0] = replace(provider.witness[0], volume=91)
    elif failure == "missing":
        provider.witness.pop(0)
    elif failure == "identity":
        provider.witness[0] = replace(provider.witness[0], provider="vps")
    elif failure == "aggregate":
        # Daily totals still agree; transferring volume between distinct
        # 15-minute buckets must not be licensed by that daily match.
        provider.minute[1] = replace(provider.minute[1], volume=200)
        provider.minute[2] = replace(provider.minute[2], volume=310)
        provider.witness = [replace(r, provider="dnse") for r in provider.minute]
    elif failure == "daily":
        provider.daily[0] = replace(provider.daily[0], volume=601)
    else:
        provider.on_witness = lambda: repo.put([replace(original[-1], volume=301)])
    match = "not independently corroborated" if failure == "disagree" else None
    with pytest.raises(DataError, match=match):
        await adopt_snapshot(
            repo, provider, "GEG", "vps", True, complete_sessions=True, corroborate="dnse"
        )
    assert repo.adoptions() == [] and repo.state("vn", "GEG", "1m")["provider"] == "legacy-api"
    assert (
        repo.read("vn", "GEG", "1m") == original
        if failure != "concurrent"
        else repo.read("vn", "GEG", "1m")[-1].volume == 301
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("complete,other", [(False, "dnse"), (True, "vps"), (True, "vci")])
async def test_correction_witness_must_be_distinct_and_configured(sparse, complete, other):
    repo, _, settings = sparse
    with pytest.raises(DataError, match="distinct configured"):
        await adopt_snapshot(
            repo,
            CorroboratingProvider(repo, settings),
            "GEG",
            "vps",
            True,
            complete_sessions=complete,
            corroborate=other,
        )
    assert repo.adoptions() == []


@pytest.mark.asyncio
async def test_correction_receipt_restores_mixed_provenance_and_rejects_changed_witness(
    sparse, tmp_path
):
    repo, archive, settings = sparse
    await adopt_snapshot(
        repo,
        CorroboratingProvider(repo, settings),
        "GEG",
        "vps",
        True,
        complete_sessions=True,
        corroborate="dnse",
    )
    rows = repo.read("vn", "GEG", "1m")
    for provider in ("legacy-api", "vps"):
        archive.publish([row for row in rows if row.provider == provider], prune=True)
    fresh = Repository(tmp_path / "correction-restore")
    fresh.initialize()
    restored = Archive(fresh, settings)
    assert restored.restore_index() == 2
    assert History(fresh, restored, settings).read("vn", "GEG", "1m") == rows
    record = copy.deepcopy(fresh.adoptions()[0])
    evidence = json.loads(record["evidence"])
    evidence["complete_session_proof"]["corroborating_minute"][0]["volume"] += 1
    record["evidence"] = json.dumps(evidence)
    with pytest.raises(
        DataError,
        match="Invalid snapshot adoption evidence: Minute correction is not independently corroborated",
    ):
        repo.validate_adoption(record)


@pytest.mark.asyncio
async def test_complete_sparse_sessions_license_append_and_preserve_snapshot(sparse):
    repo, archive, settings = sparse
    before = repo.read("vn", "GEG", "1m")
    provider = Provider(repo, settings)
    with pytest.raises(DataError, match="1000 exact"):
        await adopt_snapshot(repo, provider, "GEG", "vps", True)
    epoch = repo.epoch()
    report = await adopt_snapshot(repo, provider, "GEG", "vps", complete_sessions=True)
    assert report["dry_run"] and report["evidence"]["matched_rows"] == 15
    assert repo.epoch() == epoch and repo.adoptions() == []
    await adopt_snapshot(repo, provider, "GEG", "vps", True, complete_sessions=True)
    assert repo.read("vn", "GEG", "1m") == before
    assert repo.state("vn", "GEG", "1m")["provider"] == "vps"
    repo.validate_adoption(repo.adoptions()[0])
    new = replace(provider.minute[-1], time=before[-1].time + 60)
    provider.minute.append(new)
    assert (
        await Worker(repo, settings, provider, archive).sync(
            {"source": "vn", "symbol": "GEG"}, "1m"
        )
        == 16
    )
    assert History(repo, archive, settings).read("vn", "GEG", "1m")[-1].provider == "vps"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "missing_first",
        "missing_middle",
        "missing_tail",
        "extra",
        "daily_missing",
        "daily_volume",
        "daily_price",
        "daily_identity",
        "few_sessions",
        "zero_volume",
    ],
)
async def test_incomplete_or_disagreeing_complete_session_proof_is_rejected(sparse, failure):
    repo, _, settings = sparse
    provider = Provider(repo, settings)
    if failure == "missing_first":
        provider.minute.pop(0)
    elif failure == "missing_middle":
        provider.minute.pop(4)
    elif failure == "missing_tail":
        provider.minute.pop()
    elif failure == "extra":
        provider.minute.insert(2, replace(provider.minute[1], time=provider.minute[1].time + 60))
    elif failure == "daily_missing":
        provider.daily.pop(2)
    elif failure == "daily_volume":
        provider.daily[2] = replace(provider.daily[2], volume=601)
    elif failure == "daily_price":
        provider.daily[2] = replace(provider.daily[2], high=107)
    elif failure == "daily_identity":
        provider.daily[2] = replace(provider.daily[2], provider="dnse")
    elif failure == "few_sessions":
        provider.minute = provider.minute[3:]
    else:
        original = repo.read("vn", "GEG", "1m")[0]
        repo.put([replace(original, volume=0)])
        provider.minute[0] = replace(provider.minute[0], volume=0)
    before = repo.read("vn", "GEG", "1m")
    with pytest.raises(DataError):
        await adopt_snapshot(repo, provider, "GEG", "vps", True, complete_sessions=True)
    assert repo.read("vn", "GEG", "1m") == before
    assert repo.state("vn", "GEG", "1m")["provider"] == "legacy-api"
    assert repo.adoptions() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["value", "state"])
async def test_daily_change_during_verification_prevents_publication(sparse, change):
    repo, _, settings = sparse

    def mutate():
        if change == "value":
            row = repo.read("vn", "GEG", "1D")[0]
            repo.put([replace(row, volume=row.volume + 1)])
        else:
            repo.queue("vn", "GEG", "1D", "repair", 0, "dnse")

    before = repo.read("vn", "GEG", "1m")
    with pytest.raises(DataError, match="Daily snapshot changed"):
        await adopt_snapshot(
            repo, Provider(repo, settings, mutate), "GEG", "vps", True, complete_sessions=True
        )
    assert repo.read("vn", "GEG", "1m") == before
    assert repo.adoptions() == []


@pytest.mark.asyncio
async def test_complete_session_proof_replays_during_restore_and_rejects_rehashed_corruption(
    sparse, tmp_path
):
    repo, archive, settings = sparse
    await adopt_snapshot(repo, Provider(repo, settings), "GEG", "vps", True, complete_sessions=True)
    archive.publish(repo.read("vn", "GEG", "1m"), prune=True)
    fresh = Repository(tmp_path / "fresh")
    fresh.initialize()
    assert Archive(fresh, settings).restore_index() == 1
    assert fresh.adoptions() == repo.adoptions()
    assert History(fresh, Archive(fresh, settings), settings).read("vn", "GEG", "1m")
    record = copy.deepcopy(repo.adoptions()[0])
    evidence = json.loads(record["evidence"])
    evidence["complete_session_proof"]["minute"][0]["volume"] += 1
    evidence["overlap_checksum"] = checksum(
        [Candle(**r) for r in evidence["complete_session_proof"]["minute"]]
    )
    record["evidence"] = json.dumps(evidence)
    raw = json.dumps({"version": 1, "objects": repo.archives(), "adoptions": [record]}).encode()
    digest = hashlib.sha256(raw).hexdigest()
    path = tmp_path / "bad-manifest"
    path.write_bytes(raw)
    key = f"{settings.s3_prefix}/manifests/{digest}.json"
    archive.store.put(key, path)
    pointer = tmp_path / "pointer"
    pointer.write_text(json.dumps({"key": key, "checksum": digest}))
    archive.store.put(f"{settings.s3_prefix}/LATEST.json", pointer)
    rejected = Repository(tmp_path / "rejected")
    rejected.initialize()
    with pytest.raises(DataError, match="Invalid snapshot adoption"):
        Archive(rejected, settings).restore_index()
    assert rejected.archives() == [] and rejected.adoptions() == []
