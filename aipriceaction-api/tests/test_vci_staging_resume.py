import json
from argparse import Namespace
from dataclasses import replace

import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, date_bounds
from aipriceaction_api.providers import Page
from aipriceaction_api.storage import Repository
from scripts import stage_vci_minute_history as staging


@pytest.fixture
def setup(tmp_path, monkeypatch):
    watchlist = tmp_path / "watchlist.json"
    watchlist.write_text(json.dumps({"vn": ["FPT"]}))
    settings = replace(
        Settings(),
        database=tmp_path / "main.sqlite3",
        watchlist=watchlist,
        cache_dir=tmp_path / "cache",
    )
    monkeypatch.setattr(staging.Settings, "from_env", lambda: settings)
    main = Repository(settings.database)
    main.initialize()
    days = [date_bounds("2025-10-06"), date_bounds("2025-10-07")]
    rows = [Candle("vn", "FPT", "1m", day + 8100, 100, 110, 90, 105, 100, "vci") for day in days]
    main.put(rows)
    main.put([replace(row, interval="1D", time=day) for day, row in zip(days, rows, strict=True)])
    args = Namespace(
        symbol="FPT",
        start_date="2025-10-06",
        end_date="2025-10-07",
        output=tmp_path / "candidate",
        allow_direct=True,
        volume_proofs=None,
        resume=False,
    )
    return main, args, rows, days


@pytest.mark.asyncio
async def test_resume_preserves_verified_prefix_and_retries_failed_cursor(setup, monkeypatch):
    main, args, rows, days = setup
    calls = []
    responses = iter(
        [Page([rows[1]], "vci", cursor=rows[1].time), DataError("Captured source contradiction")]
    )

    class Provider:
        def __init__(self, *a, **kw):
            pass

        async def page(self, source, symbol, interval, before, **kwargs):
            calls.append(before)
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            return response

        async def close(self):
            pass

    monkeypatch.setattr(staging, "Providers", Provider)
    originals = main.read("vn", "FPT", "1m")
    with pytest.raises(DataError, match="contradiction"):
        await staging.run(args)
    candidate = Repository(args.output / "candidate.sqlite3")
    prefix = candidate.read("vn", "FPT", "1m")
    assert len(prefix) == 1
    responses = iter([Page([rows[0]], "vci", cursor=days[0] - 60)])
    args.resume = True
    await staging.run(args)
    report = json.loads((args.output / "report.json").read_text())
    assert report["complete"] and report["candidate_rows"] == 2
    assert calls[-1] == calls[-2] == rows[1].time
    assert candidate.read("vn", "FPT", "1m")[-1] == prefix[0]
    assert report["previous_errors"] == ["Captured source contradiction"]
    assert main.read("vn", "FPT", "1m") == originals


@pytest.mark.asyncio
async def test_resume_rejects_unrecorded_staged_rows(setup, monkeypatch):
    _, args, rows, _ = setup
    args.output.mkdir()
    candidate = Repository(args.output / "candidate.sqlite3")
    candidate.initialize()
    candidate.put([replace(rows[0], revision="vci-candidate")])
    (args.output / "report.json").write_text(
        json.dumps(
            {
                "complete": False,
                "main_publication": False,
                "source": "vn",
                "symbol": "FPT",
                "provider": "vci",
                "start_date": args.start_date,
                "end_date": args.end_date,
                "pages": [],
            }
        )
    )
    args.resume = True
    before = candidate.read("vn", "FPT", "1m")
    with pytest.raises(DataError, match="pagination checkpoint"):
        await staging.run(args)
    assert candidate.read("vn", "FPT", "1m") == before
