import asyncio
import json

import httpx
import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, parse_time
from aipriceaction_api.providers import Providers
from scripts.replay_daily_feed_failures import replay_legacy, replay_native


def payload():
    times = [parse_time(f"2020-01-0{n}") for n in (1, 2, 3)]
    return {
        "symbol": "FPT",
        "s": "ok",
        "t": times,
        "o": [10, 12, 10],
        "h": [11, 11, 11],
        "l": [9, 9, 9],
        "c": [10, 10, 10],
        "v": [100, 100, 100],
    }, times


def test_replay_keeps_valid_rows_but_does_not_relax_ingestion_validation():
    body, times = payload()
    raw = json.dumps(body).encode()

    async def run():
        settings = Settings()
        provider = Providers(
            settings,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=raw)),
        )
        try:
            with pytest.raises(DataError, match="Invalid OHLC range"):
                await provider.page(
                    "vn", "FPT", "1D", times[-1] + 86400, 100, start=times[0], provider="vps"
                )
        finally:
            await provider.close()
        return await replay_native(settings, "FPT", "vps", raw, times[0], times[-1] + 86400)

    rows, rejected = asyncio.run(run())
    assert [row["time"] for row in rows] == [times[0], times[2]]
    assert rows[0]["open"] == 10000
    assert rejected == [{"time": times[1], "reason": "Invalid OHLC range"}]
    assert json.loads(raw) == body


def test_unknown_daily_basis_cannot_be_skipped_as_a_malformed_candle():
    body, times = payload()
    body["t"] = [t + 17 * 3600 for t in times]
    body["o"] = [10, 10, 10]
    with pytest.raises(DataError, match="unverified VN daily timestamps"):
        asyncio.run(
            replay_native(
                Settings(), "FPT", "vps", json.dumps(body).encode(), times[0], times[-1] + 86400
            )
        )


def test_legacy_replay_retains_rejected_dates_and_checks_symbol():
    body, times = payload()
    rows = [
        {"symbol": "FPT", "time": t, "open": o, "high": 11, "low": 9, "close": 10, "volume": 100}
        for t, o in zip(times, body["o"], strict=True)
    ]
    accepted, rejected = replay_legacy(
        "FPT", json.dumps({"FPT": rows}).encode(), times[0], times[-1] + 86400
    )
    assert [r["time"] for r in accepted] == [times[0], times[2]]
    assert rejected == [{"time": times[1], "reason": "Invalid OHLC range"}]
    rows[0]["symbol"] = "OTHER"
    with pytest.raises(ValueError, match="symbol mismatch"):
        replay_legacy("FPT", json.dumps({"FPT": rows}).encode(), times[0], times[-1] + 86400)
