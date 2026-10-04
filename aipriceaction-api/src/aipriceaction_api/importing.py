"""Read-only legacy imports; never remove or modify the source archive."""

import csv
import io
import json
import math
import time
from dataclasses import replace
from pathlib import Path

from .domain import Candle, DataError, parse_time


def json_rows(text, source, symbol, iv, provider="legacy", revision="legacy-snapshot"):
    """Preserve numerical precision from a single-symbol legacy API response."""
    try:
        payload = json.loads(text)
        if not isinstance(payload, dict) or set(payload) - {symbol}:
            raise ValueError("Unexpected response symbols")
        records = payload.get(symbol, [])
        if not isinstance(records, list):
            raise ValueError("Expected a candle list")
        rows = {}
        for raw in records:
            if not isinstance(raw, dict) or any(
                raw.get(name, symbol) != symbol for name in ("symbol", "ticker")
            ):
                raise ValueError("Unexpected candle symbol")
            values = [raw[key] for key in ("open", "high", "low", "close", "volume")]
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in values):
                raise ValueError("Expected numeric OHLCV")
            volume = values[-1]
            if not math.isfinite(volume) or volume != int(volume):
                raise ValueError("Expected integer volume")
            stamp = parse_time(raw["time"])
            if iv == "1D":
                stamp = stamp // 86400 * 86400
            row = Candle(
                source, symbol, iv, stamp, *values[:4], int(volume), provider, revision
            ).validate()
            if row.time in rows and rows[row.time] != row:
                raise DataError("Conflicting legacy JSON candle timestamp", 400)
            rows[row.time] = row
        return sorted(rows.values(), key=lambda r: r.time)
    except (KeyError, ValueError, TypeError, OverflowError) as exc:
        raise DataError("Invalid legacy API JSON candles", 400) from exc


def csv_rows(
    text, source, symbol, iv, provider="legacy", revision="legacy-snapshot", allow_empty=False
):
    # The Rust archive writer serializes arrays without a header. Local exports
    # may have named columns, in a different order, including an optional symbol.
    columns = ("time", "open", "high", "low", "close", "volume")
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")))
    first = next(reader, None)
    if first is None:
        if allow_empty:
            return []
        raise DataError("CSV is empty", 400)
    header = [v.strip() for v in first]
    if "time" in header:
        if len(set(header)) != len(header) or not set(columns) <= set(header):
            raise DataError(
                "CSV header requires unique time/open/high/low/close/volume columns", 400
            )
        records = reader
    else:
        from itertools import chain

        header, records = list(columns), chain([first], reader)
    rows = {}
    for number, values in enumerate(records, start=1):
        if not values:
            continue
        if len(values) != len(header):
            raise DataError(f"CSV row {number} has an incorrect column count", 400)
        raw = dict(zip(header, values, strict=True))
        try:
            stamp = parse_time(raw["time"])
            if iv == "1D":
                stamp = stamp // 86400 * 86400
            volume = float(raw["volume"])
            if not math.isfinite(volume) or volume != int(volume):
                raise ValueError("volume must be a finite integer")
            row = Candle(
                source,
                raw.get("symbol") or symbol,
                iv,
                stamp,
                *(float(raw[k]) for k in ("open", "high", "low", "close")),
                int(volume),
                provider,
                revision,
            ).validate()
        except (ValueError, OverflowError) as exc:
            raise DataError(f"Invalid CSV value in row {number}", 400) from exc
        except DataError as exc:
            raise DataError(
                f"{source}/{symbol}/{iv} CSV row {number} at {stamp}: {exc}", exc.status
            ) from exc
        if row.symbol != symbol:
            raise DataError("CSV contains a different symbol than requested", 400)
        if row.time in rows and rows[row.time] != row:
            raise DataError(f"Conflicting duplicate timestamp in CSV row {number}", 400)
        rows[row.time] = row
    if not rows:
        if allow_empty:
            return []
        raise DataError("CSV has no candles", 400)
    return sorted(rows.values(), key=lambda r: r.time)


def import_csv(repo, path, source, symbol, iv, provider="legacy", revision="legacy-snapshot"):
    rows = csv_rows(Path(path).read_text(), source, symbol, iv, provider, revision)
    return repo.put(rows)


def import_historical_snapshot(
    repo, archive, path, source, symbol, iv, revision, captured_at, execute=False, format="json"
):
    """Publish a captured public response without writing or replacing primary candles."""
    if not revision or not 0 < captured_at <= int(time.time()):
        raise DataError("A named revision and past positive capture timestamp are required", 400)
    reader = {"json": json_rows, "csv": csv_rows}.get(format)
    if reader is None:
        raise DataError("Historical snapshot format must be json or csv", 400)
    text = Path(path).read_text()
    rows = [
        replace(row, updated_at=captured_at * 1_000_000_000)
        for row in reader(text, source, symbol, iv, "legacy-api", revision)
    ]
    archive.validate_historical_snapshot(rows)
    state = repo.state(source, symbol, iv)
    if state and revision == state["revision"]:
        raise DataError("Historical snapshot revision must differ from the active series", 400)
    repo.validate_basis(rows)
    archive.validate_historical_capture(rows)
    report = {
        "execute": execute,
        "source": source,
        "symbol": symbol,
        "interval": iv,
        "revision": revision,
        "captured_at": captured_at,
        "rows": len(rows),
        "start": rows[0].time,
        "end": rows[-1].time,
    }
    if execute:
        report["object"] = archive.publish(rows, historical_snapshot=True)
    return report


def import_bundle(
    repo,
    archive,
    path,
    source,
    symbol,
    iv,
    provider="legacy",
    revision="legacy-snapshot",
    recent_floor=None,
):
    rows = csv_rows(Path(path).read_text(), source, symbol, iv, provider, revision)
    older = [r for r in rows if recent_floor is not None and r.time < recent_floor]
    recent = [r for r in rows if recent_floor is None or r.time >= recent_floor]
    if older:
        archive.publish(older)
    return {"recent": repo.put(recent), "archived": len(older)}
