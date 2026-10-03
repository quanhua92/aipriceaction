import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from aipriceaction_api.adoption import adopt_snapshot
from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, parse_time
from aipriceaction_api.history import History
from aipriceaction_api.providers import Page
from aipriceaction_api.storage import Repository
from aipriceaction_api.workers import Worker


@pytest.fixture
def system(tmp_path):
    settings = replace(
        Settings(),
        database=tmp_path / "db",
        object_dir=tmp_path / "objects",
        cache_dir=tmp_path / "cache",
        archive_backend="filesystem",
    )
    repo = Repository(settings.database)
    repo.initialize()
    first = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=60
    )
    days = [first + timedelta(days=i) for i in range(40)]
    days = [day for day in days if day.weekday() < 5][:20]
    rows = [
        Candle(
            "vn",
            "VNINDEX",
            "1h",
            int(day.timestamp()) + hour * 3600 + (900 if hour == 2 else 0),
            100,
            101,
            99,
            100,
            1000,
            "legacy-api",
            "captured",
        )
        for day in days
        for hour in (2, 3, 4, 6, 7)
    ]
    repo.put(rows)
    archive = Archive(repo, settings)
    archive.publish(
        [replace(repo.read("vn", "VNINDEX", "1h")[0], time=parse_time("2020-09-01T02:15:00Z"))]
    )
    return repo, archive, settings


class Provider:
    settings = SimpleNamespace(vn_providers=("vps", "vndirect", "dnse"))

    def __init__(self, rows, provider="dnse", mutate=None):
        self.rows = [replace(row, provider=provider, revision="initial") for row in rows]
        self.provider, self.mutate, self.requests = provider, mutate, []

    async def page(self, source, symbol, iv, **kwargs):
        assert (source, symbol, iv) == ("vn", "VNINDEX", "1h")
        self.requests.append(kwargs)
        if self.mutate:
            self.mutate()
        return Page(self.rows, self.provider)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ("vps", "vndirect", "dnse"))
async def test_vn_hourly_handoff_preserves_session_labels_appends_and_restores(
    system, tmp_path, provider
):
    repo, archive, settings = system
    history = History(repo, archive, settings)
    before = repo.read("vn", "VNINDEX", "1h")
    original = history.read("vn", "VNINDEX", "1h")
    upstream = Provider(before, provider)
    epoch = repo.epoch()
    proof = await adopt_snapshot(repo, upstream, "VNINDEX", provider, iv="1h")
    assert proof["dry_run"] and proof["evidence"]["matched_rows"] == 100
    assert proof["evidence"]["completed_sessions"] == 20
    assert proof["evidence"]["kind"] == "exact_vn_hourly_snapshot_overlap"
    assert upstream.requests[0]["count"] == 200
    assert repo.epoch() == epoch and repo.adoptions() == []
    await adopt_snapshot(repo, upstream, "VNINDEX", provider, True, iv="1h")
    assert repo.read("vn", "VNINDEX", "1h") == before
    assert history.read("vn", "VNINDEX", "1h") == original
    extra = replace(before[-1], time=before[-1].time + 3600)
    assert (
        await Worker(repo, settings, Provider(before[-39:] + [extra], provider), archive).sync(
            {"source": "vn", "symbol": "VNINDEX"}, "1h"
        )
        == 40
    )
    assert len(history.read("vn", "VNINDEX", "1h")) == len(original) + 1
    for name in ("legacy-api", provider):
        archive.publish(
            [row for row in repo.read("vn", "VNINDEX", "1h") if row.provider == name], prune=True
        )
    fresh = Repository(tmp_path / "restored")
    fresh.initialize()
    restored = Archive(fresh, settings)
    assert restored.restore_index() == 3
    assert fresh.adoptions() == repo.adoptions()
    assert History(fresh, restored, settings).query(
        "vn", "VNINDEX", "4h", limit=20
    ) == history.query("vn", "VNINDEX", "4h", limit=20)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    ("few", "stale", "price", "volume", "timestamp", "duplicate", "unordered", "identity"),
)
async def test_vn_hourly_handoff_refuses_conflicts_and_invalid_overlap(system, failure):
    repo, _, _ = system
    before = repo.read("vn", "VNINDEX", "1h")
    rows = before.copy()
    if failure == "few":
        rows = rows[-99:]
    elif failure == "stale":
        rows = rows[:-1]
    elif failure == "price":
        rows[-1] = replace(rows[-1], open=100.1)
    elif failure == "volume":
        rows[-1] = replace(rows[-1], volume=999)
    elif failure == "timestamp":
        rows[0] = replace(rows[0], time=rows[0].time + 60)
    elif failure == "duplicate":
        rows.insert(1, rows[0])
    elif failure == "unordered":
        rows[0], rows[1] = rows[1], rows[0]
    else:
        rows[0] = replace(rows[0], symbol="VN30")
    with pytest.raises(DataError):
        await adopt_snapshot(repo, Provider(rows), "VNINDEX", "dnse", True, iv="1h")
    assert repo.adoptions() == [] and repo.read("vn", "VNINDEX", "1h") == before
    assert repo.state("vn", "VNINDEX", "1h")["provider"] == "legacy-api"


@pytest.mark.asyncio
async def test_vn_hourly_handoff_requires_five_observed_dates(system):
    repo, _, _ = system
    first = repo.read("vn", "VNINDEX", "1h")[0]
    rows = [replace(first, symbol="VN30", time=first.time + i * 60) for i in range(100)]
    repo.put(rows)
    provider = Provider(rows)

    async def page(*args, **kwargs):
        return Page(provider.rows, "dnse")

    provider.page = page
    with pytest.raises(DataError, match="five completed sessions"):
        await adopt_snapshot(repo, provider, "VN30", "dnse", True, iv="1h")
    assert repo.adoptions() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ("race", "job", "corrections"))
async def test_vn_hourly_handoff_keeps_race_lease_and_correction_guards(system, failure):
    repo, _, _ = system
    before = repo.read("vn", "VNINDEX", "1h")
    mutate = None
    if failure == "race":

        def mutate():
            repo.put([replace(before[0], close=100.1)])
    elif failure == "job":
        repo.queue("vn", "VNINDEX", "1h", "bootstrap", before[0].time)
        repo.claim_job("other", allowed=[("vn", "VNINDEX", "1h")])
    with pytest.raises(DataError):
        await adopt_snapshot(
            repo,
            Provider(before, mutate=mutate),
            "VNINDEX",
            "dnse",
            True,
            iv="1h",
            complete_sessions=failure == "corrections",
        )
    assert repo.adoptions() == []


@pytest.mark.asyncio
async def test_vn_hourly_certificate_binds_market_interval_size_and_finality(system):
    repo, _, _ = system
    before = repo.read("vn", "VNINDEX", "1h")
    await adopt_snapshot(repo, Provider(before), "VNINDEX", "dnse", True, iv="1h")
    record = repo.adoptions()[0]
    for patch in (
        {"source": "yahoo", "provider": "yahoo"},
        {"interval": "1m"},
        {"provider": "vci"},
    ):
        with pytest.raises(DataError, match="adoption evidence"):
            repo.validate_adoption(record | patch)
    for key, value in (
        ("matched_rows", 99),
        ("completed_before", json.loads(record["evidence"])["completed_before"] + 60),
        ("completed_before", json.loads(record["evidence"])["completed_before"] + 86400),
    ):
        evidence = json.loads(record["evidence"])
        evidence[key] = value
        with pytest.raises(DataError, match="adoption evidence"):
            repo.validate_adoption(record | {"evidence": json.dumps(evidence)})


@pytest.mark.asyncio
async def test_vn_hourly_handoff_excludes_unfinished_session_without_losing_it(system, monkeypatch):
    repo, _, _ = system
    before = repo.read("vn", "VNINDEX", "1h")
    boundary = (before[-1].time // 86400 + 1) * 86400
    provisional = [replace(row, time=boundary + row.time % 86400) for row in before[-5:]]
    repo.put(provisional)
    snapshot = repo.read("vn", "VNINDEX", "1h")
    import aipriceaction_api.adoption as adoption

    monkeypatch.setattr(adoption, "completed_vn_sessions", lambda: boundary)
    # An unfinished provider price conflict cannot license changing that bar.
    incoming = before + [replace(row, close=100.5) for row in provisional]
    proof = await adopt_snapshot(repo, Provider(incoming), "VNINDEX", "dnse", iv="1h")
    assert proof["evidence"]["matched_rows"] == 100
    assert proof["evidence"]["completed_before"] == boundary
    assert proof["evidence"]["overlap_end"] == before[-1].time
    assert repo.read("vn", "VNINDEX", "1h") == snapshot
    assert repo.adoptions() == []
