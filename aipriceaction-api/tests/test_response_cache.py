from aipriceaction_api.response_cache import TickersResponseCache


def test_cache_keeps_entry_at_exact_ttl_and_expires_after_it():
    now = [10.0]
    cache = TickersResponseCache(ttl=5, clock=lambda: now[0])
    cache.put("query", {"FPT": [{"close": 100}]})

    now[0] = 15.0
    assert cache.get("query") == {"FPT": [{"close": 100}]}
    now[0] = 15.0001
    assert cache.get("query") is None


def test_cache_hits_do_not_refresh_oldest_entry_order():
    now = [0.0]
    cache = TickersResponseCache(ttl=5, max_entries=2, clock=lambda: now[0])
    cache.put("old", {"old": []})
    now[0] = 1.0
    cache.put("new", {"new": []})

    assert cache.get("old") == {"old": []}
    now[0] = 2.0
    cache.put("third", {"third": []})

    assert cache.get("old") is None
    assert cache.get("new") == {"new": []}
    assert cache.get("third") == {"third": []}


def test_duplicate_put_at_capacity_still_evicts_oldest_entry():
    now = [0.0]
    cache = TickersResponseCache(ttl=5, max_entries=2, clock=lambda: now[0])
    cache.put("old", {"old": []})
    now[0] = 1.0
    cache.put("kept", {"kept": [1]})
    now[0] = 2.0
    cache.put("kept", {"kept": [2]})

    assert cache.get("old") is None
    assert cache.get("kept") == {"kept": [2]}
    assert len(cache.store) == 1
