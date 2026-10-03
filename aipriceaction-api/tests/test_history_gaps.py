import hashlib
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from aipriceaction_api.app import create_app
from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, parse_time
from aipriceaction_api.history import History
from aipriceaction_api.migration import LegacyImporter
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
    archive = Archive(repo, settings)
    return repo, archive, History(repo, archive, settings)


def bar(day):
    return Candle("vn", "FPT", "1D", parse_time(day), 100, 101, 99, 100, 1000)


def gap(repo, year=2022):
    repo.record_history_gap(
        "vn",
        "FPT",
        "1D",
        parse_time(f"{year}-01-01"),
        parse_time(f"{year + 1}-01-01") - 1,
        "Invalid original yearly CSV",
        {"kind": "legacy_daily_import", "year": year},
    )


@pytest.mark.parametrize(
    "read",
    [
        {"start": parse_time("2022-01-01"), "end": parse_time("2022-12-31")},
        {"end": parse_time("2022-12-31"), "limit": 1},
        {},
    ],
)
def test_known_missing_year_cannot_be_empty_skipped_or_joined(system, read):
    repo, _, history = system
    repo.put([bar(day) for day in ("2021-12-31", "2023-01-02", "2023-01-03")])
    original = repo.read("vn", "FPT", "1D")
    gap(repo)
    with pytest.raises(DataError, match="Historical data unavailable") as error:
        history.read("vn", "FPT", "1D", **read)
    assert error.value.status == 503
    assert repo.read("vn", "FPT", "1D") == original
    assert repo.status()["jobs"] == []


def test_recent_and_satisfied_forward_limits_ignore_unneeded_missing_year(system):
    repo, _, history = system
    repo.put([bar(day) for day in ("2021-12-30", "2021-12-31", "2023-01-02", "2023-01-03")])
    gap(repo)
    assert history.read("vn", "FPT", "1D", limit=1)[0].time == parse_time("2023-01-03")
    assert history.read("vn", "FPT", "1D", start=parse_time("2021-12-30"), limit=1, forward=True)[
        0
    ].time == parse_time("2021-12-30")
    assert len(history.query("vn", "FPT", "1D", limit=1, ma=False)) == 1
    with pytest.raises(DataError, match="Historical data unavailable"):
        history.query("vn", "FPT", "1D", start=parse_time("2023-01-02"), limit=1, ma=True)


def test_unavailable_range_is_scoped_to_market_ticker_and_native_interval(system):
    repo, _, history = system
    repo.put([bar("2022-01-03")])
    repo.record_history_gap(
        "yahoo", "FPT", "1D", parse_time("2022-01-01"), parse_time("2023-01-01") - 1, "Missing", {}
    )
    repo.record_history_gap(
        "vn", "OTHER", "1D", parse_time("2022-01-01"), parse_time("2023-01-01") - 1, "Missing", {}
    )
    repo.record_history_gap(
        "vn", "FPT", "1m", parse_time("2022-01-01"), parse_time("2023-01-01") - 1, "Missing", {}
    )
    assert len(history.read("vn", "FPT", "1D")) == 1


def test_http_reports_gap_and_rejects_missing_history_without_blocking_recent(system):
    repo, archive, _ = system
    repo.put([bar("2023-01-02"), bar("2023-01-03")])
    gap(repo)
    with TestClient(create_app(archive.settings)) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["storage"]["history_gaps"] == repo.history_gaps()
        response = client.get(
            "/tickers",
            params={
                "symbol": "FPT",
                "start_date": "2022-01-01",
                "end_date": "2022-12-31",
                "ma": "false",
            },
        )
        assert response.status_code == 503
        assert "Historical data unavailable" in response.text
        response = client.get("/tickers", params={"symbol": "FPT", "limit": 1, "ma": "false"})
        assert response.status_code == 200 and len(response.json()["FPT"]) == 1


def test_gap_markers_survive_backup_and_manifest_restore_without_schema_change(system, tmp_path):
    repo, archive, _ = system
    gap(repo)
    archive.publish_metadata()
    restored = Repository(tmp_path / "index")
    restored.initialize()
    assert Archive(restored, archive.settings).restore_index() == 0
    assert restored.history_gaps() == repo.history_gaps()
    backup = tmp_path / "backup"
    repo.backup(backup)
    assert Repository(backup).history_gaps() == repo.history_gaps()
    with Repository(backup).connect() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2


@pytest.mark.parametrize(
    "change",
    [
        {"source": []},
        {"start": True},
        {"end": 1.5},
        {"start": 999999999999999999},
        {"reason": ""},
        {"evidence": {"value": float("nan")}},
        {"evidence": {"value": "x" * 16384}},
    ],
)
def test_malformed_manifest_gap_rejects_before_target_mutation(system, tmp_path, change):
    repo, archive, _ = system
    gap(repo)
    record = repo.history_gaps()[0] | change
    body = json.dumps({"version": 1, "objects": [], "history_gaps": [record]}).encode()
    key = "bad-manifest.json"
    path = tmp_path / "manifest"
    path.write_bytes(body)
    archive.store.put(key, path)
    path.write_text(json.dumps({"key": key, "checksum": hashlib.sha256(body).hexdigest()}))
    archive.store.put("archive-v2/LATEST.json", path)
    target = Repository(tmp_path / "target")
    target.initialize()
    with pytest.raises(DataError, match="unavailable-history|Unavailable-history"):
        Archive(target, archive.settings).restore_index()
    assert target.findings() == [] and target.archives() == []


def test_old_manifest_without_gap_field_never_clears_newer_local_observation(system, tmp_path):
    repo, archive, _ = system
    body = b'{"version":1,"objects":[]}'
    path = tmp_path / "old"
    path.write_bytes(body)
    archive.store.put("old-manifest.json", path)
    path.write_text(
        json.dumps({"key": "old-manifest.json", "checksum": hashlib.sha256(body).hexdigest()})
    )
    archive.store.put("archive-v2/LATEST.json", path)
    gap(repo)
    original = repo.history_gaps()
    assert archive.restore_index() == 0 and repo.history_gaps() == original


@pytest.mark.parametrize("failure", ["invalid", "missing"])
@pytest.mark.asyncio
async def test_failed_dated_import_records_and_persists_unavailable_range(
    system, tmp_path, failure
):
    repo, archive, history = system
    repo.put([bar("2026-01-02")])
    original = repo.read("vn", "FPT", "1D")

    def handler(request):
        return httpx.Response(
            403 if failure == "missing" else 200, text="2019-01-02,100,101,99,150,1000\n"
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        importer = LegacyImporter(repo, archive, client=client)
        arguments = dict(years=[2019], recent_floor=parse_time("2023-01-01"), older_only=True)
        if failure == "invalid":
            with pytest.raises(DataError, match="Invalid OHLC"):
                await importer.run("https://archive.example", "vn", "FPT", "1D", **arguments)
        else:
            result = await importer.run("https://archive.example", "vn", "FPT", "1D", **arguments)
            assert result["unavailable"][0]["status"] == 403
    assert repo.history_gaps()[0]["start"] == parse_time("2019-01-01")
    assert repo.read("vn", "FPT", "1D") == original
    with pytest.raises(DataError, match="Historical data unavailable"):
        history.read("vn", "FPT", "1D", parse_time("2019-01-01"), parse_time("2019-12-31"))
    target = Repository(tmp_path / "index")
    target.initialize()
    Archive(target, archive.settings).restore_index()
    assert target.history_gaps() == repo.history_gaps()


@pytest.mark.asyncio
async def test_failed_import_preserves_verified_archived_range(system):
    repo, archive, history = system
    repo.put([bar("2019-01-02"), bar("2019-12-31")])
    archived = repo.read("vn", "FPT", "1D")
    archive.publish(archived, prune=True)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(403))
    ) as client:
        await LegacyImporter(repo, archive, client=client).run(
            "https://archive.example",
            "vn",
            "FPT",
            "1D",
            years=[2019],
            recent_floor=parse_time("2023-01-01"),
            older_only=True,
        )
    assert (
        history.read("vn", "FPT", "1D", parse_time("2019-01-02"), parse_time("2019-12-31"))
        == archived
    )
    assert repo.history_gaps()[0]["end"] == parse_time("2019-01-02") - 1
