from aipriceaction_api.redis_zset import RedisZsetReader, parse_member, zset_key

_UNSET = object()


def member(timestamp_ms, close, crawl_ts_ms=None, *, prefix=None):
    fields = [
        str(timestamp_ms),
        str(close - 1),
        str(close + 1),
        str(close - 2),
        str(close),
        "100",
    ]
    if crawl_ts_ms is not None:
        fields.append(str(crawl_ts_ms))
    if prefix is not None:
        fields[1] = prefix
    return "|".join(fields)


class FakeRedis:
    """Minimal in-memory ZSET client with Redis reverse-range ordering."""

    def __init__(self):
        self.zsets = {}
        self.calls = []
        self.fail_reads = False
        self.withscores_override = _UNSET

    def add(self, key, score, value):
        if isinstance(value, str):
            value = value.encode("utf-8")
        self.zsets.setdefault(key, []).append((score, value))

    def _ordered(self, key):
        # Redis orders equal-score members lexicographically; reverse commands
        # reverse both score and member order.
        return sorted(self.zsets.get(key, ()), key=lambda pair: (pair[0], pair[1]), reverse=True)

    def zrevrange(self, name, start, end, *, withscores=False):
        if withscores:
            self.calls.append(("zrevrange", name, start, end, True))
        else:
            self.calls.append(("zrevrange", name, start, end))
        if self.fail_reads:
            raise ConnectionError("fixture Redis is unavailable")
        if withscores and self.withscores_override is not _UNSET:
            return self.withscores_override
        ordered = self._ordered(name)
        if start < 0:
            start = max(0, len(ordered) + start)
        if end < 0:
            end = len(ordered) + end
        selected = ordered[start : end + 1] if end >= start else []
        if withscores:
            return [(value, score) for score, value in selected]
        return [value for _, value in selected]

    def zrevrangebyscore(self, name, max_score, min_score, *, start, num):
        self.calls.append(("zrevrangebyscore", name, max_score, min_score, start, num))
        if self.fail_reads:
            raise ConnectionError("fixture Redis is unavailable")
        assert min_score == "-inf"
        selected = [value for score, value in self._ordered(name) if score <= max_score]
        return selected[start:] if num < 0 else selected[start : start + num]


def test_parse_rust_member_bytes_and_legacy_six_field_member():
    row, crawl = parse_member(b"1000|10|12|9|11|123|2000", "1m")
    assert (row.timestamp_ms, row.open, row.high, row.low, row.close, row.volume) == (
        1000,
        10.0,
        12.0,
        9.0,
        11.0,
        123,
    )
    assert crawl == 2000

    legacy_row, legacy_crawl = parse_member("1000|10|12|9|11|123", "1D")
    assert legacy_row == row
    assert legacy_crawl is None


def test_parse_member_matches_rust_splitn_seven_on_extra_pipe():
    parsed = parse_member("1000|10|12|9|11|123|bad|extra", "1m")
    assert parsed is not None
    assert parsed[0].close == 11.0
    assert parsed[1] is None


def test_parse_member_skips_invalid_utf8_and_malformed_or_out_of_range_integers():
    assert parse_member(b"\xff", "1m") is None
    assert parse_member("1|10|12|9|11", "1m") is None
    assert parse_member("9223372036854775808|10|12|9|11|1", "1m") is None
    assert parse_member("1|10|12|9|11|9223372036854775808", "1m") is None
    assert parse_member("1|1_0|12|9|11|1", "1m") is None
    assert parse_member("1| 10|12|9|11|1", "1m") is None


def test_parse_member_enforces_chrono_datetime_millisecond_bounds():
    chrono_min_ms = -8_334_601_228_800_000
    chrono_max_ms = 8_210_266_876_799_999

    assert parse_member(f"{chrono_min_ms}|10|12|9|11|1", "1D") is not None
    assert parse_member(f"{chrono_max_ms}|10|12|9|11|1", "1D") is not None
    assert parse_member(f"{chrono_min_ms - 1}|10|12|9|11|1", "1D") is None
    assert parse_member(f"{chrono_max_ms + 1}|10|12|9|11|1", "1D") is None


def test_reader_uses_rust_key_and_returns_bounded_newest_first_tail():
    redis = FakeRedis()
    key = zset_key("vn", "FPT", "1D")
    redis.add(key, 1000, member(1000, 10, 1))
    redis.add(key, 3000, member(3000, 30, 3))
    redis.add(key, 2000, member(2000, 20, 2))

    result = RedisZsetReader(redis).read("vn", "FPT", "1D", limit=2)

    assert result is not None
    assert [row.timestamp_ms for row in result.rows] == [3000, 2000]
    assert result.raw_member_count == result.aligned_count == 2
    assert result.requested_limit == 2
    assert result.base_interval == "1D"
    assert redis.calls == [("zrevrange", key, 0, 1)]


def test_reader_deduplicates_by_latest_crawl_after_applying_raw_member_limit():
    redis = FakeRedis()
    key = zset_key("crypto", "BTCUSDT", "1m")
    redis.add(key, 1000, member(1000, 10, 10))
    redis.add(key, 1000, member(1000, 12, 20))
    redis.add(key, 2000, "malformed-member")
    redis.add(key, 500, member(500, 5, 5))

    # The newest three raw members include one malformed member and two
    # versions of the same timestamp; limit is applied before parsing/dedup.
    result = RedisZsetReader(redis).read("crypto", "BTCUSDT", "1m", limit=3)

    assert result is not None
    assert result.raw_member_count == 3
    assert result.aligned_count == 1
    assert [(row.timestamp_ms, row.close) for row in result.rows] == [(1000, 12.0)]


def test_reader_score_range_is_inclusive_and_remains_bounded():
    redis = FakeRedis()
    key = zset_key("yahoo", "AAPL", "1D")
    for timestamp in (1000, 2000, 3000, 4000):
        redis.add(key, timestamp, member(timestamp, timestamp / 100))

    result = RedisZsetReader(redis).read("yahoo", "AAPL", "1D", limit=2, max_score_ms=3000)

    assert result is not None
    assert [row.timestamp_ms for row in result.rows] == [3000, 2000]
    assert redis.calls == [("zrevrangebyscore", key, 3000, "-inf", 0, 2)]


def test_reader_preserves_signed_raw_limit_redis_semantics_and_metadata():
    redis = FakeRedis()
    key = zset_key("vn", "FPT", "1D")
    redis.add(key, 1000, member(1000, 10))
    redis.add(key, 2000, member(2000, 20))
    redis.add(key, 3000, member(3000, 30))
    reader = RedisZsetReader(redis)

    no_end = reader.read_signed("vn", "FPT", "1D", limit=-1)
    assert no_end is not None
    assert [row.timestamp_ms for row in no_end.rows] == [3000, 2000]
    assert no_end.raw_member_count == 2
    assert no_end.requested_limit == -1
    assert redis.calls[-1] == ("zrevrange", key, 0, -2)

    end_bounded = reader.read_signed("vn", "FPT", "1D", limit=-1, max_score_ms=2000)
    assert end_bounded is not None
    assert [row.timestamp_ms for row in end_bounded.rows] == [2000, 1000]
    assert end_bounded.raw_member_count == 2
    assert end_bounded.requested_limit == -1
    assert redis.calls[-1] == ("zrevrangebyscore", key, 2000, "-inf", 0, -1)

    no_end_zero = reader.read_signed("vn", "FPT", "1D", limit=0)
    assert no_end_zero is not None
    assert [row.timestamp_ms for row in no_end_zero.rows] == [3000, 2000, 1000]
    assert no_end_zero.requested_limit == 0
    assert redis.calls[-1] == ("zrevrange", key, 0, -1)

    assert reader.read_signed("vn", "FPT", "1D", limit=0, max_score_ms=2000) is None
    assert redis.calls[-1] == ("zrevrangebyscore", key, 2000, "-inf", 0, 0)


def test_reader_omits_empty_or_unparseable_zsets_and_handles_missing_client():
    redis = FakeRedis()
    key = zset_key("vn", "FPT", "1D")
    redis.add(key, 1000, "not-an-ohlcv-member")

    reader = RedisZsetReader(redis)
    assert reader.read("vn", "MISSING", "1D", limit=1) is None
    assert reader.read("vn", "FPT", "1D", limit=1) is None
    assert RedisZsetReader(None).read("vn", "FPT", "1D", limit=1) is None


def test_reader_treats_redis_command_failure_as_cache_miss():
    redis = FakeRedis()
    redis.fail_reads = True

    assert RedisZsetReader(redis).read("vn", "FPT", "1D", limit=1) is None


def test_covers_start_uses_oldest_score_and_includes_equal_boundary():
    redis = FakeRedis()
    key = zset_key("vn", "FPT", "1D")
    # Deliberately make the member timestamp differ: Rust checks the ZSET
    # score, not the timestamp embedded in the OHLCV member.
    redis.add(key, 1000, member(5000, 10, 1))
    redis.add(key, 3000, member(3000, 30, 3))
    reader = RedisZsetReader(redis)

    assert reader.covers_start("vn", "FPT", "1D", 1000)
    assert reader.covers_start("vn", "FPT", "1D", 1001)
    assert not reader.covers_start("vn", "FPT", "1D", 999)
    assert redis.calls == [
        ("zrevrange", key, -1, -1, True),
        ("zrevrange", key, -1, -1, True),
        ("zrevrange", key, -1, -1, True),
    ]


def test_covers_start_returns_false_for_missing_or_unavailable_cache():
    redis = FakeRedis()
    reader = RedisZsetReader(redis)

    assert not reader.covers_start("vn", "MISSING", "1D", 1000)
    assert not RedisZsetReader(None).covers_start("vn", "FPT", "1D", 1000)

    redis.fail_reads = True
    assert not reader.covers_start("vn", "FPT", "1D", 1000)


def test_covers_start_rejects_invalid_score_metadata_and_result_shapes():
    redis = FakeRedis()
    reader = RedisZsetReader(redis)
    invalid_responses = [
        None,
        [],
        [b"member"],
        [(b"member",)],
        [(b"member", b"not-a-score")],
        {b"member": 1000},
        [(b"member", float("nan"))],
        [(b"member", float("inf"))],
        [(b"member", True)],
        [(b"member", 2**63)],
    ]

    for response in invalid_responses:
        redis.withscores_override = response
        assert not reader.covers_start("vn", "FPT", "1D", 1000)

    redis.withscores_override = [(b"member", 1000.9)]
    assert reader.covers_start("vn", "FPT", "1D", 1000)

    # The flat shape is also accepted because Fred's Rust client returns it.
    redis.withscores_override = [b"member", b"1000"]
    assert reader.covers_start("vn", "FPT", "1D", 1000)
    assert not reader.covers_start("vn", "FPT", "1D", True)


def test_reader_rejects_nonpositive_limit_before_calling_client():
    redis = FakeRedis()
    reader = RedisZsetReader(redis)

    for limit in (0, -1):
        try:
            reader.read("vn", "FPT", "1D", limit=limit)
        except ValueError as error:
            assert str(error) == "limit must be positive"
        else:
            raise AssertionError("nonpositive read limit should be rejected")

    assert redis.calls == []
