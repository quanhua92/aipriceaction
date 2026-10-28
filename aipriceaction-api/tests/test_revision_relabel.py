from dataclasses import replace
from datetime import date

import pytest

from aipriceaction_api.domain import Candle, DataError, parse_time
from aipriceaction_api.providers import Page
from aipriceaction_api.revision_relabel import repair
from aipriceaction_api.storage import Repository


@pytest.fixture
def sample(tmp_path):
    repo = Repository(tmp_path / "data.sqlite3")
    repo.initialize()
    start = parse_time("2023-10-25")
    rows = [
        Candle("yahoo", "AVGO", "1D", start + i * 86400, 100, 102, 99, 101, 1000, "yahoo", "current")
        for i in range(5)
    ]
    repo.put(rows)
    with repo.connect() as con:
        con.execute("UPDATE candles SET revision='old' WHERE time=?", (start,))
    return repo, rows


class Provider:
    def __init__(self, rows, mutate=None):
        self.rows, self.mutate = rows, mutate

    async def page(self, *args, **kwargs):
        if self.mutate:
            self.mutate()
        return Page(self.rows, "yahoo")


@pytest.mark.asyncio
async def test_relabel_requires_observed_agreement_and_changes_no_market_values(sample):
    repo, rows = sample
    before = repo.read("yahoo", "AVGO", "1D")
    provider = Provider(rows)
    dry = await repair(repo, provider, "AVGO", date(2023, 10, 25))
    assert dry["status"] == "verified_dry_run"
    assert repo.read("yahoo", "AVGO", "1D") == before
    result = await repair(repo, provider, "AVGO", date(2023, 10, 25), apply=True)
    after = repo.read("yahoo", "AVGO", "1D")
    assert result["status"] == "applied" and len(result["evidence"]["references"]) == 4
    assert after == [replace(before[0], revision="current"), *before[1:]]
    assert (await repair(repo, provider, "AVGO", date(2023, 10, 25), apply=True))["status"] == "already_current"
    with repo.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM quality WHERE kind='revision_relabel_verified'").fetchone()[0] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["price", "volume", "references", "race", "repair"])
async def test_relabel_fails_closed_for_bad_evidence_or_concurrent_changes(sample, defect):
    repo, rows = sample
    before = repo.read("yahoo", "AVGO", "1D")
    if defect == "price":
        rows[0] = replace(rows[0], close=100)
    elif defect == "volume":
        rows[0] = replace(rows[0], volume=999)
    elif defect == "references":
        rows = rows[:3]
    elif defect == "repair":
        repo.queue("yahoo", "AVGO", "1D", "repair", rows[0].time, "yahoo")

    def mutate():
        if defect == "race":
            with repo.connect() as con:
                con.execute("UPDATE candles SET close=100 WHERE time=?", (rows[0].time,))

    with pytest.raises(DataError):
        await repair(repo, Provider(rows, mutate), "AVGO", date(2023, 10, 25), apply=True)
    after = repo.read("yahoo", "AVGO", "1D")
    assert after[0].revision == "old"
    if defect != "race":
        assert after == before
    with repo.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM quality WHERE kind='revision_relabel_verified'").fetchone()[0] == 0
