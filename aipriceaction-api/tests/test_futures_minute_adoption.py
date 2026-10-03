from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from aipriceaction_api.adoption import adopt_snapshot
from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError
from aipriceaction_api.providers import Page
from aipriceaction_api.storage import Repository


@pytest.fixture
def futures(tmp_path):
    settings = replace(
        Settings(),
        database=tmp_path / "db",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        archive_backend="filesystem",
    )
    repo = Repository(settings.database)
    repo.initialize()
    first = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=10
    )
    rows = [
        Candle(
            "yahoo",
            "GC=F",
            "1m",
            int(first.timestamp()) + day * 86400 + minute * 60,
            100,
            101,
            99,
            100,
            10,
            "legacy-api",
            "captured",
        )
        for day in range(5)
        for minute in range(1380)
    ]
    repo.put(rows)
    return repo, settings, repo.read("yahoo", "GC=F", "1m")


class Provider:
    settings = SimpleNamespace(vn_providers=("vps", "vndirect", "dnse"))

    def __init__(self, rows, limit=None):
        self.rows = [replace(row, provider="yahoo", revision="initial") for row in rows]
        self.limit = limit
        self.requests = []

    async def page(self, source, symbol, iv, *, count, provider):
        assert (source, symbol, iv, provider) == ("yahoo", "GC=F", "1m", "yahoo")
        self.requests.append(count)
        return Page(self.rows[-min(count, self.limit or count) :], "yahoo")


@pytest.mark.asyncio
async def test_long_session_futures_overlap_adopts_without_dropping_originals_and_restores(
    futures, tmp_path
):
    repo, settings, before = futures
    provider = Provider(before)
    report = await adopt_snapshot(repo, provider, "GC=F", "yahoo", True, source="yahoo")
    assert report["evidence"]["matched_rows"] == 6900
    assert report["evidence"]["completed_sessions"] == 5
    assert provider.requests == [10000]
    assert repo.read("yahoo", "GC=F", "1m") == before
    assert repo.state("yahoo", "GC=F", "1m")["provider"] == "yahoo"
    archive = Archive(repo, settings)
    archive.publish(before, prune=True)
    restored = Repository(tmp_path / "restored")
    restored.initialize()
    assert Archive(restored, settings).restore_index() == 1
    assert restored.adoptions() == repo.adoptions()


@pytest.mark.asyncio
async def test_more_rows_do_not_relax_five_date_partition_requirement(futures):
    repo, _, before = futures
    with pytest.raises(DataError, match="five completed sessions"):
        await adopt_snapshot(
            repo, Provider(before, limit=2000), "GC=F", "yahoo", True, source="yahoo"
        )
    assert repo.adoptions() == []
    assert repo.read("yahoo", "GC=F", "1m") == before
    assert repo.state("yahoo", "GC=F", "1m")["provider"] == "legacy-api"


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", (("open", 100.1), ("volume", 11)))
async def test_conflict_outside_old_two_thousand_row_tail_still_blocks_futures_adoption(
    futures, field, value
):
    repo, _, before = futures
    provider = Provider(before)
    provider.rows[0] = replace(provider.rows[0], **{field: value})
    with pytest.raises(DataError, match="disagrees"):
        await adopt_snapshot(repo, provider, "GC=F", "yahoo", True, source="yahoo")
    assert repo.adoptions() == []
    assert repo.read("yahoo", "GC=F", "1m") == before
