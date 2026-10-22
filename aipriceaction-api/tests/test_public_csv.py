import json
from dataclasses import replace
from datetime import date

import pytest
from botocore.exceptions import ClientError

from aipriceaction_api.catalog import Catalog
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, parse_time
from aipriceaction_api.public_csv import (
    daily_key,
    period_bounds,
    plan_candles,
    plan_catalog,
    publish_object,
    rust_content_hash,
    yearly_key,
)
from aipriceaction_api.storage import Repository


def candle(time=None, *, symbol="VCB", interval="1D"):
    if time is None:
        time = parse_time("2026-10-21")
    return Candle("vn", symbol, interval, time, 1.0, 2.5, 0.5, 2.0, 42, "test", "r1")


def test_public_csv_paths_and_headerless_rust_row_format():
    item = plan_candles([candle()], "vn", "VCB", "1D", day=date(2026, 10, 21))

    assert item.key == "ohlcv/vn/VCB/1D/VCB-1D-2026-10-21.csv"
    assert item.body == b"2026-10-21 00:00:00,1,2.5,0.5,2,42\n"
    assert item.content_type == "text/csv"
    assert item.rows == 1 and len(item.sha256) == 64
    assert daily_key("vn", "VCB", "1D", date(2026, 10, 21)) == item.key
    assert yearly_key("vn", "VCB", "1D", 2026) == (
        "ohlcv/vn/VCB/yearly/VCB-1D-2026.csv"
    )
    assert yearly_key("vn", "VCB", "1h", 2026) == (
        "ohlcv/vn/VCB/yearly/VCB-1h-2026.csv"
    )


@pytest.mark.parametrize(
    ("source", "symbol"),
    (("vn", "ACB"), ("crypto", "BTCUSDT"), ("sjc", "SJC-GOLD"), ("yahoo", "AAPL")),
)
def test_all_market_sources_stay_under_the_existing_ohlcv_root(source, symbol):
    assert daily_key(source, symbol, "1D", date(2026, 10, 21)) == (
        f"ohlcv/{source}/{symbol}/1D/{symbol}-1D-2026-10-21.csv"
    )
    assert yearly_key(source, symbol, "1D", 2026) == (
        f"ohlcv/{source}/{symbol}/yearly/{symbol}-1D-2026.csv"
    )


def test_production_daily_sample_matches_rust_csv_and_content_hash():
    row = Candle(
        "vn",
        "ACB",
        "1D",
        parse_time("2014-05-13"),
        1975.5,
        2001.84,
        1975.5,
        1975.5,
        118603,
        "production-sample",
        "observed",
    )
    item = plan_candles(
        [row], "vn", "ACB", "1D", day=date(2014, 5, 13)
    )
    assert item.key == "ohlcv/vn/ACB/1D/ACB-1D-2014-05-13.csv"
    assert item.body == b"2014-05-13 00:00:00,1975.5,2001.84,1975.5,1975.5,118603\n"
    assert item.sha256 == "a590961836a0897fd03a26d307e3246336157d7796f32fc67dccc21aed74715d"
    assert item.content_hash == "b07b63c2f48ffe4b504d5056ea44cf82ab1dc78c9d3901686c8537c2ac3e90e8"
    assert rust_content_hash([row]) == item.content_hash


def test_public_csv_requires_ordered_matching_nonempty_rows():
    with pytest.raises(DataError, match="ordered, unique"):
        plan_candles(
            [candle(), candle(time=parse_time("2026-10-20"))],
            "vn",
            "VCB",
            "1D",
            day=date(2026, 10, 21),
        )
    with pytest.raises(DataError, match="yearly files only"):
        yearly_key("vn", "VCB", "1m", 2026)
    with pytest.raises(DataError, match="empty"):
        plan_candles([], "vn", "VCB", "1D", day=date(2026, 10, 21))
    with pytest.raises(DataError, match="UTC date"):
        plan_candles([candle()], "vn", "VCB", "1D", day=date(2026, 10, 20))
    with pytest.raises(DataError, match="UTC year"):
        plan_candles([candle()], "vn", "VCB", "1D", year=2025)


def test_price_fields_use_rust_fixed_f64_display():
    row = Candle(
        "vn",
        "VCB",
        "1D",
        parse_time("2026-10-21"),
        1e-7,
        1e20,
        1e-6,
        100.0,
        1,
        "test",
        "r1",
    )
    item = plan_candles([row], "vn", "VCB", "1D", day=date(2026, 10, 21))
    assert item.body == (
        b"2026-10-21 00:00:00,0.0000001,100000000000000000000,0.000001,100,1\n"
    )


def test_public_period_bounds_are_utc_and_inclusive():
    start, end = period_bounds(day=date(2026, 10, 21))
    assert end - start == 86_399
    year_start, year_end = period_bounds(year=2024)
    assert year_end - year_start + 1 == 366 * 86_400


def test_public_catalog_matches_legacy_metadata_shape(tmp_path):
    (tmp_path / "vn.csv").write_text(
        "ticker,organ_name,en_organ_name,exchange,type\n"
        "VCB,Vietcombank,Vietcombank,HOSE,stock\n",
        encoding="utf-8",
    )
    (tmp_path / "ticker_group.json").write_text(json.dumps({"BANK": ["VCB"]}))
    (tmp_path / "binance_tickers.json").write_text(
        json.dumps({"data": [{"symbol": "BTCUSDT", "name": "Bitcoin"}]})
    )
    (tmp_path / "global_tickers.json").write_text(
        json.dumps({"data": [{"symbol": "AAPL", "name": "Apple", "category": "Stock"}]})
    )
    (tmp_path / "sjc_tickers.json").write_text(
        json.dumps({"data": [{"symbol": "SJC-GOLD", "name": "SJC", "category": "Gold"}]})
    )
    settings = replace(Settings(), database=tmp_path / "db.sqlite3", catalog_dir=tmp_path)
    repo = Repository(settings.database)
    repo.initialize()
    repo.register("vn", "VCB", "Vietcombank")
    catalog = Catalog(settings)

    item = plan_catalog(catalog, repo)
    records = json.loads(item.body)
    by_identity = {(row["source"], row["ticker"]): row for row in records}

    assert item.key == "meta/tickers.json"
    assert item.content_type == "application/json"
    assert by_identity[("vn", "VCB")] == {
        "source": "vn",
        "ticker": "VCB",
        "name": "Vietcombank",
        "exchange": "HOSE",
        "type": "stock",
        "category": None,
        "group": "BANK",
    }
    assert by_identity[("crypto", "BTCUSDT")]["group"] == "CRYPTO_TOP_100"
    assert by_identity[("yahoo", "AAPL")]["category"] == "Stock"
    assert by_identity[("sjc", "SJC-GOLD")]["group"] == "Gold"


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.puts = []

    def head_object(self, *, Bucket, Key):
        value = self.objects.get((Bucket, Key))
        if value is None:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {
            "Metadata": value["Metadata"],
            "ContentLength": len(value["Body"]),
            "ContentType": value["ContentType"],
        }

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs


def test_public_publisher_sets_sdk_hash_and_skips_unchanged_objects():
    item = plan_candles([candle()], "vn", "VCB", "1D", day=date(2026, 10, 21))
    s3 = FakeS3()

    first = publish_object(s3, "one-bucket", item)
    second = publish_object(s3, "one-bucket", item)

    assert first["uploaded"] and first["verified"]
    assert not second["uploaded"] and second["verified"]
    assert len(s3.puts) == 1
    put = s3.puts[0]
    assert put["Key"] == "ohlcv/vn/VCB/1D/VCB-1D-2026-10-21.csv"
    assert put["ContentType"] == "text/csv"
    assert put["Metadata"] == {"content-hash": item.content_hash}
    assert "ACL" not in put


def test_public_publisher_skips_unchanged_rust_published_object():
    item = plan_candles(
        [
            Candle(
                "vn",
                "ACB",
                "1D",
                parse_time("2014-05-13"),
                1975.5,
                2001.84,
                1975.5,
                1975.5,
                118603,
                "production-sample",
                "observed",
            )
        ],
        "vn",
        "ACB",
        "1D",
        day=date(2014, 5, 13),
    )
    s3 = FakeS3()
    s3.objects[("one-bucket", item.key)] = {
        "Body": item.body,
        "ContentType": "text/csv",
        "Metadata": {"content-hash": item.content_hash},
    }

    result = publish_object(s3, "one-bucket", item)

    assert not result["uploaded"] and result["verified"]
    assert s3.puts == []


def test_settings_default_to_one_parquet_prefix_and_public_export():
    settings = Settings()
    assert settings.s3_prefix == "parquet"
    assert settings.public_export_enabled is True
