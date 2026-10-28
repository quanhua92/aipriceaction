import time
from dataclasses import replace

import httpx
import pytest

from aipriceaction_api.config import Settings
from aipriceaction_api.providers import Providers
from aipriceaction_api.storage import Repository
from aipriceaction_api.workers import Worker


@pytest.mark.asyncio
async def test_yahoo_hourly_recovery_never_requests_before_its_retention_floor(tmp_path):
    floor = (int(time.time()) - 729 * 86400) // 3600 * 3600
    cursor = floor + 100 * 86400
    stamp = cursor - 3600
    captured = []

    def handler(request):
        captured.append(dict(request.url.params))
        if int(request.url.params["period1"]) < floor:
            return httpx.Response(422, json={"chart": {"error": {"code": "Unprocessable Entity"}}})
        return httpx.Response(
            200,
            json={
                "chart": {
                    "result": [{
                        "timestamp": [stamp],
                        "indicators": {"quote": [{
                            "open": [100], "high": [101], "low": [99],
                            "close": [100], "volume": [10],
                        }]},
                    }],
                    "error": None,
                }
            },
        )

    settings = replace(Settings(), database=tmp_path / "db", proxies=(), public_export_enabled=False)
    repo = Repository(settings.database)
    repo.initialize()
    job_id = repo.queue("yahoo", "EXPE", "1h", "bootstrap", floor, "yahoo")
    with repo.connect() as con:
        con.execute("UPDATE jobs SET cursor=? WHERE id=?", (cursor, job_id))
    providers = Providers(settings, httpx.MockTransport(handler))
    worker = Worker(repo, settings, providers=providers, redis_client=False)
    try:
        assert await worker.repair_page(repo.claim_job(worker.owner)) == 1
    finally:
        await providers.close()
    assert len(captured) == 1
    assert int(captured[0]["period1"]) == floor
    assert int(captured[0]["period2"]) == cursor
