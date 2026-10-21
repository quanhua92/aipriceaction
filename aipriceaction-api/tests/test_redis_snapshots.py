import pytest

from aipriceaction_api.redis_snapshots import (
    joined_snapshot_response,
    read_redis_snapshot,
    redis_snapshot_field,
    redis_snapshot_key,
    validate_joined_data_rows,
    validate_stock_data_rows,
    write_redis_snapshot,
)


class FakeRedis:
    def __init__(self, value):
        self.value = value
        self.calls = []

    def hget(self, name, key):
        self.calls.append((name, key))
        return self.value


def test_key_and_field_match_rust_snapshot_layout():
    assert redis_snapshot_key("vn", "VCB", "1D") == "snap:vn:VCB:1D"
    assert redis_snapshot_field(25) == "25:sma"
    assert redis_snapshot_field(25, ema=True) == "25:ema"
    assert redis_snapshot_field(25, joined=True) == "25:sma:joined"
    assert redis_snapshot_field(25, ema=True, joined=True) == "25:ema:joined"


@pytest.mark.parametrize(
    ("ema", "joined", "field"),
    [
        (False, False, "25:sma"),
        (True, False, "25:ema"),
        (False, True, "25:sma:joined"),
        (True, True, "25:ema:joined"),
    ],
)
def test_reader_uses_exact_key_and_field_and_decodes_utf8_json(ema, joined, field):
    redis = FakeRedis(b'[{"time":"2026-09-01","close":12.5}]')

    rows = read_redis_snapshot(redis, "vn", "VCB", "1D", 25, ema=ema, joined=joined)

    assert rows == [{"time": "2026-09-01", "close": 12.5}]
    assert redis.calls == [("snap:vn:VCB:1D", field)]


def test_reader_accepts_string_payload_and_preserves_row_values():
    redis = FakeRedis('[{"nested":[1,2]}, ["also", "a row"]]')

    assert read_redis_snapshot(redis, "crypto", "BTCUSDT", "1D", 1) == [
        {"nested": [1, 2]},
        ["also", "a row"],
    ]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        b"not-json",
        b"\xff",
        "[]",
        "null",
        "{}",
        '"row"',
        "1",
        123,
    ],
)
def test_missing_corrupt_empty_or_non_list_payload_is_a_miss(payload):
    redis = FakeRedis(payload)

    assert read_redis_snapshot(redis, "vn", "VCB", "1D", 25) is None
    assert redis.calls == [("snap:vn:VCB:1D", "25:sma")]


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_reader_rejects_nonstandard_json_numeric_constants(value):
    redis = FakeRedis('[{"extra":' + value + "}]")

    assert read_redis_snapshot(redis, "vn", "VCB", "1D", 25) is None


@pytest.mark.parametrize("payload", [r'[{"extra":1e400}]', r'[{"extra":-1e400}]'])
def test_reader_rejects_exponents_outside_finite_float_range(payload):
    assert read_redis_snapshot(FakeRedis(payload), "vn", "VCB", "1D", 25) is None


def test_reader_rejects_integer_overflow_in_an_ignored_field():
    payload = '[{"extra":' + "9" * 400 + "}]"

    assert read_redis_snapshot(FakeRedis(payload), "vn", "VCB", "1D", 25) is None


def test_reader_rejects_json_nesting_beyond_serde_limit_in_ignored_field():
    nested_value = "[" * 130 + "0" + "]" * 130
    payload = '[{"extra":' + nested_value + "}]"

    assert read_redis_snapshot(FakeRedis(payload), "vn", "VCB", "1D", 25) is None


@pytest.mark.parametrize(
    "payload",
    [
        r'[{"extra":"\ud800"}]',
        r'[{"extra":"\udfff"}]',
        r'[{"\ud800":"unknown key"}]',
    ],
)
def test_reader_rejects_unpaired_surrogates_even_in_ignored_fields(payload):
    assert read_redis_snapshot(FakeRedis(payload), "vn", "VCB", "1D", 25) is None


def test_reader_checks_malformed_values_overwritten_by_duplicate_unknown_keys():
    payload = '[{"extra":"' + chr(92) + 'ud800","extra":2}]'

    assert read_redis_snapshot(FakeRedis(payload), "vn", "VCB", "1D", 25) is None


def test_reader_accepts_valid_escaped_surrogate_pair():
    rows = read_redis_snapshot(FakeRedis(r'[{"extra":"\ud83d\ude00"}]'), "vn", "VCB", "1D", 25)

    assert rows == [{"extra": "😀"}]


def test_reader_preserves_negative_zero_as_float_so_integer_volumes_are_rejected():
    stock_json = (
        '[{"time":"2026-09-01","open":1,"high":2,"low":0,"close":1,"volume":-0,"symbol":"VCB"}]'
    )
    joined_json = (
        '[{"ticker":"VCB","time":"2026-09-01T00:00:00Z","open":1,'
        '"high":2,"low":0,"close":1,"volume":-0}]'
    )
    stock_rows = read_redis_snapshot(FakeRedis(stock_json), "vn", "VCB", "1D", 25)
    joined_rows = read_redis_snapshot(FakeRedis(joined_json), "vn", "VCB", "1D", 25, joined=True)

    assert validate_stock_data_rows(stock_rows) is None
    assert validate_joined_data_rows(joined_rows) is None


@pytest.mark.parametrize("field", ["open", "high", "low", "close"])
def test_stock_and_joined_snapshots_reject_null_required_ohlc(field):
    stock_row = {
        "time": "2026-09-01",
        "open": 1,
        "high": 2,
        "low": 0,
        "close": 1,
        "volume": 2,
        "symbol": "VCB",
        "ma20_score": None,
    }
    joined_row = {
        "ticker": "VCB",
        "time": "2026-09-01T00:00:00Z",
        "open": 1,
        "high": 2,
        "low": 0,
        "close": 1,
        "volume": 2,
        "ma20_score": None,
    }
    stock_row[field] = None
    joined_row[field] = None

    assert validate_stock_data_rows([stock_row]) is None
    assert validate_joined_data_rows([joined_row]) is None


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-09-01X00:00:00+00:00",
        "2026-09-01T00:00+00:00",
        "2026-09-01T24:00:00Z",
        "2026-09-01T00:00:61Z",
        "2026-09-01T00:00:00+24:00",
    ],
)
def test_joined_snapshot_rejects_timestamps_outside_chrono_parser(timestamp):
    row = {
        "ticker": "VCB",
        "time": timestamp,
        "open": 1,
        "high": 2,
        "low": 0,
        "close": 1,
        "volume": 2,
    }

    assert validate_joined_data_rows([row]) is None


@pytest.mark.parametrize(
    "timestamp",
    [
        " 2026-09-01T00:00:00Z",
        "2026-09-01T 00:00:00Z",
        "2026-09-01 00:00:00+00:00",
        "2026-9-1t1:2:3z",
        "2026 - 9 - 1T1 : 2 : 3 UTC",
        "2026-09-01T00:00:00+0000",
        "2026-09-01T00:00:00+00 00",
    ],
)
def test_joined_snapshot_accepts_chrono_relaxed_timestamp_forms(timestamp):
    row = {
        "ticker": "VCB",
        "time": timestamp,
        "open": 1,
        "high": 2,
        "low": 0,
        "close": 1,
        "volume": 2,
    }

    assert validate_joined_data_rows([row]) is not None


def test_joined_snapshot_accepts_chrono_leap_second_and_converts_utc_date():
    row = {
        "ticker": "VCB",
        "time": "2026-09-01T23:59:60Z",
        "open": 1,
        "high": 2,
        "low": 0,
        "close": 1,
        "volume": 2,
    }

    validated = validate_joined_data_rows([row])

    assert validated is not None
    assert joined_snapshot_response(validated[0])["time"] == "2026-09-01"
    assert joined_snapshot_response(validated[0], daily=False)["time"] == ("2026-09-01T23:59:60")


@pytest.mark.parametrize(
    "timestamp",
    [
        "0001-01-01T00:00:00+23:59",
        "9999-12-31T23:59:59-23:59",
    ],
)
def test_joined_snapshot_misses_when_utc_normalization_exceeds_python_year_range(timestamp):
    row = {
        "ticker": "VCB",
        "time": timestamp,
        "open": 1,
        "high": 2,
        "low": 0,
        "close": 1,
        "volume": 2,
    }

    assert validate_joined_data_rows([row]) is None


def test_redis_client_errors_propagate_instead_of_becoming_cache_misses():
    class UnavailableRedis:
        def hget(self, name, key):
            raise ConnectionError("Redis unavailable")

    with pytest.raises(ConnectionError, match="Redis unavailable"):
        read_redis_snapshot(UnavailableRedis(), "vn", "VCB", "1D", 25)


def test_stock_data_snapshot_validation_matches_typed_rust_rows():
    row = {
        "time": "2026-09-01",
        "open": 1,
        "high": 2,
        "low": 0.5,
        "close": 1.5,
        "volume": 2,
        "symbol": "VCB",
        "ma20": None,
        "unknown": "ignored by serde",
    }

    assert validate_stock_data_rows([row]) == [
        {
            "time": "2026-09-01",
            "open": 1,
            "high": 2,
            "low": 0.5,
            "close": 1.5,
            "volume": 2,
            "symbol": "VCB",
        }
    ]
    assert type(validate_stock_data_rows([row])[0]["open"]) is float


def test_snapshot_validation_normalizes_integer_prices_and_rejects_float_overflow():
    stock_row = {
        "time": "2026-09-01",
        "open": 10**1000,
        "high": 2,
        "low": 0,
        "close": 1,
        "volume": 2,
        "symbol": "VCB",
    }
    ordinary_row = stock_row | {"open": 1}

    assert validate_stock_data_rows([stock_row]) is None
    normalized = validate_stock_data_rows([ordinary_row])
    assert normalized is not None
    assert type(normalized[0]["open"]) is float


def test_stock_snapshot_rejects_duplicate_typed_field_but_ignores_unknown_fields():
    duplicate_field = read_redis_snapshot(
        FakeRedis(
            '[{"time":"2026-09-01","open":1,"high":2,"low":0.5,'
            '"close":1.5,"close":1.6,"volume":2,"symbol":"VCB"}]'
        ),
        "vn",
        "VCB",
        "1D",
        25,
    )
    duplicate_unknown = read_redis_snapshot(
        FakeRedis(
            '[{"time":"2026-09-01","open":1,"high":2,"low":0.5,'
            '"close":1.5,"volume":2,"symbol":"VCB",'
            '"extra":1,"extra":2}]'
        ),
        "vn",
        "VCB",
        "1D",
        25,
    )

    assert validate_stock_data_rows(duplicate_field) is None
    assert validate_stock_data_rows(duplicate_unknown) is not None


@pytest.mark.parametrize(
    "row",
    [
        {"time": "2026-09-01"},
        {
            "time": "2026-09-01",
            "open": 1,
            "high": 2,
            "low": 0.5,
            "close": 1.5,
            "volume": True,
            "symbol": "VCB",
        },
        {
            "time": "2026-09-01",
            "open": 1,
            "high": float("nan"),
            "low": 0.5,
            "close": 1.5,
            "volume": 2,
            "symbol": "VCB",
        },
        {
            "time": "2026-09-01",
            "open": 1,
            "high": 2,
            "low": 0.5,
            "close": 1.5,
            "volume": -1,
            "symbol": "VCB",
        },
    ],
)
def test_incompatible_stock_data_snapshot_rows_are_misses(row):
    assert validate_stock_data_rows([row]) is None


def test_snapshot_writer_uses_rust_hash_layout_and_ttl():
    class WritableRedis(FakeRedis):
        def __init__(self):
            super().__init__(None)
            self.expiry = None

        def hset(self, name, key=None, value=None, *, mapping=None):
            self.calls = (name, mapping)

        def expire(self, name, time):
            self.expiry = (name, time)

    redis = WritableRedis()
    row = {
        "time": "2026-09-01",
        "open": 1,
        "high": 2,
        "low": 0.5,
        "close": 1.5,
        "volume": 2,
        "symbol": "VCB",
    }

    write_redis_snapshot(redis, "vn", "VCB", "1D", 25, [row], ema=True)

    assert redis.calls[0] == "snap:vn:VCB:1D"
    assert redis.calls[1].keys() == {"25:ema"}
    assert redis.expiry == ("snap:vn:VCB:1D", 30)
