"""Provider adapters. Market source and upstream provider are separate identities."""

import asyncio
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx

from .binance_files import BinanceFiles
from .domain import INDEXES, Candle, DataError


@dataclass
class Page:
    rows: list[Candle]
    provider: str
    no_data: bool = False
    # Oldest selected timestamp before retention filtering, used only to
    # advance pagination. It does not establish an exchange calendar.
    cursor: int | None = None


class RateLimiter:
    def __init__(self, rpm):
        self.delay = 60 / rpm
        self.next = 0.0
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            pause = max(0, self.next - time.monotonic())
            if pause:
                await asyncio.sleep(pause)
            self.next = time.monotonic() + self.delay


class Providers:
    VN = {
        "vps": ("https://histdatafeed.vps.com.vn/tradingview/history", "https://www.vps.com.vn/"),
        "vndirect": (
            "https://dchart-api.vndirect.com.vn/dchart/history",
            "https://dchart.vndirect.com.vn/",
        ),
        "dnse": ("https://api.dnse.com.vn/chart-api/v2/ohlcs/stock", "https://www.dnse.com.vn/"),
    }

    def __init__(self, settings, transport=None):
        self.settings = settings
        self.transport = transport
        self.clients = {}
        self.limits = {}
        self.binance_files = BinanceFiles(
            settings.cache_dir.parent / "binance-files", self.binary_request
        )

    async def close(self):
        for client in self.clients.values():
            await client.aclose()

    def routes(self, vn):
        routes = list(self.settings.proxies)
        if not vn or self.settings.allow_direct or self.transport:
            routes.append(None)
        if not routes:
            raise DataError("VN requests require HTTP_PROXIES or explicit ALLOW_DIRECT=true")
        return routes

    async def request(self, provider, url, params=None, referer=None, data=None, vn=False):
        last = None
        routes = self.routes(vn)
        for attempt in range(3):
            route = routes[attempt % len(routes)]
            client_key = (provider, route)
            if client_key not in self.clients:
                self.clients[client_key] = httpx.AsyncClient(
                    proxy=route,
                    transport=self.transport,
                    timeout=20,
                    follow_redirects=True,
                    headers={
                        "User-Agent": "Mozilla/5.0 AIPriceAction/0.1",
                        "Accept": "application/json, text/plain, */*",
                    },
                )
            limiter = self.limits.setdefault(
                client_key, RateLimiter(self.settings.requests_per_minute)
            )
            await limiter.acquire()
            try:
                client = self.clients[client_key]
                response = await client.request(
                    "POST" if data is not None else "GET",
                    url,
                    params=params,
                    data=data,
                    headers={"Referer": referer} if referer else None,
                )
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last = type(exc).__name__
                if attempt < 2:
                    await asyncio.sleep(min(4, 2**attempt))
        # Never log proxies/URLs containing credentials or raw HTTP exceptions.
        raise DataError(f"{provider} request failed ({last})")

    async def binary_request(self, url, budget):
        """Use the same Binance request budget for bounded official file downloads."""
        last = None
        routes = self.routes(False)
        for attempt in range(3):
            route = routes[attempt % len(routes)]
            key = ("binance", route)
            if key not in self.clients:
                self.clients[key] = httpx.AsyncClient(
                    proxy=route,
                    transport=self.transport,
                    timeout=20,
                    follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0 AIPriceAction/0.1"},
                )
            limiter = self.limits.setdefault(key, RateLimiter(self.settings.requests_per_minute))
            await limiter.acquire()
            try:
                async with self.clients[key].stream("GET", url) as response:
                    if response.status_code == 404:
                        return None
                    response.raise_for_status()
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > budget:
                            raise DataError("Binance file exceeds download size budget")
                        chunks.append(chunk)
                    return b"".join(chunks)
            except httpx.HTTPError as exc:
                last = type(exc).__name__
                if attempt < 2:
                    await asyncio.sleep(min(4, 2**attempt))
        raise DataError(f"Binance file request failed ({last})")

    @staticmethod
    def normalize(rows, before, count, provider, start=None):
        by_time = {}
        conflicts = set()
        for row in rows:
            if row.time >= before:
                continue
            if row.time in by_time and row != by_time[row.time]:
                conflicts.add(row.time)
            by_time[row.time] = row
        selected = sorted(by_time.values(), key=lambda r: r.time)[-count:]
        cursor = selected[0].time if selected else None
        # A repair can observe an older boundary without ingesting candles
        # outside its requested window. Every retained row remains strict.
        result = [row for row in selected if start is None or row.time >= start]
        # VN endpoints often ignore countback and return a much larger range.
        # Validate the requested page, not unrelated older candles. Invalid
        # candles inside the page still reject it; never clamp or fabricate OHLC.
        for row in result:
            row.validate()
            if row.time in conflicts:
                raise DataError(f"{provider} has conflicting candles at {row.time}")
        if result and result[0].source == "crypto":
            step = {"1D": 86400, "1h": 3600, "1m": 60}[result[0].interval]
            for previous, current in zip(result, result[1:], strict=False):
                if current.time != previous.time + step:
                    raise DataError(
                        f"Binance page has a gap from {previous.time} to {current.time}"
                    )
        return Page(result, provider, not result, cursor)

    async def vn_page(self, provider, symbol, iv, before, count, start=None):
        if provider not in self.VN:
            raise DataError("Unsupported VN provider")
        url, referer = self.VN[provider]
        if provider == "dnse" and symbol in INDEXES:
            url = url.replace("/stock", "/index")
        resolution = {"1D": "D", "1h": "60", "1m": "1"}[iv]
        if provider == "dnse":
            resolution = {"1D": "1D", "1h": "1H", "1m": "1"}[iv]
        # Generous bounded date windows; countback tells supporting endpoints how
        # much is required. Provider short/no-data pages never prove listing dates.
        duration = {
            "1D": 86400 * count * 3,
            "1h": 86400 * max(10, count),
            "1m": 86400 * max(7, (count // 200) * 3),
        }[iv]
        payload = await self.request(
            provider,
            url,
            {
                "symbol": symbol,
                "resolution": resolution,
                "from": before - duration,
                "to": before - 1,
                "countback": count,
            },
            referer,
            vn=True,
        )
        if payload.get("s") == "no_data":
            return Page([], provider, True)
        arrays = [payload.get(k) or [] for k in ("t", "o", "h", "l", "c", "v")]
        if len({len(a) for a in arrays}) != 1 or not arrays[0]:
            raise DataError(f"{provider} invalid/missing OHLCV arrays")
        multiplier = 1 if symbol in INDEXES else 1000
        records = []
        local_midnight_days, utc_midnight_days, unverified_days = set(), set(), set()
        for stamp, o, h, low, c, v in zip(*arrays, strict=True):
            timestamp = int(stamp)
            if timestamp > 10_000_000_000:
                timestamp //= 1000
            if iv == "1D":
                # Observed daily conventions: UTC midnight on VPS/VNDirect;
                # DNSE also uses 02:00 UTC (09:00 Vietnam session start).
                # Verified VNINDEX responses additionally use 02:15 UTC
                # before May 2025. Accept that observed index convention only.
                # Both map to the same market date. Unknown conventions remain
                # blocked until verified against dated provider evidence.
                verified_offsets = {0, 2 * 3600} if provider == "dnse" else {0}
                if provider == "dnse" and symbol == "VNINDEX":
                    verified_offsets.add(2 * 3600 + 15 * 60)
                if timestamp % 86400 not in verified_offsets:
                    unverified_days.add(timestamp // 86400 * 86400)
                if timestamp % 86400 == 17 * 3600:
                    local_midnight_days.add(timestamp // 86400 * 86400)
                elif timestamp % 86400 == 0:
                    utc_midnight_days.add(timestamp // 86400 * 86400)
            timestamp = timestamp // 86400 * 86400 if iv == "1D" else timestamp // 60 * 60
            records.append((timestamp, o, h, low, c, v))
        selected_times = sorted({row[0] for row in records if row[0] < before})[-count:]
        cursor = selected_times[0] if selected_times else None
        requested_times = {stamp for stamp in selected_times if start is None or stamp >= start}
        rows = []
        for timestamp, o, h, low, c, v in records:
            if timestamp not in requested_times:
                continue
            rows.append(
                Candle(
                    "vn",
                    symbol,
                    iv,
                    timestamp,
                    float(o) * multiplier,
                    float(h) * multiplier,
                    float(low) * multiplier,
                    float(c) * multiplier,
                    int(float(v)),
                    provider,
                )
            )
        page = self.normalize(rows, before, count, provider)
        page.cursor = cursor
        if (
            local_midnight_days
            and utc_midnight_days
            and any(r.time in local_midnight_days for r in page.rows)
        ):
            # Observed VPS index replies mix UTC midnight with 17:00 UTC
            # (Vietnam midnight). Their duplicate market dates have different
            # opens. Choosing one or shifting dates silently corrupts a series.
            raise DataError(f"{provider} mixes conflicting VN daily timestamp bases")
        if page.cursor in unverified_days or any(r.time in unverified_days for r in page.rows):
            # A pure Vietnam-midnight reply requires verification; flooring it
            # to UTC silently moves its market date backward. Preserve published
            # candles rather than inventing a conversion for an unknown basis.
            raise DataError(f"{provider} uses unverified VN daily timestamps")
        return page

    async def crypto_page(self, symbol, iv, before, count):
        if iv == "1m" and count > 1000:
            rows = await self.binance_files.month(symbol, iv, before)
            if rows is not None:
                return self.normalize(rows, before, count, "binance")
        payload = await self.request(
            "binance",
            "https://api.binance.com/api/v3/klines",
            {
                "symbol": symbol,
                "interval": {"1D": "1d", "1h": "1h", "1m": "1m"}[iv],
                "endTime": before * 1000 - 1,
                "limit": min(1000, count),
            },
        )
        if not isinstance(payload, list):
            raise DataError("Binance invalid kline response")
        rows = [
            Candle(
                "crypto",
                symbol,
                iv,
                int(r[0]) // 1000,
                float(r[1]),
                float(r[2]),
                float(r[3]),
                float(r[4]),
                int(float(r[5])),
                "binance",
            )
            for r in payload
        ]
        return self.normalize(rows, before, count, "binance")

    async def yahoo_page(self, symbol, iv, before, count):
        wire = symbol.removesuffix(":US")
        duration = {
            "1D": count * 3 * 86400,
            "1h": min(729 * 86400, count * 86400),
            "1m": 6 * 86400,
        }[iv]
        payload = await self.request(
            "yahoo",
            f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(wire, safe='')}",
            {
                "period1": max(0, before - duration),
                "period2": before,
                "interval": {"1D": "1d", "1h": "60m", "1m": "1m"}[iv],
                "events": "div,splits",
            },
        )
        chart = payload.get("chart", {})
        if chart.get("error") or not chart.get("result"):
            raise DataError("Yahoo history unavailable for requested range")
        data = chart["result"][0]
        stamps = data.get("timestamp") or []
        indicators = data.get("indicators", {})
        quotes = indicators.get("quote", [{}])[0]
        adjusted = indicators.get("adjclose", [{}])[0].get("adjclose") or []
        rows = []
        for i, stamp in enumerate(stamps):
            values = [
                quotes.get(k, [])[i] if i < len(quotes.get(k, [])) else None
                for k in ("open", "high", "low", "close", "volume")
            ]
            if any(v is None for v in values):
                continue  # Null/non-trading candles are not invented.
            o, h, low, c, v = values
            factor = (
                adjusted[i] / c
                if iv == "1D" and i < len(adjusted) and adjusted[i] is not None and c
                else 1
            )
            timestamp = int(stamp) // 86400 * 86400 if iv == "1D" else int(stamp) // 60 * 60
            rows.append(
                Candle(
                    "yahoo",
                    symbol,
                    iv,
                    timestamp,
                    o * factor,
                    h * factor,
                    low * factor,
                    c * factor,
                    int(v),
                    "yahoo",
                )
            )
        return self.normalize(rows, before, count, "yahoo")

    async def sjc_page(self, symbol, iv, before, count):
        if iv != "1D" or symbol != "SJC-GOLD":
            raise DataError("SJC supports SJC-GOLD daily quotes only")
        # The upstream endpoint provides dated quotes, not trade candles. Mark
        # provider explicitly and preserve the legacy quote-derived representation.
        day = datetime.fromtimestamp(before - 1, UTC).date()
        rows = []
        for _ in range(min(count + 1, 30)):
            payload = await self.request(
                "sjc",
                "https://sjc.com.vn/GoldPrice/Services/PriceService.ashx",
                referer="https://sjc.com.vn/",
                data={"method": "GetSJCGoldPriceByDate", "toDate": day.strftime("%d/%m/%Y")},
            )
            records = [r for r in payload.get("data", []) if r.get("BranchName") == "Hồ Chí Minh"]
            if not payload.get("success") or not records:
                raise DataError(f"SJC quote unavailable for {day}")
            record = records[0]
            buy, sell = float(record["BuyValue"]), float(record["SellValue"])
            mid = (buy + sell) / 2
            rows.append(
                Candle(
                    "sjc",
                    symbol,
                    iv,
                    int(datetime.combine(day, datetime.min.time(), UTC).timestamp()),
                    mid,
                    sell,
                    buy,
                    mid,
                    1,
                    "sjc-quote",
                )
            )
            day -= timedelta(days=1)
        rows.sort(key=lambda r: r.time)
        rows = [replace(r, open=rows[i - 1].close) if i else r for i, r in enumerate(rows)]
        return self.normalize(rows, before, count, "sjc-quote")

    async def page(self, source, symbol, iv, before=None, count=500, provider=None, start=None):
        before = before or int(time.time()) + 1
        if source == "vn":
            errors = []
            selected_providers = list(self.settings.vn_providers)
            if iv == "1h" and "dnse" in selected_providers:
                selected_providers.remove("dnse")
                selected_providers.insert(0, "dnse")
            for selected in (provider,) if provider else selected_providers:
                try:
                    page = await self.vn_page(selected, symbol, iv, before, count, start)
                    if provider or page.rows:
                        return page
                    errors.append(f"{selected}: no data")
                except DataError as exc:
                    errors.append(str(exc))
            raise DataError("VN providers unavailable: " + "; ".join(errors))
        method = {"crypto": self.crypto_page, "yahoo": self.yahoo_page, "sjc": self.sjc_page}.get(
            source
        )
        if method is None:
            raise DataError("Unknown market source")
        return await method(symbol, iv, before, count)


def adjustment_changes(
    existing, incoming, completed_before, tolerance=1e-6, min_matches=3, snapshot_provider=None
):
    """Corroborate historical price revisions; don't label them proven dividends.

    Thresholds are configurable worker policy, not inferred corporate-action facts.
    Match one pinned upstream, or its explicitly verified imported snapshot.
    A later switch needs staged recovery rather than price-ratio inference.
    The default tolerates Float32 representation noise but detects small,
    corroborated corrections rather than only large dividend-sized changes.
    """
    old = {r.time: r for r in existing if r.time < completed_before}
    changed = []
    for row in incoming:
        previous = old.get(row.time)
        if (
            previous
            and previous.close != 0
            and (
                previous.provider == row.provider
                or (snapshot_provider is not None and previous.provider == snapshot_provider)
            )
            and row.time < completed_before
        ):
            ratio = row.close / previous.close
            if abs(ratio - 1) > tolerance:
                changed.append((row.time, ratio))
    return changed if len(changed) >= min_matches else []
