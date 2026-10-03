import json
from dataclasses import replace

import httpx
import pytest

from aipriceaction_api.archive import Archive
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, parse_time
from aipriceaction_api.history import History
from aipriceaction_api.importing import json_rows
from aipriceaction_api.migration import LegacyImporter
from aipriceaction_api.storage import Repository


def quote():
    # Exact fields from the captured public GC=F hourly response.
    return Candle(
        "yahoo",
        "GC=F",
        "1h",
        parse_time("2026-04-02T20:59:59"),
        4702.7001953125,
        4702.7001953125,
        4702.7001953125,
        4702.7001953125,
        0,
        "legacy-api",
        "quote-snapshot",
    )


def test_legacy_hourly_futures_quote_preserves_original_seconds_and_prices():
    row = quote()
    assert row.validate() is row
    parsed = json_rows(
        json.dumps(
            {
                "GC=F": [
                    dict(
                        time="2026-04-02T20:59:59",
                        **{k: getattr(row, k) for k in ("open", "high", "low", "close", "volume")},
                    )
                ]
            }
        ),
        "yahoo",
        "GC=F",
        "1h",
        "legacy-api",
        "quote-snapshot",
    )
    assert parsed == [row]


@pytest.mark.parametrize(
    "changes",
    [
        {"provider": "yahoo"},
        {"provider": "legacy-s3"},
        {"provider": "import"},
        {"interval": "1m"},
        {"interval": "1D"},
        {"symbol": "AAPL"},
        {"source": "crypto"},
        {"source": "vn"},
        {"volume": 1},
        {"high": 4703},
        {"close": float("nan")},
    ],
)
def test_quote_exception_does_not_accept_other_unaligned_candles(changes):
    with pytest.raises(DataError):
        replace(quote(), **changes).validate()


@pytest.mark.asyncio
async def test_quote_import_records_evidence_and_survives_archive_history_restore(tmp_path):
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
    row = quote()
    original = json.dumps(
        {
            "GC=F": [
                {
                    "time": "2026-04-02T20:59:59",
                    **{
                        key: getattr(row, key) for key in ("open", "high", "low", "close", "volume")
                    },
                }
            ]
        }
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=original))
    ) as client:
        report = await LegacyImporter(repo, archive, client=client).run(
            "https://api.example.test",
            "yahoo",
            "GC=F",
            "1h",
            years=[2026],
            provider="legacy-api",
            revision=row.revision,
            from_api=True,
            api_format="json",
        )
    assert report["periods"][0]["legacy_quote_events"] == {
        "rows": 1,
        "start": row.time,
        "end": row.time,
    }
    assert any(item["kind"] == "legacy_quote_events" for item in repo.findings())
    stored = repo.read("yahoo", "GC=F", "1h")
    assert len(stored) == 1 and replace(stored[0], updated_at=0) == row
    obj = archive.publish(stored, prune=True)
    assert archive.read(obj, refresh=True) == stored
    fresh = Repository(tmp_path / "restored")
    fresh.initialize()
    restored = Archive(fresh, settings)
    assert restored.restore_index() == 1
    history = History(fresh, restored, settings)
    response = history.query("yahoo", "GC=F", "1h", limit=1, ma=False)
    assert response[0]["time"] == "2026-04-02T20:59:59"
    assert response[0]["volume"] == 0
    assert all(response[0][key] == getattr(row, key) for key in ("open", "high", "low", "close"))
    aggregated = history.query("yahoo", "GC=F", "4h", limit=1, ma=False)
    assert aggregated[0]["time"] == "2026-04-02T20:00:00"
    assert all(
        aggregated[0][key] == getattr(row, key)
        for key in ("open", "high", "low", "close", "volume")
    )
