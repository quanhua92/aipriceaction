from aipriceaction_api.response_cache import TickersResponseCache


def test_cache_keeps_entry_at_exact_ttl_and_expires_after_it():
    now = [10.0]
    cache = TickersResponseCache(ttl=5, clock=lambda: now[0])
    cache.put("query", {"FPT": [{"close": 100}]})

    now[0] = 15.0
    assert cache.get("query") == {"FPT": [{"close": 100}]}
    now[0] = 15.0001
    assert cache.get("query") is None


def test_cache_hits_refresh_lru_order_without_refreshing_ttl():
    now = [0.0]
    cache = TickersResponseCache(ttl=5, max_entries=2, clock=lambda: now[0])
    cache.put("old", {"old": []})
    now[0] = 1.0
    cache.put("new", {"new": []})

    assert cache.get("old") == {"old": []}
    now[0] = 2.0
    cache.put("third", {"third": []})

    assert cache.get("old") == {"old": []}
    assert cache.get("new") is None
    assert cache.get("third") == {"third": []}
    now[0] = 5.0001
    assert cache.get("old") is None


def test_replacing_entry_at_capacity_preserves_other_lru_entry():
    now = [0.0]
    cache = TickersResponseCache(ttl=5, max_entries=2, clock=lambda: now[0])
    cache.put("old", {"old": []})
    now[0] = 1.0
    cache.put("kept", {"kept": [1]})
    now[0] = 2.0
    cache.put("kept", {"kept": [2]})

    assert cache.get("old") == {"old": []}
    assert cache.get("kept") == {"kept": [2]}
    assert len(cache.store) == 2


def test_cache_evicts_least_recent_entries_to_stay_within_memory_budget():
    old = {"old": []}
    kept = {"new": []}
    cache = TickersResponseCache(
        ttl=60, max_entries=4, max_bytes=2 * TickersResponseCache._size(old) - 1
    )
    cache.put("old", old)
    cache.put("kept", kept)

    assert cache.stored_bytes <= cache.max_bytes
    assert cache.get("old") is None
    assert cache.get("kept") == kept


def test_oversize_response_is_not_retained():
    cache = TickersResponseCache(ttl=60, max_bytes=256)
    cache.put("large", {"body": "x" * 1024})

    assert cache.get("large") is None
    assert cache.stored_bytes == 0


def test_large_ticker_windows_skip_recursive_response_cache_sizing():
    cache = TickersResponseCache(ttl=60, max_bytes=20 * 1024 * 1024)
    data = {"VNINDEX": [{}] * (cache._MAX_CACHED_ROWS + 1)}

    cache.put("large-window", data)

    assert cache.get("large-window") is None
    assert cache.stored_bytes == 0
