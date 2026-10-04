import csv
import io
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from aipriceaction_api.app import create_app
from aipriceaction_api.archive import Archive
from aipriceaction_api.domain import Candle, parse_time
from aipriceaction_api.responses import CSV_COLUMNS


@pytest.fixture
def client(tmp_path):
    settings = replace(
        __import__("aipriceaction_api.config", fromlist=["Settings"]).Settings(),
        database=tmp_path / "db.sqlite3",
        cache_dir=tmp_path / "cache",
        object_dir=tmp_path / "objects",
        archive_backend="filesystem",
        sync_tokens=("test-token",),
        refresh_secret="refresh-test",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        origin = datetime(2024, 1, 1, tzinfo=UTC)
        rows = []
        for symbol, start in (("FPT", 100000), ("VCB", 80000), ("VNINDEX", 1200)):
            for i in range(240):
                close = start + i * 10
                rows.append(
                    Candle(
                        "vn",
                        symbol,
                        "1D",
                        int((origin + timedelta(days=i)).timestamp()),
                        close,
                        close + 10,
                        close - 10,
                        close,
                        200000 + i,
                        "fixture",
                    )
                )
        app.state.repo.put(rows)
        client.app_instance = app
        yield client


def test_repeated_symbols_defaults_and_order(client):
    data = client.get("/tickers?symbol=FPT&symbol=VCB").json()
    assert set(data) == {"FPT", "VCB"}
    assert len(data["FPT"]) == 1
    single = client.get("/tickers?symbol=FPT").json()["FPT"]
    assert len(single) == 240
    assert single[0]["time"] < single[-1]["time"]
    assert single[-1]["ma20"] == 102295
    assert client.get("/tickers?symbol=&limit=1").json() == {}


def test_health_separates_old_candle_dates_from_current_ingestion(client):
    data = client.get("/health").json()
    coverage = data["storage"]["coverage"]
    row = next(r for r in coverage if r["source"] == "vn" and r["interval"] == "1D")
    assert row["rows"] == data["daily_records_count"] == 720
    assert row["first_candle_at"].startswith("2024-01-01")
    assert row["latest_candle_at"].startswith("2024-08-27")
    assert datetime.fromisoformat(row["last_ingest_at"]) > datetime.fromisoformat(
        row["latest_candle_at"]
    )
    assert data["daily_last_sync"] == row["last_ingest_at"]


def test_csv_legacy_and_index_scaling(client):
    response = client.get("/tickers?symbol=FPT&symbol=VNINDEX&limit=1&format=csv&legacy=true")
    reader = csv.DictReader(io.StringIO(response.text))
    assert tuple(reader.fieldnames) == CSV_COLUMNS
    rows = list(reader)
    assert float(rows[0]["close"]) == 102.39
    # Legacy behavior scales OHLC, not MAs or index prices.
    assert float(rows[0]["ma20"]) == 102295
    assert float(rows[1]["close"]) == 3590


def test_query_validation_and_ma_false(client):
    assert client.get("/tickers?interval=bogus").status_code == 400
    assert client.get("/tickers?limit=-1").status_code == 400
    assert client.get("/tickers?start_date=broken").status_code == 400
    row = client.get("/tickers?symbol=FPT&limit=1&ma=false").json()["FPT"][0]
    assert "ma20" not in row
    assert "close_changed" in row
    assert client.get("/tickers?symbol=FPT&mode=stocks&limit=1").status_code == 200


def test_start_date_returns_first_candles_instead_of_latest(client):
    rows = client.get("/tickers?symbol=FPT&start_date=2024-02-01&limit=2").json()["FPT"]
    assert [r["time"] for r in rows] == ["2024-02-01", "2024-02-02"]
    monthly = client.get("/tickers?symbol=FPT&interval=1M&start_date=2024-02-01&limit=1").json()[
        "FPT"
    ]
    assert monthly[0]["time"] == "2024-02-01"
    assert monthly[0]["close"] == 100590  # Complete February, including leap day.


def test_minute_monthly_and_weekly_contract(client):
    assert client.get("/tickers?symbol=FPT&interval=1m").json() == {}
    monthly = client.get("/tickers?symbol=FPT&interval=1M&limit=2").json()["FPT"]
    assert len(monthly) == 2
    assert all(row["time"].endswith("-01") for row in monthly)
    weekly = client.get("/tickers?symbol=FPT&interval=1W&limit=2").json()["FPT"]
    assert all(datetime.fromisoformat(r["time"]).weekday() == 0 for r in weekly)


@pytest.mark.parametrize(
    "iv,stamp", [("1W", "2024-02-05"), ("2W", "2024-02-05"), ("1M", "2024-02-01")]
)
@pytest.mark.parametrize("indicator", ["none", "sma", "ema"])
def test_dated_aggregate_keeps_partial_first_bucket_and_clips_input(client, iv, stamp, indicator):
    response = client.get(
        "/tickers",
        params={
            "symbol": "FPT",
            "interval": iv,
            "start_date": "2024-02-07",
            "end_date": "2024-02-08",
            "ma": str(indicator != "none").lower(),
            "ema": str(indicator == "ema").lower(),
            "limit": 1,
        },
    )
    assert response.status_code == 200
    rows = response.json()["FPT"]
    assert len(rows) == 1
    row = rows[0]
    assert row["time"] == stamp
    assert (row["open"], row["high"], row["low"], row["close"], row["volume"]) == (
        100370,
        100390,
        100360,
        100380,
        400075,
    )


def test_midmonth_limit_completes_only_requested_first_bucket(client):
    response = client.get(
        "/tickers",
        params={
            "symbol": "FPT",
            "interval": "1M",
            "start_date": "2024-02-07",
            "limit": 1,
            "ma": "false",
        },
    )
    row = response.json()["FPT"][0]
    assert row["time"] == "2024-02-01"
    assert row["open"] == 100370 and row["close"] == 100590
    assert row["volume"] == sum(200000 + i for i in range(37, 60))


def test_minute_only_ticker_serves_hourly_aliases_and_legacy_csv(client):
    origin = parse_time("2024-01-03T02:15:00+00:00")
    client.app_instance.state.repo.put(
        [
            Candle("vn", "OCB", "1m", origin + i * 60, 100 + i, 102 + i, 99 + i, 101 + i, 10)
            for i in range(120)
        ]
    )
    params = dict(symbol="OCB", limit=20, ma="false", cache="false")
    expected = client.get("/tickers", params={**params, "interval": "1h"}).json()["OCB"]
    assert len(expected) == 3
    assert [row["volume"] for row in expected] == [450, 600, 150]
    for alias in ("1H", "hourly"):
        assert client.get("/tickers", params={**params, "interval": alias}).json() == {
            "OCB": expected
        }
    response = client.get(
        "/tickers", params={**params, "interval": "4h", "format": "csv", "legacy": "true"}
    )
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 1
    assert tuple(rows[0]) == CSV_COLUMNS
    assert [float(rows[0][key]) for key in ("open", "high", "low", "close")] == [
        0.1,
        0.221,
        0.099,
        0.22,
    ]
    assert float(rows[0]["volume"]) == 1200


def test_metadata_static_and_health(client):
    assert "BANKING" in client.get("/tickers/group").json() or client.get("/tickers/group").json()
    assert "FPT" in client.get("/tickers/name").json()
    assert "SJC-GOLD" in client.get("/tickers/group?mode=all").json()["Commodity"]
    assert client.get("/tickers/info?ticker=FPT").json()["ticker"] == "FPT"
    health = client.get("/health").json()
    assert health["daily_records_count"] == 720
    assert health["trading_hours_timezone"] == "Asia/Ho_Chi_Minh"
    assert client.get("/explorer").status_code == 200
    assert client.get("/public/js/app.js").headers["cache-control"] == "max-age=300, public"


def test_archive_only_ticker_discovery_survives_index_reconstruction(client, tmp_path):
    app = client.app_instance
    repo = app.state.repo
    repo.register("vn", "OLDVN", "Historical fixture company")
    rows = [
        Candle("vn", "OLDVN", "1D", parse_time(day), 100, 101, 99, 100, 1000)
        for day in ("2017-01-03", "2017-01-04")
    ]
    app.state.archive.publish(rows)
    assert repo.state("vn", "OLDVN", "1D") is None
    assert client.get("/tickers/name").json()["OLDVN"] == "Historical fixture company"
    assert "OLDVN" not in client.get("/tickers/name?mode=crypto").json()
    assert client.get("/tickers/name?mode=all").json()["OLDVN"] == "Historical fixture company"
    query = {"symbol": "OLDVN", "start_date": "2017-01-03", "end_date": "2017-01-04", "ma": "false"}
    before = client.get("/tickers", params=query)
    assert before.status_code == 200 and len(before.json()["OLDVN"]) == 2

    settings = replace(app.state.settings, database=tmp_path / "restored-index.sqlite3")
    fresh_app = create_app(settings)
    with TestClient(fresh_app) as restored:
        fresh_repo = fresh_app.state.repo
        assert Archive(fresh_repo, settings).restore_index() == 1
        # An index carries candle identities, not invented company metadata.
        assert restored.get("/tickers/name").json()["OLDVN"] == "OLDVN"
        assert restored.get("/tickers", params=query).json() == before.json()
        health = restored.get("/health").json()
        assert health["daily_records_count"] == health["active_tickers_count"] == 0
        row = next(r for r in health["storage"]["series"] if r["symbol"] == "OLDVN")
        assert row["status"] == "archived" and not row["enabled"]
        assert row["rows"] == 0 and row["archive_rows"] == 2 and row["archive_objects"] == 1
        assert row["pending_archive_repairs"] == 0
        assert row["first_archived_candle_at"].startswith("2017-01-03")
        assert row["latest_archived_candle_at"].startswith("2017-01-04")
        assert row["latest_candle_at"] is None and row["last_ingest_at"] is None
        assert row["last_provider_success_at"] is None and not row["verification_current"]
        assert row["latest_verification"] == "unverified"
        assert fresh_repo.status()["jobs"] == []


def test_historical_names_keep_source_modes_and_catalog_precedence(client):
    repo = client.app_instance.state.repo
    for source, symbol, name in (
        ("vn", "FPT", "Fixture override"),
        ("vn", "OLDVN", "Historical VN fixture"),
        ("yahoo", "OLDGLOBAL", "Historical global fixture"),
        ("sjc", "OLDGOLD", "Historical gold fixture"),
        ("crypto", "OLDCOIN", "Historical crypto fixture"),
        ("vn", "COLLISION", "VN fixture"),
        ("yahoo", "COLLISION", "Global fixture"),
    ):
        repo.register(source, symbol, name)
    assert client.get("/tickers/name").json()["FPT"] != "Fixture override"
    vn = client.get("/tickers/name?mode=stocks").json()
    assert vn["OLDVN"] == "Historical VN fixture" and "OLDGLOBAL" not in vn
    global_names = client.get("/tickers/name?mode=yahoo").json()
    assert global_names["OLDGLOBAL"] == "Historical global fixture"
    assert global_names["OLDGOLD"] == "Historical gold fixture" and "OLDVN" not in global_names
    assert client.get("/tickers/name?mode=crypto").json()["OLDCOIN"] == "Historical crypto fixture"
    all_names = client.get("/tickers/name?mode=all").json()
    assert all_names["COLLISION"] == "VN fixture"
    assert list(all_names) == sorted(all_names)
    assert not any(r["enabled"] for r in repo.tickers())


def test_health_separates_published_pending_and_superseded_history(client):
    app = client.app_instance
    repo = app.state.repo
    rows = [
        Candle("vn", "OLDVN", "1D", parse_time(day), 100, 101, 99, 100, 1000)
        for day in ("2017-01-03", "2017-01-04", "2017-01-05")
    ]
    repo.put(rows)
    rows = repo.read("vn", "OLDVN", "1D")
    app.state.archive.publish(rows[:1], prune=True)
    pending = app.state.archive.prepare([replace(rows[1], revision="pending-basis")])
    repo.publish_archive(pending | {"status": "pending_repair"})
    obsolete = app.state.archive.prepare([replace(rows[2], revision="superseded-basis")])
    repo.publish_archive(obsolete | {"status": "superseded"})
    pending_only = app.state.archive.prepare([replace(rows[0], symbol="PENDINGVN")])
    repo.publish_archive(pending_only | {"status": "pending_repair"})
    health = client.get("/health").json()
    assert health["daily_records_count"] == 722
    row = next(r for r in health["storage"]["series"] if r["symbol"] == "OLDVN")
    assert row["rows"] == 2 and row["archive_rows"] == 1 and row["archive_objects"] == 1
    assert row["status"] == "ready" and row["revision"] == rows[0].revision
    assert row["pending_archive_repairs"] == 1
    assert row["first_archived_candle_at"] == row["latest_archived_candle_at"]
    assert row["latest_archived_candle_at"].startswith("2017-01-03")
    pending_row = next(r for r in health["storage"]["series"] if r["symbol"] == "PENDINGVN")
    assert (
        pending_row["status"] == "archive_pending" and pending_row["pending_archive_repairs"] == 1
    )
    assert pending_row["rows"] == pending_row["archive_rows"] == pending_row["archive_objects"] == 0
    assert pending_row["first_archived_candle_at"] is None
    assert pending_row["latest_archived_candle_at"] is None
    assert not pending_row["verification_current"] and not pending_row["enabled"]


def test_analysis_envelopes_and_historical_date(client):
    result = client.get("/analysis/top-performers?limit=1&date=2024-02-01").json()
    assert result["analysis_type"] == "top_performers"
    assert result["total_analyzed"] == 2
    assert result["data"]["performers"][0]["close"] in (100310, 80310)
    sectors = client.get("/analysis/ma-scores-by-sector?ma_period=20").json()
    assert sectors["data"]["sectors"]
    assert client.get("/analysis/ma-scores-by-sector?ma_period=7").status_code == 400
    rrg = client.get("/analysis/rrg?algorithm=mascore&trails=0").json()
    assert rrg["data"]["algorithm"] == "mascore"
    assert len(rrg["data"]["tickers"]) == 2
    jdk = client.get("/analysis/rrg?algorithm=jdk&benchmark=VNINDEX").json()
    assert jdk["data"]["tickers"]


@pytest.mark.parametrize(
    "route,field,bits,signed",
    [
        ("top-performers", "limit", 64, False),
        ("top-performers", "min_volume", 64, False),
        ("ma-scores-by-sector", "ma_period", 32, False),
        ("ma-scores-by-sector", "top_per_sector", 64, False),
        ("volume-profile", "bins", 64, False),
        ("rrg", "period", 64, False),
        ("rrg", "trails", 64, False),
        ("rrg", "min_volume", 64, True),
    ],
)
def test_analysis_integer_query_bounds_reject_before_reading_history(
    client, monkeypatch, route, field, bits, signed
):
    def unexpected(*args, **kwargs):
        raise AssertionError("Invalid query reached market data reads")

    monkeypatch.setattr(client.app_instance.state.history, "query", unexpected)
    monkeypatch.setattr(client.app_instance.state.history, "read", unexpected)
    lower = -(1 << (bits - 1)) if signed else 0
    upper = (1 << (bits - int(signed))) - 1
    for value in (lower - 1, upper + 1):
        params = {field: value}
        if route == "volume-profile":
            params.update(symbol="FPT", date="2024-02-01")
        response = client.get(f"/analysis/{route}", params=params)
        assert response.status_code == 400


@pytest.mark.parametrize(
    "mode,expected", [("CRYPTO", "crypto"), ("YaHoO", "yahoo"), ("unknown", "vn")]
)
def test_volume_profile_legacy_mode_dispatch(client, monkeypatch, mode, expected):
    stamp = parse_time("2024-02-01T02:00:00")
    rows = [Candle(expected, "FPT", "1m", stamp, 100, 110, 90, 105, 1000)]
    calls = []

    def read(source, symbol, interval, start, end):
        calls.append((source, symbol, interval, start, end))
        return rows

    monkeypatch.setattr(client.app_instance.state.history, "read", read)
    response = client.get(
        "/analysis/volume-profile", params={"symbol": "FPT", "date": "2024-02-01", "mode": mode}
    )
    assert response.status_code == 200
    assert calls[0][:3] == (expected, "FPT", "1m")
    assert response.json()["data"]["total_minutes"] == 1


def test_volume_profile_empty_symbol_error_precedes_date_validation(client):
    response = client.get("/analysis/volume-profile", params={"symbol": ""})
    assert response.status_code == 400
    assert response.json() == {"error": "symbol parameter is required"}


def test_analysis_zero_controls_keep_legacy_semantics(client):
    performers = client.get("/analysis/top-performers", params={"limit": 0})
    assert performers.status_code == 200 and len(performers.json()["data"]["performers"]) == 1
    sectors = client.get("/analysis/ma-scores-by-sector", params={"top_per_sector": 0})
    assert sectors.status_code == 200
    assert sectors.json()["data"]["sectors"]
    assert all(not item["top_stocks"] for item in sectors.json()["data"]["sectors"])
    rrg = client.get("/analysis/rrg", params={"period": 0, "trails": 0, "min_volume": -1})
    assert rrg.status_code == 200 and rrg.json()["data"]["period"] == 4
    assert all("trails" not in item for item in rrg.json()["data"]["tickers"])


@pytest.mark.parametrize("value", ["1.0", " 1", "1 ", "1e0", "１", "--1"])
def test_integer_query_lexical_validation(client, value):
    for path, params in (
        ("/tickers", {"symbol": "FPT", "limit": value}),
        ("/analysis/top-performers", {"limit": value}),
        ("/analysis/rrg", {"min_volume": value}),
    ):
        assert client.get(path, params=params).status_code == 400


def test_unsigned_negative_zero_and_signed_integer_forms(client):
    assert client.get("/analysis/top-performers", params={"limit": "-0"}).status_code == 400
    assert client.get("/analysis/top-performers", params={"limit": "+01"}).status_code == 200
    assert client.get("/tickers", params={"symbol": "FPT", "limit": "+01"}).status_code == 200
    rrg = client.get("/analysis/rrg", params={"min_volume": "-0", "trails": "0"})
    assert rrg.status_code == 200 and rrg.json()["data"]["tickers"]


def test_archive_backed_web_response(client):
    app = client.app_instance
    before = client.get(
        "/tickers?symbol=FPT&start_date=2024-01-01&end_date=2024-08-20&limit=1000&cache=false"
    ).json()
    old = app.state.repo.read(
        "vn", "FPT", "1D", end=int(datetime(2024, 7, 1, tzinfo=UTC).timestamp())
    )
    app.state.archive.publish(old, prune=True)
    after = client.get(
        "/tickers?symbol=FPT&start_date=2024-01-01&end_date=2024-08-20&limit=1000&cache=false"
    ).json()
    assert before == after


def test_sync_and_refresh_auth(client):
    key = str(uuid.uuid4())
    headers = {"Authorization": "Bearer test-token"}
    response = client.post(
        f"/sync/{key}", json={"secret": "s", "value": {"watchlists": []}}, headers=headers
    )
    assert response.status_code == 200
    assert client.get(f"/sync/{key}?secret=s", headers=headers).json() == response.json()
    assert client.get(f"/sync/{key}?secret=x", headers=headers).status_code == 403
    assert client.get(f"/sync/{key}?secret=s").status_code == 401
    assert client.post("/tickers/refresh", json={"interval": "1D"}).status_code == 401
    refreshed = client.post(
        "/tickers/refresh", json={"interval": "1D", "key": "refresh-test"}
    ).json()
    assert refreshed["interval"] == "next_1d"


SYNC_KEY = "550e8400-e29b-41d4-a716-446655440000"


@pytest.mark.parametrize(
    "key",
    [
        "uuid:" + SYNC_KEY,
        "urn:" + SYNC_KEY,
        "{{" + SYNC_KEY + "}}",
        "{" + SYNC_KEY.replace("-", "") + "}",
        "urn:uuid:" + SYNC_KEY.replace("-", ""),
        SYNC_KEY.replace("-", "", 1),
        SYNC_KEY.replace("-", "--", 1),
        "-" + SYNC_KEY.replace("-", ""),
    ],
)
def test_sync_rejects_uuid_shapes_rejected_by_legacy_parser(client, key):
    headers = {"Authorization": "Bearer test-token"}
    expected = {"success": False, "error": "Key must be a valid UUID"}
    get = client.get(f"/sync/{key}", params={"secret": "s"}, headers=headers)
    post = client.post(f"/sync/{key}", json={"secret": "s", "value": {}}, headers=headers)
    assert get.status_code == post.status_code == 400
    assert get.json() == post.json() == expected
    with client.app.state.repo.connect() as con:
        assert con.execute("SELECT count(*) FROM sync_kv").fetchone()[0] == 0


@pytest.mark.parametrize(
    "key",
    [SYNC_KEY.upper(), SYNC_KEY.replace("-", ""), "{" + SYNC_KEY + "}", "urn:uuid:" + SYNC_KEY],
)
def test_sync_valid_uuid_formats_share_one_canonical_record_and_secret(client, key):
    headers = {"Authorization": "Bearer test-token"}
    created = client.post(
        f"/sync/{key}", json={"secret": "s", "value": {"watchlists": []}}, headers=headers
    )
    assert created.status_code == 200 and created.json()["id"] == SYNC_KEY
    assert (
        client.get(f"/sync/{SYNC_KEY}", params={"secret": "s"}, headers=headers).json()
        == created.json()
    )
    rejected = client.post(
        f"/sync/{SYNC_KEY}", json={"secret": "wrong", "value": {}}, headers=headers
    )
    assert rejected.status_code == 403
    updated = client.post(
        f"/sync/{SYNC_KEY}",
        json={"secret": "s", "value": {"watchlists": ["VN30"]}},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["created_at"] == created.json()["created_at"]
    assert (
        client.get(f"/sync/{key}", params={"secret": "s"}, headers=headers).json() == updated.json()
    )
    with client.app.state.repo.connect() as con:
        assert con.execute("SELECT count(*) FROM sync_kv").fetchone()[0] == 1


def test_cors_and_cache_epoch(client):
    response = client.options(
        "/sync/example",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    client.get("/tickers?symbol=FPT&limit=1")
    cached = client.get("/tickers?symbol=FPT&limit=1")
    assert cached.headers["x-data-source"] == "in-memory"
    repo = client.app_instance.state.repo
    row = repo.read("vn", "FPT", "1D", limit=1)[0]
    repo.put([replace(row, high=row.high + 10, close=row.close + 5)])
    assert client.get("/tickers?symbol=FPT&limit=1").json()["FPT"][0]["close"] == row.close + 5
