import asyncio
import importlib.util
import time
from argparse import Namespace
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, parse_time
from aipriceaction_api.history import History
from aipriceaction_api.providers import Page, Providers, adjustment_changes
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
        requests_per_minute=100000,
    )
    repo = Repository(settings.database)
    repo.initialize()
    archive = Archive(repo, settings)
    return repo, archive, settings


def candle(day, price=100, provider="vps", revision="initial", symbol="FPT"):
    return Candle(
        "vn",
        symbol,
        "1D",
        parse_time(f"2024-01-{day:02}"),
        price,
        price + 1,
        price - 1,
        price,
        1000,
        provider,
        revision,
    )


class Pages:
    def __init__(self, *pages):
        self.pages = list(pages)
        self.calls = []

    async def page(self, source, symbol, iv, before=None, count=500, provider=None, start=None):
        self.calls.append(provider)
        value = self.pages.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "probe_error", [DataError("Historical provider unavailable"), RuntimeError("private detail")]
)
async def test_failed_historical_probe_preserves_committed_daily_success(
    system, monkeypatch, probe_error
):
    repo, archive, settings = system
    now = parse_time("2026-07-01")
    monkeypatch.setattr("aipriceaction_api.workers.time.time", lambda: now)
    age = 90 + (now // 86400 % 10) * 90
    sample_end = now - age * 86400
    original = [replace(candle(1), time=sample_end - i * 86400) for i in range(10)]
    latest = [replace(candle(1), time=now - i * 86400) for i in range(1, 41)]
    repo.put(original + latest)
    published_history = repo.read("vn", "FPT", "1D", end=sample_end)
    incoming = sorted(latest, key=lambda r: r.time)[1:] + [replace(latest[0], time=now)]
    provider = Pages(Page(incoming, "vps"), probe_error)
    worker = Worker(repo, settings, provider, archive)
    assert await worker.sync({"source": "vn", "symbol": "FPT"}, "1D") == 40
    status = repo.status()["series"][0]
    assert status["outcome"] == "succeeded" and status["completed_rows"] == 40
    assert repo.read("vn", "FPT", "1D", limit=1)[0].time == now
    findings = repo.findings()
    assert not any(row["kind"] == "provider_failure" for row in findings)
    failure = next(row for row in findings if row["kind"] == "historical_probe_failure")
    assert failure["detail"] == (
        str(probe_error) if isinstance(probe_error, DataError) else "RuntimeError"
    )
    assert repo.read("vn", "FPT", "1D", end=sample_end) == published_history


@pytest.mark.asyncio
async def test_cancelled_historical_probe_preserves_committed_daily_success(system, monkeypatch):
    repo, archive, settings = system
    recent = [replace(candle(1), time=parse_time("2026-06-01") + i * 86400) for i in range(40)]
    repo.put(recent)
    worker = Worker(repo, settings, Pages(Page(recent, "vps")), archive)

    async def cancel(*args):
        raise asyncio.CancelledError

    monkeypatch.setattr(worker, "sentinel", cancel)
    with pytest.raises(asyncio.CancelledError):
        await worker.sync({"source": "vn", "symbol": "FPT"}, "1D")
    status = repo.status()["series"][0]
    assert status["outcome"] == "succeeded" and status["completed_rows"] == 40
    assert not any(row["kind"] == "provider_failure" for row in repo.findings())


@pytest.mark.asyncio
async def test_historical_probe_timeout_does_not_exhaust_live_update_budget(system, monkeypatch):
    repo, archive, settings = system
    recent = [replace(candle(1), time=parse_time("2026-06-01") + i * 86400) for i in range(40)]
    repo.put(recent)
    worker = Worker(repo, settings, Pages(Page(recent, "vps")), archive)
    worker.deadline = 0.2

    async def stall(*args):
        await asyncio.sleep(1)

    monkeypatch.setattr(worker, "sentinel", stall)
    assert await worker.sync({"source": "vn", "symbol": "FPT"}, "1D") == 40
    assert repo.status()["series"][0]["outcome"] == "succeeded"
    assert any(row["kind"] == "historical_probe_failure" for row in repo.findings())
    assert not any(row["kind"] == "provider_failure" for row in repo.findings())


@pytest.mark.asyncio
async def test_historical_revision_outside_live_overlap_still_queues_repair(system, monkeypatch):
    repo, archive, settings = system
    now = parse_time("2026-07-01")
    monkeypatch.setattr("aipriceaction_api.workers.time.time", lambda: now)
    age = 90 + (now // 86400 % 10) * 90
    sample_end = now - age * 86400
    old = [replace(candle(1), time=sample_end - i * 86400) for i in range(10)]
    recent = [replace(candle(1), time=now - i * 86400) for i in range(1, 41)]
    repo.put(old + recent)
    before = repo.read("vn", "FPT", "1D")
    changed = sorted(
        [replace(r, open=90, high=91, low=89, close=90) for r in old], key=lambda r: r.time
    )
    provider = Pages(Page(sorted(recent, key=lambda r: r.time), "vps"), Page(changed, "vps"))
    worker = Worker(repo, settings, provider, archive)
    assert await worker.sync({"source": "vn", "symbol": "FPT"}, "1D") == 40
    assert repo.state("vn", "FPT", "1D")["status"] == "repairing"
    assert [(r.time, r.close) for r in repo.read("vn", "FPT", "1D")] == [
        (r.time, r.close) for r in before
    ]
    assert any(row["kind"] == "historical_revision" for row in repo.findings())


@pytest.mark.parametrize("elapsed", [100, 1200])
@pytest.mark.asyncio
async def test_crypto_restart_fills_bounded_gap_or_queues_recovery(system, monkeypatch, elapsed):
    repo, archive, settings = system
    first = parse_time("2026-01-01")
    original = Candle("crypto", "BTCUSDT", "1m", first, 100, 101, 99, 100, 1000, "binance")
    repo.put([original])
    calls = []

    class Live:
        async def page(self, source, symbol, iv, before=None, count=40, provider=None):
            calls.append(count)
            return Page(
                [
                    replace(original, time=first + 60 * i)
                    for i in range(max(0, elapsed - count + 1), elapsed + 1)
                ],
                "binance",
            )

    worker = Worker(repo, settings, providers=Live(), archive=archive)
    monkeypatch.setattr(worker, "floor", lambda entry, iv: first)
    monkeypatch.setattr("aipriceaction_api.workers.time.time", lambda: first + elapsed * 60)
    processed = await worker.sync({"source": "crypto", "symbol": "BTCUSDT"}, "1m")
    if elapsed == 100:
        assert processed == 101 and len(repo.read("crypto", "BTCUSDT", "1m")) == 101
        assert calls == [140] and repo.status()["jobs"] == []
    else:
        assert processed == 0 and calls == [1000]
        assert repo.read("crypto", "BTCUSDT", "1m")[0].time == first
        jobs = repo.status()["jobs"]
        assert len(jobs) == 1 and jobs[0]["kind"] == "repair" and jobs[0]["status"] == "pending"
        assert any(r["kind"] == "coverage_pending" for r in repo.findings())


@pytest.mark.parametrize(
    "source,iv", [("vn", "1D"), ("vn", "1h"), ("vn", "1m"), ("crypto", "1m"), ("yahoo", "1m")]
)
@pytest.mark.asyncio
async def test_sparse_replacement_preserves_completed_published_coverage(system, source, iv):
    repo, archive, settings = system
    first = parse_time("2026-01-05")
    step = {"1D": 86400, "1h": 3600, "1m": 60}[iv]
    original = [
        Candle(source, "TEST", iv, first + i * step, 100, 101, 99, 100, 1000, "original")
        for i in range(3)
    ]
    repo.put(original)
    original = repo.read(source, "TEST", iv)
    # The first/last bars and floor are present, yet a completed candle within
    # that session disappeared. Neither cursor traversal nor the newest bar proves
    # that this source is a safe replacement for the published window.
    incoming = [replace(original[i], provider="replacement") for i in (0, 2)]
    repo.queue(source, "TEST", iv, "repair", first, "replacement")
    worker = Worker(repo, settings, providers=Pages(Page(incoming, "replacement")), archive=archive)
    assert await worker.repair_page(repo.claim_job(worker.owner)) == 0
    assert repo.read(source, "TEST", iv) == original
    assert repo.status()["jobs"][0]["status"] == "pending"
    assert any("drops completed observed coverage" in r["detail"] for r in repo.findings())


@pytest.mark.parametrize("iv", ["1m", "1h", "1D"])
@pytest.mark.parametrize("elapsed,reply_cap", [(100, None), (1200, None), (100, 40)])
@pytest.mark.asyncio
async def test_vn_restart_requires_observed_tail_overlap(
    system, monkeypatch, iv, elapsed, reply_cap
):
    repo, archive, settings = system
    step = {"1m": 60, "1h": 3600, "1D": 86400}[iv]
    first = (
        parse_time("2026-07-01") - elapsed * step
        if iv == "1D"
        else parse_time("2026-01-05T02:00:00")
    )
    original = Candle("vn", "FPT", iv, first, 100, 101, 99, 100, 1000, "vps")
    repo.put([original])
    original_rows = repo.read("vn", "FPT", iv)
    calls = []

    class Live:
        async def page(self, source, symbol, interval, count=40, provider=None, start=None):
            calls.append((count, provider))
            assert start == (first if count > 40 else None)
            size = min(count, reply_cap) if reply_cap else count
            return Page(
                [
                    replace(original, time=first + step * i)
                    for i in range(max(0, elapsed - size + 1), elapsed + 1)
                ],
                "vps",
            )

    worker = Worker(repo, settings, providers=Live(), archive=archive)

    async def skip_sentinel(*args):
        pass

    monkeypatch.setattr(worker, "sentinel", skip_sentinel)
    monkeypatch.setattr(worker, "floor", lambda entry, interval: first)
    monkeypatch.setattr("aipriceaction_api.workers.time.time", lambda: first + elapsed * step)
    processed = await worker.sync({"source": "vn", "symbol": "FPT"}, iv)
    assert calls == [(40, "vps"), (min(1000, elapsed + 40), "vps")]
    if elapsed == 100 and reply_cap is None:
        assert processed == 101
        assert [r.time for r in repo.read("vn", "FPT", iv)] == [
            first + i * step for i in range(101)
        ]
        assert repo.status()["jobs"] == []
        assert repo.status()["series"][0]["outcome"] == "succeeded"
    else:
        assert processed == 0
        assert repo.read("vn", "FPT", iv) == original_rows
        jobs = repo.status()["jobs"]
        assert len(jobs) == 1 and jobs[0]["kind"] == "repair"
        assert repo.claim_job(worker.owner)["provider"] == "vps"
        assert repo.status()["series"][0]["outcome"] == "repair_queued"
        assert any("does not overlap" in r["detail"] for r in repo.findings())
    assert repo.live_claim("vn", "FPT", iv, "another-worker")


@pytest.mark.parametrize("iv", ["1m", "1h", "1D"])
@pytest.mark.asyncio
async def test_vn_sparse_weekend_page_needs_no_calendar_guess_or_expansion(system, monkeypatch, iv):
    repo, archive, settings = system
    dates = ["2026-01-02T07:00:00", "2026-01-05T02:00:00", "2026-01-05T03:00:00"]
    if iv == "1D":
        dates = ["2026-01-02", "2026-01-05", "2026-01-06"]
    rows = [
        Candle("vn", "FPT", iv, parse_time(day), 100, 101, 99, 100, 1000, "vps") for day in dates
    ]
    repo.put(rows[:1])
    calls = []

    class Live:
        async def page(self, *args, **kwargs):
            calls.append(kwargs["count"])
            return Page(rows, "vps")

    worker = Worker(repo, settings, providers=Live(), archive=archive)
    monkeypatch.setattr("aipriceaction_api.workers.time.time", lambda: rows[-1].time + 60)
    assert await worker.sync({"source": "vn", "symbol": "FPT"}, iv) == 3
    assert calls == [40] and repo.status()["jobs"] == []
    assert [r.time for r in repo.read("vn", "FPT", iv)] == [r.time for r in rows]


@pytest.mark.parametrize("iv", ["1m", "1D"])
@pytest.mark.asyncio
async def test_vn_expanded_overlap_failure_preserves_published_candles(system, monkeypatch, iv):
    repo, archive, settings = system
    step = 86400 if iv == "1D" else 60
    first = parse_time("2026-06-01") if iv == "1D" else parse_time("2026-01-05T02:00:00")
    original = Candle("vn", "FPT", iv, first, 100, 101, 99, 100, 1000, "vps")
    repo.put([original])
    original_rows = repo.read("vn", "FPT", iv)
    calls = []

    class Live:
        async def page(self, *args, count=40, provider=None, start=None):
            calls.append((count, provider))
            if count > 40:
                raise DataError("Expanded native history unavailable")
            return Page([replace(original, time=first + i * step) for i in range(61, 101)], "vps")

    worker = Worker(repo, settings, providers=Live(), archive=archive)
    monkeypatch.setattr("aipriceaction_api.workers.time.time", lambda: first + 100 * step)
    assert await worker.sync({"source": "vn", "symbol": "FPT"}, iv) == 0
    assert calls == [(40, "vps"), (140, "vps")]
    assert repo.read("vn", "FPT", iv) == original_rows
    assert repo.status()["series"][0]["error"] == "Expanded native history unavailable"
    assert repo.live_claim("vn", "FPT", iv, "another-worker")


@pytest.mark.parametrize("iv", ["1m", "1D"])
@pytest.mark.asyncio
async def test_vn_outage_fallback_queues_basis_recovery_without_expanding_alternate(system, iv):
    repo, archive, settings = system
    original = Candle("vn", "FPT", iv, parse_time("2026-01-05"), 100, 101, 99, 100, 1000, "vps")
    repo.put([original])
    original_rows = repo.read("vn", "FPT", iv)
    incoming = replace(
        original, provider="vndirect", time=original.time + 100 * (86400 if iv == "1D" else 60)
    )
    providers = Pages(DataError("VPS unavailable"), Page([incoming], "vndirect"))
    worker = Worker(repo, settings, providers=providers, archive=archive)
    assert await worker.sync({"source": "vn", "symbol": "FPT"}, iv) == 0
    assert providers.calls == ["vps", "vndirect"]
    assert repo.read("vn", "FPT", iv) == original_rows
    jobs = repo.status()["jobs"]
    assert len(jobs) == 1 and repo.claim_job(worker.owner)["provider"] == "vndirect"
    assert repo.status()["series"][0]["outcome"] == "repair_queued"


@pytest.mark.parametrize("iv", ["1m", "1h", "1D"])
@pytest.mark.parametrize("ratio", [1.001, 1.0000001])
@pytest.mark.asyncio
async def test_vn_expanded_page_checks_revisions_outside_normal_overlap(
    system, monkeypatch, iv, ratio
):
    repo, archive, settings = system
    step = {"1m": 60, "1h": 3600, "1D": 86400}[iv]
    first = parse_time("2024-01-01") if iv == "1D" else parse_time("2026-01-05T02:00:00")
    old = [
        Candle("vn", "FPT", iv, first + i * step, 100, 101, 99, 100, 1000, "vps")
        for i in range(200)
    ]
    repo.put(old)
    published = repo.read("vn", "FPT", iv)
    changed = {140, 141, 142}  # Outside the last 50 original candles.
    incoming = [
        replace(
            r, open=r.open * ratio, high=r.high * ratio, low=r.low * ratio, close=r.close * ratio
        )
        if i in changed
        else r
        for i, r in enumerate(old)
    ] + [
        replace(old[-1], time=old[-1].time + (200 * 86400 if iv == "1D" else 86400) + i * step)
        for i in range(150)
    ]
    calls = []

    class Live:
        async def page(self, *args, count=40, provider=None, start=None):
            calls.append(count)
            return Page(incoming[-count:], "vps")

    worker = Worker(repo, settings, providers=Live(), archive=archive)
    monkeypatch.setattr("aipriceaction_api.workers.time.time", lambda: incoming[-1].time + step)
    count = await worker.sync({"source": "vn", "symbol": "FPT"}, iv)
    assert calls[0] == 40 and 50 < calls[1] <= 1000
    assert all(
        r.time < published[-50].time for r in incoming if r.time in {old[i].time for i in changed}
    )
    if ratio == 1.001:
        assert count == 0 and repo.read("vn", "FPT", iv) == published
        assert repo.status()["series"][0]["outcome"] == "repair_queued"
        assert any(r["kind"] == "historical_revision" for r in repo.findings())
    else:
        assert count > 150 and repo.status()["jobs"] == []
        assert repo.status()["series"][0]["outcome"] == "succeeded"


def test_validation_uses_requested_page_but_rejects_invalid_selected_candles():
    invalid = replace(candle(1), close=200)
    assert Providers.normalize([invalid, candle(2), candle(3)], candle(4).time, 2, "vps").rows == [
        candle(2),
        candle(3),
    ]
    with pytest.raises(DataError, match="OHLC range"):
        Providers.normalize([invalid, candle(2), candle(3)], candle(4).time, 3, "vps")


def test_conflicting_duplicate_rejects_only_the_selected_page():
    rows = [candle(1), replace(candle(1), close=100.5), candle(2), candle(3)]
    assert Providers.normalize(rows, candle(4).time, 2, "vps").rows == [candle(2), candle(3)]
    with pytest.raises(DataError, match="conflicting candles"):
        Providers.normalize(rows, candle(4).time, 3, "vps")
    assert Providers.normalize([candle(1), candle(1)], candle(2).time, 3, "vps").rows == [candle(1)]


@pytest.mark.parametrize(
    "conflict_day,complete,old_close", [(1, True, 200), (1, True, None), (2, False, 200)]
)
@pytest.mark.asyncio
async def test_native_repair_bounds_validation_to_retained_window(
    system, conflict_day, complete, old_close
):
    repo, archive, settings = system
    original = [candle(day) for day in (2, 3, 4)]
    repo.put(original)
    original = repo.read("vn", "FPT", "1D")
    rows = [candle(day, provider="dnse") for day in (1, 2, 3, 4)]
    rows.append(
        replace(rows[conflict_day - 1], time=rows[conflict_day - 1].time + 7200, close=100.5)
    )
    # The extra old invalid row and duplicate emulate DNSE's over-returned
    # page. Both inside-boundary defects must still prevent publication.
    rows[0] = replace(rows[0], close=old_close)
    payload = {
        name: [
            None
            if getattr(row, field) is None
            else getattr(row, field) / (1000 if name in ("o", "h", "l", "c") else 1)
            for row in rows
        ]
        for name, field in (
            ("t", "time"),
            ("o", "open"),
            ("h", "high"),
            ("l", "low"),
            ("c", "close"),
            ("v", "volume"),
        )
    }
    providers = Providers(
        settings, httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )
    worker = Worker(repo, replace(settings, vn_providers=("dnse",)), providers, archive)
    repo.queue("vn", "FPT", "1D", "repair", candle(2).time, "dnse")
    try:
        assert await worker.repair_page(repo.claim_job(worker.owner)) == (3 if complete else 0)
    finally:
        await providers.close()
    if complete:
        published = repo.read("vn", "FPT", "1D")
        assert [row.time for row in published] == [row.time for row in original]
        assert {row.provider for row in published} == {"dnse"}
        assert repo.state("vn", "FPT", "1D")["status"] == "ready"
        assert repo.status()["jobs"] == []
    else:
        assert repo.read("vn", "FPT", "1D") == original
        assert "conflicting candles" in repo.status()["jobs"][0]["error"]


@pytest.mark.asyncio
async def test_scoped_empty_terminal_page_finishes_only_previously_staged_data(system):
    repo, archive, settings = system
    original = [candle(day) for day in (3, 4)]
    repo.put(original)
    floor = candle(2).time
    outside = Providers.normalize(
        [replace(candle(1), close=200)], candle(3).time, 500, "vps", start=floor
    )
    assert outside.rows == [] and outside.cursor == candle(1).time
    worker = Worker(repo, settings, Pages(Page(original, "vps"), outside), archive)
    repo.queue("vn", "FPT", "1D", "repair", floor, "vps")
    assert await worker.repair_page(repo.claim_job(worker.owner)) == 2
    assert repo.state("vn", "FPT", "1D")["status"] == "repairing"
    assert await worker.repair_page(repo.claim_job(worker.owner)) == 0
    assert repo.state("vn", "FPT", "1D")["status"] == "ready"
    assert [row.time for row in repo.read("vn", "FPT", "1D")] == [row.time for row in original]
    assert repo.status()["jobs"] == []


@pytest.mark.asyncio
async def test_scoped_old_only_initial_page_never_replaces_published_data(system):
    repo, archive, settings = system
    repo.put([candle(3), candle(4)])
    original = repo.read("vn", "FPT", "1D")
    floor = candle(2).time
    page = Providers.normalize([candle(1)], candle(5).time, 500, "vps", start=floor)
    worker = Worker(repo, settings, Pages(page), archive)
    repo.queue("vn", "FPT", "1D", "repair", floor, "vps")
    assert await worker.repair_page(repo.claim_job(worker.owner)) == 0
    assert repo.read("vn", "FPT", "1D") == original
    assert repo.status()["jobs"][0]["status"] == "pending"


def test_scoped_validation_still_rejects_invalid_candle_at_floor():
    with pytest.raises(DataError, match="OHLC range"):
        Providers.normalize(
            [candle(1), replace(candle(2), close=200), candle(3)],
            candle(4).time,
            500,
            "dnse",
            start=candle(2).time,
        )


@pytest.mark.parametrize("iv,step", [("1D", 86400), ("1h", 3600), ("1m", 60)])
def test_crypto_provider_rejects_holes_inside_requested_pages(iv, step):
    first = parse_time("2026-01-05")
    rows = [
        Candle("crypto", "BTCUSDT", iv, first + i * step, 100, 101, 99, 100, 1000, "binance")
        for i in range(3)
    ]
    assert Providers.normalize(rows, first + 3 * step, 3, "binance").rows == rows
    with pytest.raises(DataError, match="page has a gap"):
        Providers.normalize([rows[0], rows[2]], first + 3 * step, 3, "binance")
    assert Providers.normalize([rows[0], rows[2]], first + 3 * step, 1, "binance").rows == [rows[2]]


@pytest.mark.asyncio
async def test_mixed_vn_daily_timestamp_bases_reject_only_affected_pages(system):
    _, _, settings = system
    # Real VPS VNINDEX pattern: Vietnam midnight and UTC midnight encode
    # the same market date but disagree on its opening price.
    times = [parse_time("2025-05-04T17:00:00"), parse_time("2025-05-05"), parse_time("2026-10-02")]
    payload = {
        "s": "ok",
        "t": times,
        "o": [1226.3, 1233.97, 1747.48],
        "h": [1241.51, 1241.51, 1751.6],
        "l": [1226.3, 1226.3, 1731.03],
        "c": [1240.05, 1240.05, 1737.71],
        "v": [562828082, 562828082, 615757736],
    }
    p = Providers(
        settings, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    )
    try:
        with pytest.raises(DataError, match="timestamp bases"):
            await p.vn_page("vps", "VNINDEX", "1D", parse_time("2025-05-06"), 2)
        recent = await p.vn_page("vps", "VNINDEX", "1D", parse_time("2026-10-03"), 1)
        assert len(recent.rows) == 1 and recent.rows[0].time == times[-1]
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_interrupted_repair_preserves_old_revision_and_resumes(system):
    repo, archive, settings = system
    repo.put([candle(d) for d in range(1, 6)])
    archive.publish([candle(1)], prune=False)
    repo.queue("vn", "FPT", "1D", "repair", candle(1).time, "vps")
    provider = Pages(
        Page([candle(4, 90), candle(5, 90)], "vps"),
        DataError("temporary failure"),
        Page([candle(d, 90) for d in range(1, 4)], "vps"),
    )
    worker = Worker(repo, settings, provider, archive)
    assert await worker.repair_page(repo.claim_job(worker.owner)) == 2
    assert repo.read("vn", "FPT", "1D")[-1].close == 100
    assert await worker.repair_page(repo.claim_job(worker.owner)) == 0
    assert repo.read("vn", "FPT", "1D")[-1].close == 100
    with repo.connect() as con:
        con.execute("UPDATE jobs SET retry_at=0")
    assert await worker.repair_page(repo.claim_job(worker.owner)) == 3
    assert [r.close for r in repo.read("vn", "FPT", "1D")] == [90] * 5
    assert repo.state("vn", "FPT", "1D")["status"] == "ready"
    assert repo.archives()[0]["status"] == "pending_repair"
    assert provider.calls == ["vps"] * 3


@pytest.mark.asyncio
async def test_provider_switch_queues_rebuild_instead_of_appending(system):
    repo, archive, settings = system
    repo.put([candle(1)])
    repo.register("vn", "FPT", enabled=True)
    provider = Pages(DataError("VPS unavailable"), Page([candle(2, 90, "vndirect")], "vndirect"))
    worker = Worker(repo, settings, provider, archive)
    assert await worker.sync({"source": "vn", "symbol": "FPT"}, "1D") == 0
    assert repo.read("vn", "FPT", "1D")[0].close == 100
    assert repo.state("vn", "FPT", "1D")["status"] == "repairing"
    job = repo.claim_job(worker.owner)
    assert job["provider"] == "vndirect"
    assert provider.calls == ["vps", "vndirect"]


@pytest.mark.asyncio
async def test_provider_cannot_change_halfway_through_staging(system):
    repo, archive, settings = system
    repo.queue("vn", "FPT", "1D", "bootstrap", candle(1).time)
    worker = Worker(
        repo,
        settings,
        Pages(Page([candle(5)], "vps"), Page([candle(1, provider="dnse")], "dnse")),
        archive,
    )
    assert await worker.repair_page(repo.claim_job(worker.owner)) == 1
    assert await worker.repair_page(repo.claim_job(worker.owner)) == 0
    assert len(repo.read("vn", "FPT", "1D")) == 1
    assert repo.read("vn", "FPT", "1D")[0].provider == "vps"
    assert "mix providers" in repo.status()["jobs"][0]["error"]


def test_page_fairness_leases_and_archive_queue_isolation(system):
    repo, _, _ = system
    first = repo.queue("vn", "FPT", "1D", "bootstrap", candle(1).time)
    second = repo.queue("vn", "VCB", "1D", "bootstrap", candle(1).time)
    repo.queue("vn", "FPT", "1D", "archive_repair:object:revision", candle(1).time)
    job = repo.claim_job("a")
    assert job["id"] == first
    next_job = repo.claim_job("b")
    assert next_job["id"] == second
    repo.stage(job, [replace(candle(5), revision=job["revision"])], candle(5).time, "vps")
    repo.fail_job(next_job, "temporary")
    assert repo.claim_job("a")["id"] == first


def test_failure_after_staging_receives_cooldown(system):
    repo, _, _ = system
    repo.queue("vn", "FPT", "1D", "bootstrap", candle(1).time)
    job = repo.claim_job("worker")
    repo.stage(job, [replace(candle(1), revision=job["revision"])], candle(1).time, "vps")
    repo.fail_job(job, "manifest unavailable")
    assert repo.claim_job("worker") is None
    assert repo.status()["jobs"][0]["attempts"] == 1


def test_indicator_warmup_cannot_cross_revisions(system):
    repo, archive, settings = system
    repo.put([candle(1)])
    archive.publish(repo.read("vn", "FPT", "1D"), prune=True)
    with repo.connect() as con:
        con.execute("UPDATE series SET revision='adjusted'")
    repo.put([candle(2, 90, revision="adjusted")])
    with pytest.raises(DataError, match="adjustment revisions"):
        History(repo, archive, settings).query("vn", "FPT", "1D", limit=1)


@pytest.mark.parametrize("ratio", [0.8, 1.2])
def test_adjustment_detection_is_bidirectional_and_ignores_unfinished(ratio):
    old = [candle(d) for d in range(1, 6)]
    new = [candle(d, 100 * ratio) for d in range(1, 6)]
    assert len(adjustment_changes(old, new, candle(4).time)) == 3
    assert adjustment_changes(old, new, candle(3).time) == []
    assert adjustment_changes(old, [candle(1, 101)], candle(6).time) == []
    assert adjustment_changes(old, [replace(r, provider="dnse") for r in new], candle(6).time) == []


@pytest.mark.parametrize("ratio", [0.999, 1.001, 1.0000001])
@pytest.mark.asyncio
async def test_small_completed_price_revisions_stage_repair_without_overwriting(
    system, monkeypatch, ratio
):
    repo, archive, settings = system
    old = [candle(d) for d in range(1, 6)]
    repo.put(old)
    original = repo.read("vn", "FPT", "1D")
    worker = Worker(
        repo,
        settings,
        providers=Pages(Page([candle(d, 100 * ratio) for d in range(1, 6)], "vps")),
        archive=archive,
    )
    monkeypatch.setattr(worker, "floor", lambda entry, iv: candle(1).time)

    async def skip_sentinel(*args):
        pass

    monkeypatch.setattr(worker, "sentinel", skip_sentinel)
    count = await worker.sync({"source": "vn", "symbol": "FPT"}, "1D")
    if ratio == 1.0000001:
        assert count == 5 and repo.status()["jobs"] == []
        assert repo.read("vn", "FPT", "1D")[-1].close == 100 * ratio
    else:
        assert count == 0 and repo.read("vn", "FPT", "1D") == original
        jobs = repo.status()["jobs"]
        assert len(jobs) == 1 and jobs[0]["kind"] == "repair"
        with repo.connect() as con:
            assert (
                con.execute("SELECT provider FROM jobs WHERE id=?", (jobs[0]["id"],)).fetchone()[0]
                == "vps"
            )
        assert any(r["kind"] == "historical_revision" for r in repo.findings())


@pytest.mark.parametrize("provider", ["vps", "vndirect", "dnse"])
@pytest.mark.asyncio
async def test_vn_parsing_scale_order_cursor_and_index(system, provider):
    _, _, settings = system
    payload = {
        "s": "ok",
        "t": [candle(2).time * 1000, candle(1).time * 1000],
        "o": [100, 100],
        "h": [101, 101],
        "l": [99, 99],
        "c": [100, 100],
        "v": [1000, 1000],
    }
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json=payload)

    providers = Providers(settings, httpx.MockTransport(respond))
    try:
        page = await providers.page("vn", "FPT", "1D", candle(3).time, provider=provider)
        assert [r.time for r in page.rows] == [candle(1).time, candle(2).time]
        assert page.rows[0].close == 100000
        index = await providers.page("vn", "VNMIDCAP", "1D", candle(2).time, provider=provider)
        assert index.rows[0].close == 100
        if provider == "dnse":
            assert "/index" in str(seen[-1].url)
            assert seen[-1].url.params["resolution"] == "1D"
    finally:
        await providers.close()


@pytest.mark.asyncio
async def test_dnse_vnindex_session_timestamp_transition_preserves_market_dates(system):
    _, _, settings = system
    # Captured DNSE index bars: UTC midnight, 09:15 ICT, then 09:00 ICT.
    payload = {
        "s": "ok",
        "t": [1709769600, 1709864100, 1746410400],
        "o": [1263.51, 1272.62, 1226.3],
        "h": [1269.88, 1274.3, 1241.51],
        "l": [1260.24, 1247.35, 1226.3],
        "c": [1268.46, 1247.35, 1240.05],
        "v": [987451920, 1343076480, 562287400],
    }
    providers = Providers(
        settings, httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    )
    try:
        page = await providers.page(
            "vn", "VNINDEX", "1D", parse_time("2025-05-06"), count=3, provider="dnse"
        )
        assert [row.time for row in page.rows] == [
            parse_time("2024-03-07"),
            parse_time("2024-03-08"),
            parse_time("2025-05-05"),
        ]
        assert [row.close for row in page.rows] == payload["c"]
        assert [row.volume for row in page.rows] == payload["v"]
        assert page.cursor == parse_time("2024-03-07")
    finally:
        await providers.close()


@pytest.mark.parametrize(
    "provider,symbol", [("dnse", "FPT"), ("vps", "VNINDEX"), ("vndirect", "VNINDEX")]
)
@pytest.mark.asyncio
async def test_vnindex_session_convention_does_not_allow_other_series(system, provider, symbol):
    _, _, settings = system
    payload = {
        "s": "ok",
        "t": [1709864100],
        "o": [1272.62],
        "h": [1274.3],
        "l": [1247.35],
        "c": [1247.35],
        "v": [1343076480],
    }
    providers = Providers(
        settings, httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    )
    try:
        with pytest.raises(DataError, match="unverified VN daily timestamps"):
            await providers.page(
                "vn", symbol, "1D", parse_time("2024-03-09"), count=1, provider=provider
            )
    finally:
        await providers.close()


@pytest.mark.parametrize("violation", ["conflict", "ohlc"])
@pytest.mark.asyncio
async def test_dnse_vnindex_session_bars_still_require_valid_unique_ohlcv(system, violation):
    _, _, settings = system
    day = parse_time("2024-03-08")
    payload = {
        "s": "ok",
        "t": [day, day + 2 * 3600 + 15 * 60],
        "o": [1272.62, 1272.62],
        "h": [1274.3, 1274.3],
        "l": [1247.35, 1247.35],
        "c": [1247.35, 1247.35],
        "v": [1343076480, 1343076480],
    }
    if violation == "conflict":
        payload["v"][1] += 1
    else:
        payload["h"][1] = 1269.88
    providers = Providers(
        settings, httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    )
    try:
        with pytest.raises(DataError, match="conflicting candles|OHLC range"):
            await providers.page("vn", "VNINDEX", "1D", day + 86400, count=1, provider="dnse")
    finally:
        await providers.close()


@pytest.mark.asyncio
async def test_provider_rejects_malformed_arrays(system):
    _, _, settings = system
    providers = Providers(
        settings, httpx.MockTransport(lambda _: httpx.Response(200, json={"t": [1], "o": []}))
    )
    try:
        with pytest.raises(DataError, match="arrays"):
            await providers.page("vn", "FPT", "1D", provider="vps")
    finally:
        await providers.close()


@pytest.mark.parametrize("hour", [17, 4])
@pytest.mark.asyncio
async def test_unknown_vn_daily_timestamp_convention_cannot_shift_market_dates(system, hour):
    _, _, settings = system
    payload = {
        "s": "ok",
        "t": [candle(d).time + hour * 3600 for d in (1, 2)],
        "o": [100, 100],
        "h": [101, 101],
        "l": [99, 99],
        "c": [100, 100],
        "v": [1000, 1000],
    }
    providers = Providers(
        settings, httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    )
    try:
        with pytest.raises(DataError, match="unverified VN daily timestamps"):
            await providers.page("vn", "FPT", "1D", candle(3).time, count=2, provider="vps")
    finally:
        await providers.close()


@pytest.mark.asyncio
async def test_unrequested_old_timestamp_convention_does_not_reject_verified_page(system):
    _, _, settings = system
    payload = {
        "s": "ok",
        "t": [candle(1).time + 17 * 3600, candle(2).time, candle(3).time],
        "o": [100] * 3,
        "h": [101] * 3,
        "l": [99] * 3,
        "c": [100] * 3,
        "v": [1000] * 3,
    }
    providers = Providers(
        settings, httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    )
    try:
        page = await providers.page("vn", "FPT", "1D", candle(4).time, count=2, provider="vps")
        assert [r.time for r in page.rows] == [candle(2).time, candle(3).time]
    finally:
        await providers.close()


@pytest.mark.asyncio
async def test_dnse_daily_session_start_preserves_the_market_date(system):
    _, _, settings = system
    payload = {
        "s": "ok",
        "t": [candle(d).time + 2 * 3600 for d in (1, 2)],
        "o": [100, 100],
        "h": [101, 101],
        "l": [99, 99],
        "c": [100, 100],
        "v": [1000, 1000],
    }
    providers = Providers(
        settings, httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    )
    try:
        page = await providers.page("vn", "FPT", "1D", candle(3).time, count=2, provider="dnse")
        assert [r.time for r in page.rows] == [candle(1).time, candle(2).time]
        assert all(r.provider == "dnse" and r.close == 100000 for r in page.rows)
    finally:
        await providers.close()


def test_direct_vn_calls_require_explicit_configuration(system):
    _, _, settings = system
    with pytest.raises(DataError, match="HTTP_PROXIES"):
        Providers(settings).routes(True)


def test_expired_lease_cannot_publish(system):
    repo, _, _ = system
    repo.queue("vn", "FPT", "1D", "bootstrap", candle(1).time)
    job = repo.claim_job("a")
    with repo.connect() as con:
        con.execute("UPDATE jobs SET lease_until=?", (int(time.time()) - 1,))
    with pytest.raises(DataError, match="lease expired"):
        repo.stage(job, [replace(candle(1), revision=job["revision"])], candle(1).time, "vps")


@pytest.mark.asyncio
async def test_archived_adjustment_repairs_have_independent_object_checkpoints(system):
    repo, archive, settings = system
    repo.put([candle(1), candle(2), candle(3)])
    for day in (1, 2):
        archive.publish(
            repo.read("vn", "FPT", "1D", candle(day).time, candle(day).time), prune=True
        )
    repo.queue("vn", "FPT", "1D", "repair", candle(3).time, "vps")
    worker = Worker(
        repo,
        settings,
        Pages(
            Page([candle(2, 90), candle(3, 90)], "vps"),
            Page([candle(2, 90)], "vps"),
            Page([candle(2, 90), candle(3, 90)], "vps"),
            Page([candle(1, 90)], "vps"),
            Page([candle(2, 90), candle(3, 90)], "vps"),
        ),
        archive,
    )
    assert await worker.repair_page(repo.claim_job(worker.owner)) == 2
    assert all(obj["status"] == "pending_repair" for obj in repo.archives())
    assert await worker.archive_repair() == 1
    assert await worker.archive_repair() == 1
    assert len(repo.archives()) == 2
    assert all(obj["status"] == "published" for obj in repo.archives())
    assert [r.close for r in History(repo, archive, settings).read("vn", "FPT", "1D")] == [90] * 3
    with repo.connect() as con:
        assert (
            con.execute(
                "SELECT COUNT(*) FROM jobs WHERE kind LIKE 'archive_repair:%' AND status='complete'"
            ).fetchone()[0]
            == 2
        )


@pytest.mark.parametrize("changed", [True, False])
@pytest.mark.asyncio
async def test_archive_repair_verifies_retained_basis_before_publication(system, changed):
    repo, archive, settings = system
    retained = [candle(d) for d in (3, 4, 5)]
    repo.put(retained)
    original = archive.publish([candle(1, provider="legacy", revision="snapshot")])
    repo.mark_archive_repairs("vn", "FPT", "1D")
    head = [candle(d, 90) for d in (3, 4, 5)] if changed else [candle(8)]
    worker = Worker(repo, settings, Pages(Page([candle(1)], "vps"), Page(head, "vps")), archive)
    assert await worker.archive_repair("vn", "FPT", "1D") == 0
    assert [(obj["id"], obj["status"]) for obj in repo.archives()] == [
        (original["id"], "pending_repair")
    ]
    assert archive.read(original)[0].close == 100
    assert [r.close for r in repo.read("vn", "FPT", "1D")] == [100] * 3
    assert (repo.state("vn", "FPT", "1D")["status"] == "repairing") == changed
    jobs = repo.status()["jobs"]
    assert any(j["kind"] == "repair" for j in jobs) == changed
    expected = "retained prices changed" if changed else "verify completed retained overlap"
    assert any(expected in r["detail"] for r in repo.findings())


@pytest.mark.asyncio
async def test_archive_repair_filter_preserves_other_series_jobs(system):
    repo, archive, settings = system
    repo.put([candle(d) for d in (3, 4, 5)] + [candle(3, symbol="VIC")])
    for symbol, day in (("FPT", 1), ("VIC", 2)):
        archive.publish([candle(day, symbol=symbol, provider="legacy", revision="snapshot")])
        repo.mark_archive_repairs("vn", symbol, "1D")
    worker = Worker(
        repo,
        settings,
        Pages(Page([candle(1)], "vps"), Page([candle(d) for d in (3, 4, 5)], "vps")),
        archive,
    )
    assert await worker.archive_repair("vn", "FPT", "1D") == 1
    assert repo.archives("vn", "FPT", "1D")[0]["status"] == "published"
    assert repo.archives("vn", "VIC", "1D")[0]["status"] == "pending_repair"
    with repo.connect() as con:
        assert not con.execute("SELECT 1 FROM jobs WHERE symbol='VIC'").fetchone()


@pytest.mark.asyncio
async def test_small_archive_partition_does_not_request_unrelated_invalid_history(system):
    repo, archive, settings = system
    retained = [candle(d) for d in (3, 4, 5)]
    repo.put(retained)
    archive.publish([candle(1, provider="legacy", revision="snapshot")])
    repo.mark_archive_repairs("vn", "FPT", "1D")
    counts = []

    class Provider:
        async def page(self, source, symbol, iv, before=None, count=500, provider=None):
            counts.append(count)
            if before < candle(3).time:
                invalid = replace(candle(1), time=candle(1).time - 86400, close=200)
                return Providers.normalize([invalid, candle(1)], before, count, "vps")
            return Page(retained, "vps")

    worker = Worker(repo, settings, Provider(), archive)
    assert await worker.archive_repair("vn", "FPT", "1D") == 1
    assert counts == [1, 40]
    assert repo.archives("vn", "FPT", "1D")[0]["status"] == "published"


@pytest.mark.asyncio
async def test_archive_repair_retries_publication_without_redownloading_completed_history(system):
    repo, archive, settings = system
    repo.put([candle(d) for d in (3, 4, 5)])
    archive.publish([candle(1, provider="legacy", revision="snapshot")])
    repo.mark_archive_repairs("vn", "FPT", "1D")
    worker = Worker(
        repo,
        settings,
        Pages(
            Page([candle(1)], "vps"),
            DataError("temporary head outage"),
            Page([candle(d) for d in (3, 4, 5)], "vps"),
        ),
        archive,
    )
    assert await worker.archive_repair("vn", "FPT", "1D") == 0
    with repo.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM staging").fetchone()[0] == 1
        con.execute("UPDATE jobs SET retry_at=0")
    # Only recheck the current basis; no second old-history page is available.
    assert await worker.archive_repair("vn", "FPT", "1D") == 0
    assert not worker.providers.pages
    assert repo.archives("vn", "FPT", "1D")[0]["status"] == "published"
    assert [r.time for r in History(repo, archive, settings).read("vn", "FPT", "1D")] == [
        candle(d).time for d in (1, 3, 4, 5)
    ]
    assert repo.status()["jobs"] == []


@pytest.mark.asyncio
async def test_daily_archive_repair_includes_dnse_session_timestamp_and_preserves_rollover(system):
    repo, archive, settings = system
    retained = [candle(d, 100000, "dnse") for d in (3, 4, 5)]
    repo.put(retained)
    original = archive.publish([candle(d, 100000, "legacy", "snapshot") for d in (1, 2)])
    rollover = archive.publish([candle(2, 100000, "dnse")])
    repo.mark_archive_repairs("vn", "FPT", "1D")
    saved = repo.read("vn", "FPT", "1D")
    requests = []

    def respond(request):
        requests.append(request)
        end = int(request.url.params["to"])
        rows = [
            candle(day, 100000, "dnse") for day in (1, 2, 3, 4, 5) if candle(day).time + 7200 <= end
        ]
        return httpx.Response(
            200,
            json={
                "s": "ok",
                "t": [r.time + 7200 for r in rows],
                **{
                    k[0]: [getattr(r, k) / 1000 for r in rows]
                    for k in ("open", "high", "low", "close")
                },
                "v": [r.volume for r in rows],
            },
        )

    providers = Providers(settings, httpx.MockTransport(respond))
    try:
        assert (
            await Worker(repo, settings, providers, archive).archive_repair("vn", "FPT", "1D") == 2
        )
        assert int(requests[0].url.params["to"]) == candle(3).time - 1
        active = repo.archives("vn", "FPT", "1D")
        assert len(active) == 2 and all(r["status"] == "published" for r in active)
        assert rollover in active
        assert archive.read(original)[-1].provider == "legacy"
        assert repo.read("vn", "FPT", "1D") == saved
    finally:
        await providers.close()


@pytest.mark.parametrize("leased", [False, True])
@pytest.mark.asyncio
async def test_explicit_archive_restart_replaces_failed_staging_but_preserves_live_owner(
    system, leased
):
    repo, archive, settings = system
    retained = [candle(d) for d in (3, 4, 5)]
    repo.put(retained)
    saved = repo.read("vn", "FPT", "1D")
    original = archive.publish([candle(d, provider="legacy", revision="snapshot") for d in (1, 2)])
    repo.mark_archive_repairs("vn", "FPT", "1D")
    providers = Pages(
        Page([candle(1)], "vps"), Page([candle(1), candle(2)], "vps"), Page(retained, "vps")
    )
    worker = Worker(repo, settings, providers, archive)
    assert await worker.archive_repair("vn", "FPT", "1D") == 0
    with repo.connect() as con:
        job = dict(con.execute("SELECT * FROM jobs").fetchone())
        assert job["error"] and job["cursor"] == candle(1).time
        con.execute("UPDATE jobs SET retry_at=?", (int(time.time()) + 3600,))
        if leased:
            con.execute(
                "UPDATE jobs SET lease_owner='other',lease_until=?", (int(time.time()) + 120,)
            )
    count = await worker.archive_repair("vn", "FPT", "1D", restart_failed=True)
    with repo.connect() as con:
        if leased:
            assert count == 0 and len(providers.pages) == 2
            assert con.execute("SELECT COUNT(*) FROM staging").fetchone()[0] == 1
            assert con.execute("SELECT cursor FROM jobs").fetchone()[0] == job["cursor"]
            assert repo.archives()[0]["status"] == "pending_repair"
        else:
            assert count == 2 and not providers.pages
            assert con.execute("SELECT COUNT(*) FROM staging").fetchone()[0] == 0
            assert con.execute("SELECT status FROM jobs").fetchone()[0] == "complete"
            assert repo.archives()[0]["status"] == "published"
    assert archive.read(original)[-1].provider == "legacy"
    assert repo.read("vn", "FPT", "1D") == saved


@pytest.mark.asyncio
async def test_archive_restart_requires_explicit_series_filters(system):
    repo, archive, settings = system
    worker = Worker(repo, settings, Pages(), archive)
    with pytest.raises(DataError, match="Restart requires"):
        await worker.archive_repair(restart_failed=True)


@pytest.mark.asyncio
async def test_daily_migration_keeps_valid_years_when_another_year_is_corrupt(system, monkeypatch):
    repo, archive, settings = system
    repo.put([candle(d) for d in (3, 4, 5)])
    settings = replace(settings, watchlist=repo.path.parent / "watchlist.json")
    settings.watchlist.write_text('{"vn":[{"symbol":"FPT","intervals":["1D"]}]}')
    path = Path(__file__).resolve().parents[1] / "scripts/migrate_vn_daily_history.py"
    spec = importlib.util.spec_from_file_location("migration_daily", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    monkeypatch.setattr(script.Settings, "from_env", lambda: settings)
    original_client = httpx.AsyncClient
    calls = []

    def reply(request):
        calls.append(str(request.url))
        if "2019.csv" in request.url.path:
            return httpx.Response(200, text="2019-01-02,100,101,99,150,1000\n")
        return httpx.Response(200, text="2020-01-02,100,101,99,100,1000\n")

    monkeypatch.setattr(
        script.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(reply), **kwargs),
    )
    report = await script.run(
        Namespace(
            allow_direct=False,
            symbol=["FPT"],
            max_symbols=None,
            reconcile_pages=0,
            years="2019,2020",
            revision="snapshot",
            report=repo.path.parent / "migration-report.json",
        )
    )
    result = report["symbols"][0]
    assert len(calls) == 2
    assert "CSV row 1 at" in result["imports"][0]["error"]
    assert result["imports"][1]["result"]["archived"] == 1
    assert result["pending_objects"] == 1
    obj = repo.archives("vn", "FPT", "1D")[0]
    assert obj["start"] == parse_time("2020-01-02")
    assert obj["status"] == "pending_repair"
    assert [r.close for r in repo.read("vn", "FPT", "1D")] == [100] * 3


@pytest.mark.asyncio
async def test_newest_candles_remain_available_during_incomplete_bootstrap(system):
    repo, archive, settings = system
    repo.queue("vn", "FPT", "1D", "bootstrap", candle(1).time)
    worker = Worker(repo, settings, Pages(Page([candle(5)], "vps"), Page([], "vps", True)), archive)
    assert await worker.repair_page(repo.claim_job(worker.owner)) == 1
    assert repo.read("vn", "FPT", "1D")[-1].time == candle(5).time
    assert await worker.repair_page(repo.claim_job(worker.owner)) == 0
    assert repo.read("vn", "FPT", "1D")[-1].time == candle(5).time
    assert any(f["kind"] == "coverage_pending" for f in repo.findings())


def test_adjustment_cancels_bootstrap_without_reviving_stale_jobs(system):
    repo, _, _ = system
    repo.queue("vn", "FPT", "1D", "bootstrap", candle(1).time)
    bootstrap = repo.claim_job("a")
    repo.queue("vn", "FPT", "1D", "repair", candle(1).time, "dnse")
    repo.fail_job(bootstrap, "late stale worker failure")
    assert repo.claim_job("b")["kind"] == "repair"


@pytest.mark.asyncio
async def test_repeated_midway_failure_restarts_on_one_fallback_basis(system):
    repo, archive, settings = system
    repo.put([candle(d) for d in range(1, 6)])
    repo.queue("vn", "FPT", "1D", "repair", candle(1).time, "vps")
    worker = Worker(
        repo,
        settings,
        Pages(
            Page([candle(5, 90)], "vps"),
            DataError("upstream unavailable"),
            Page([candle(3, 80, "vndirect")], "vndirect"),
            Page([candle(d, 80, "vndirect") for d in range(1, 6)], "vndirect"),
        ),
        archive,
    )
    first = repo.claim_job(worker.owner)
    assert await worker.repair_page(first) == 1
    with repo.connect() as con:
        con.execute("UPDATE jobs SET attempts=2")
    assert await worker.repair_page(repo.claim_job(worker.owner)) == 0
    replacement = repo.claim_job(worker.owner)
    assert replacement["provider"] == "vndirect"
    assert replacement["cursor"] is None
    assert replacement["revision"] != first["revision"]
    assert all(r.close == 100 for r in repo.read("vn", "FPT", "1D"))
    assert await worker.repair_page(replacement) == 5
    assert {(r.provider, r.close) for r in repo.read("vn", "FPT", "1D")} == {("vndirect", 80)}


def test_filtered_workers_only_claim_requested_tickers(system):
    repo, _, _ = system
    repo.queue("vn", "FPT", "1D", "bootstrap", candle(1).time)
    repo.queue("vn", "VCB", "1D", "bootstrap", candle(1).time)
    assert repo.claim_job("worker", allowed=[("vn", "VCB", "1D")])["symbol"] == "VCB"


@pytest.mark.asyncio
async def test_initial_no_data_tries_other_configured_sources(system):
    _, _, settings = system

    def response(request):
        if "vps.com.vn" in request.url.host:
            return httpx.Response(200, json={"s": "no_data"})
        return httpx.Response(
            200,
            json={
                "t": [candle(1).time],
                "o": [100],
                "h": [101],
                "l": [99],
                "c": [100],
                "v": [1000],
            },
        )

    providers = Providers(settings, httpx.MockTransport(response))
    try:
        page = await providers.page("vn", "FPT", "1D", before=candle(2).time)
        assert page.provider == "vndirect"
        assert len(page.rows) == 1
    finally:
        await providers.close()


@pytest.mark.asyncio
async def test_provider_timeout_keeps_data_and_other_jobs_progressing(system):
    repo, archive, settings = system
    repo.put([candle(5)])
    repo.queue("vn", "FPT", "1D", "repair", candle(1).time, "vps")
    repo.queue("vn", "VCB", "1D", "bootstrap", candle(1).time)

    class Slow:
        async def page(self, *args, **kwargs):
            await asyncio.sleep(1)

    worker = Worker(repo, settings, Slow(), archive)
    worker.deadline = 0.01
    assert await worker.repair_page(repo.claim_job(worker.owner)) == 0
    assert repo.read("vn", "FPT", "1D")[-1].close == 100
    assert repo.claim_job(worker.owner)["symbol"] == "VCB"
    assert any("time budget" in f["detail"] for f in repo.findings())


@pytest.mark.asyncio
async def test_ancient_page_cannot_complete_an_empty_recent_window(system):
    repo, archive, settings = system
    repo.queue("vn", "FPT", "1D", "bootstrap", candle(5).time)
    worker = Worker(repo, settings, Pages(Page([candle(1)], "vps")), archive)
    assert await worker.repair_page(repo.claim_job(worker.owner)) == 0
    assert repo.state("vn", "FPT", "1D") is None
    assert any("precedes configured retained" in f["detail"] for f in repo.findings())
