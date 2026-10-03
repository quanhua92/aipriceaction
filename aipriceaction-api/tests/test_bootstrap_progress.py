from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.bootstrap_progress import publish_bootstrap_progress
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError
from aipriceaction_api.providers import Page
from aipriceaction_api.storage import Repository


@pytest.fixture
def system(tmp_path):
    settings = replace(
        Settings(),
        database=tmp_path / "db",
        archive_backend="filesystem",
        object_dir=tmp_path / "objects",
        cache_dir=tmp_path / "cache",
    )
    repo = Repository(settings.database)
    repo.initialize()
    first = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=80
    )
    days = [first + timedelta(days=i) for i in range(60)]
    days = [day for day in days if day.weekday() < 5][:40]
    repo.queue("vn", "FPT", "1h", "bootstrap", int(first.timestamp()), "dnse")
    job = repo.claim_job("seed")
    rows = [
        Candle(
            "vn",
            "FPT",
            "1h",
            int(day.timestamp()) + hour * 3600,
            100,
            101,
            99,
            100,
            10,
            "dnse",
            job["revision"],
        )
        for day in days
        for hour in (2, 3, 4, 6, 7)
    ]
    repo.stage(job, rows, rows[0].time, "dnse")
    repo.put(rows[-150:])
    return repo, Archive(repo, settings), settings


class Provider:
    def __init__(self, repo, settings, mutate=None):
        self.settings = settings
        self.rows = [replace(row, revision="initial") for row in repo.read("vn", "FPT", "1h")]
        self.mutate = mutate

    async def page(self, source, symbol, iv, **kwargs):
        assert (source, symbol, iv, kwargs["provider"]) == ("vn", "FPT", "1h", "dnse")
        if self.mutate:
            self.mutate()
        return Page(self.rows, "dnse")


@pytest.mark.asyncio
async def test_progress_appends_only_older_rows_preserves_jobs_and_readback_evidence(system):
    repo, archive, settings = system
    original = repo.read("vn", "FPT", "1h")
    daily = replace(original[0], interval="1D", time=original[0].time // 86400 * 86400)
    repo.put([daily])
    daily = repo.read("vn", "FPT", "1D")
    before = repo.status()
    state = repo.state("vn", "FPT", "1h")
    epoch = repo.epoch()
    provider = Provider(repo, settings)
    preview = await publish_bootstrap_progress(repo, provider, archive, "FPT")
    assert preview["append_rows"] == 50 and not preview["published"]
    assert repo.epoch() == epoch
    report = await publish_bootstrap_progress(repo, provider, archive, "FPT", True)
    assert report["published"] and not report["complete_retention_proven"]
    published = repo.read("vn", "FPT", "1h")
    assert len(published) == 200 and published[-150:] == original
    assert archive.read(report["beforeimage"], refresh=True) == original
    assert archive.read(report["replacement_image"], refresh=True) == published
    assert archive.store.read(report["intent_object_key"])
    assert repo.status()["jobs"] == before["jobs"]
    assert repo.state("vn", "FPT", "1h") == state
    assert repo.read("vn", "FPT", "1D") == daily
    assert repo.archives() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ("staged", "native", "short", "tail", "active", "update", "race")
)
async def test_invalid_or_changed_progress_never_appends(system, failure):
    repo, archive, settings = system
    original = repo.read("vn", "FPT", "1h")
    provider = Provider(repo, settings)
    if failure == "staged":
        with repo.connect() as con:
            con.execute("UPDATE staging SET close=100.5 WHERE time=?", (original[0].time,))
    elif failure == "native":
        provider.rows[0] = replace(provider.rows[0], volume=11)
    elif failure == "short":
        provider.rows = provider.rows[-99:]
    elif failure == "tail":
        provider.rows = provider.rows[:-1]
    elif failure == "active":
        repo.claim_job("another")
    elif failure == "update":
        assert repo.live_claim("vn", "FPT", "1h", "another")
    else:
        prepare = archive.prepare

        def changed(rows):
            result = prepare(rows)
            with repo.connect() as con:
                con.execute(
                    "UPDATE staging SET updated_at=updated_at+1 WHERE time=?", (rows[0].time,)
                )
            return result

        archive.prepare = changed
    with pytest.raises(DataError):
        await publish_bootstrap_progress(repo, provider, archive, "FPT", True)
    assert repo.read("vn", "FPT", "1h") == original
    assert repo.archives() == []
    assert repo.status()["jobs"][0]["status"] != "complete"
