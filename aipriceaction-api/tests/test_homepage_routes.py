from collections import Counter

from aipriceaction_api.homepage_routes import CORE_HOME_PAGE_URLS


def test_home_page_benchmark_keeps_exact_19_requests_and_browser_duplicates():
    counts = Counter(CORE_HOME_PAGE_URLS)

    assert len(CORE_HOME_PAGE_URLS) == 19
    assert counts[
        "/analysis/rrg?algorithm=jdk&mode=vn&trails=5&min_volume=1000&benchmark=VNINDEX&period=10&date=2026-10-21&ema=true"
    ] == 2
    assert counts["/tickers?limit=1&ma=false&mode=vn&format=csv"] == 2
    assert counts["/tickers?limit=1&ma=false&mode=crypto&format=csv"] == 2
    assert counts["/tickers?limit=1&ma=false&mode=yahoo&format=csv"] == 2
