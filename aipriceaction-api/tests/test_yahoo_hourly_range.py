import time
from dataclasses import replace

import httpx
import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError
from aipriceaction_api.providers import Providers


@pytest.mark.asyncio
async def test_current_hourly_range_uses_legacy_wire_policy_and_preserves_prices():
    stamp = int(time.time()) // 3600 * 3600 - 7200 + 1800
    captured = []

    def handler(request):
        captured.append(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "chart": {
                    "result": [
                        {
                            "timestamp": [stamp],
                            "indicators": {
                                "quote": [
                                    {
                                        "open": [100.123456789],
                                        "high": [101],
                                        "low": [99],
                                        "close": [100],
                                        "volume": [12],
                                    }
                                ]
                            },
                        }
                    ]
                }
            },
        )

    providers = Providers(replace(Settings(), proxies=()), httpx.MockTransport(handler))
    try:
        page = await providers.page("yahoo", "GC=F", "1h", count=200, yahoo_hourly_range="5d")
    finally:
        await providers.close()
    assert captured == [
        {"range": "5d", "interval": "1h", "events": "div|split|capitalGains", "symbol": "GC=F"}
    ]
    assert page.rows[0].time == stamp // 3600 * 3600
    assert page.rows[0].open == 100.123456789 and page.rows[0].volume == 12


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [{"before": 1}, {"start": 1}, {"iv": "1m"}, {"source": "vn"}, {"yahoo_hourly_range": "1mo"}],
)
async def test_relative_range_rejects_historical_or_wrong_market_requests_before_network(kwargs):
    def handler(request):
        pytest.fail("Invalid range policy must not make a request")

    providers = Providers(Settings(), httpx.MockTransport(handler))
    args = {"source": "yahoo", "symbol": "GC=F", "iv": "1h", "yahoo_hourly_range": "5d"} | kwargs
    try:
        with pytest.raises(DataError, match="range policy"):
            await providers.page(**args)
    finally:
        await providers.close()
