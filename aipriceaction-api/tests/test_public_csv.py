import hashlib
import io
import json
from dataclasses import replace
from datetime import date

import pytest
from botocore.exceptions import ClientError

from aipriceaction_api.catalog import Catalog
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle, DataError, parse_time
from aipriceaction_api.public_csv import (
    PUBLIC_CSV_CACHE_CONTROL,
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


def test_public_catalog_uses_full_vn_directory_metadata(tmp_path):
    (tmp_path / "vn_tickers.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-10-20T00:00:00Z",
                "stocks": [
                    {
                        "symbol": "VCB",
                        "name": "Vietcombank",
                        "en_name": "Vietcombank",
                        "exchange": "HOSE",
                        "type": "stock",
                        "listing_status": "listed",
                        "icb_level2_name": "BANK",
                        "market_cap_vnd": 100,
                    }
                ],
            }
        )
    )
    (tmp_path / "watchlist.json").write_text(json.dumps({"vn": []}))
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


def test_configured_watchlist_overrides_packaged_catalog_watchlist(tmp_path):
    catalog_dir = tmp_path / "catalog"
    catalog_dir.mkdir()
    (catalog_dir / "watchlist.json").write_text(
        json.dumps({"vn": [{"symbol": "PACKAGED", "asset_type": "index"}]})
    )
    (catalog_dir / "vn_tickers.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "stocks": [
                    {
                        "symbol": "VCB",
                        "name": "Vietcombank",
                        "type": "stock",
                        "listing_status": "listed",
                        "market_cap_vnd": 100,
                    }
                ],
            }
        )
    )
    configured_watchlist = tmp_path / "configured-watchlist.json"
    configured_watchlist.write_text(
        json.dumps({"vn": [{"symbol": "ACTIVE", "asset_type": "index"}]})
    )
    settings = replace(
        Settings(), catalog_dir=catalog_dir, watchlist=configured_watchlist
    )
    catalog = Catalog(settings)

    assert catalog.names("vn")["ACTIVE"] == "ACTIVE"
    assert "PACKAGED" not in catalog.names("vn")
    assert catalog.groups("vn")["INDEX"] == ["ACTIVE"]


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.puts = []
        self.copies = []

    def etag(self, value):
        return '"' + hashlib.sha256(value["Body"]).hexdigest() + '"'

    def check(self, value, condition):
        if condition and (value is None or self.etag(value) != condition):
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "S3")

    def head_object(self, *, Bucket, Key):
        value = self.objects.get((Bucket, Key))
        if value is None:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {
            "Metadata": value["Metadata"],
            "ContentLength": len(value["Body"]),
            "ContentType": value["ContentType"],
            "ETag": self.etag(value),
            **{k: value[k] for k in ("CacheControl", "ContentLanguage") if k in value},
        }

    def put_object(self, **kwargs):
        existing = self.objects.get((kwargs["Bucket"], kwargs["Key"]))
        self.check(existing, kwargs.get("IfMatch"))
        if kwargs.get("IfNoneMatch") == "*" and existing is not None:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.puts.append(kwargs)
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs

    def get_object(self, *, Bucket, Key, **kwargs):
        value = self.objects[(Bucket, Key)]
        self.check(value, kwargs.get("IfMatch"))
        return {"Body": io.BytesIO(value["Body"])}

    def copy_object(self, **kwargs):
        value = self.objects[(kwargs["Bucket"], kwargs["Key"])]
        self.check(value, kwargs.get("CopySourceIfMatch"))
        self.check(value, kwargs.get("IfMatch"))
        self.copies.append(kwargs)
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = value | kwargs


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
    assert put["Metadata"] == {"content-hash": item.content_hash, "sha256": item.sha256}
    assert put["CacheControl"] == PUBLIC_CSV_CACHE_CONTROL
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
        "ContentLanguage": "en",
    }

    result = publish_object(s3, "one-bucket", item)

    assert not result["uploaded"] and result["verified"]
    assert s3.puts == []
    assert len(s3.copies) == 1
    assert s3.objects[("one-bucket", item.key)]["Body"] == item.body
    assert s3.objects[("one-bucket", item.key)]["ContentLanguage"] == "en"
    assert s3.objects[("one-bucket", item.key)]["CacheControl"] == PUBLIC_CSV_CACHE_CONTROL


def test_public_publisher_detects_change_hidden_by_rust_fingerprint():
    s3 = FakeS3()
    old = plan_candles([candle()], "vn", "VCB", "1D", day=date(2026, 10, 21))
    new = plan_candles([replace(candle(), high=2.6)], "vn", "VCB", "1D", day=date(2026, 10, 21))
    assert old.content_hash == new.content_hash and len(old.body) == len(new.body)
    s3.objects[("one-bucket", old.key)] = {
        "Body": old.body, "ContentType": "text/csv", "Metadata": {"content-hash": old.content_hash}
    }

    assert publish_object(s3, "one-bucket", new)["uploaded"]
    assert s3.objects[("one-bucket", new.key)]["Body"] == new.body


@pytest.mark.parametrize("same_content", [True, False])
def test_public_publisher_does_not_overwrite_concurrent_update(same_content):
    class RacingS3(FakeS3):
        def get_object(self, **kwargs):
            result = super().get_object(**kwargs)
            self.objects[(kwargs["Bucket"], kwargs["Key"])]["Body"] = b"newer writer"
            return result

    s3 = RacingS3()
    old = plan_candles([candle()], "vn", "VCB", "1D", day=date(2026, 10, 21))
    new = old if same_content else plan_candles(
        [replace(candle(), high=2.6)], "vn", "VCB", "1D", day=date(2026, 10, 21)
    )
    s3.objects[("one-bucket", old.key)] = {
        "Body": old.body, "ContentType": "text/csv", "Metadata": {"content-hash": old.content_hash}
    }
    with pytest.raises(ClientError, match="PreconditionFailed"):
        publish_object(s3, "one-bucket", new)
    assert s3.objects[("one-bucket", old.key)]["Body"] == b"newer writer"


def test_public_publisher_preserves_existing_timestamps_after_restore():
    s3 = FakeS3()
    old_rows = [candle(parse_time("2026-10-20")), candle()]
    old = plan_candles(old_rows, "vn", "VCB", "1D", year=2026)
    publish_object(s3, "one-bucket", old)

    incomplete = plan_candles([candle()], "vn", "VCB", "1D", year=2026)
    result = publish_object(s3, "one-bucket", incomplete)
    assert not result["uploaded"] and result["preserved_extra_rows"] == 1
    assert s3.objects[("one-bucket", old.key)]["Body"] == old.body
    assert len(s3.puts) == 1

    incompatible = plan_candles([replace(candle(), close=1.5)], "vn", "VCB", "1D", year=2026)
    with pytest.raises(DataError, match="1 existing timestamp.*2026-10-20"):
        publish_object(s3, "one-bucket", incompatible)

    # A complete replacement may update prices and add new days.
    updated = plan_candles(
        [replace(old_rows[0], close=1.5), old_rows[1], candle(parse_time("2026-10-22"))],
        "vn", "VCB", "1D", year=2026,
    )
    assert publish_object(s3, "one-bucket", updated)["uploaded"]
    assert s3.objects[("one-bucket", old.key)]["Body"] == updated.body


def test_matching_legacy_superset_keeps_all_rows_and_upgrades_cache_metadata():
    old = plan_candles([candle(parse_time("2026-10-20")), candle()], "vn", "VCB", "1D", year=2026)
    subset = plan_candles([candle()], "vn", "VCB", "1D", year=2026)
    s3 = FakeS3()
    s3.objects[("one-bucket", old.key)] = {
        "Body": old.body, "ContentType": "text/csv", "Metadata": {"content-hash": old.content_hash},
    }
    result = publish_object(s3, "one-bucket", subset)
    assert result["verified"] and result["preserved_extra_rows"] == 1 and not result["uploaded"]
    saved = s3.objects[("one-bucket", old.key)]
    assert saved["Body"] == old.body and saved["Metadata"]["sha256"] == old.sha256
    assert saved["CacheControl"] == PUBLIC_CSV_CACHE_CONTROL and len(s3.copies) == 1


def test_public_publisher_adds_new_rows_without_losing_matching_legacy_rows():
    old_rows = [candle(parse_time("2026-10-20")), candle()]
    new_rows = [candle(), candle(parse_time("2026-10-22"))]
    old = plan_candles(old_rows, "vn", "VCB", "1D", year=2026)
    new = plan_candles(new_rows, "vn", "VCB", "1D", year=2026)
    expected = plan_candles(old_rows + new_rows[1:], "vn", "VCB", "1D", year=2026)
    s3 = FakeS3()
    publish_object(s3, "one-bucket", old)

    result = publish_object(s3, "one-bucket", new)

    assert result["uploaded"] and result["verified"] and result["preserved_extra_rows"] == 1
    saved = s3.objects[("one-bucket", old.key)]
    assert saved["Body"] == expected.body
    assert saved["Metadata"]["sha256"] == expected.sha256
    assert saved["Metadata"]["content-hash"] == expected.content_hash
    assert not publish_object(s3, "one-bucket", new)["uploaded"]


def test_public_publisher_refuses_to_merge_non_overlapping_or_conflicting_rows():
    old = plan_candles(
        [candle(parse_time("2026-10-20")), candle()], "vn", "VCB", "1D", year=2026
    )
    s3 = FakeS3()
    publish_object(s3, "one-bucket", old)
    no_overlap = plan_candles([candle(parse_time("2026-10-22"))], "vn", "VCB", "1D", year=2026)
    conflicting = plan_candles(
        [replace(candle(), close=1.5), candle(parse_time("2026-10-22"))],
        "vn", "VCB", "1D", year=2026,
    )
    for candidate in (no_overlap, conflicting):
        with pytest.raises(DataError, match="existing timestamp"):
            publish_object(s3, "one-bucket", candidate)
        assert s3.objects[("one-bucket", old.key)]["Body"] == old.body


@pytest.mark.parametrize("overlap", [True, False])
def test_yahoo_hourly_year_can_append_without_overwriting_unverified_history(overlap):
    base = replace(candle(), source="yahoo", symbol="AAPL", interval="1h", provider="yahoo")
    old_rows = [replace(base, time=parse_time("2026-10-20T13:00:00Z")),
                replace(base, time=parse_time("2026-10-20T14:00:00Z"))]
    new_tail = replace(base, time=parse_time("2026-10-20T15:00:00Z"))
    incoming = [replace(old_rows[-1], close=1.5), new_tail] if overlap else [new_tail]
    old = plan_candles(old_rows, "yahoo", "AAPL", "1h", year=2026)
    candidate = plan_candles(incoming, "yahoo", "AAPL", "1h", year=2026)
    expected = plan_candles(old_rows + [new_tail], "yahoo", "AAPL", "1h", year=2026)
    s3 = FakeS3()
    publish_object(s3, "one-bucket", old)

    result = publish_object(s3, "one-bucket", candidate)

    assert result["uploaded"] and result["reconciliation_pending"]
    saved = s3.objects[("one-bucket", old.key)]
    assert saved["Body"] == expected.body
    assert saved["Metadata"]["sha256"] == expected.sha256
    assert saved["Metadata"]["content-hash"] == expected.content_hash
    assert saved["Metadata"]["reconciliation-pending"] == "true"
    if overlap:
        with pytest.raises(DataError, match="existing timestamp"):
            publish_object(s3, "one-bucket", candidate)
    else:
        assert publish_object(s3, "one-bucket", candidate)["reconciliation_pending"]
    assert s3.objects[("one-bucket", old.key)]["Body"] == expected.body
    assert not publish_object(s3, "one-bucket", expected)["reconciliation_pending"]
    assert "reconciliation-pending" not in s3.objects[("one-bucket", old.key)]["Metadata"]


def test_public_publisher_refuses_to_overwrite_unreadable_existing_csv():
    s3 = FakeS3()
    item = plan_candles([candle()], "vn", "VCB", "1D", day=date(2026, 10, 21))
    s3.objects[("one-bucket", item.key)] = {
        "Body": b"unreadable,old,format\n", "ContentType": "text/csv", "Metadata": {},
    }
    with pytest.raises(DataError, match="invalid rows"):
        publish_object(s3, "one-bucket", item)
    assert not s3.puts


def test_superset_check_rejects_material_changes_for_tiny_prices():
    s3 = FakeS3()
    tiny = replace(candle(), open=1e-9, high=3e-9, low=1e-9, close=2e-9)
    old = plan_candles([replace(tiny, time=parse_time("2026-10-20")), tiny], "vn", "VCB", "1D", year=2026)
    publish_object(s3, "one-bucket", old)
    changed = plan_candles([replace(tiny, close=2.5e-9)], "vn", "VCB", "1D", year=2026)
    with pytest.raises(DataError, match="existing timestamp"):
        publish_object(s3, "one-bucket", changed)
    assert s3.objects[("one-bucket", old.key)]["Body"] == old.body


def test_settings_default_to_one_parquet_prefix_and_public_export():
    settings = Settings()
    assert settings.s3_prefix == "parquet"
    assert settings.public_export_enabled is True
