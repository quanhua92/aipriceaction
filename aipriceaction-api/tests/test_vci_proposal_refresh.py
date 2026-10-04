import asyncio
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError
from aipriceaction_api.providers import Page
from aipriceaction_api.vci_volume import validate_volume_proof
from scripts import refresh_vci_volume_proposals
from scripts.refresh_vci_volume_proposals import refresh


def fixture():
    proof = json.loads(
        gzip.decompress(
            (Path(__file__).parent / "fixtures/vci_fpt_volume_proof.json.gz").read_bytes()
        )
    )
    fields = {
        "t": "time",
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close",
        "v": "volume",
        "accumulatedVolume": "cumulative_volume",
    }
    body = {
        "symbol": proof["symbol"],
        **{key: [row[field] for row in proof["source_rows"]] for key, field in fields.items()},
    }
    return proof, json.dumps([body]).encode()


@pytest.mark.parametrize("defect", [None, "missing", "wrong_symbol", "wrong_volume"])
def test_refresh_requires_two_exact_fresh_daily_witnesses_and_reuses_day_cache(defect):
    saved, raw = fixture()
    witnesses = {row["provider"]: row for row in saved["daily_witnesses"]}
    calls = []

    class Providers:
        async def page(self, source, symbol, interval, before, count, start, provider):
            assert (source, symbol, interval, before, count, start) == (
                "vn",
                saved["symbol"],
                "1D",
                saved["day"] + 86400,
                100,
                saved["day"],
            )
            calls.append(provider)
            row = dict(witnesses[provider])
            if provider == "dnse":
                if defect == "missing":
                    return Page([], provider, True)
                if defect == "wrong_symbol":
                    row["symbol"] = "MWG"
                if defect == "wrong_volume":
                    row["volume"] += 1
            return Page([Candle(**row)], provider)

    cache = {}
    target = validate_volume_proof(saved)
    if defect:
        with pytest.raises(DataError):
            asyncio.run(refresh(Providers(), raw, saved["symbol"], target.time, cache))
    else:
        result = asyncio.run(refresh(Providers(), raw, saved["symbol"], target.time, cache))
        assert validate_volume_proof(result).volume == target.volume
        assert result["verified_at_ns"] >= saved["verified_at_ns"]
        asyncio.run(refresh(Providers(), raw, saved["symbol"], target.time, cache))
        assert calls == ["vndirect", "dnse"]


@pytest.mark.parametrize("tamper", [False, True])
def test_exact_date_refresh_can_recover_missing_witness_but_rejects_changed_capture(
    tmp_path, monkeypatch, tamper
):
    saved, raw = fixture()
    capture = tmp_path / f"native-{hashlib.sha256(raw).hexdigest()}.json"
    capture.write_bytes(raw if not tamper else b"[]")
    inputs = tmp_path / "proposals"
    inputs.mkdir()
    (inputs / "proofs.json").write_text("[]")
    (inputs / "report.json").write_text(
        json.dumps(
            {
                "proposals": [],
                "blocked": [
                    {
                        "symbol": saved["symbol"],
                        "time": validate_volume_proof(saved).time,
                        "capture": str(capture),
                        "error": "Missing daily witness",
                    }
                ],
            }
        )
    )
    existing = tmp_path / "existing.json"
    existing.write_text("[]")
    closed = []

    class Providers:
        def __init__(self, settings, transport):
            self.transport = transport

        async def page(self, source, symbol, interval, before, count, start, provider):
            row = next(row for row in saved["daily_witnesses"] if row["provider"] == provider)
            return Page([Candle(**row)], provider)

        async def close(self):
            closed.append(True)
            await self.transport.aclose()

    monkeypatch.setattr(refresh_vci_volume_proposals, "Providers", Providers)
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: Settings()))
    args = SimpleNamespace(
        proposals=inputs,
        existing=existing,
        output=tmp_path / "refreshed",
        start_date="2020-01-01",
        end_date="2026-10-02",
        include_blocked=True,
    )
    if tamper:
        with pytest.raises(DataError, match="capture changed"):
            asyncio.run(refresh_vci_volume_proposals.run(args))
    else:
        report = asyncio.run(refresh_vci_volume_proposals.run(args))
        assert len(report["verified"]) == 1
        assert report["blocked"] == []
        assert not report["active_catalog_changed"]
        assert len(json.loads((args.output / "proofs.json").read_text())) == 1
    assert closed == [True]
