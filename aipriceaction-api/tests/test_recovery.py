import json
from dataclasses import replace

import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.cli import execute, parser
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, parse_time
from aipriceaction_api.history import History
from aipriceaction_api.migration import digest, encode
from aipriceaction_api.providers import Page
from aipriceaction_api.recovery import recover_daily
from aipriceaction_api.storage import Repository
from aipriceaction_api.workers import Worker


def candle(day, close=100):
    return Candle(
        "vn", "FPT", "1D", parse_time(day), close, close + 1, close - 1, close, 1000, "vps"
    )


class Pages:
    def __init__(self, *pages):
        self.pages = list(pages)

    async def page(self, *args, **kwargs):
        return Page(self.pages.pop(0), "vps")


@pytest.fixture
def setup(tmp_path):
    settings = replace(
        Settings(),
        database=tmp_path / "db",
        archive_backend="filesystem",
        object_dir=tmp_path / "objects",
        cache_dir=tmp_path / "cache",
        watchlist=tmp_path / "watchlist.json",
    )
    settings.watchlist.write_text('{"vn":[{"symbol":"FPT","intervals":["1D"]}]}')
    repo = Repository(settings.database)
    repo.initialize()
    repo.put([candle(f"2026-01-{day:02}") for day in (5, 6, 7)])
    archived = [candle("2019-01-02"), candle("2019-01-03")]
    head = repo.read("vn", "FPT", "1D")
    archive = Archive(repo, settings)
    worker = Worker(repo, settings, Pages(archived, head), archive)
    path = tmp_path / "FPT-1D-2019.csv"
    path.write_text("2019-01-02,100,101,99,150,1000\n2019-01-03,100,101,99,100,1000\n")
    return repo, archive, worker, path


@pytest.mark.asyncio
async def test_corrupt_daily_recovery_preserves_originals_and_survives_index_restore(
    setup, tmp_path
):
    repo, archive, worker, path = setup
    original, retained = path.read_bytes(), repo.read("vn", "FPT", "1D")
    repo.finding("vn", "FPT", "1D", "migration_daily_history", '{"year":2019,"error":"invalid"}')
    repo.finding("vn", "FPT", "1D", "migration_daily_history", '{"year":2020,"error":"invalid"}')
    result = await recover_daily(worker, path, "FPT", 2019)
    assert path.read_bytes() == archive.store.read(result["raw_key"]) == original
    assert repo.read("vn", "FPT", "1D") == retained
    assert [
        r.close
        for r in History(repo, archive, worker.settings).read(
            "vn", "FPT", "1D", end=parse_time("2020-01-01")
        )
    ] == [100, 100]
    assert result["head"]["matched_rows"] == 3
    assert len(repo.recoveries()) == 1
    assert [r["detail"] for r in repo.findings()] == ['{"year":2020,"error":"invalid"}']
    fresh = Repository(tmp_path / "restored")
    fresh.initialize()
    restored = Archive(fresh, archive.settings)
    assert restored.restore_index() == 1
    assert fresh.recoveries() == repo.recoveries()
    assert restored.read(fresh.archives()[0])[0].close == 100


@pytest.mark.parametrize("failure", ["missing_date", "invalid_price", "changed_head"])
@pytest.mark.asyncio
async def test_recovery_failures_publish_nothing_and_preserve_retained_data(setup, failure):
    repo, archive, worker, path = setup
    original, retained = path.read_bytes(), repo.read("vn", "FPT", "1D")
    if failure == "missing_date":
        worker.providers.pages[0] = worker.providers.pages[0][:1]
    elif failure == "invalid_price":
        worker.providers.pages[0][0] = replace(worker.providers.pages[0][0], close=200)
    else:
        worker.providers.pages[1] = [candle(f"2026-01-{day:02}", 90) for day in (5, 6, 7)]
    with pytest.raises(DataError):
        await recover_daily(worker, path, "FPT", 2019)
    assert path.read_bytes() == original
    assert repo.read("vn", "FPT", "1D") == retained
    assert repo.archives() == [] and repo.recoveries() == []


@pytest.mark.asyncio
async def test_complete_recovery_clears_only_its_verified_unavailable_year(setup, tmp_path):
    repo, archive, worker, path = setup
    for year in (2019, 2020):
        repo.record_history_gap(
            "vn",
            "FPT",
            "1D",
            parse_time(f"{year}-01-01"),
            parse_time(f"{year + 1}-01-01") - 1,
            "Original CSV invalid",
            {"year": year},
        )
    await recover_daily(worker, path, "FPT", 2019)
    assert [r["evidence"]["year"] for r in repo.history_gaps()] == [2020]
    assert (
        len(
            History(repo, archive, worker.settings).read(
                "vn",
                "FPT",
                "1D",
                end=parse_time("2019-12-31"),
                limit=2,
            )
        )
        == 2
    )
    restored = Repository(tmp_path / "gap-index")
    restored.initialize()
    Archive(restored, archive.settings).restore_index()
    assert restored.history_gaps() == repo.history_gaps()


@pytest.mark.asyncio
async def test_incomplete_recovery_never_clears_unavailable_history(setup):
    repo, _, worker, path = setup
    repo.record_history_gap(
        "vn",
        "FPT",
        "1D",
        parse_time("2019-01-01"),
        parse_time("2020-01-01") - 1,
        "Original CSV invalid",
        {"year": 2019},
    )
    original = repo.history_gaps()
    retained = repo.read("vn", "FPT", "1D")
    worker.providers.pages[0] = worker.providers.pages[0][:1]
    with pytest.raises(DataError, match="timestamp coverage"):
        await recover_daily(worker, path, "FPT", 2019)
    assert repo.history_gaps() == original
    assert repo.read("vn", "FPT", "1D") == retained
    assert repo.archives() == [] and repo.recoveries() == []


@pytest.mark.asyncio
async def test_recovery_receipt_metadata_respects_writer_lease_and_can_be_republished(
    setup, tmp_path, monkeypatch
):
    repo, archive, worker, path = setup
    original = repo.read("vn", "FPT", "1D")
    repo.record_history_gap(
        "vn",
        "FPT",
        "1D",
        parse_time("2019-01-01"),
        parse_time("2020-01-01") - 1,
        "Original CSV invalid",
        {"year": 2019},
    )
    publish = archive.publish
    previous_pointer = []

    def competing_writer(*args, **kwargs):
        obj = publish(*args, **kwargs)
        previous_pointer.append(archive.store.read(f"{archive.settings.s3_prefix}/LATEST.json"))
        assert repo.live_claim("vn", "__ARCHIVE_WRITER__", "1D", "other-writer", lease=3600)
        return obj

    monkeypatch.setattr(archive, "publish", competing_writer)
    with pytest.raises(DataError, match="Another archive writer is active"):
        await recover_daily(worker, path, "FPT", 2019)
    assert len(repo.recoveries()) == 1 and repo.history_gaps() == []
    assert repo.read("vn", "FPT", "1D") == original
    assert archive.store.read(f"{archive.settings.s3_prefix}/LATEST.json") == previous_pointer[0]
    repo.live_release("vn", "__ARCHIVE_WRITER__", "1D", "other-writer")
    await execute(parser().parse_args(["publish-index"]), archive.settings)
    fresh = Repository(tmp_path / "republished-index")
    fresh.initialize()
    assert Archive(fresh, archive.settings).restore_index() == 1
    assert fresh.recoveries() == repo.recoveries() and fresh.history_gaps() == []
    assert (
        len(
            History(fresh, Archive(fresh, archive.settings), archive.settings).read(
                "vn", "FPT", "1D", end=parse_time("2019-12-31")
            )
        )
        == 2
    )


@pytest.mark.asyncio
async def test_corrupt_recovery_evidence_cannot_partially_restore_an_index(setup, tmp_path):
    repo, archive, worker, path = setup
    result = await recover_daily(worker, path, "FPT", 2019)
    archive.store.path(result["raw_key"]).write_bytes(b"2019-01-02,100,101,99,100,1000\n")
    fresh = Repository(tmp_path / "restored")
    fresh.initialize()
    with pytest.raises(DataError, match="evidence"):
        Archive(fresh, archive.settings).restore_index()
    assert fresh.archives() == [] and fresh.recoveries() == []


def closure_snapshot(setup):
    repo, archive, worker, path = setup
    path = path.with_name("FPT-1D-2018.csv")
    path.write_text(
        "2018-01-22,100,101,99,100,1000\n"
        "2018-01-23,100,100,100,100,0\n"
        "2018-01-24,100,100,100,100,0\n"
        "2018-01-25,100,101,99,100,1000\n"
    )
    worker.providers = Pages(
        [candle("2018-01-22"), candle("2018-01-25")], repo.read("vn", "FPT", "1D")
    )
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", ["FPT", "VCB", "MBB", "VIC", "HPG"])
async def test_reviewed_closure_recovery_preserves_all_original_evidence(setup, tmp_path, symbol):
    repo, archive, worker, _ = setup
    path = closure_snapshot(setup)
    if symbol != "FPT":
        repo.put([replace(row, symbol=symbol) for row in repo.read("vn", "FPT", "1D")])
        worker.settings.watchlist.write_text(
            json.dumps({"vn": [{"symbol": symbol, "intervals": ["1D"]}]})
        )
        worker.providers.pages = [
            [replace(row, symbol=symbol) for row in page] for page in worker.providers.pages
        ]
    original = path.read_bytes()
    retained = repo.read("vn", symbol, "1D")
    from aipriceaction_api.importing import csv_rows

    old_rows = csv_rows(original.decode(), "vn", symbol, "1D", "legacy", "legacy-2018")
    old = archive.publish(old_rows)
    repo.mark_archive_repairs("vn", symbol, "1D")
    result = await recover_daily(worker, path, symbol, 2018, exclude_verified_sessions=True)
    assert result["rows"] == 2 and result["original_rows"] == 4
    assert result["session_exclusion"]["dates"] == ["2018-01-23", "2018-01-24"]
    assert archive.store.read(result["raw_key"]) == path.read_bytes() == original
    assert len(archive.read(old)) == 4  # Immutable original object is still preserved.
    assert repo.read("vn", symbol, "1D") == retained
    assert [
        r.time
        for r in History(repo, archive, worker.settings).read(
            "vn", symbol, "1D", end=parse_time("2019-01-01")
        )
    ] == [parse_time("2018-01-22"), parse_time("2018-01-25")]
    fresh = Repository(tmp_path / "closure-restored")
    fresh.initialize()
    assert Archive(fresh, archive.settings).restore_index() == 1
    assert fresh.recoveries() == repo.recoveries()


@pytest.mark.parametrize(
    "failure",
    [
        "ordinary_missing",
        "nonflat",
        "positive_volume",
        "flag_off",
        "unknown_year",
        "unreviewed_ticker",
    ],
)
@pytest.mark.asyncio
async def test_closure_recovery_never_licenses_other_timestamp_drops(setup, failure):
    repo, archive, worker, _ = setup
    path = closure_snapshot(setup)
    if failure == "ordinary_missing":
        worker.providers.pages[0] = worker.providers.pages[0][:1]
    elif failure == "nonflat":
        path.write_text(path.read_text().replace("100,100,100,100,0", "100,101,100,100,0"))
    elif failure == "positive_volume":
        path.write_text(path.read_text().replace("100,100,100,100,0", "100,100,100,100,1"))
    symbol = "FPT"
    if failure == "unreviewed_ticker":
        symbol = "PNJ"
        repo.put([replace(row, symbol=symbol) for row in repo.read("vn", "FPT", "1D")])
        worker.settings.watchlist.write_text(
            json.dumps({"vn": [{"symbol": symbol, "intervals": ["1D"]}]})
        )
    year = 2019 if failure == "unknown_year" else 2018
    with pytest.raises(DataError):
        await recover_daily(
            worker, path, symbol, year, exclude_verified_sessions=failure != "flag_off"
        )
    assert repo.archives() == [] and repo.recoveries() == []
    assert len(repo.read("vn", "FPT", "1D")) == 3


@pytest.mark.asyncio
async def test_rehashed_closure_evidence_cannot_expand_the_reviewed_scope(setup, tmp_path):
    repo, archive, worker, _ = setup
    path = closure_snapshot(setup)
    result = await recover_daily(worker, path, "FPT", 2018, exclude_verified_sessions=True)
    proof = json.loads(archive.store.read(result["proof_key"]))
    proof["session_exclusion"]["dates"].append("2018-01-25")
    data = encode(proof)
    key = result["proof_key"].rsplit("/", 1)[0] + "/" + digest(data) + ".json"
    archive.store.path(key).write_bytes(data)
    result.update(
        proof_key=key, proof_checksum=digest(data), session_exclusion=proof["session_exclusion"]
    )
    with repo.connect() as con:
        con.execute("UPDATE legacy_imports SET result=?", (json.dumps(result),))
    archive.manifest(repo.archives())
    fresh = Repository(tmp_path / "tampered-closure")
    fresh.initialize()
    with pytest.raises(DataError, match="evidence"):
        Archive(fresh, archive.settings).restore_index()
    assert fresh.archives() == [] and fresh.recoveries() == []


@pytest.mark.parametrize("volume", [0, 1])
@pytest.mark.asyncio
async def test_provider_closure_placeholders_require_the_same_zero_volume_guard(setup, volume):
    repo, archive, worker, _ = setup
    path = closure_snapshot(setup)
    worker.providers.pages[0] = [
        candle("2018-01-22"),
        replace(candle("2018-01-23"), high=100, low=100, volume=volume),
        replace(candle("2018-01-24"), high=100, low=100, volume=volume),
        candle("2018-01-25"),
    ]
    if volume:
        with pytest.raises(DataError, match="zero-volume"):
            await recover_daily(worker, path, "FPT", 2018, exclude_verified_sessions=True)
        assert not repo.archives()
    else:
        result = await recover_daily(worker, path, "FPT", 2018, exclude_verified_sessions=True)
        assert result["rows"] == 2
