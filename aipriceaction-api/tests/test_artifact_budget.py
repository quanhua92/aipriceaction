import asyncio
import json
from types import SimpleNamespace

import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle
from aipriceaction_api.providers import Page
from scripts import compare_vn_feeds, compare_vn_minute_universe
from scripts.artifact_budget import ArtifactBudget, ArtifactBudgetExceeded


def test_budget_counts_existing_and_peak_replacement_bytes(tmp_path):
    path = tmp_path / "capture.json"
    path.write_bytes(b"1234")
    budget = ArtifactBudget(tmp_path, 8)
    budget.write(path, b"1234")
    assert budget.used == 4
    with pytest.raises(ArtifactBudgetExceeded):
        budget.write(path, b"12345")
    assert path.read_bytes() == b"1234"
    budget.write(path, b"12")
    assert budget.used == 2
    assert list(tmp_path.iterdir()) == [path]
    with pytest.raises(ValueError, match="outside"):
        budget.write(tmp_path.parent / "escape", b"123")


def test_resume_cannot_ignore_existing_oversized_artifacts(tmp_path):
    (tmp_path / "leftover.tmp").write_bytes(b"12345")
    with pytest.raises(ArtifactBudgetExceeded, match="Existing"):
        ArtifactBudget(tmp_path, 4)


def test_universe_batches_share_budget_and_resume_contract(tmp_path, monkeypatch):
    proofs = tmp_path / "proofs.json"
    proofs.write_text("[]")
    observed = []

    async def compare(args):
        observed.append(args)
        args.output.mkdir(exist_ok=args.resume)
        (args.output / "request.json").write_text("{}")
        return {
            "counts": {"four_feed_agreement": 1},
            "errors": [],
            "provider_window_stops": [],
            "symbols": args.symbol,
        }

    monkeypatch.setattr(compare_vn_minute_universe, "compare", compare)
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: Settings()))
    args = SimpleNamespace(
        output=tmp_path / "audit",
        symbol=["FPT", "MWG", "VCB"],
        batch_size=2,
        artifact_budget_mib=16,
        resume=False,
        start_date="2025-10-04",
        end_date="2026-10-02",
        max_pages=20,
        vci_volume_proofs=proofs,
    )
    result = asyncio.run(compare_vn_minute_universe.run(args))
    assert result["completed"]
    assert [call.symbol for call in observed] == [["FPT", "MWG"], ["VCB"]]
    assert observed[0].artifact_budget is observed[1].artifact_budget
    args.resume = True
    asyncio.run(compare_vn_minute_universe.run(args))
    assert all(call.resume for call in observed[2:])
    proofs.write_text("[{}]")
    with pytest.raises(ValueError, match="proof identity"):
        asyncio.run(compare_vn_minute_universe.run(args))
    assert json.loads((args.output / "summary.json").read_text())["completed"]


def test_comparison_exhaustion_closes_collectors_and_preserves_checkpoints(tmp_path, monkeypatch):
    closed = []

    class Providers:
        def __init__(self, settings, transport):
            self.transport = transport

        async def page(self, source, symbol, interval, before, count, start, provider):
            return Page([Candle(source, symbol, interval, start, 10, 11, 9, 10, 100)], provider)

        async def close(self):
            closed.append(self)
            await self.transport.aclose()

    monkeypatch.setattr(compare_vn_feeds, "Providers", Providers)
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: Settings()))
    root = tmp_path / "audit"
    args = SimpleNamespace(
        output=root,
        symbol=["FPT"],
        interval=["1m"],
        daily_start="2020-01-02",
        intraday_start="2020-01-02",
        end_date="2020-01-02",
        native_providers=True,
        artifact_budget=ArtifactBudget(root, 1100),
    )
    with pytest.raises(ArtifactBudgetExceeded):
        asyncio.run(compare_vn_feeds.run(args))
    assert closed
    assert args.artifact_budget.used <= 1100
    assert sum(path.stat().st_size for path in root.rglob("*") if path.is_file()) <= 1100
    assert (root / "request.json").exists()
    assert list(root.glob("*/FPT-1m.json"))
    assert not list(root.rglob("*.tmp"))


def test_universe_budget_stop_is_explicit_and_incomplete(tmp_path, monkeypatch):
    proofs = tmp_path / "proofs.json"
    proofs.write_text("[]")

    async def compare(args):
        raise ArtifactBudgetExceeded("budget")

    monkeypatch.setattr(compare_vn_minute_universe, "compare", compare)
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: Settings()))
    args = SimpleNamespace(
        output=tmp_path / "audit",
        symbol=["FPT"],
        batch_size=1,
        artifact_budget_mib=16,
        resume=False,
        start_date="2025-10-04",
        end_date="2026-10-02",
        max_pages=20,
        vci_volume_proofs=proofs,
    )
    report = asyncio.run(compare_vn_minute_universe.run(args))
    assert not report["completed"]
    assert report["stop"] == "artifact_budget"
    assert json.loads((args.output / "summary.json").read_text()) == report
