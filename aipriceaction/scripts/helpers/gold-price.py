"""
Vietnamese Gold Price Fetcher

Fetches gold price data from multiple free public APIs:

  CafeF providers (bulk historical, single request):
    - cafef-sjc:  SJC gold bars daily buy/sell (millions VND, ~500 days)
    - cafef-ring: Gold ring buy/sell (thousands VND, ~180 days)

  Direct provider (per-date):
    - sjc:  SJC official API, per-date buy/sell by branch (data from 2015+)

Usage:
    python3 gold-price.py cafef-sjc            # CafeF SJC history
    python3 gold-price.py cafef-ring           # CafeF gold ring history
    python3 gold-price.py sjc                  # SJC today's price (all branches)
    python3 gold-price.py sjc --date 2025-01-15  # SJC on a specific date
    python3 gold-price.py sjc --date-range 2024-01-01:2024-12-31  # SJC range (HCM)
    python3 gold-price.py cafef-sjc --format ohlcv  # Output as OHLCV CSV
    python3 gold-price.py sjc-batch                 # Batch download 2016-now to CSV
    python3 gold-price.py sjc-batch --date-range 2024-01-01:2024-12-31  # Custom range
    python3 gold-price.py sjc-batch --output sjc-all.csv --branch Miền Bắc
"""

import argparse
import csv
import datetime
import json
import os
import sys
import time
import urllib.request


# ── CafeF URLs ──────────────────────────────────────────────────────────────

CAFEF_SJC_URL = "https://cafef.vn/du-lieu/Ajax/ajaxgoldpricehistory.ashx?index=all"
CAFEF_RING_URL = "https://cafef.vn/du-lieu/Ajax/AjaxGoldPriceRing.ashx?time=all&zone=24"

# ── Direct provider URLs ────────────────────────────────────────────────────

SJC_API_URL = "https://sjc.com.vn/GoldPrice/Services/PriceService.ashx"


# ── Helpers ─────────────────────────────────────────────────────────────────

def _fetch_json(url):
    req = urllib.request.Request(url)
    req.add_header(
        "User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _post_json(url, payload):
    data = payload.encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header(
        "User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    )
    req.add_header("Referer", "https://sjc.com.vn/")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _parse_date(ts):
    """Parse various timestamp formats into YYYY-MM-DD (local time)."""
    if not ts:
        return None

    import datetime

    # "/Date(1743976800000)/"
    if ts.startswith("/Date("):
        millis = int(ts[6:ts.index(")")])
        dt = datetime.datetime.fromtimestamp(millis / 1000)
        return dt.strftime("%Y-%m-%d")

    # ISO: "2025-02-08T00:00:00.000Z" — convert to local date
    if "T" in ts:
        ts_clean = ts.rstrip("Z")
        try:
            dt = datetime.datetime.fromisoformat(ts_clean)
            dt_local = dt.astimezone(datetime.timezone.utc).astimezone()
            return dt_local.strftime("%Y-%m-%d")
        except (ValueError, OSError):
            return ts[:10]

    # Plain datetime or date: "2026-04-07 11:43:46" / "2025-02-08"
    if len(ts) >= 10 and "-" in ts:
        return ts[:10]

    return None


def print_csv(rows, columns):
    writer = csv.DictWriter(sys.stdout, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)


def to_ohlcv(records):
    """Convert daily buy/sell data into synthetic OHLCV candles.

    Each date produces one candle:
      open  = first buy of the day
      high  = max(buy, sell) across all snapshots that day
      low   = min(buy, sell)
      close = last buy of the day
      volume = 1 (synthetic)
    """
    from collections import OrderedDict

    grouped = OrderedDict()
    for r in records:
        date = r["date"]
        if date not in grouped:
            grouped[date] = {"buys": [], "sells": []}
        grouped[date]["buys"].append(r["buy"])
        grouped[date]["sells"].append(r["sell"])

    candles = []
    for date, prices in grouped.items():
        all_prices = prices["buys"] + prices["sells"]
        candles.append({
            "date": date,
            "open": prices["buys"][0],
            "high": max(all_prices),
            "low": min(all_prices),
            "close": prices["buys"][-1],
            "volume": 1,
        })
    return candles


# ── CafeF providers ─────────────────────────────────────────────────────────

def fetch_cafef_sjc():
    """SJC gold bar daily buy/sell from CafeF.

    Returns list of {date, buy, sell} — prices in full VND.
    """
    resp = _fetch_json(CAFEF_SJC_URL)
    data = resp.get("Data", {}).get("goldPriceWorldHistories", [])
    results = []
    for row in data:
        date_str = _parse_date(row.get("createdAt", ""))
        if not date_str:
            continue
        buy = float(row.get("buyPrice", 0)) * 1_000_000
        sell = float(row.get("sellPrice", 0)) * 1_000_000
        results.append({"date": date_str, "buy": buy, "sell": sell})
    return results


def fetch_cafef_ring():
    """Gold ring (Nhẫn ép vỉ Kim Gia Bảo) daily prices from CafeF.

    Returns list of {date, buy, sell, buy_change, sell_change} — prices in full VND.
    """
    resp = _fetch_json(CAFEF_RING_URL)
    data = resp.get("Data", {}).get("goldPriceWorldHistories", [])
    results = []
    for row in data:
        date_str = _parse_date(row.get("lastUpdated", ""))
        if not date_str:
            continue
        buy = float(row.get("buyPrice", 0)) * 1_000
        sell = float(row.get("sellPrice", 0)) * 1_000
        buy_change = float(row.get("buyChangePrice", 0)) * 1_000
        sell_change = float(row.get("sellChangePrice", 0)) * 1_000
        results.append({
            "date": date_str,
            "buy": buy,
            "sell": sell,
            "buy_change": buy_change,
            "sell_change": sell_change,
        })
    return results


# ── SJC direct provider ─────────────────────────────────────────────────────

def fetch_sjc(date_str=None):
    """Fetch SJC gold price for a single date from sjc.com.vn.

    Args:
        date_str: "YYYY-MM-DD" or None for today.

    Returns list of {date, branch, buy, sell, buy_diff, sell_diff} — prices in full VND.
    Returns empty list if no data for the date.
    """
    import datetime

    if date_str is None:
        input_date = datetime.date.today()
    else:
        input_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()

    formatted_date = input_date.strftime("%d/%m/%Y")
    payload = f"method=GetSJCGoldPriceByDate&toDate={formatted_date}"

    result = _post_json(SJC_API_URL, payload)
    if not result.get("success"):
        return []

    records = []
    for row in result.get("data", []):
        records.append({
            "date": date_str or input_date.strftime("%Y-%m-%d"),
            "branch": row.get("BranchName", ""),
            "buy": row.get("BuyValue", 0),
            "sell": row.get("SellValue", 0),
            "buy_diff": row.get("BuyDifferValue", 0),
            "sell_diff": row.get("SellDifferValue", 0),
        })
    return records


def fetch_sjc_range(start_date, end_date, branch="Hồ Chí Minh"):
    """Fetch SJC gold price for a date range, one request per day.

    Args:
        start_date: "YYYY-MM-DD"
        end_date: "YYYY-MM-DD"
        branch: Branch name to filter (default: "Hồ Chí Minh")

    Returns list of {date, branch, buy, sell, buy_diff, sell_diff} — prices in full VND.
    """
    import datetime

    start = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()

    results = []
    current = start
    day_count = (end - start).days + 1
    done = 0

    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        records = fetch_sjc(date_str)
        # Filter to requested branch
        for r in records:
            if r["branch"] == branch:
                results.append(r)
                break

        done += 1
        if done % 30 == 0:
            print(f"  Fetched {done}/{day_count} days...", file=sys.stderr)
        current += datetime.timedelta(days=1)
        # Be polite — slight delay between requests
        time.sleep(0.1)

    print(f"  Fetched {done}/{day_count} days, {len(results)} records for branch '{branch}'",
          file=sys.stderr)
    return results


def _load_existing_dates(csv_path):
    """Read dates already present in a CSV file for resume support."""
    if not os.path.exists(csv_path):
        return set()
    dates = set()
    with open(csv_path, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if "date" in row:
                dates.add(row["date"])
    return dates


def sjc_batch(csv_path, branch="Hồ Chí Minh", start_date="2016-01-01", end_date_str=None):
    """Batch download SJC daily prices, resumable, appends to CSV.

    Args:
        csv_path: Output CSV path.
        branch: Branch to filter (default: Hồ Chí Minh).
        start_date: "YYYY-MM-DD" (default: 2016-01-01).
        end_date_str: "YYYY-MM-DD" (default: today).
    """
    end_date = (datetime.datetime.strptime(end_date_str, "%Y-%m-%d").date()
                if end_date_str else datetime.date.today())
    start = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
    total_days = (end_date - start).days + 1

    # Resume: find last fetched date
    existing = _load_existing_dates(csv_path)
    if existing:
        latest = max(existing)
        resume_from = datetime.datetime.strptime(latest, "%Y-%m-%d").date()
        resume_from += datetime.timedelta(days=1)
        if resume_from > end_date:
            print(f"CSV already up to date ({latest}). Nothing to fetch.", file=sys.stderr)
            return
        skipped = (resume_from - start).days
        print(f"Resuming from {resume_from} ({skipped} days already fetched, "
              f"{total_days - skipped} remaining)", file=sys.stderr)
        start = resume_from
        total_days = (end_date - start).days + 1
    else:
        print(f"Starting fresh: {start} to {end_date} ({total_days} days)",
              file=sys.stderr)

    write_header = not existing
    columns = ["date", "branch", "buy", "sell", "buy_diff", "sell_diff"]

    current = start
    done = 0
    fetched = 0
    errors = 0

    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        if write_header:
            writer.writeheader()

        while current <= end_date:
            date_str = current.strftime("%Y-%m-%d")
            try:
                records = fetch_sjc(date_str)
                for r in records:
                    if r["branch"] == branch:
                        writer.writerow(r)
                        f.flush()
                        fetched += 1
                        break
            except Exception as e:
                errors += 1
                print(f"  ERROR {date_str}: {e}", file=sys.stderr)

            done += 1
            if done % 100 == 0:
                print(f"  {done}/{total_days} days fetched "
                      f"(fetched={fetched}, errors={errors})", file=sys.stderr)
            current += datetime.timedelta(days=1)
            time.sleep(0.15)

    total = len(_load_existing_dates(csv_path))
    print(f"Done. {total} rows in {csv_path} "
          f"(this run: +{fetched} fetched, {errors} errors)", file=sys.stderr)


# ── CLI ─────────────────────────────────────────────────────────────────────

PROVIDERS = {
    "cafef-sjc": "CafeF SJC gold bar history",
    "cafef-ring": "CafeF gold ring history",
    "sjc": "SJC official (per-date, all branches)",
    "sjc-batch": "SJC batch download 2016-now, resumable, appends to CSV",
}

RAW_COLUMNS = {
    "cafef-sjc": ["date", "buy", "sell"],
    "cafef-ring": ["date", "buy", "sell", "buy_change", "sell_change"],
    "sjc": ["date", "branch", "buy", "sell", "buy_diff", "sell_diff"],
}


def main():
    parser = argparse.ArgumentParser(
        description="Fetch Vietnamese gold prices from multiple providers",
    )
    parser.add_argument(
        "source",
        choices=PROVIDERS,
        help="Data source",
    )
    parser.add_argument(
        "--format",
        choices=["raw", "ohlcv"],
        default="raw",
        help="Output format (ohlcv only for time-series sources)",
    )
    parser.add_argument(
        "--date",
        help="Single date for SJC provider (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--date-range",
        help="Date range for SJC/sjc-batch: START:END (YYYY-MM-DD:YYYY-MM-DD)",
    )
    parser.add_argument(
        "--branch",
        default="Hồ Chí Minh",
        help="Branch for SJC queries (default: Hồ Chí Minh)",
    )
    parser.add_argument(
        "--output",
        help="Output CSV path for sjc-batch (default: sjc-batch.csv)",
    )
    args = parser.parse_args()

    if args.source == "cafef-sjc":
        records = fetch_cafef_sjc()
        print(f"CafeF SJC: {len(records)} records", file=sys.stderr)
        if args.format == "ohlcv":
            print_csv(to_ohlcv(records), ["date", "open", "high", "low", "close", "volume"])
        else:
            print_csv(records, RAW_COLUMNS["cafef-sjc"])

    elif args.source == "cafef-ring":
        records = fetch_cafef_ring()
        print(f"CafeF Gold Ring: {len(records)} records", file=sys.stderr)
        if args.format == "ohlcv":
            print_csv(to_ohlcv(records), ["date", "open", "high", "low", "close", "volume"])
        else:
            print_csv(records, RAW_COLUMNS["cafef-ring"])

    elif args.source == "sjc":
        if args.date_range:
            parts = args.date_range.split(":")
            if len(parts) != 2:
                print("Error: --date-range format is START:END (YYYY-MM-DD:YYYY-MM-DD)",
                      file=sys.stderr)
                sys.exit(1)
            records = fetch_sjc_range(parts[0], parts[1], branch=args.branch)
            if args.format == "ohlcv":
                print_csv(to_ohlcv(records), ["date", "open", "high", "low", "close", "volume"])
            else:
                print_csv(records, RAW_COLUMNS["sjc"])
        else:
            records = fetch_sjc(args.date)
            print(f"SJC ({args.date or 'today'}): {len(records)} records", file=sys.stderr)
            print_csv(records, RAW_COLUMNS["sjc"])

    elif args.source == "sjc-batch":
        csv_path = args.output or "sjc-batch.csv"
        if args.date_range:
            parts = args.date_range.split(":")
            if len(parts) != 2:
                print("Error: --date-range format is START:END (YYYY-MM-DD:YYYY-MM-DD)",
                      file=sys.stderr)
                sys.exit(1)
            sjc_batch(csv_path, branch=args.branch, start_date=parts[0], end_date_str=parts[1])
        else:
            sjc_batch(csv_path, branch=args.branch)


if __name__ == "__main__":
    main()
