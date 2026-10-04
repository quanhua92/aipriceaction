import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from aipriceaction_api.adoption import adopt_snapshot
from aipriceaction_api.archive import Archive
from aipriceaction_api.cli import parser
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
            "yahoo",
            "SPY",
            "1h",
            int(day.timestamp()) + hour * 3600,
            100,
            101,
            99,
            100,
            1000,
            "legacy-api",
            "captured",
        )
        for day in days
        for hour in range(13, 23)
    ]
    repo.put(rows)
    archive = Archive(repo, settings)
    # An older public :30 label is retained, even though current native bars
    # use whole-hour labels. Adoption must not rewrite its historical date.
    archive.publish(
        [replace(repo.read("yahoo", "SPY", "1h")[0], time=parse_time("2020-09-01T13:30:00Z"))]
    )
    return repo, archive, settings


class Provider:
    settings = SimpleNamespace(vn_providers=("vps", "vndirect", "dnse"))

    def __init__(self, rows, mutate=None):
        self.rows = [replace(row, provider="yahoo", revision="initial") for row in rows]
        self.mutate = mutate

    async def page(self, source, symbol, iv, **kwargs):
        assert (source, symbol, iv) == ("yahoo", "SPY", "1h")
        if self.mutate:
            self.mutate()
        return Page(self.rows, "yahoo")


@pytest.mark.asyncio
async def test_five_day_policy_is_verified_pinned_for_live_and_archive_heads_and_restored(
    system, tmp_path
):
    repo, archive, settings = system
    before = repo.read("yahoo", "SPY", "1h")
    requests = []

    class RangeProvider(Provider):
        async def page(self, *args, **kwargs):
            requests.append((args, kwargs.copy()))
            assert kwargs["yahoo_hourly_range"] == "5d"
            assert kwargs.get("start") is None
            assert args[:3] == ("yahoo", "SPY", "1h")
            return Page(self.rows, "yahoo")

    proof = await adopt_snapshot(
        repo,
        RangeProvider(before),
        "SPY",
        "yahoo",
        source="yahoo",
        iv="1h",
        yahoo_hourly_range="5d",
    )
    assert proof["evidence"]["yahoo_hourly_range"] == "5d"
    assert not repo.adoptions()
    await adopt_snapshot(
        repo,
        RangeProvider(before),
        "SPY",
        "yahoo",
        True,
        source="yahoo",
        iv="1h",
        yahoo_hourly_range="5d",
    )
    assert repo.read("yahoo", "SPY", "1h") == before
    state = repo.state("yahoo", "SPY", "1h")
    assert repo.snapshot_hourly_range(state) == "5d"
    extra = replace(before[-1], time=before[-1].time + 3600)
    worker = Worker(repo, settings, RangeProvider(before[-39:] + [extra]), archive)
    assert await worker.sync({"source": "yahoo", "symbol": "SPY"}, "1h") == 40
    await worker.verify_archive_head({"source": "yahoo", "symbol": "SPY", "interval": "1h"}, state)
    assert requests[-1][0][-1] is None  # A relative range never receives historical bounds.
    for provider in ("legacy-api", "yahoo"):
        archive.publish(
            [r for r in repo.read("yahoo", "SPY", "1h") if r.provider == provider], prune=True
        )
    fresh = Repository(tmp_path / "range-restored")
    fresh.initialize()
    restored = Archive(fresh, settings)
    assert restored.restore_index() == 3
    assert fresh.snapshot_hourly_range(state) == "5d"
    assert fresh.adoptions() == repo.adoptions()


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"source": "vn"}, {"iv": "1m"}, {"yahoo_hourly_range": "1mo"}])
async def test_range_adoption_rejects_other_markets_intervals_or_windows(system, changes):
    repo, _, _ = system
    before = repo.read("yahoo", "SPY", "1h")
    args = {"source": "yahoo", "iv": "1h", "yahoo_hourly_range": "5d"} | changes
    with pytest.raises(DataError, match="range policy"):
        await adopt_snapshot(repo, Provider(before), "SPY", "yahoo", True, **args)
    assert not repo.adoptions() and repo.read("yahoo", "SPY", "1h") == before


@pytest.mark.asyncio
async def test_certificate_rejects_unknown_range_policy(system):
    repo, _, _ = system
    before = repo.read("yahoo", "SPY", "1h")
    await adopt_snapshot(repo, Provider(before), "SPY", "yahoo", True, source="yahoo", iv="1h")
    record = repo.adoptions()[0]
    evidence = json.loads(record["evidence"]) | {"yahoo_hourly_range": "1mo"}
    with pytest.raises(DataError, match="hourly request policy"):
        repo.validate_adoption(record | {"evidence": json.dumps(evidence)})


@pytest.mark.asyncio
@pytest.mark.parametrize("has_overlap", [False, True])
async def test_range_catchup_keeps_policy_and_queues_recovery_for_disjoint_pages(
    system, has_overlap
):
    repo, archive, settings = system
    before = repo.read("yahoo", "SPY", "1h")
    await adopt_snapshot(
        repo,
        Provider(before),
        "SPY",
        "yahoo",
        True,
        source="yahoo",
        iv="1h",
        yahoo_hourly_range="5d",
    )
    incoming = replace(
        before[-1], time=before[-1].time + 50 * 3600, provider="yahoo", revision="initial"
    )
    calls = []

    class Catchup:
        async def page(self, source, symbol, iv, **kwargs):
            calls.append(kwargs.copy())
            assert kwargs["yahoo_hourly_range"] == "5d" and kwargs.get("start") is None
            rows = (
                [replace(r, provider="yahoo", revision="initial") for r in before[-2:]]
                if len(calls) > 1 and has_overlap
                else []
            )
            return Page(rows + [incoming], "yahoo")

    result = await Worker(repo, settings, Catchup(), archive).sync(
        {"source": "yahoo", "symbol": "SPY"}, "1h"
    )
    assert len(calls) == 2 and calls[0]["count"] == 40 and calls[1]["count"] > 40
    if has_overlap:
        assert result == 3 and len(repo.read("yahoo", "SPY", "1h")) == len(before) + 1
    else:
        assert result == 0 and repo.read("yahoo", "SPY", "1h") == before
        assert repo.status()["jobs"][0]["kind"] == "repair"


@pytest.mark.asyncio
async def test_hourly_handoff_preserves_old_dates_appends_and_restores(system, tmp_path):
    repo, archive, settings = system
    history = History(repo, archive, settings)
    before = repo.read("yahoo", "SPY", "1h")
    original = history.read("yahoo", "SPY", "1h")
    epoch = repo.epoch()
    proof = await adopt_snapshot(repo, Provider(before), "SPY", "yahoo", source="yahoo", iv="1h")
    assert proof["dry_run"] and proof["evidence"]["matched_rows"] == 200
    assert repo.epoch() == epoch and repo.adoptions() == []
    job = repo.queue("yahoo", "SPY", "1h", "bootstrap", before[0].time)
    await adopt_snapshot(repo, Provider(before), "SPY", "yahoo", True, source="yahoo", iv="1h")
    assert history.read("yahoo", "SPY", "1h") == original
    with repo.connect() as con:
        assert (
            con.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0] == "cancelled"
        )
    assert repo.state("yahoo", "SPY", "1h")["provider"] == "yahoo"
    extra = replace(before[-1], time=before[-1].time + 3600)
    assert (
        await Worker(repo, settings, Provider(before[-39:] + [extra]), archive).sync(
            {"source": "yahoo", "symbol": "SPY"}, "1h"
        )
        == 40
    )
    assert len(history.read("yahoo", "SPY", "1h")) == len(original) + 1
    for provider in ("legacy-api", "yahoo"):
        archive.publish(
            [r for r in repo.read("yahoo", "SPY", "1h") if r.provider == provider], prune=True
        )
    fresh = Repository(tmp_path / "restored")
    fresh.initialize()
    restored = Archive(fresh, settings)
    assert restored.restore_index() == 3
    assert fresh.adoptions() == repo.adoptions()
    assert History(fresh, restored, settings).query(
        "yahoo", "SPY", "4h", limit=20
    ) == history.query("yahoo", "SPY", "4h", limit=20)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["few", "stale", "price", "volume", "date"])
async def test_hourly_handoff_rejects_inadequate_or_conflicting_overlap(system, failure):
    repo, _, _ = system
    before = repo.read("yahoo", "SPY", "1h")
    rows = before.copy()
    if failure == "few":
        rows = rows[-99:]
    elif failure == "stale":
        rows = rows[:-1]
    elif failure == "price":
        rows[-1] = replace(rows[-1], open=100.1)
    elif failure == "volume":
        rows[-1] = replace(rows[-1], volume=999)
    else:
        rows[0] = replace(rows[0], time=rows[0].time + 60)
    with pytest.raises(DataError):
        await adopt_snapshot(repo, Provider(rows), "SPY", "yahoo", True, source="yahoo", iv="1h")
    assert repo.adoptions() == [] and repo.read("yahoo", "SPY", "1h") == before
    assert repo.state("yahoo", "SPY", "1h")["provider"] == "legacy-api"


@pytest.mark.asyncio
@pytest.mark.parametrize("leased", [False, True])
async def test_hourly_handoff_rejects_races_and_active_jobs(system, leased):
    repo, _, _ = system
    before = repo.read("yahoo", "SPY", "1h")

    def mutate():
        repo.put([replace(before[0], close=100.1)])

    if leased:
        repo.queue("yahoo", "SPY", "1h", "bootstrap", before[0].time)
        repo.claim_job("other", allowed=[("yahoo", "SPY", "1h")])
        mutate = None
    with pytest.raises(DataError, match="Snapshot changed|job is active"):
        await adopt_snapshot(
            repo, Provider(before, mutate), "SPY", "yahoo", True, source="yahoo", iv="1h"
        )
    assert repo.adoptions() == [] and repo.state("yahoo", "SPY", "1h")["provider"] == "legacy-api"


@pytest.mark.asyncio
async def test_hourly_certificate_rejects_forged_market_interval_size_and_finality(system):
    repo, _, _ = system
    before = repo.read("yahoo", "SPY", "1h")
    await adopt_snapshot(repo, Provider(before), "SPY", "yahoo", True, source="yahoo", iv="1h")
    record = repo.adoptions()[0]
    for patch in ({"source": "vn", "provider": "vps"}, {"interval": "1m"}):
        with pytest.raises(DataError, match="adoption evidence"):
            repo.validate_adoption(record | patch)
    for key, value in (
        ("matched_rows", 99),
        ("completed_before", json.loads(record["evidence"])["completed_before"] + 60),
    ):
        evidence = json.loads(record["evidence"])
        evidence[key] = value
        with pytest.raises(DataError, match="adoption evidence"):
            repo.validate_adoption(record | {"evidence": json.dumps(evidence)})
    args = parser().parse_args(
        [
            "adopt-snapshot",
            "--source",
            "yahoo",
            "--symbol",
            "SPY",
            "--interval",
            "1h",
            "--provider",
            "yahoo",
        ]
    )
    assert args.interval == "1h"


@pytest.mark.asyncio
@pytest.mark.parametrize("source,complete", [("vn", False), ("yahoo", True)])
async def test_hourly_adoption_rejects_unsupported_market_and_minute_corrections(
    system, source, complete
):
    repo, _, _ = system
    with pytest.raises(DataError):
        await adopt_snapshot(
            repo, Provider([]), "SPY", "yahoo", source=source, iv="1h", complete_sessions=complete
        )
    assert repo.adoptions() == []
