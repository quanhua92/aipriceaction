import csv
import io

from .domain import INDEXES, PERIODS

CSV_COLUMNS = (
    "symbol",
    "time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    *(f"ma{p}" for p in PERIODS),
    *(f"ma{p}_score" for p in PERIODS),
    "close_changed",
    "volume_changed",
    "total_money_changed",
)


def legacy_rows(data, mode):
    result = {}
    for symbol, rows in data.items():
        apply = (
            mode == "vn"
            or mode == "all"
            and len(symbol) == 3
            and symbol.isascii()
            and symbol.isupper()
        ) and symbol not in INDEXES
        result[symbol] = [
            row | ({k: row[k] / 1000 for k in ("open", "high", "low", "close")} if apply else {})
            for row in rows
        ]
    return result


def csv_response(data):
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for _, rows in sorted(data.items()):
        for row in rows:
            close = abs(row["close"])
            decimals = (
                2
                if close == 0 or close >= 100
                else 6
                if close < 1e-5
                else 5
                if close < 1e-3
                else 4
                if close < 1
                else 3
            )
            price_fields = {"open", "high", "low", "close", *(f"ma{p}" for p in PERIODS)}
            values = []
            for name in CSV_COLUMNS:
                value = row.get(name)
                if value is None:
                    values.append("")
                elif name in price_fields:
                    values.append(f"{value:.{decimals}f}")
                elif name in ("symbol", "time", "volume"):
                    values.append(value)
                else:
                    values.append(f"{value:.4f}".rstrip("0").rstrip("."))
            writer.writerow(values)
    return out.getvalue()
