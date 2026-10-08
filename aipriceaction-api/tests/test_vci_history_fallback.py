import gzip
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from aipriceaction_api.adoption import adopt_snapshot
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, parse_time
from aipriceaction_api.providers import Page, Providers, vn_provider_order
from aipriceaction_api.storage import Repository
from aipriceaction_api.workers import Worker
from scripts.stage_vci_minute_history import original_changes

STAMP = parse_time("2025-10-03T02:15:00Z")


def payload():
    return [
        {
            "symbol": "FPT",
            "t": [str(STAMP)],
            "o": [85466.24],
            "h": [85554.99],
            "l": [85377.49],
            "c": [85466.24],
            "v": [19100],
        }
    ]


def settings(enabled=True):
    return replace(Settings(), vci_history_fallback=enabled, requests_per_minute=100000)


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", ("FPT", "TPB"))
async def test_captured_vci_volume_contradictions_are_quarantined(symbol):
    fixture = json.loads(
        (
            Path(__file__).parent / f"fixtures/vci_{symbol.lower()}_volume_contradiction.json"
        ).read_text()
    )
    body = fixture["payload"][0]
    last = max(int(t) for t in body["t"])
    providers = Providers(
        settings(), httpx.MockTransport(lambda request: httpx.Response(200, json=[body]))
    )
    try:
        with pytest.raises(DataError, match="volume contradicts cumulative total"):
            await providers.page("vn", symbol, "1m", last + 60, provider="vci")
        # The capture is preserved. Only a separately corroborated correction
        # can make this volume internally consistent; prices stay untouched.
        index = body["t"].index(str(last))
        previous = body["t"].index(str(last - 60))
        body["v"][index] = int(body["accumulatedVolume"][index]) - int(
            body["accumulatedVolume"][previous]
        )
        page = await providers.page("vn", symbol, "1m", last + 60, provider="vci")
        assert len(page.rows) == 2
        assert next(row for row in page.rows if row.time == last).volume == body["v"][index]
    finally:
        await providers.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ("gap", "day_reset", "filtered", "length", "fractional"))
async def test_vci_cumulative_check_respects_gaps_sessions_and_retention(case):
    body = payload()[0]
    first = parse_time("2025-10-02T16:59:00Z") if case == "day_reset" else STAMP
    last = first + (120 if case == "gap" else 60)
    for key in ("o", "h", "l", "c", "v"):
        body[key] *= 2
    body["t"] = [str(first), str(last)]
    body["accumulatedVolume"] = [50000, 19100]
    if case == "length":
        body["accumulatedVolume"] = [50000]
    elif case == "fractional":
        body["accumulatedVolume"][1] = 19100.5
    providers = Providers(
        settings(), httpx.MockTransport(lambda request: httpx.Response(200, json=[body]))
    )
    try:
        if case in ("length", "fractional"):
            with pytest.raises(DataError):
                await providers.page("vn", "FPT", "1m", last + 60, provider="vci")
        else:
            page = await providers.page(
                "vn",
                "FPT",
                "1m",
                last + 60,
                provider="vci",
                start=last + 60 if case == "filtered" else None,
            )
            assert len(page.rows) == (0 if case == "filtered" else 2)
    finally:
        await providers.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapped", (False, True))
async def test_vci_protocol_preserves_vnd_units_and_minute_labels(wrapped):
    seen = []

    def respond(request):
        seen.append(request)
        body = payload()
        return httpx.Response(200, json={"data": body} if wrapped else body)

    providers = Providers(settings(), httpx.MockTransport(respond))
    try:
        page = await providers.page("vn", "FPT", "1m", STAMP + 60, count=2000, provider="vci")
    finally:
        await providers.close()
    assert page.provider == "vci" and page.cursor == STAMP
    assert page.rows[0].open == 85466.24 and page.rows[0].volume == 19100
    assert page.rows[0].time == STAMP
    assert seen[0].method == "POST"
    assert json.loads(seen[0].content) == {
        "timeFrame": "ONE_MINUTE",
        "symbols": ["FPT"],
        "to": STAMP + 59,
        "countBack": 2000,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,iv", [(False, "1m"), (True, "1D"), (True, "1h")])
async def test_vci_requires_opt_in_and_never_supplies_daily_or_hourly(enabled, iv):
    def unexpected(request):
        pytest.fail("Rejected VCI request reached the network")

    providers = Providers(settings(enabled), httpx.MockTransport(unexpected))
    try:
        with pytest.raises(DataError, match="explicit historical-minute"):
            await providers.page("vn", "FPT", iv, STAMP + 60, provider="vci")
    finally:
        await providers.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect", ("symbol", "length", "range", "seconds", "fractional_volume", "conflict")
)
async def test_vci_rejects_ambiguous_or_invalid_requested_candles(defect):
    body = payload()[0]
    if defect == "symbol":
        body["symbol"] = "VCB"
    elif defect == "length":
        body["v"] = []
    elif defect == "range":
        body["h"] = [1]
    elif defect == "seconds":
        body["t"] = [str(STAMP + 1)]
    elif defect == "fractional_volume":
        body["v"] = [1.5]
    else:
        for field in ("t", "o", "h", "l", "c", "v"):
            body[field].append(body[field][0])
        body["v"][1] += 1
    providers = Providers(
        settings(), httpx.MockTransport(lambda request: httpx.Response(200, json=[body]))
    )
    try:
        with pytest.raises(DataError):
            await providers.page("vn", "FPT", "1m", STAMP + 60, provider="vci")
    finally:
        await providers.close()


@pytest.mark.asyncio
async def test_vci_is_last_resort_and_only_for_older_minutes():
    seen = []

    def respond(request):
        seen.append(request.url.host)
        return httpx.Response(
            200, json=payload() if "vietcap" in request.url.host else {"s": "no_data"}
        )

    providers = Providers(settings(), httpx.MockTransport(respond))
    try:
        result = await providers.page("vn", "FPT", "1m", STAMP + 60)
    finally:
        await providers.close()
    assert result.provider == "vci"
    assert seen == [
        "histdatafeed.vps.com.vn",
        "api.dnse.com.vn",
        "dchart-api.vndirect.com.vn",
        "trading.vietcap.com.vn",
    ]
    assert "vci" not in vn_provider_order(settings(), "1D", STAMP)
    assert "vci" not in vn_provider_order(settings(), "1h", STAMP)
    assert "vci" not in vn_provider_order(settings(False), "1m", STAMP)
    assert "vci" not in vn_provider_order(settings(), "1m", 10**12)


@pytest.mark.parametrize("interval", ["1m", "1h", "1D"])
def test_indexes_use_dnse_consistently_across_native_intervals(interval):
    assert vn_provider_order(settings(), interval, STAMP, "VNINDEX")[0] == "dnse"


def test_stock_minute_keeps_vps_primary_and_dnse_before_vndirect():
    assert vn_provider_order(settings(), "1m", STAMP, "FPT")[:3] == [
        "vps",
        "dnse",
        "vndirect",
    ]


@pytest.mark.asyncio
async def test_vci_does_not_displace_a_working_preferred_source():
    seen = []

    def respond(request):
        seen.append(request.url.host)
        return httpx.Response(
            200, json={"t": [STAMP], "o": [85], "h": [86], "l": [84], "c": [85], "v": [100]}
        )

    providers = Providers(settings(), httpx.MockTransport(respond))
    try:
        result = await providers.page("vn", "FPT", "1m", STAMP + 60)
    finally:
        await providers.close()
    assert result.provider == "vps"
    assert seen == ["histdatafeed.vps.com.vn"]


@pytest.mark.asyncio
async def test_vci_page_cursor_survives_requested_floor_filter():
    providers = Providers(
        settings(), httpx.MockTransport(lambda request: httpx.Response(200, json=payload()))
    )
    try:
        page = await providers.page("vn", "FPT", "1m", STAMP + 60, provider="vci", start=STAMP + 30)
    finally:
        await providers.close()
    assert page.rows == [] and page.cursor == STAMP


@pytest.mark.asyncio
async def test_empty_vci_response_does_not_prove_historical_coverage():
    providers = Providers(
        settings(), httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    )
    try:
        page = await providers.page("vn", "FPT", "1m", STAMP + 60, provider="vci")
    finally:
        await providers.close()
    assert page.no_data and page.rows == [] and page.cursor is None


@pytest.mark.asyncio
async def test_captured_vci_overlap_can_license_an_unchanged_snapshot(tmp_path, monkeypatch):
    raw = gzip.decompress((Path(__file__).parent / "fixtures/vci_fpt_recent.json.gz").read_bytes())
    providers = Providers(
        settings(), httpx.MockTransport(lambda request: httpx.Response(200, content=raw))
    )
    repo = Repository(tmp_path / "db")
    repo.initialize()
    monkeypatch.setattr(
        "aipriceaction_api.adoption.completed_vn_sessions", lambda: parse_time("2026-10-03")
    )
    try:
        page = await providers.page(
            "vn", "FPT", "1m", parse_time("2026-10-03"), count=2000, provider="vci"
        )
        original = [replace(row, provider="legacy-api", revision="captured") for row in page.rows]
        repo.put(original)
        original = repo.read("vn", "FPT", "1m")
        proof = await adopt_snapshot(repo, providers, "FPT", "vci")
        assert proof["dry_run"] and proof["evidence"]["matched_rows"] == 2000
        assert proof["evidence"]["completed_sessions"] >= 5
        assert repo.read("vn", "FPT", "1m") == original
        await adopt_snapshot(repo, providers, "FPT", "vci", execute=True)
        assert repo.state("vn", "FPT", "1m")["provider"] == "vci"
        assert repo.read("vn", "FPT", "1m") == original
        assert len(repo.adoptions()) == 1
    finally:
        await providers.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", (False, True))
async def test_unverified_vci_fallback_stages_without_publishing(tmp_path, existing):
    repo = Repository(tmp_path / "db")
    repo.initialize()
    row = Candle("vn", "FPT", "1m", STAMP, 85000, 86000, 84000, 85000, 100, "vps")
    if existing:
        repo.put([row])
    before = repo.read("vn", "FPT", "1m")
    repo.queue("vn", "FPT", "1m", "repair" if existing else "bootstrap", STAMP, "vci")

    class Source:
        async def page(self, *args, **kwargs):
            return Page([replace(row, provider="vci", close=85500)], "vci", cursor=STAMP)

    worker = Worker(repo, settings(), providers=Source())
    assert await worker.repair_page(repo.claim_job(worker.owner)) == 1
    assert repo.read("vn", "FPT", "1m") == before
    assert any(r["kind"] == "vci_verification_pending" for r in repo.findings())
    with repo.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM staging").fetchone()[0] == 1


@pytest.mark.parametrize("change", ("version", "price", "addition"))
def test_candidate_source_snapshot_tracks_values_and_timestamp_growth(change):
    row = Candle("vn", "FPT", "1m", STAMP, 85000, 86000, 84000, 85000, 100, "vps")
    current = (
        [replace(row, updated_at=1)]
        if change == "version"
        else [replace(row, close=85500)]
        if change == "price"
        else [row, replace(row, time=STAMP + 60)]
    )
    result = original_changes([row], current)
    assert result["record_versions_changed"] == int(change != "addition")
    assert result["ohlcv_changed"] == int(change == "price")
    assert result["new_timestamps"] == ([STAMP + 60] if change == "addition" else [])
    assert not result["missing_timestamps"]
