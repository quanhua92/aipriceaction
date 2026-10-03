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


def quote(iv="1h"):
    # Exact fields from the captured public GC=F hourly response.
    return Candle(
        "yahoo",
        "GC=F",
        iv,
        parse_time("2026-04-02T20:59:59"),
        4702.7001953125,
        4702.7001953125,
        4702.7001953125,
        4702.7001953125,
        0,
        "legacy-api",
        "quote-snapshot",
    )


@pytest.mark.parametrize("iv", ("1h", "1m"))
def test_legacy_intraday_futures_quote_preserves_original_seconds_and_prices(iv):
    row = quote(iv)
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
        iv,
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
        {"interval": "5m"},
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
@pytest.mark.parametrize("iv", ("1h", "1m"))
async def test_quote_import_records_evidence_and_survives_archive_history_restore(tmp_path, iv):
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
    row = quote(iv)
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
            iv,
            provider="legacy-api",
            revision=row.revision,
            from_api=True,
            api_format="json",
            **({"years": [2026]} if iv == "1h" else {"start": "2026-04-02", "end": "2026-04-02"}),
        )
    assert report["periods"][0]["legacy_quote_events"] == {
        "rows": 1,
        "start": row.time,
        "end": row.time,
    }
    assert any(item["kind"] == "legacy_quote_events" for item in repo.findings())
    stored = repo.read("yahoo", "GC=F", iv)
    assert len(stored) == 1 and replace(stored[0], updated_at=0) == row
    obj = archive.publish(stored, prune=True)
    assert archive.read(obj, refresh=True) == stored
    fresh = Repository(tmp_path / "restored")
    fresh.initialize()
    restored = Archive(fresh, settings)
    assert restored.restore_index() == 1
    history = History(fresh, restored, settings)
    response = history.query("yahoo", "GC=F", iv, limit=1, ma=False)
    assert response[0]["time"] == "2026-04-02T20:59:59"
    assert response[0]["volume"] == 0
    assert all(response[0][key] == getattr(row, key) for key in ("open", "high", "low", "close"))
    aggregated = history.query("yahoo", "GC=F", "4h", limit=1, ma=False)
    assert aggregated[0]["time"] == "2026-04-02T20:00:00"
    assert all(
        aggregated[0][key] == getattr(row, key)
        for key in ("open", "high", "low", "close", "volume")
    )


def test_minute_bar_and_legacy_quote_in_same_minute_keep_distinct_observed_times():
    # Exact first two observations from the April 5 public minute response.
    raw = {
        "GC=F": [
            {
                "time": "2026-04-05T22:00:00",
                "open": 4675.0,
                "high": 4699.2998046875,
                "low": 4654.0,
                "close": 4658.7998046875,
                "volume": 0,
            },
            {
                "time": "2026-04-05T22:00:44",
                "open": 4669.5,
                "high": 4669.5,
                "low": 4669.5,
                "close": 4669.5,
                "volume": 0,
            },
        ]
    }
    rows = json_rows(json.dumps(raw), "yahoo", "GC=F", "1m", "legacy-api")
    assert [r.time for r in rows] == [
        parse_time("2026-04-05T22:00:00"),
        parse_time("2026-04-05T22:00:44"),
    ]
    assert rows[0].close == 4658.7998046875 and rows[1].close == 4669.5
    with pytest.raises(DataError, match="aligned to a minute"):
        json_rows(json.dumps(raw), "yahoo", "GC=F", "1m", "yahoo")
