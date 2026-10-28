import asyncio
import time
from dataclasses import replace

import httpx
import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError
from aipriceaction_api.providers import Providers, RateLimiter


def test_binance_budget_is_separate_from_other_providers():
    settings = replace(Settings(), requests_per_minute=10, binance_requests_per_minute=30)
    providers = Providers(settings)
    assert providers.provider_limiter("binance", None).delay == 2
    assert providers.provider_limiter("yahoo", None).delay == 6
    assert providers.provider_limiter("vps", None).delay == 6
    assert providers.provider_limiter("binance", None) is providers.provider_limiter("binance", None)
    inherited = Providers(replace(settings, binance_requests_per_minute=0))
    assert inherited.provider_limiter("binance", None).delay == 6


def test_binance_budget_must_be_nonnegative(monkeypatch):
    monkeypatch.setenv("BINANCE_PROVIDER_RPM", "30")
    assert Settings.from_env().binance_requests_per_minute == 30
    monkeypatch.setenv("BINANCE_PROVIDER_RPM", "-1")
    with pytest.raises(ValueError, match="BINANCE_PROVIDER_RPM"):
        Settings.from_env()


@pytest.mark.asyncio
async def test_rate_limiter_rechecks_deadline_after_a_waiting_task_is_postponed():
    limiter = RateLimiter(1000)
    limiter.next = time.monotonic() + 0.02
    task = asyncio.create_task(limiter.acquire())
    await asyncio.sleep(0.005)
    started = time.monotonic()
    limiter.postpone(0.08)
    await task
    assert time.monotonic() - started >= 0.07


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [418, 429])
@pytest.mark.parametrize("binary", [False, True])
async def test_binance_rate_limit_honors_retry_after_per_route(status, binary, monkeypatch):
    transport = httpx.MockTransport(
        lambda _: httpx.Response(status, headers={"Retry-After": "120"}, json={})
    )
    providers = Providers(replace(Settings(), proxies=(), binance_requests_per_minute=30), transport)
    monkeypatch.setattr(providers, "shuffled_routes", lambda _: ([None], 1))
    started = time.monotonic()
    try:
        with pytest.raises(DataError, match="Binance file request failed|binance request failed"):
            if binary:
                await providers.binary_request("https://example.test/binance.zip", 1024)
            else:
                await providers.request("binance", "https://example.test/klines")
        assert providers.limits[("binance", None)].next >= started + 119
    finally:
        await providers.close()
