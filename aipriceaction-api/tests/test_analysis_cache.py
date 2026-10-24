from aipriceaction_api.analysis_cache import VersionedAnalysisCache


def test_version_change_invalidates_and_cached_values_are_isolated():
    cache = VersionedAnalysisCache()
    original = {"data": {"tickers": [{"symbol": "VCB", "value": 100}]}}
    version = (("VCB", 4), ("VNINDEX", 8))
    cache.put("rrg", version, original)

    first = cache.get("rrg", version)
    first["data"]["tickers"][0]["value"] = -1

    assert cache.get("rrg", version) == original
    assert cache.size_bytes > 0
    assert cache.get("rrg", (("VCB", 5), ("VNINDEX", 8))) is None
    assert cache.size_bytes == 0


def test_analysis_cache_timing_reports_absent_and_version_change_misses():
    cache = VersionedAnalysisCache()
    stats = {}

    assert cache.get("rrg", 1, timing_stats=stats) is None
    assert stats["cache_miss_reason"] == "absent"

    cache.put("rrg", 1, {"value": 1})
    stats.clear()
    assert cache.get("rrg", 2, timing_stats=stats) is None
    assert stats["cache_miss_reason"] == "input_version_changed"


def test_analysis_cache_evicts_least_recent_entries_with_a_small_lru():
    cache = VersionedAnalysisCache(max_entries=2)
    cache.put("old", 1, {"value": 1})
    cache.put("kept", 1, {"value": 2})
    assert cache.get("old", 1) == {"value": 1}

    cache.put("new", 1, {"value": 3})

    assert cache.get("old", 1) == {"value": 1}
    assert cache.get("kept", 1) is None
    assert cache.get("new", 1) == {"value": 3}


def test_oversized_analysis_result_is_not_cached():
    cache = VersionedAnalysisCache(max_bytes=256)

    cache.put("large", 1, {"payload": "x" * 1024})

    assert cache.get("large", 1) is None
    assert cache.size_bytes == 0
