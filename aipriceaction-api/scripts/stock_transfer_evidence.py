"""Source-bound stock transfer annotations, independent of runtime candle recovery."""

import json
import re
from datetime import date, timedelta

from aipriceaction_api.domain import DataError
from scripts.check_hose_scheduled_dates import dates_between, verify_source


def load_transfers(path, source_files):
    if path is None:
        if source_files:
            raise DataError("Transfer source files require a declared transfer review")
        return None, {}
    declaration = json.loads(path.read_text())
    events = declaration.get("events", [])
    if (
        declaration.get("schema") != 1
        or declaration.get("source") != "vn"
        or not isinstance(events, list)
        or not 1 <= len(events) <= 100
    ):
        raise DataError("Unsupported stock transfer declaration")
    if len(source_files) != 2 * len(events):
        raise DataError("Supply both source files for every declared transfer in order")
    dates = {}
    for i, event in enumerate(events):
        symbol = event.get("symbol", "")
        if (
            not re.fullmatch(r"[A-Z]{3}", symbol)
            or event.get("kind") != "venue_transfer"
            or event.get("from_venue") != "UPCOM"
            or event.get("to_venue") != "HOSE"
            or [s.get("role") for s in event.get("sources", [])]
            != ["last_trading_day", "first_trading_day"]
        ):
            raise DataError("Unsupported reviewed stock transfer scope")
        last = date.fromisoformat(event["last_trading_date"])
        first = date.fromisoformat(event["first_trading_date"])
        if not 1 < (first - last).days <= 90 or first.weekday() > 4 or last.weekday() > 4:
            raise DataError("Invalid stock transfer trading boundaries")
        for source, source_file in zip(
            event["sources"], source_files[2 * i : 2 * i + 2], strict=True
        ):
            verify_source(source, source_file)
        between = set(dates_between(last + timedelta(days=1), first - timedelta(days=1)))
        existing = dates.setdefault(symbol, set())
        if existing & between:
            raise DataError("Overlapping stock transfer review dates")
        existing.update(between)
    return declaration, dates
