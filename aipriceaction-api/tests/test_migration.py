import json
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, parse_time
from aipriceaction_api.history import History
from aipriceaction_api.importing import csv_rows, json_rows
from aipriceaction_api.migration import LegacyImporter, api_batches, legacy_files
from aipriceaction_api.storage import Repository


@pytest.fixture
def system(tmp_path):
    settings = replace(
        Settings(),
        database=tmp_path / "db",
        object_dir=tmp_path / "objects",
        cache_dir=tmp_path / "archive-cache",
        archive_backend="filesystem",
    )
    repo = Repository(settings.database)
    repo.initialize()
    archive = Archive(repo, settings)
    return repo, archive, History(repo, archive, settings)


def csv(day, close=100):
    return f"{day},100,110,90,{close},1000\n"


def test_real_legacy_headerless_format_and_named_local_exports():
    # Format emitted by Rust's rows_to_csv, observed on the public FPT archive.
    raw = "2022-01-04 00:00:00,47407.71,47813.78,47356.96,47458.47,1935000\n"
    rows = csv_rows(raw, "vn", "FPT", "1D")
    assert len(rows) == 1 and rows[0].time == parse_time("2022-01-04")
    assert rows[0].open == 47407.71 and rows[0].volume == 1935000
    named = "symbol,volume,close,low,high,open,time\nFPT,1935000,47458.47,47356.96,47813.78,47407.71,2022-01-04\n"
    assert csv_rows(named, "vn", "FPT", "1D") == rows
    assert csv_rows(raw + raw, "vn", "FPT", "1D") == rows
    with pytest.raises(DataError, match="Conflicting duplicate"):
        csv_rows(raw + raw.replace("47458.47", "47460.00"), "vn", "FPT", "1D")
    with pytest.raises(DataError, match="Invalid CSV value"):
        csv_rows(raw.replace("1935000", "1935000.5"), "vn", "FPT", "1D")


def test_minute_file_plan_is_per_day_and_bounded():
    files = legacy_files("vn", "FPT", "1m", start="2024-02-28", end="2024-03-01")
    assert len(files) == 3
    assert files[1][1] == "ohlcv/vn/FPT/1m/FPT-1m-2024-02-29.csv"
    assert files[2][0] == "2024-03"
    with pytest.raises(DataError, match="not --years"):
        legacy_files("vn", "FPT", "1m", years=[2025])
    with pytest.raises(DataError, match="366 days"):
        legacy_files("vn", "FPT", "1m", start="2024-01-01", end="2025-01-01")


def test_api_batches_cover_exact_dates_and_preserve_month_receipts():
    files = legacy_files("vn", "FPT", "1m", start="2024-02-28", end="2024-04-03")
    batches = api_batches(files, 31)
    assert len(batches) == 3
    assert [(x[0], x[2], x[3]) for x in batches] == [
        ("2024-02", parse_time("2024-02-28"), parse_time("2024-03-01") - 1),
        ("2024-03", parse_time("2024-03-01"), parse_time("2024-04-01") - 1),
        ("2024-04", parse_time("2024-04-01"), parse_time("2024-04-04") - 1),
    ]
    weekly = api_batches(files, 7)
    assert all(x[3] - x[2] < 7 * 86400 for x in weekly)
    assert weekly[0][2] == files[0][2] and weekly[-1][3] == files[-1][3]
    assert all(a[3] + 1 == b[2] for a, b in zip(weekly, weekly[1:], strict=False))


@pytest.mark.asyncio
async def test_batched_api_import_resumes_without_rewinding_and_rejects_truncation(system):
    repo, archive, _ = system
    calls = []
    full = False

    def respond(request):
        first, last = request.url.params["start_date"], request.url.params["end_date"]
        calls.append((first, last))
        raw = csv(first + " 02:15:00") + csv(last + " 02:15:00")
        if full:
            # An API limit may truncate a broad range, even if returned dates
            # are in range. Reject before publishing or recording a receipt.
            raw = "".join(
                csv(
                    datetime.fromtimestamp(parse_time(first) + 60 * i, UTC).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                )
                for i in range(10000)
            )
        return httpx.Response(200, text=raw)

    kwargs = dict(start="2025-10-02", end="2025-10-31", from_api=True, api_batch_days=31)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        importer = LegacyImporter(repo, archive, client=client)
        result = await importer.run("https://example.test", "vn", "FPT", "1m", **kwargs)
        assert result["files"] == 1 and result["imported"] == 2
        repo.put([replace(repo.read("vn", "FPT", "1m")[0], close=105)])
        result = await importer.run("https://example.test", "vn", "FPT", "1m", **kwargs)
        assert result["cached"] == 1 and result["periods"][0]["resumed"]
        assert calls == [("2025-10-02", "2025-10-31")]
        assert repo.read("vn", "FPT", "1m")[0].close == 105
        full = True
        with pytest.raises(DataError, match="limit"):
            await importer.run("https://example.test", "vn", "VCB", "1m", **kwargs)
        assert repo.read("vn", "VCB", "1m") == []


@pytest.mark.asyncio
async def test_api_batch_days_reject_unrelated_inputs(system):
    repo, archive, _ = system
    importer = LegacyImporter(repo, archive)
    for kwargs in ({"api_batch_days": 0}, {"api_batch_days": 32}, {"api_batch_days": 7}):
        with pytest.raises(DataError, match="API batch days"):
            await importer.run("https://example.test", "vn", "FPT", "1D", years=[2025], **kwargs)


@pytest.mark.asyncio
async def test_minute_import_splits_retention_and_preserves_one_basis(system):
    repo, archive, history = system

    def respond(request):
        day = request.url.path[-14:-4]
        return httpx.Response(200, text=csv(day + " 02:15:00"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await LegacyImporter(repo, archive, client=client).run(
            "https://example.test",
            "vn",
            "FPT",
            "1m",
            start="2025-01-01",
            end="2025-01-03",
            recent_floor=parse_time("2025-01-03"),
        )
    assert result["imported"] == 1 and result["archived"] == 2
    assert len(repo.archives()) == 1  # Monthly partition, not one object per day.
    assert len(repo.read("vn", "FPT", "1m")) == 1
    assert len(history.read("vn", "FPT", "1m")) == 3


@pytest.mark.asyncio
async def test_failed_publication_resumes_verified_downloads(system, monkeypatch):
    repo, archive, history = system
    calls = []

    def respond(request):
        calls.append(request.url.path)
        return httpx.Response(200, text=csv("2022-01-04"))

    publish = archive.manifest

    def fail(_):
        raise DataError("Interrupted manifest upload")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        importer = LegacyImporter(repo, archive, client=client)
        monkeypatch.setattr(archive, "manifest", fail)
        with pytest.raises(DataError, match="Interrupted"):
            await importer.run(
                "https://example.test",
                "vn",
                "FPT",
                "1D",
                years=[2022],
                recent_floor=parse_time("2023-01-01"),
            )
        assert len(repo.archives()) == 1 and repo.read("vn", "FPT", "1D") == []
        assert history.read("vn", "FPT", "1D")[0].close == 100
        monkeypatch.setattr(archive, "manifest", publish)
        result = await importer.run(
            "https://example.test",
            "vn",
            "FPT",
            "1D",
            years=[2022],
            recent_floor=parse_time("2023-01-01"),
        )
    assert result["cached"] == 1 and result["downloaded"] == 0
    assert len(calls) == 1 and history.read("vn", "FPT", "1D")[0].close == 100


@pytest.mark.asyncio
async def test_completed_import_does_not_overwrite_newer_corrections(system):
    repo, archive, _ = system
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=csv("2025-01-02")))
    ) as client:
        importer = LegacyImporter(repo, archive, client=client)
        await importer.run("https://example.test", "vn", "FPT", "1D", years=[2025])
        repo.put([replace(repo.read("vn", "FPT", "1D")[0], close=105)])
        result = await importer.run("https://example.test", "vn", "FPT", "1D", years=[2025])
    assert result["periods"][0]["resumed"] is True
    assert result["imported"] == 0 and repo.read("vn", "FPT", "1D")[0].close == 105


@pytest.mark.asyncio
async def test_resumed_incomplete_month_adds_missing_rows_without_rewinding_updates(system):
    repo, archive, _ = system
    available = False

    def respond(request):
        day = request.url.path[-14:-4]
        if day == "2025-01-02" and not available:
            return httpx.Response(404)
        return httpx.Response(200, text=csv(day + " 02:15:00"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        importer = LegacyImporter(repo, archive, client=client)
        await importer.run(
            "https://example.test", "vn", "FPT", "1m", start="2025-01-01", end="2025-01-02"
        )
        repo.put([replace(repo.read("vn", "FPT", "1m")[0], close=105)])
        available = True
        result = await importer.run(
            "https://example.test", "vn", "FPT", "1m", start="2025-01-01", end="2025-01-02"
        )
    assert result["imported"] == 1
    assert [r.close for r in repo.read("vn", "FPT", "1m")] == [105, 100]


@pytest.mark.asyncio
async def test_recent_provider_conflict_is_rejected_before_archive_upload(system):
    repo, archive, _ = system
    repo.put(
        [
            Candle(
                "vn",
                "FPT",
                "1D",
                parse_time("2025-01-03"),
                100,
                110,
                90,
                100,
                1000,
                "vps",
                "current",
            )
        ]
    )
    text = csv("2025-01-01") + csv("2025-01-02")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=text))
    ) as client:
        importer = LegacyImporter(repo, archive, client=client)
        with pytest.raises(DataError, match="conflicts"):
            await importer.run(
                "https://example.test",
                "vn",
                "FPT",
                "1D",
                years=[2025],
                recent_floor=parse_time("2025-01-02"),
            )
        assert repo.archives() == []
        result = await importer.run(
            "https://example.test",
            "vn",
            "FPT",
            "1D",
            years=[2025],
            recent_floor=parse_time("2025-01-02"),
            older_only=True,
        )
    assert result["archived"] == 1 and result["skipped_recent"] == 1
    assert repo.state("vn", "FPT", "1D")["provider"] == "vps"
    assert repo.findings()[0]["kind"] == "imported_revision_boundary"


@pytest.mark.asyncio
async def test_inaccessible_objects_are_reported_and_retried(system):
    repo, archive, history = system
    available = False

    def respond(request):
        if request.url.path.endswith("2022.csv") and not available:
            return httpx.Response(403)
        year = request.url.path[-8:-4]
        return httpx.Response(200, text=csv(year + "-01-02"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        importer = LegacyImporter(repo, archive, client=client)
        first = await importer.run("https://example.test", "vn", "FPT", "1D", years=[2022, 2023])
        assert first["unavailable"][0]["status"] == 403 and first["imported"] == 1
        available = True
        result = await importer.run("https://example.test", "vn", "FPT", "1D", years=[2022, 2023])
    assert result["imported"] == 1 and len(history.read("vn", "FPT", "1D")) == 2


@pytest.mark.asyncio
async def test_corrupt_cache_and_wrong_dated_file_never_import(system):
    repo, archive, _ = system
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=csv("2023-01-02")))
    ) as client:
        importer = LegacyImporter(repo, archive, client=client)
        with pytest.raises(DataError, match="dated object key"):
            await importer.run("https://example.test", "vn", "FPT", "1D", years=[2022])
        assert repo.read("vn", "FPT", "1D") == []
        await importer.run("https://example.test", "vn", "FPT", "1D", years=[2023])
        next(importer.cache_dir.glob("*.csv")).write_bytes(b"corrupted")
        with pytest.raises(DataError, match="cache is corrupt"):
            await importer.run("https://example.test", "vn", "FPT", "1D", years=[2023])


@pytest.mark.asyncio
async def test_dry_run_inventories_files_without_mutation(system):
    repo, archive, _ = system
    epoch = repo.epoch()
    calls = []

    def respond(request):
        calls.append(request.method)
        return httpx.Response(200, headers={"content-length": "123"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        importer = LegacyImporter(repo, archive, client=client)
        result = await importer.run(
            "https://example.test",
            "vn",
            "FPT",
            "1m",
            start="2025-01-01",
            end="2025-01-03",
            dry_run=True,
        )
    assert calls == ["HEAD"] * 3 and result["dry_run"] is True
    assert repo.epoch() == epoch and repo.findings() == [] and repo.archives() == []
    assert not importer.cache_dir.exists()


@pytest.mark.asyncio
async def test_public_api_csv_export_retains_provenance_and_date_bounds(system):
    repo, archive, history = system
    calls = []

    def respond(request):
        calls.append(request)
        day = request.url.params["start_date"]
        text = "symbol,time,open,high,low,close,volume\n"
        if day != "2025-01-03":
            text += f"FPT,{day} 02:15:00,100,110,90,100,1000\n"
        return httpx.Response(200, text=text)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await LegacyImporter(repo, archive, client=client).run(
            "https://example.test",
            "vn",
            "FPT",
            "1m",
            start="2025-01-01",
            end="2025-01-03",
            provider="legacy-api",
            from_api=True,
        )
    assert result["imported"] == 2 and len(result["empty_files"]) == 1
    assert result["unavailable"] == []
    assert all(
        r.url.path == "/tickers"
        and r.url.params["format"] == "csv"
        and r.url.params["limit"] == "10000"
        for r in calls
    )
    assert history.read("vn", "FPT", "1m")[0].provider == "legacy-api"


def precise_quote(time="2025-01-02T13:30:00"):
    return dict(
        time=time,
        open=768.3499755859375,
        high=768.47998046875,
        low=767.6519775390625,
        close=767.6519775390625,
        volume=1672604,
        symbol="SPY",
    )


@pytest.mark.asyncio
async def test_json_api_import_preserves_precision_and_frozen_cache(system):
    repo, archive, _ = system
    calls = []
    body = json.dumps({"SPY": [precise_quote()]})

    def respond(request):
        calls.append(request)
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        importer = LegacyImporter(repo, archive, client=client)
        options = dict(
            start="2025-01-02",
            end="2025-01-02",
            from_api=True,
            api_format="json",
            provider="legacy-api",
            revision="json-snapshot",
        )
        first = await importer.run("https://example.test", "yahoo", "SPY", "1m", **options)
        saved = repo.read("yahoo", "SPY", "1m")
        second = await importer.run("https://example.test", "yahoo", "SPY", "1m", **options)
    assert first["imported"] == 1 and second["cached"] == 1
    assert len(calls) == 1 and calls[0].url.params["format"] == "json"
    assert saved == repo.read("yahoo", "SPY", "1m")
    assert saved[0].close == precise_quote()["close"]
    assert saved[0].revision == "json-snapshot" and saved[0].updated_at > 0
    assert any(path.read_text() == body for path in importer.cache_dir.glob("*.json"))


@pytest.mark.parametrize(
    "payload",
    [
        {"OTHER": [precise_quote()]},
        {"SPY": [precise_quote() | {"volume": 1.5}]},
        {"SPY": [precise_quote(), precise_quote() | {"close": 768.0}]},
        {"SPY": [precise_quote() | {"symbol": "OTHER"}]},
    ],
)
def test_json_api_rejects_wrong_symbols_invalid_volume_and_conflicts(payload):
    with pytest.raises(DataError):
        json_rows(json.dumps(payload), "yahoo", "SPY", "1m")


@pytest.mark.asyncio
async def test_empty_json_api_response_retries_before_freezing(system):
    repo, archive, _ = system
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={} if len(calls) == 1 else {"SPY": [precise_quote()]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        importer = LegacyImporter(repo, archive, client=client)
        options = dict(start="2025-01-02", end="2025-01-02", from_api=True, api_format="json")
        first = await importer.run("https://example.test", "yahoo", "SPY", "1m", **options)
        second = await importer.run("https://example.test", "yahoo", "SPY", "1m", **options)
    assert first["empty_files"] and second["imported"] == 1 and len(calls) == 2


@pytest.mark.asyncio
async def test_format_change_cannot_reuse_a_published_snapshot_receipt(system):
    repo, archive, _ = system

    def respond(request):
        if request.url.params["format"] == "json":
            return httpx.Response(200, json={"SPY": [precise_quote()]})
        return httpx.Response(200, text="2025-01-02T13:30:00,768.35,768.48,767.65,767.65,1672604\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        importer = LegacyImporter(repo, archive, client=client)
        options = dict(start="2025-01-02", end="2025-01-02", from_api=True)
        await importer.run("https://example.test", "yahoo", "SPY", "1m", **options)
        before = repo.read("yahoo", "SPY", "1m")
        with pytest.raises(DataError, match="new revision"):
            await importer.run(
                "https://example.test", "yahoo", "SPY", "1m", api_format="json", **options
            )
    assert repo.read("yahoo", "SPY", "1m") == before


@pytest.mark.asyncio
@pytest.mark.parametrize("truncated", [False, True])
async def test_json_export_bounds_and_limit_prevent_publication(system, truncated):
    repo, archive, _ = system
    rows = [precise_quote("2025-01-08T13:30:00")]
    if truncated:
        start = parse_time("2025-01-01")
        rows = [precise_quote(start + i * 60) for i in range(10000)]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"SPY": rows}))
    ) as client:
        with pytest.raises(DataError, match="limit|dated object"):
            await LegacyImporter(repo, archive, client=client).run(
                "https://example.test",
                "yahoo",
                "SPY",
                "1m",
                start="2025-01-01",
                end="2025-01-07",
                api_batch_days=7,
                from_api=True,
                api_format="json",
            )
    assert repo.read("yahoo", "SPY", "1m") == [] and repo.archives() == []


@pytest.mark.asyncio
async def test_empty_api_response_is_retried_and_does_not_prove_coverage(system):
    repo, archive, _ = system
    available = False
    calls = []

    def respond(request):
        calls.append(request)
        text = "symbol,time,open,high,low,close,volume\n"
        if available:
            text += "FPT,2025-01-02 02:15:00,100,110,90,100,1000\n"
        return httpx.Response(200, text=text)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        importer = LegacyImporter(repo, archive, client=client)
        first = await importer.run(
            "https://example.test",
            "vn",
            "FPT",
            "1m",
            start="2025-01-02",
            end="2025-01-02",
            from_api=True,
        )
        assert first["imported"] == 0 and len(first["empty_files"]) == 1
        available = True
        second = await importer.run(
            "https://example.test",
            "vn",
            "FPT",
            "1m",
            start="2025-01-02",
            end="2025-01-02",
            from_api=True,
        )
    assert len(calls) == 2 and second["imported"] == 1
