import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError
from aipriceaction_api.providers import Page
from scripts import compare_vn_feeds
from scripts.compare_vn_feeds import paged_window
from scripts.validate_ohlcv import exceptions


def page(stamp, cursor):
    return Page([Candle("vn", "FPT", "1m", stamp, 10, 11, 9, 10, 100)], "vps", cursor=cursor)


class Provider:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.cursors = []

    async def page(self, source, symbol, interval, before, **kwargs):
        self.cursors.append(before)
        result = next(self.pages)
        if isinstance(result, Exception):
            raise result
        return result


def collect(provider, max_pages=10):
    return asyncio.run(paged_window(provider, "FPT", "1m", 60, 300, "vps", 1000, max_pages))


def test_short_pages_continue_until_requested_boundary():
    provider = Provider([page(240, 240), page(120, 120), page(60, 0)])
    rows, window = collect(provider)
    assert sorted(r["time"] for r in rows) == [60, 120, 240]
    assert provider.cursors == [300, 240, 120]
    assert window["reached_requested_start"]
    assert window["stop"] == "requested_boundary"


@pytest.mark.parametrize(
    "terminal,stop",
    [
        (Page([], "vps", True), "provider_empty_before_boundary"),
        (DataError("Unverified historical volume"), "provider_error"),
    ],
)
def test_incomplete_windows_keep_valid_rows_without_certifying_coverage(terminal, stop):
    rows, window = collect(Provider([page(240, 240), terminal]))
    assert [r["time"] for r in rows] == [240]
    assert window["stop"] == stop
    assert not window["reached_requested_start"]


def test_page_budget_is_explicit():
    rows, window = collect(Provider([page(240, 240)]), max_pages=1)
    assert len(rows) == 1
    assert window["stop"] == "page_budget"
    assert not window["reached_requested_start"]


def test_nonmoving_cursor_is_rejected():
    with pytest.raises(ValueError, match="did not move backwards"):
        collect(Provider([page(240, 300)]))


def test_resume_preserves_completed_failures_and_rejects_changed_proof(tmp_path, monkeypatch):
    watchlist = tmp_path / "watchlist.json"
    watchlist.write_text(json.dumps({"vn": ["FPT"]}))
    proofs = tmp_path / "proofs.json"
    proofs.write_text("[]")
    settings = replace(Settings(), watchlist=watchlist, vci_volume_proofs=proofs)
    calls = []

    class FailedProviders:
        def __init__(self, config, transport):
            self.transport = transport

        async def page(self, *args, **kwargs):
            calls.append(kwargs["provider"])
            raise DataError("Unverified historical volume")

        async def close(self):
            await self.transport.aclose()

    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(compare_vn_feeds, "Providers", FailedProviders)
    args = SimpleNamespace(
        output=tmp_path / "audit",
        symbol=None,
        interval=["1m"],
        daily_start="2020-01-02",
        intraday_start="2020-01-02",
        end_date="2020-01-02",
        native_providers=True,
        paginate=True,
    )
    first = asyncio.run(compare_vn_feeds.run(args))
    assert len(calls) == len(first["errors"]) == 4
    args.resume = True
    resumed = asyncio.run(compare_vn_feeds.run(args))
    assert len(calls) == 4
    assert resumed["errors"] == first["errors"]
    assert not list(args.output.rglob("*.tmp"))
    proofs.write_text("[{}]")
    with pytest.raises(ValueError, match="proof identity"):
        asyncio.run(compare_vn_feeds.run(args))
    assert len(calls) == 4


def test_equal_partial_windows_still_report_incomplete_history():
    report = {
        "errors": [],
        "comparisons": [],
        "provider_window_stops": [
            {
                "symbol": "FPT",
                "interval": "1m",
                "feed": "vps",
                "stop": "page_budget",
                "reached_requested_start": False,
                "pages": [],
            },
            {
                "symbol": "FPT",
                "interval": "1m",
                "feed": "vci",
                "stop": "requested_boundary",
                "reached_requested_start": True,
                "pages": [],
            },
        ],
    }
    issues = exceptions(report, [])
    assert len(issues) == 1
    assert issues[0]["kind"] == "provider_window_incomplete"
    assert issues[0]["feed"] == "vps"
