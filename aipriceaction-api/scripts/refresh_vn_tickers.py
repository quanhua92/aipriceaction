#!/usr/bin/env python3
"""Refresh the complete Vietnamese ordinary-stock directory without extra packages.

The output keeps every Vietcap ``STOCK`` listing. Market-cap values are an
optional daily snapshot calculated from KBS's batched quote board:
price (VND) * total listed shares. The script never filters stocks by cap;
the API applies that filter when clients request the directory.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DATA_DIR = Path(__file__).resolve().parents[1] / "src" / "aipriceaction_api" / "data"
DEFAULT_OUTPUT = DATA_DIR / "vn_tickers.json"
VIETCAP_LISTING_URL = "https://trading.vietcap.com.vn/api/price/symbols/getAll"
VIETCAP_ICB_URL = "https://iq.vietcap.com.vn/api/iq-insight-service/v1/sectors/icb-codes"
VIETCAP_COMPANY_SEARCH_URL = "https://iq.vietcap.com.vn/api/iq-insight-service/v2/company/search-bar?language=1"
KBS_QUOTES_URL = "https://kbbuddywts.kbsec.com.vn/iis-server/investment/stock/iss"
MINIMUM_STOCK_COUNT = 1500
MINIMUM_CAP_COVERAGE = 0.75


def request_json(
    url: str,
    timeout: float,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> object:
    request = Request(
        url,
        data=data,
        headers=headers
        or {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0 (compatible; AIPriceAction-VN-directory/1.0)",
            "Origin": "https://iq.vietcap.com.vn",
            "Referer": "https://iq.vietcap.com.vn/",
        },
        method="POST" if data is not None else "GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise ValueError(f"{url} returned HTTP {response.status}")
            return json.load(response)
    except HTTPError as exc:
        raise ValueError(f"{url} returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise ValueError(f"Could not reach {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ValueError(f"Request timed out: {url}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON from {url}") from exc


def fetch_vietcap_stocks(timeout: float) -> list[dict]:
    payload = request_json(VIETCAP_LISTING_URL, timeout)
    if not isinstance(payload, list) or not payload:
        raise ValueError("Vietcap listing response must be a non-empty JSON array")
    if not all(isinstance(row, dict) for row in payload):
        raise ValueError("Vietcap listing contains a non-object row")

    try:
        company_names = fetch_vietcap_company_names(timeout)
    except (OSError, ValueError) as exc:
        print(
            f"Warning: company-name enrichment failed ({exc}); listing names will be used.",
            file=sys.stderr,
        )
        company_names = {}
    stocks = []
    seen = set()
    for row in payload:
        if str(row.get("type") or "").strip().upper() != "STOCK":
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            raise ValueError("Vietcap returned a STOCK row without a symbol")
        if symbol in seen:
            raise ValueError(f"Vietcap returned duplicate stock symbol {symbol}")
        fallback = company_names.get(symbol, {})
        row = row | {
            "organName": str(row.get("organName") or "").strip()
            or fallback.get("name"),
            "organShortName": str(row.get("organShortName") or "").strip()
            or fallback.get("short_name"),
        }
        seen.add(symbol)
        stocks.append(row | {"symbol": symbol})

    return sorted(stocks, key=lambda row: row["symbol"])


def fetch_vietcap_company_names(timeout: float) -> dict[str, dict[str, str]]:
    payload = request_json(VIETCAP_COMPANY_SEARCH_URL, timeout)
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Vietcap company search response must contain a data array")

    result = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("code") or "").strip().upper()
        if not symbol:
            continue
        candidate = {
            "name": str(row.get("name") or "").strip(),
            "short_name": str(row.get("shortName") or "").strip(),
        }
        if any(candidate.values()):
            result.setdefault(symbol, candidate)
    return result


def fetch_icb_taxonomy(timeout: float) -> dict[str, dict[str, str]]:
    payload = request_json(VIETCAP_ICB_URL, timeout)
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("Vietcap ICB response must contain a non-empty data array")

    result = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("icbLevel") != 2:
            continue
        code = str(row.get("name") or "").strip()
        name_vi = str(row.get("viSector") or "").strip()
        name_en = str(row.get("enSector") or "").strip()
        if code and name_vi and name_en:
            if code in result and result[code] != {"name_vi": name_vi, "name_en": name_en}:
                raise ValueError(f"Vietcap returned conflicting ICB level-2 names for {code}")
            result[code] = {"name_vi": name_vi, "name_en": name_en}
    if not result:
        raise ValueError("Vietcap taxonomy returned no usable ICB level-2 entries")
    return result


def fetch_kbs_quotes(
    symbols: list[str], timeout: float, batch_size: int, pause_seconds: float
) -> dict[str, dict]:
    result = {}
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; AIPriceAction-VN-directory/1.0)",
        "x-lang": "vi",
    }
    for start in range(0, len(symbols), batch_size):
        batch = symbols[start : start + batch_size]
        payload = request_json(
            KBS_QUOTES_URL,
            timeout,
            data=json.dumps({"code": ",".join(batch)}).encode("utf-8"),
            headers=headers,
        )
        if not isinstance(payload, list):
            raise ValueError(f"KBS quote batch {start // batch_size + 1} was not a JSON array")
        for row in payload:
            if not isinstance(row, dict):
                raise ValueError("KBS quote response contains a non-object row")
            symbol = str(row.get("SB") or "").strip().upper()
            if not symbol:
                continue
            if symbol in result:
                raise ValueError(f"KBS returned duplicate quote rows for {symbol}")
            result[symbol] = row
        if pause_seconds:
            time.sleep(pause_seconds)
    return result


def integer_value(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number != number.to_integral_value():
        return None
    return int(number)


def timestamp_iso(value: object) -> str | None:
    milliseconds = integer_value(value)
    if milliseconds is None or milliseconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
    except (OverflowError, OSError, ValueError):
        return None


def market_cap_fields(quote: dict | None) -> dict:
    if quote is None:
        return {
            "market_cap_vnd": None,
            "market_cap_as_of": None,
            "market_cap_price_vnd": None,
            "market_cap_price_basis": None,
            "shares_listed": None,
        }

    shares = integer_value(quote.get("TLQ"))
    if shares is None or shares <= 0:
        return {
            "market_cap_vnd": None,
            "market_cap_as_of": timestamp_iso(quote.get("t")),
            "market_cap_price_vnd": None,
            "market_cap_price_basis": None,
            "shares_listed": shares if shares and shares > 0 else None,
        }

    last_price = integer_value(quote.get("CP"))
    if last_price is not None and last_price > 0:
        price = last_price
        basis = "last_trade"
    else:
        reference_price = integer_value(quote.get("RE"))
        if reference_price is None or reference_price <= 0:
            return {
                "market_cap_vnd": None,
                "market_cap_as_of": timestamp_iso(quote.get("t")),
                "market_cap_price_vnd": None,
                "market_cap_price_basis": None,
                "shares_listed": shares,
            }
        price = reference_price
        basis = "reference_price"

    return {
        "market_cap_vnd": price * shares,
        "market_cap_as_of": timestamp_iso(quote.get("t")),
        "market_cap_price_vnd": price,
        "market_cap_price_basis": basis,
        "shares_listed": shares,
    }


def build_catalog(stocks: list[dict], taxonomy: dict[str, dict[str, str]], quotes: dict[str, dict]):
    output = []
    for row in stocks:
        symbol = row["symbol"]
        board = str(row.get("board") or "").strip().upper() or None
        icb_code = str(row.get("icbCode2") or "").strip() or None
        if icb_code is not None and icb_code not in taxonomy:
            raise ValueError(f"No ICB level-2 taxonomy entry for {symbol} code {icb_code}")
        icb_names = taxonomy.get(icb_code) if icb_code else None
        exchange = {"HSX": "HOSE", "HOSE": "HOSE"}.get(board, board)
        if exchange == "DELISTED":
            exchange = None
        record = {
            "symbol": symbol,
            "name": str(row.get("organName") or "").strip() or None,
            "en_name": str(row.get("enOrganName") or "").strip() or None,
            "short_name": str(row.get("organShortName") or "").strip() or None,
            "en_short_name": str(row.get("enOrganShortName") or "").strip() or None,
            "exchange": exchange,
            "listing_board": board,
            "listing_status": "delisted" if board == "DELISTED" else "listed",
            "type": "stock",
            "icb_level2_code": icb_code,
            "icb_level2_name": icb_names["name_vi"] if icb_names else None,
            "icb_level2_name_en": icb_names["name_en"] if icb_names else None,
        }
        record.update(market_cap_fields(quotes.get(symbol)))
        output.append(record)

    symbols = [row["symbol"] for row in output]
    if len(symbols) != len(set(symbols)):
        raise ValueError("Generated VN stock catalog contains duplicate symbols")
    if len(output) != len(stocks):
        raise ValueError("Generated catalog count differs from Vietcap STOCK listing")
    return output


def atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--pause", type=float, default=0.1)
    parser.add_argument("--minimum-stock-count", type=int, default=MINIMUM_STOCK_COUNT)
    parser.add_argument(
        "--minimum-market-cap-coverage",
        type=float,
        default=MINIMUM_CAP_COVERAGE,
        help="abort rather than publish if fewer than this fraction of stocks have a cap",
    )
    parser.add_argument("--skip-market-cap", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.timeout <= 0 or not 1 <= args.batch_size <= 250 or args.pause < 0:
        parser.error("timeout must be positive, batch-size must be 1-250, and pause non-negative")
    if args.minimum_stock_count < 1:
        parser.error("minimum-stock-count must be positive")
    if not 0 <= args.minimum_market_cap_coverage <= 1:
        parser.error("minimum-market-cap-coverage must be from 0 to 1")

    try:
        stocks = fetch_vietcap_stocks(args.timeout)
        if len(stocks) < args.minimum_stock_count:
            raise ValueError(
                f"Vietcap returned {len(stocks)} stocks; minimum is {args.minimum_stock_count}"
            )
        taxonomy = fetch_icb_taxonomy(args.timeout)
        quotes = (
            {}
            if args.skip_market_cap
            else fetch_kbs_quotes(
                [row["symbol"] for row in stocks],
                args.timeout,
                args.batch_size,
                args.pause,
            )
        )
        if quotes:
            absent = sorted({row["symbol"] for row in stocks} - quotes.keys())
            if absent:
                print(
                    f"Warning: KBS returned no quote row for {len(absent)} stocks; cap will be null.",
                    file=sys.stderr,
                )

        rows = build_catalog(stocks, taxonomy, quotes)
        missing_active_names = [
            row["symbol"]
            for row in rows
            if row["listing_status"] != "delisted"
            and not (row["name"] or row["en_name"])
        ]
        if missing_active_names:
            sample = ", ".join(missing_active_names[:10])
            raise ValueError(
                f"Vietcap returned no company name for listed stocks: {sample}"
            )
        known_caps = sum(row["market_cap_vnd"] is not None for row in rows)
        coverage = known_caps / len(rows)
        if not args.skip_market_cap and coverage < args.minimum_market_cap_coverage:
            raise ValueError(
                f"Market-cap coverage {coverage:.1%} is below the required "
                f"{args.minimum_market_cap_coverage:.1%}; existing catalog was not changed"
            )

        payload = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "universe": "Vietcap type=STOCK only",
            "market_cap_method": (
                "KBS quote price in VND * KBS total listed shares; CP when positive, "
                "otherwise RE reference price"
            ),
            "market_cap_source": "KBS stock/iss; missing values remain null",
            "stock_count": len(rows),
            "market_cap_known_count": known_caps,
            "market_cap_unknown_count": len(rows) - known_caps,
            "icb_level2_known_count": sum(row["icb_level2_code"] is not None for row in rows),
            "company_name_missing_count": sum(not row["name"] for row in rows),
            "sources": {
                "stock_listing": VIETCAP_LISTING_URL,
                "icb_taxonomy": VIETCAP_ICB_URL,
                "company_names": VIETCAP_COMPANY_SEARCH_URL,
                "market_cap_quotes": KBS_QUOTES_URL if not args.skip_market_cap else None,
            },
            "stocks": rows,
        }
        if not args.dry_run:
            atomic_write(args.output, payload)

        delisted = sum(row["listing_status"] == "delisted" for row in rows)
        print(
            f"Stocks: {len(rows)} (delisted={delisted}); ICB level-2 mapped: "
            f"{payload['icb_level2_known_count']}; market cap: {known_caps} known, "
            f"{len(rows) - known_caps} unknown ({coverage:.1%} coverage); "
            f"company names missing: {payload['company_name_missing_count']}."
        )
        print(f"{'Validated' if args.dry_run else 'Wrote'} {args.output}")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"refresh_vn_tickers: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
