"""Regressions for archive gaps and incompatible short live overlays."""
import json
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pandas as pd
import pytest
import requests
import responses

from aipriceaction import AIPriceAction
from aipriceaction.client import _MA_COLUMNS
from aipriceaction.exceptions import AIPriceActionError


@pytest.fixture
def client(tmp_path, monkeypatch):
    c = AIPriceAction(cache_dir=str(tmp_path), live_url="http://localhost:9000", utc_offset=0)
    monkeypatch.setattr(c, "_resolve_tickers", lambda syms, source: [(source or "vn", s) for s in syms])
    return c


def candle(time, close=100):
    return {"time": time, "open": close, "high": close + 1, "low": close - 1,
            "close": close, "volume": 1000}


@responses.activate
def test_requested_history_and_warmed_ma_never_use_stale_archive(client, monkeypatch):
    rows = [candle("2026-10-01", 101), candle("2026-10-02", 102)]
    for row in rows:
        row.update({col: 37.25 for col in _MA_COLUMNS})
    responses.get(re.compile(r"http://localhost:9000/tickers\?"), json={"FPT": rows})
    def no_archive(*args, **kwargs):
        pytest.fail("Successful API range must not read an old archive basis")
    monkeypatch.setattr(client, "_fetch_ohlcv_for_ticker", no_archive)
    df = client.get_ohlcv("FPT", limit=2, ema=True)
    assert df["time"].tolist() == ["2026-10-01", "2026-10-02"]
    assert df["close"].tolist() == [101, 102]
    assert df["ma200"].tolist() == [37.25, 37.25]
    params = parse_qs(urlsplit(responses.calls[0].request.url).query)
    assert params["ma"] == ["true"] and params["ema"] == ["true"]
    assert params["limit"] == ["2"] and params["symbol"] == ["FPT"]
    assert df.attrs["data_source"] == "api"


@pytest.mark.parametrize("missing", ["volume_changed", "ma200"])
@responses.activate
def test_undefined_indicator_stays_missing_without_archive_fallback(client, monkeypatch, missing):
    rows = [candle("2026-10-01", 101), candle("2026-10-02", 102)]
    for row in rows:
        row.update({col: 37.25 for col in _MA_COLUMNS})
    rows[0].pop(missing)
    responses.get(re.compile(r"http://localhost:9000/tickers\?"), json={"FPT": rows})
    def no_archive(*args, **kwargs):
        pytest.fail("Undefined indicators must not replace valid API candles with old archive data")
    monkeypatch.setattr(client, "_fetch_ohlcv_for_ticker", no_archive)
    df = client.get_ohlcv("FPT", limit=2, ema=True)
    assert df["close"].tolist() == [101, 102]
    assert pd.isna(df[missing].iloc[0]) and df[missing].iloc[1] == 37.25
    assert df.attrs["data_source"] == "api"


@responses.activate
def test_multiple_tickers_fallback_as_whole_series(client, monkeypatch):
    responses.get(re.compile(r"http://localhost:9000/tickers\?"), json={"FPT": [candle("2026-10-02", 200)]})
    archive_calls = []
    def archive(src, sym, iv, days, **kwargs):
        archive_calls.append(sym)
        return pd.DataFrame([candle("2026-08-27", 50)])
    monkeypatch.setattr(client, "_fetch_ohlcv_for_ticker", archive)
    df = client.get_ohlcv(tickers=["FPT", "VCB"], ma=False)
    assert archive_calls == ["VCB"]
    assert df.set_index("symbol")["close"].to_dict() == {"FPT": 200, "VCB": 50}
    assert df.attrs["data_source"] == "api+archive_fallback"
    assert len(responses.calls) == 1  # Small multi-ticker requests remain batched.


@responses.activate
def test_sjc_source_uses_existing_yahoo_api_mode(client, monkeypatch):
    responses.get(re.compile(r"http://localhost:9000/tickers\?"), json={"SJC-GOLD": [candle("2026-10-02")]})
    def no_archive(*args, **kwargs):
        pytest.fail("The SJC market source must use its existing compatible API mode")
    monkeypatch.setattr(client, "_fetch_ohlcv_for_ticker", no_archive)
    frame = client.get_ohlcv("SJC-GOLD", source="sjc", limit=1, ma=False)
    assert len(frame) == 1 and frame.attrs["data_source"] == "api"
    assert parse_qs(urlsplit(responses.calls[0].request.url).query)["mode"] == ["yahoo"]


@responses.activate
def test_consistency_error_is_not_hidden_by_archive_fallback(client, monkeypatch):
    responses.get(re.compile(r"http://localhost:9000/tickers\?"), status=503,
                  json={"error": "Historical adjustment revision is pending repair"})
    def no_archive(*args, **kwargs):
        pytest.fail("Consistency failures must reach the caller")
    monkeypatch.setattr(client, "_fetch_ohlcv_for_ticker", no_archive)
    with pytest.raises(AIPriceActionError, match="pending repair"):
        client.get_ohlcv("FPT", limit=20)


@responses.activate
def test_unavailable_api_uses_archive_without_short_overlay(client, monkeypatch):
    responses.get(re.compile(r"http://localhost:9000/tickers\?"), body=requests.ConnectionError("offline"))
    monkeypatch.setattr(client, "_fetch_ohlcv_for_ticker", lambda *a, **kw: pd.DataFrame([
        candle("2026-08-26", 100), candle("2026-08-27", 101)]))
    df = client.get_ohlcv("FPT", limit=2, ma=False)
    assert df["time"].tolist() == ["2026-08-26", "2026-08-27"]
    assert df.attrs["data_source"] == "archive_fallback"


@responses.activate
def test_large_minute_request_pages_whole_days_without_truncation(client):
    rows = [candle((datetime(2025, 1, 1) + timedelta(minutes=i)).isoformat(), i + 1)
            for i in range(11000)]
    def reply(request):
        params = parse_qs(urlsplit(request.url).query)
        eligible = [r for r in rows if r["time"][:10] <= params["end_date"][0]]
        return 200, {}, json.dumps({"FPT": eligible[-int(params["limit"][0]):]})
    responses.add_callback(responses.GET, re.compile(r"http://localhost:9000/tickers\?"), callback=reply)
    df = client.get_ohlcv("FPT", interval="1m", limit=10001, end_date="2025-01-08", ma=False)
    assert len(df) == 10001
    assert df["close"].tolist() == list(range(1000, 11001))
    assert len(responses.calls) == 2


@responses.activate
def test_failed_later_page_discards_partial_api_history(client, monkeypatch):
    rows = [candle((datetime(2025, 1, 2) + timedelta(minutes=i)).isoformat(), 200)
            for i in range(10000)]
    responses.get(re.compile(r"http://localhost:9000/tickers\?"), json={"FPT": rows})
    responses.get(re.compile(r"http://localhost:9000/tickers\?"), status=500)
    monkeypatch.setattr(client, "_fetch_ohlcv_for_ticker", lambda *a, **kw: pd.DataFrame([candle("2025-01-01", 50)]))
    df = client.get_ohlcv("FPT", interval="1m", limit=10001, end_date="2025-01-09", ma=False)
    assert df["close"].tolist() == [50]
    assert df.attrs["data_source"] == "archive_fallback"


@responses.activate
def test_explicit_dates_keep_sdk_latest_limit_behavior(client):
    responses.get(re.compile(r"http://localhost:9000/tickers\?"), json={"FPT": [
        candle("2025-04-28", 100), candle("2025-04-29", 101)]})
    df = client.get_ohlcv("FPT", start_date="2025-04-29", end_date="2025-04-29", limit=2, ma=False)
    assert df["time"].tolist() == ["2025-04-29"]
    params = parse_qs(urlsplit(responses.calls[0].request.url).query)
    assert params["end_date"] == ["2025-04-29"]
    assert "start_date" not in params


@responses.activate
def test_empty_archive_aggregation_with_ma_returns_empty_frame(client, monkeypatch):
    responses.get(re.compile(r"http://localhost:9000/tickers\?"), json={})
    monkeypatch.setattr(client, "_fetch_ohlcv_for_ticker", lambda *a, **kw: pd.DataFrame(columns=[
        "time", "open", "high", "low", "close", "volume"]))
    df = client.get_ohlcv("FPT", interval="15m", limit=20, ma=True)
    assert df.empty and "symbol" in df.columns
