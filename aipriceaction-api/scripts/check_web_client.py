"""Read-only public-web rehearsal with an isolated browser and local API routing.

Playwright and its Chromium headless shell are optional verification tools;
neither is an application dependency. Production routing and browser profiles
are untouched. Other requests retain the public website's normal destinations.
"""

import argparse
import csv
import io
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Error as BrowserError
from playwright.sync_api import sync_playwright


def check(api_url, symbol, report, market="vn", intervals=None, minimum_dates=None):
    if urlsplit(api_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Use a loopback replacement API for this rehearsal")
    report.parent.mkdir(parents=True, exist_ok=True)
    calls, errors, blocked, network_errors = [], [], [], []
    passed = False
    path = {"vn": "chart", "crypto": "crypto", "global": "global"}[market]
    intervals = ("1D", *(intervals or (("1W",) if market == "global" else ("15m",))))
    intervals = tuple(dict.fromkeys(intervals))
    minimum_dates = minimum_dates or {}
    if set(minimum_dates) - set(intervals):
        raise ValueError("Freshness bounds must refer to requested chart intervals")
    for value in minimum_dates.values():
        date.fromisoformat(value)

    def matches(url, interval):
        parts = urlsplit(url)
        params = parse_qs(parts.query)
        return (
            parts.path == "/tickers"
            and params.get("symbol") == [symbol]
            and params.get("interval") == [interval]
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, timeout=30_000)
        page = browser.new_page()

        def forward(route):
            parts = urlsplit(route.request.url)
            if route.request.method not in {"GET", "HEAD", "OPTIONS"}:
                blocked.append({"path": parts.path, "method": route.request.method})
                route.abort()
                return
            try:
                response = route.fetch(
                    url=route.request.url.replace(
                        "https://api.aipriceaction.com", api_url.rstrip("/"), 1
                    ),
                    timeout=30_000,
                )
            except BrowserError as exc:
                network_errors.append({"path": parts.path, "error": type(exc).__name__})
                route.abort()
                return
            params = parse_qs(parts.query)
            public_query = {
                key: value
                for key, value in params.items()
                if key in {"symbol", "interval", "mode", "date", "start_date", "end_date", "limit"}
            }
            body = response.body()
            candles = (
                list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
                if params.get("format") == ["csv"]
                else []
            )
            times = [row["time"] for row in candles if row.get("time")]
            calls.append(
                {
                    "path": parts.path,
                    "query": public_query,
                    "status": response.status,
                    "bytes": len(body),
                    "csv_rows": max(0, len(body.splitlines()) - 1)
                    if params.get("format") == ["csv"]
                    else None,
                    "first_time": min(times) if times else None,
                    "last_time": max(times) if times else None,
                }
            )
            route.fulfill(response=response)

        page.route("https://api.aipriceaction.com/**", forward)
        page.on("pageerror", lambda exc: errors.append(str(exc)[:300]))
        try:
            page.goto(
                "https://aipriceaction.com/" + path,
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            page.wait_for_timeout(1500)
            # An initial watchlist request does not select that ticker's chart.
            # Always open the requested ticker before exercising its controls.
            page.get_by_placeholder("Search by symbol...").fill(symbol)
            with page.expect_response(lambda r: matches(r.url, "1D"), timeout=30_000):
                page.locator("button").filter(
                    has_text=re.compile("^" + re.escape(symbol))
                ).first.click()
            page.wait_for_timeout(1500)
            page.screenshot(path=str(report.with_suffix(".daily.png")), full_page=True)
            dialog = page.get_by_role("dialog")
            controls = dialog if dialog.count() else page
            for interval in intervals[1:]:
                button = controls.locator("button:visible").filter(
                    has_text=re.compile("^" + re.escape(interval) + "$")
                )
                assert button.count(), ("No visible interval control", interval)
                with page.expect_response(
                    lambda r, selected=interval: matches(r.url, selected), timeout=30_000
                ):
                    button.last.click()
                page.wait_for_timeout(1500)
                page.screenshot(
                    path=str(report.with_suffix("." + interval + ".png")), full_page=True
                )
            for interval in intervals:
                found = [
                    r
                    for r in calls
                    if r["path"] == "/tickers"
                    and r["query"].get("symbol") == [symbol]
                    and r["query"].get("interval") == [interval]
                    and r["status"] == 200
                    and (r["csv_rows"] or 0) >= 20
                ]
                assert found, ("No populated chart response", symbol, interval)
                if interval in minimum_dates:
                    assert all(
                        r["last_time"] and r["last_time"][:10] >= minimum_dates[interval]
                        for r in found
                    ), ("Stale chart response", symbol, interval, minimum_dates[interval])
                if market == "global":
                    for benchmark in ("^GSPC", "^DJI"):
                        benchmarks = [
                            r
                            for r in calls
                            if (
                                r["path"] == "/tickers"
                                and r["query"].get("symbol") == [benchmark]
                                and r["query"].get("interval") == [interval]
                                and r["status"] == 200
                                and (r["csv_rows"] or 0) >= 20
                            )
                        ]
                        assert benchmarks, (
                            "No populated global benchmark chart",
                            benchmark,
                            interval,
                        )
                        if interval in minimum_dates:
                            assert all(
                                r["last_time"] and r["last_time"][:10] >= minimum_dates[interval]
                                for r in benchmarks
                            ), ("Stale benchmark chart", benchmark, interval)
            if market != "global":
                profile_loaded = any(
                    r["path"] == "/analysis/volume-profile"
                    and r["query"].get("symbol") == [symbol]
                    and r["status"] == 200
                    for r in calls
                )
                if not profile_loaded:
                    # Replacing the first watchlist chart does not always
                    # select it for the separate details/profile panel.
                    # Exercise the ordinary chart-selection click explicitly.
                    with page.expect_response(
                        lambda r: (
                            urlsplit(r.url).path == "/analysis/volume-profile"
                            and parse_qs(urlsplit(r.url).query).get("symbol") == [symbol]
                        ),
                        timeout=30_000,
                    ):
                        # Chart libraries stack drawing canvases. Clicking the
                        # containing surface lets the top interaction layer
                        # receive the normal pointer event.
                        controls.locator("canvas:visible").first.locator("..").click(
                            position={"x": 40, "y": 60}
                        )
                    page.wait_for_timeout(1500)
                    page.screenshot(path=str(report.with_suffix(".profile.png")), full_page=True)
                assert any(
                    r["path"] == "/analysis/volume-profile"
                    and r["query"].get("symbol") == [symbol]
                    and r["status"] == 200
                    for r in calls
                ), "Selected ticker's volume profile did not load"
            assert not errors, errors
            assert not blocked, blocked
            assert not network_errors, network_errors
            passed = True
        finally:
            page.unroute_all(behavior="wait")
            result = {
                "symbol": symbol,
                "market": market,
                "requested_intervals": intervals,
                "minimum_chart_dates": minimum_dates,
                "passed": passed,
                "calls": calls,
                "page_errors": errors,
                "blocked_writes": blocked,
                "network_errors": network_errors,
            }
            report.write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result, indent=2), flush=True)
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:3001")
    parser.add_argument("--symbol", default="FPT")
    parser.add_argument("--market", choices=("vn", "crypto", "global"), default="vn")
    parser.add_argument("--interval", action="append", choices=("15m", "1h", "4h", "1W", "1M"))
    parser.add_argument(
        "--minimum-chart-date",
        action="append",
        default=[],
        metavar="INTERVAL=YYYY-MM-DD",
        help="Require selected and benchmark chart tails to reach this date; may repeat",
    )
    parser.add_argument("--report", type=Path, default=Path("data/web-rehearsal.json"))
    args = parser.parse_args()
    minimum_dates = {}
    for bound in args.minimum_chart_date:
        interval, separator, value = bound.partition("=")
        if not separator or interval in minimum_dates:
            parser.error("Use each INTERVAL=YYYY-MM-DD freshness bound once")
        minimum_dates[interval] = value
    check(args.api_url, args.symbol.upper(), args.report, args.market, args.interval, minimum_dates)
