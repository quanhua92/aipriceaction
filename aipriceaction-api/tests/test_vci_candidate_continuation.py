import asyncio
import json
from types import SimpleNamespace

import pytest

from aipriceaction_api.domain import Candle, DataError, date_bounds
from aipriceaction_api.providers import Page
from scripts.artifact_budget import ArtifactBudgetExceeded
from scripts.continue_vci_candidate_pages import continue_record


def inputs():
    first = date_bounds("2025-10-04")
    seam = Page(
        [Candle("vn", "FPT", "1m", first + 600, 10, 10, 10, 10, 100)], "vci", cursor=first + 600
    )
    record = {"start_date": "2025-10-04"}
    replay = {
        "next_cursor": seam.cursor,
        "pages": [{"before": first + 1200, "rows": 1}],
        "last_page": seam,
    }
    return first, seam, record, replay


class Feeds:
    def __init__(self, pages):
        self.pages, self.calls = iter(pages), []

    async def page(self, source, symbol, interval, before, **kwargs):
        self.calls.append(before)
        page = next(self.pages)
        if isinstance(page, Exception):
            raise page
        return page


def test_changed_adjustment_or_volume_at_seam_prevents_older_requests():
    first, seam, record, replay = inputs()
    seam.rows = list(seam.rows)
    altered = Page(
        [Candle("vn", "FPT", "1m", first + 600, 11, 11, 11, 11, 100)], "vci", cursor=first + 600
    )
    feeds = Feeds([altered])
    result = asyncio.run(continue_record(feeds, "FPT", record, replay, 20))
    assert not result["seam_verified"]
    assert result["stop"] == "provider_error"
    assert len(feeds.calls) == 1


def test_requested_boundary_is_not_market_accuracy_certification():
    first, seam, record, replay = inputs()
    older = Page([Candle("vn", "FPT", "1m", first, 10, 10, 10, 10, 100)], "vci", cursor=first)
    feeds = Feeds([seam, older])
    result = asyncio.run(continue_record(feeds, "FPT", record, replay, 20))
    assert result["boundary_reached"] and result["seam_verified"]
    assert not result["requested_year_proven"]
    assert result["new_rows_by_date"] == {"2025-10-04": 1}
    assert result["captured_rows"] == 1
    assert result["new_rows"] == 1


@pytest.mark.parametrize("failure", ["empty", "contradiction", "cursor", "out_of_range"])
def test_partial_windows_remain_explicitly_incomplete(failure):
    first, seam, record, replay = inputs()
    page = {
        "empty": Page([], "vci"),
        "contradiction": DataError("VCI minute volume contradicts cumulative volume"),
        "cursor": Page([], "vci", cursor=first + 600),
        "out_of_range": Page(
            [Candle("vn", "FPT", "1m", first + 600, 10, 10, 10, 10, 100)], "vci", cursor=first
        ),
    }[failure]
    result = asyncio.run(continue_record(Feeds([seam, page]), "FPT", record, replay, 20))
    assert not result["boundary_reached"]
    assert result["stop"] == (
        "provider_empty_before_boundary" if failure == "empty" else "provider_error"
    )


def test_page_limit_preserves_partial_progress():
    first, seam, record, replay = inputs()
    page = Page(
        [Candle("vn", "FPT", "1m", first + 300, 10, 10, 10, 10, 100)], "vci", cursor=first + 300
    )
    result = asyncio.run(continue_record(Feeds([seam, page]), "FPT", record, replay, 1))
    assert result["stop"] == "page_budget"
    assert result["next_cursor"] == first + 300
    assert result["new_rows"] == 1


def test_artifact_exhaustion_closes_provider_and_keeps_failure_checkpoint(tmp_path, monkeypatch):
    from scripts import continue_vci_candidate_pages as module

    first, seam, record, replay = inputs()
    audit = tmp_path / "audit"
    (audit / "vci").mkdir(parents=True)
    (audit / "summary.json").write_text(
        json.dumps({"completed": True, "batches": [{"path": str(audit), "symbols": ["FPT"]}]})
    )
    (audit / "vci/FPT-1m.json").write_text(json.dumps(record | {"error": "old"}))
    proofs = tmp_path / "proofs.json"
    proofs.write_text("[]")
    capture = {"path": "retained-native.json", "sha256": "frozen"}

    class Provider:
        closed = False

        def __init__(self, settings, transport):
            self.transport = transport

        async def page(self, *args, **kwargs):
            self.transport.captures.append(capture)
            raise ArtifactBudgetExceeded("test cap")

        async def close(self):
            Provider.closed = True
            await self.transport.aclose()

    async def offline(*args, **kwargs):
        return replay | {"captured_pages_passed": True}

    monkeypatch.setattr(module, "Providers", Provider)
    monkeypatch.setattr(module, "replay_record", offline)
    args = SimpleNamespace(
        audit=audit, proofs=proofs, output=tmp_path / "output", max_pages=20, artifact_budget_mib=16
    )
    result = asyncio.run(module.run(args))
    assert Provider.closed
    assert not result["completed"]
    assert result["stop"] == "artifact_budget"
    assert result["interrupted_series"] == {"symbol": "FPT", "captures": [capture]}
    assert json.loads((args.output / "report.json").read_text()) == result
    assert not list(args.output.rglob("*.sqlite*"))
