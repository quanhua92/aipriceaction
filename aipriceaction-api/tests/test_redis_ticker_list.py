import pytest

from aipriceaction_api.catalog import RUST_TICKER_LIST_KEY, Catalog


class FakeRedis:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error
        self.calls = []

    def get(self, key):
        self.calls.append(key)
        if self.error:
            raise self.error
        return self.value


def catalog():
    return Catalog(settings=None)


def test_reads_rust_ticker_info_json_from_meta_key_and_ignores_extra_fields():
    redis = FakeRedis(
        b'[{"source":"vn","ticker":"FPT","extra":{"revision":2}},'
        b'{"source":"crypto","ticker":"BTCUSDT"}]'
    )

    assert catalog().redis_ticker_list(redis) == [
        {"source": "vn", "ticker": "FPT"},
        {"source": "crypto", "ticker": "BTCUSDT"},
    ]
    assert redis.calls == [RUST_TICKER_LIST_KEY]


def test_valid_empty_json_list_is_distinct_from_a_cache_miss():
    redis = FakeRedis("[]")

    assert catalog().redis_ticker_list(redis) == []
    assert catalog().redis_ticker_list(None) is None
    assert catalog().redis_ticker_list(FakeRedis(None)) is None


@pytest.mark.parametrize(
    "value",
    [
        b"\xff",
        "not-json",
        "null",
        "{}",
        "[null]",
        '[{"source":"vn"}]',
        '[{"ticker":"FPT"}]',
        '[{"source":null,"ticker":"FPT"}]',
        '[{"source":"vn","ticker":7}]',
        '[{"source":"vn","ticker":"FPT","source":"crypto"}]',
        '[{"source":"vn","ticker":"FPT","ticker":"VCB"}]',
        "[NaN]",
    ],
)
def test_missing_or_unreadable_or_incompatible_cache_values_return_none(value):
    assert catalog().redis_ticker_list(FakeRedis(value)) is None


def test_redis_client_failure_is_a_miss_like_rust_reader():
    redis = FakeRedis(error=ConnectionError("Redis unavailable"))

    assert catalog().redis_ticker_list(redis) is None
    assert redis.calls == [RUST_TICKER_LIST_KEY]
