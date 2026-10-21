import json
import math

from fastapi.responses import JSONResponse

from .domain import PERIODS


def _serde_float_text(value):
    """Format finite floats like serde_json's Ryu output."""
    if not math.isfinite(value):
        raise ValueError("Out of range float values are not JSON compliant")
    text = repr(value).lower()
    if "e" not in text:
        return text

    mantissa, exponent_text = text.split("e", 1)
    exponent = int(exponent_text)
    if -5 <= exponent < 16:
        negative = mantissa.startswith("-")
        if negative:
            mantissa = mantissa[1:]
        decimal_at = mantissa.find(".")
        if decimal_at < 0:
            decimal_at = len(mantissa)
        digits = mantissa.replace(".", "")
        decimal_at += exponent
        if decimal_at <= 0:
            result = "0." + ("0" * -decimal_at) + digits
        elif decimal_at >= len(digits):
            result = digits + ("0" * (decimal_at - len(digits))) + ".0"
        else:
            result = digits[:decimal_at] + "." + digits[decimal_at:]
        return "-" + result if negative else result

    return f"{mantissa}e{'+' if exponent >= 0 else ''}{exponent}"


def _serde_json_text(value):
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _serde_float_text(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_serde_json_text(item) for item in value) + "]"
    if isinstance(value, dict):
        items = []
        for key, item in value.items():
            if isinstance(key, str):
                key_text = key
            elif key is None:
                key_text = "null"
            elif key is True:
                key_text = "true"
            elif key is False:
                key_text = "false"
            elif isinstance(key, int):
                key_text = str(key)
            elif isinstance(key, float):
                key_text = _serde_float_text(key)
            else:
                raise TypeError(f"keys must be str, int, float, bool or None, not {type(key).__name__}")
            items.append(
                json.dumps(key_text, ensure_ascii=False, separators=(",", ":"))
                + ":"
                + _serde_json_text(item)
            )
        return "{" + ",".join(items) + "}"
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


class RustJSONResponse(JSONResponse):
    """JSON response using serde_json-compatible finite float formatting."""

    def render(self, content):
        return _serde_json_text(content).encode("utf-8")

_LEGACY_INDEXES = frozenset(
    {
        "VNINDEX",
        "VN30",
        "VN30F1M",
        "HNX30",
        "VN100",
        "VNMIDCAP",
        "VNSMALLCAP",
        "VNALLSHARE",
        "VNXALLSHARE",
        "VNMITECH",
        "VNUTI",
        "VNCONS",
        "VNCOND",
        "VNHEAL",
        "VNIND",
        "VNFIN",
        "VNFINLEAD",
        "VNFINSELECT",
        "VNDIAMOND",
        "VNDIVIDEND",
        "VNREAL",
        "VNENE",
    }
)

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

TICKER_JSON_FIELDS = (
    "time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "symbol",
    *(f"ma{p}" for p in PERIODS),
    *(f"ma{p}_score" for p in PERIODS),
    "close_changed",
    "volume_changed",
    "total_money_changed",
)


def sort_json_value_keys(value):
    """Recursively order JSON objects like serde_json::Value's default map."""
    if isinstance(value, dict):
        return {key: sort_json_value_keys(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [sort_json_value_keys(item) for item in value]
    return value


def legacy_rows(data, mode):
    result = {}
    for symbol, rows in data.items():
        apply = (
            mode == "vn"
            or mode == "all"
            and len(symbol) == 3
            and symbol.isascii()
            and all("A" <= character <= "Z" for character in symbol)
        ) and symbol.upper() not in _LEGACY_INDEXES
        result[symbol] = [
            row | ({k: row[k] / 1000 for k in ("open", "high", "low", "close")} if apply else {})
            for row in rows
        ]
    return result


def csv_response(data):
    # Match the Rust encoder, which writes symbol/time fields directly without
    # CSV quoting. The web parser is positional and does not implement quoted
    # CSV fields, so preserving Rust's wire bytes matters for unusual symbols.
    output = [",".join(CSV_COLUMNS)]
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
            output.append(",".join(str(value) for value in values))
    return "\n".join(output) + "\n"
