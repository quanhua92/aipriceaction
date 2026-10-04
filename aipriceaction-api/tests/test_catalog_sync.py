import json
from dataclasses import replace

import httpx
import pytest

from aipriceaction_api.catalog import Catalog
from aipriceaction_api.catalog_sync import sync_catalog
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError
from aipriceaction_api.storage import Repository
from aipriceaction_api.workers import Worker


def live_groups():
    return {
        "vn": {"INDEX": ["VNINDEX"], "BANK": ["VCB", "TCB"]},
        "crypto": {"CRYPTO_TOP_100": ["BTCUSDT", "ETHUSDT"]},
        "yahoo": {"Commodity": ["GC=F", "SJC-GOLD"], "Stock": ["AAPL", "MSFT"]},
    }


def transport_for(groups):
    def handler(request):
        mode = request.url.params["mode"]
        return httpx.Response(200, json=groups[mode])

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_live_catalog_drives_groups_and_complete_ingestion_universe(tmp_path):
    snapshot = tmp_path / "catalog.json"
    result = await sync_catalog(
        "https://api.example.test", snapshot, transport=transport_for(live_groups())
    )
    assert result["counts"] == {"vn": 3, "crypto": 2, "yahoo": 4}
    assert len(result["sha256"]) == 64

    seed = tmp_path / "watchlist.json"
    seed.write_text(
        json.dumps(
            {
                "vn": [{"symbol": "VCB", "history_start": "2020-01-01"}],
                "crypto": [],
                "yahoo": [{"symbol": "AAPL", "intervals": ["1D", "1m"]}],
                "sjc": [],
            }
        )
    )
    settings = replace(
        Settings(),
        database=tmp_path / "db.sqlite3",
        watchlist=seed,
        catalog_snapshot=snapshot,
        ingest_universe="catalog",
    )
    catalog = Catalog(settings)
    assert catalog.groups("vn") == live_groups()["vn"]
    assert catalog.groups("crypto") == live_groups()["crypto"]
    assert catalog.groups("yahoo") == live_groups()["yahoo"]
    assert catalog.groups_by_source["sjc"] == {"Commodity": ["SJC-GOLD"]}
    assert catalog.groups_by_source["yahoo"] == {
        "Commodity": ["GC=F"],
        "Stock": ["AAPL", "MSFT"],
    }

    repo = Repository(settings.database)
    repo.initialize()
    entries = Worker(repo, settings).load_watchlist()
    identities = {(row["source"], row["symbol"]) for row in entries}
    assert identities == {
        ("vn", "VNINDEX"),
        ("vn", "VCB"),
        ("vn", "TCB"),
        ("crypto", "BTCUSDT"),
        ("crypto", "ETHUSDT"),
        ("yahoo", "GC=F"),
        ("yahoo", "AAPL"),
        ("yahoo", "MSFT"),
        ("sjc", "SJC-GOLD"),
    }
    vcb = next(row for row in entries if row["source"] == "vn" and row["symbol"] == "VCB")
    aapl = next(row for row in entries if row["source"] == "yahoo" and row["symbol"] == "AAPL")
    assert vcb["history_start"] == "2020-01-01"
    assert vcb["intervals"] == ["1D", "1h", "1m"]
    assert aapl["intervals"] == ["1D", "1m"]
    assert all(row["enabled"] for row in repo.tickers())


@pytest.mark.asyncio
async def test_invalid_live_catalog_does_not_replace_last_good_snapshot(tmp_path):
    destination = tmp_path / "catalog.json"
    destination.write_text('{"last":"good"}\n')
    groups = live_groups()
    groups["crypto"] = {"bad": [""]}
    with pytest.raises(DataError, match="invalid symbol"):
        await sync_catalog("https://api.example.test", destination, transport=transport_for(groups))
    assert destination.read_text() == '{"last":"good"}\n'
