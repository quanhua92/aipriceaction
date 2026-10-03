import hashlib
import io
import zipfile
from dataclasses import replace

import httpx
import pytest

from aipriceaction_api.binance_files import BinanceFiles, month_bounds, parse_zip
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, parse_time
from aipriceaction_api.providers import Providers
from aipriceaction_api.storage import Repository
from aipriceaction_api.workers import Worker


def bundle(month="2025-02", iv="1h", multiplier=1_000_000, edit=None, member=None):
    start, end = month_bounds(month)
    step = {"1m": 60, "1h": 3600}[iv]
    rows = [
        [str(t * multiplier), "10", "11", "9", "10.5", "100.75", "0", "0", "1", "0", "0", "0"]
        for t in range(start, end, step)
    ]
    if edit:
        edit(rows)
    raw = "\n".join(",".join(row) for row in rows).encode()
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member or f"BTCUSDT-{iv}-{month}.csv", raw)
    return result.getvalue()


@pytest.mark.parametrize("month,multiplier", [("2024-12", 1000), ("2025-01", 1_000_000)])
def test_timestamp_transition_and_legacy_volume_match_live_representation(month, multiplier):
    rows = parse_zip(
        bundle(month, multiplier=multiplier), f"BTCUSDT-1h-{month}.csv", "BTCUSDT", "1h", month
    )
    start, end = month_bounds(month)
    assert rows[0].time == start and rows[-1].time == end - 3600
    assert len(rows) == (end - start) // 3600
    assert (rows[0].open, rows[0].high, rows[0].low, rows[0].close, rows[0].volume) == (
        10,
        11,
        9,
        10.5,
        100,
    )


@pytest.mark.parametrize(
    "edit",
    [
        lambda r: r.pop(100),
        lambda r: r.insert(100, r[99]),
        lambda r: r.pop(),
        lambda r: r[100].__setitem__(2, "8"),
        lambda r: r[100].__setitem__(5, "-0.5"),
        lambda r: r[100].__setitem__(0, str(int(r[100][0]) + 1)),
    ],
)
def test_corruption_and_missing_candles_are_rejected(edit):
    with pytest.raises(DataError):
        parse_zip(bundle(edit=edit), "BTCUSDT-1h-2025-02.csv", "BTCUSDT", "1h", "2025-02")


def test_wrong_member_is_rejected():
    with pytest.raises(DataError, match="ZIP member"):
        parse_zip(bundle(member="other.csv"), "BTCUSDT-1h-2025-02.csv", "BTCUSDT", "1h", "2025-02")


@pytest.fixture(scope="module")
def minute_zip():
    return bundle(iv="1m")


@pytest.mark.asyncio
async def test_month_download_uses_checksum_and_cache_without_changing_live_updates(
    tmp_path, minute_zip
):
    calls = []
    checksum = hashlib.sha256(minute_zip).hexdigest()
    stamp = parse_time("2025-02-28")

    def respond(request):
        calls.append(request.url.path)
        if request.url.path.endswith(".CHECKSUM"):
            return httpx.Response(200, text=checksum + "  BTCUSDT-1m-2025-02.zip")
        if request.url.path.endswith(".zip"):
            return httpx.Response(200, content=minute_zip)
        return httpx.Response(200, json=[[stamp * 1000, "10", "11", "9", "10.5", "100.75"]])

    settings = replace(Settings(), cache_dir=tmp_path / "cache", requests_per_minute=100000)
    providers = Providers(settings, httpx.MockTransport(respond))
    try:
        page = await providers.page(
            "crypto", "BTCUSDT", "1m", parse_time("2025-03-01"), count=50000
        )
        assert len(page.rows) == 40320 and page.rows[0].time == parse_time("2025-02-01")
        assert page.provider == "binance"
        second = await providers.page(
            "crypto", "BTCUSDT", "1m", parse_time("2025-02-02"), count=50000
        )
        assert len(second.rows) == 1440 and second.rows[-1].time == parse_time("2025-02-02") - 60
        live = await providers.page("crypto", "BTCUSDT", "1m", parse_time("2025-03-01"), count=40)
        assert live.rows[-1].volume == page.rows[-1].volume == 100
    finally:
        await providers.close()
    assert sum(path.endswith(".zip") for path in calls) == 1
    assert sum(path.endswith(".CHECKSUM") for path in calls) == 2
    assert calls[-1] == "/api/v3/klines"


@pytest.mark.asyncio
async def test_missing_month_falls_back_to_live_and_retries_file_later(tmp_path, monkeypatch):
    calls = []

    def respond(request):
        calls.append(request.url.path)
        if request.url.path.endswith(".CHECKSUM"):
            return httpx.Response(404)
        return httpx.Response(
            200, json=[[parse_time("2025-02-28") * 1000, "10", "11", "9", "10.5", "100.75"]]
        )

    providers = Providers(
        replace(Settings(), cache_dir=tmp_path / "cache", requests_per_minute=100000),
        httpx.MockTransport(respond),
    )
    try:
        for _ in range(2):
            page = await providers.page(
                "crypto", "BTCUSDT", "1m", parse_time("2025-03-01"), count=50000
            )
            assert page.rows and page.provider == "binance"
        assert sum(p.endswith(".CHECKSUM") for p in calls) == 1
        providers.binance_files.unavailable[("BTCUSDT", "1m", "2025-02")] -= 3601
        await providers.page("crypto", "BTCUSDT", "1m", parse_time("2025-03-01"), count=50000)
        assert sum(p.endswith(".CHECKSUM") for p in calls) == 2
    finally:
        await providers.close()


@pytest.mark.asyncio
async def test_tampered_file_keeps_published_snapshot_and_resumable_cursor(tmp_path, minute_zip):
    def respond(request):
        if request.url.path.endswith(".CHECKSUM"):
            return httpx.Response(200, text="0" * 64 + "  BTCUSDT-1m-2025-02.zip")
        if request.url.path.endswith(".zip"):
            return httpx.Response(200, content=minute_zip)
        return httpx.Response(
            200, json=[[parse_time("2025-03-01") * 1000, "10", "11", "9", "10.5", "100.75"]]
        )

    settings = replace(
        Settings(),
        database=tmp_path / "db",
        cache_dir=tmp_path / "cache",
        requests_per_minute=100000,
    )
    repo = Repository(settings.database)
    repo.initialize()
    repo.queue("crypto", "BTCUSDT", "1m", "bootstrap", parse_time("2025-02-01"))
    providers = Providers(settings, httpx.MockTransport(respond))
    worker = Worker(repo, settings, providers)
    try:
        assert await worker.repair_page(repo.claim_job(worker.owner)) == 1
        original = repo.read("crypto", "BTCUSDT", "1m")
        assert await worker.repair_page(repo.claim_job(worker.owner)) == 0
        assert repo.read("crypto", "BTCUSDT", "1m") == original
        with repo.connect() as con:
            job = dict(
                con.execute(
                    "SELECT * FROM jobs WHERE source='crypto' AND symbol='BTCUSDT' AND interval='1m'"
                ).fetchone()
            )
        assert job["cursor"] == parse_time("2025-03-01") and "checksum mismatch" in job["error"]
    finally:
        await providers.close()


@pytest.mark.asyncio
async def test_download_and_uncompressed_size_budgets(tmp_path, monkeypatch):
    import aipriceaction_api.binance_files as files

    raw = bundle()

    async def request(url, budget):
        return (
            (hashlib.sha256(raw).hexdigest() + "  BTCUSDT-1h-2025-02.zip").encode()
            if url.endswith(".CHECKSUM")
            else raw
        )

    monkeypatch.setattr(files, "CSV_BUDGET", 10)
    with pytest.raises(DataError, match="uncompressed size"):
        await BinanceFiles(tmp_path, request).month("BTCUSDT", "1h", parse_time("2025-03-01"))
    assert not list(tmp_path.glob("*.zip"))

    def respond(request):
        return httpx.Response(200, content=b"x" * 11)

    providers = Providers(
        replace(Settings(), requests_per_minute=100000), httpx.MockTransport(respond)
    )
    try:
        with pytest.raises(DataError, match="download size"):
            await providers.binary_request("https://data.binance.vision/file.zip", 10)
    finally:
        await providers.close()
