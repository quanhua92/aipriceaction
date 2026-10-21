import asyncio

import pytest

from aipriceaction_api.request_coalescing import AsyncSingleFlight


@pytest.mark.asyncio
async def test_identical_active_reads_share_work_then_allow_fresh_work():
    flight = AsyncSingleFlight()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"value": 42}

    first = asyncio.create_task(flight.run(("tickers", "same-query"), operation))
    await started.wait()
    second = asyncio.create_task(flight.run(("tickers", "same-query"), operation))
    await asyncio.sleep(0)

    release.set()
    assert await asyncio.gather(first, second) == [{"value": 42}, {"value": 42}]
    assert calls == 1
    await asyncio.sleep(0)

    assert await flight.run(("tickers", "same-query"), operation) == {"value": 42}
    assert calls == 2


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_shared_read():
    flight = AsyncSingleFlight()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "done"

    first = asyncio.create_task(flight.run("same", operation))
    await started.wait()
    second = asyncio.create_task(flight.run("same", operation))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    release.set()
    assert await second == "done"
    assert calls == 1


@pytest.mark.asyncio
async def test_failed_shared_read_propagates_and_key_can_be_retried():
    flight = AsyncSingleFlight()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def failing_operation():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        raise ValueError("read failed")

    first = asyncio.create_task(flight.run("same", failing_operation))
    await started.wait()
    second = asyncio.create_task(flight.run("same", failing_operation))
    await asyncio.sleep(0)
    release.set()
    failures = await asyncio.gather(first, second, return_exceptions=True)

    assert all(isinstance(exc, ValueError) for exc in failures)
    assert calls == 1
    await asyncio.sleep(0)
    assert await flight.run("same", lambda: asyncio.sleep(0, result="retried")) == "retried"
