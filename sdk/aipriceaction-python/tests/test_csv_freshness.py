import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import responses

from aipriceaction.client import AIPriceAction


@responses.activate
def test_missing_current_csv_is_retried_after_short_ttl(tmp_path):
    client = AIPriceAction(base_url="https://example.test", cache_dir=str(tmp_path), use_live=False)
    today = datetime.now(timezone.utc).date()
    url = f"https://example.test/{client._csv_key('crypto', 'BTCUSDT', '1m', today)}"
    responses.get(url, status=404)
    responses.get(url, body=f"{today} 00:00:00,1,2,1,2,10\n", headers={"x-amz-meta-sha256": "new"})

    assert client._fetch_csv("crypto", "BTCUSDT", "1m", today) is None
    assert client._fetch_csv("crypto", "BTCUSDT", "1m", today) is None
    assert len(responses.calls) == 1
    assert "aipa_fresh=" in responses.calls[0].request.url
    path = client._cache_key("crypto", "BTCUSDT", "1m", today)
    client._freshness[path]["checked_at"] -= 16
    assert client._fetch_csv("crypto", "BTCUSDT", "1m", today).iloc[0]["close"] == 2
    assert len(responses.calls) == 2


@responses.activate
def test_exact_sha_detects_change_even_when_rust_hash_does_not(tmp_path):
    client = AIPriceAction(base_url="https://example.test", cache_dir=str(tmp_path), use_live=False)
    day = datetime.now(timezone.utc).date() - timedelta(days=30)
    url = f"https://example.test/{client._csv_key('vn', 'FPT', '1D', day)}"
    responses.get(url, body=f"{day} 00:00:00,1,2,1,2,10\n",
                  headers={"x-amz-meta-sha256": "old", "x-amz-meta-content-hash": "same"})
    assert client._fetch_csv("vn", "FPT", "1D", day).iloc[0]["high"] == 2
    path = client._cache_key("vn", "FPT", "1D", day)
    client._freshness[path]["checked_at"] -= 1801
    responses.head(url, headers={"x-amz-meta-sha256": "new", "x-amz-meta-content-hash": "same"})
    responses.get(url, body=f"{day} 00:00:00,1,3,1,2,10\n",
                  headers={"x-amz-meta-sha256": "new", "x-amz-meta-content-hash": "same"})
    assert client._fetch_csv("vn", "FPT", "1D", day).iloc[0]["high"] == 3


@responses.activate
def test_fresh_hits_for_other_files_do_not_hide_an_expired_correction(tmp_path):
    client = AIPriceAction(base_url="https://example.test", cache_dir=str(tmp_path), use_live=False)
    day = datetime.now(timezone.utc).date() - timedelta(days=30)
    for index in range(12):
        path = tmp_path / f"file-{index}.csv"
        path.write_text("old")
        client._freshness[str(path)] = {"hash": "old", "checked_at": time.monotonic()}
        assert client._is_fresh(str(path), "https://example.test/historical.csv")
    path = tmp_path / "changed.csv"
    path.write_text("old")
    client._save_hash(str(path), "old")
    client._freshness[str(path)] = {"hash": "old", "checked_at": time.monotonic() - 1801}
    url = f"https://example.test/{client._csv_key('vn', 'FPT', '1D', day)}"
    responses.head(url, headers={"x-amz-meta-sha256": "new"})
    assert not client._is_fresh(str(path), url)
    assert len(responses.calls) == 1


@responses.activate
def test_unknown_disk_content_is_downloaded_before_hash_is_saved(tmp_path):
    client = AIPriceAction(base_url="https://example.test", cache_dir=str(tmp_path), use_live=False)
    day = datetime.now(timezone.utc).date() - timedelta(days=30)
    path = client._cache_key("vn", "FPT", "1D", day)
    Path(path).parent.mkdir(parents=True)
    Path(path).write_text(f"{day} 00:00:00,1,2,1,2,10\n")
    url = f"https://example.test/{client._csv_key('vn', 'FPT', '1D', day)}"
    responses.head(url, headers={"x-amz-meta-sha256": "current"})
    responses.get(url, body=f"{day} 00:00:00,1,3,1,2,10\n",
                  headers={"x-amz-meta-sha256": "current"})
    assert client._fetch_csv("vn", "FPT", "1D", day).iloc[0]["high"] == 3
