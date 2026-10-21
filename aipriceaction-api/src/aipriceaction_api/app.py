import asyncio
import hmac
import json
import logging
import math
import mimetypes
import os
import re
import sqlite3
import stat
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from email.utils import formatdate, parsedate_to_datetime
from functools import partial
from typing import Annotated, Any, Literal
from urllib.parse import unquote_to_bytes

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, BeforeValidator, Field, field_validator
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Match

from .analysis import Analysis
from .archive import Archive
from .catalog import Catalog
from .config import Settings, trim_rust_whitespace
from .domain import SOURCES, DataError, base_interval, date_bounds, interval, mode
from .history import History
from .redis_client import create_redis_client, is_snapshot_command_miss
from .redis_snapshots import (
    read_redis_snapshot,
    validate_stock_data_rows,
    write_redis_snapshot,
)
from .request_coalescing import AsyncSingleFlight
from .response_cache import TickersResponseCache
from .responses import (
    TICKER_JSON_FIELDS,
    csv_response,
    legacy_rows,
    sort_json_value_keys,
)
from .responses import (
    RustJSONResponse as JSONResponse,
)
from .storage import RefreshScheduleError, Repository
from .transport import RustCompatibleCompressionMiddleware
from .uploads import router_for as uploads_router_for

log = logging.getLogger(__name__)
request_cancelled: ContextVar[asyncio.Event | None] = ContextVar("request_cancelled", default=None)
RUST_CORS_VARY = "origin, access-control-request-method, access-control-request-headers"
RUST_FIXED_PATHS = frozenset(
    {
        "/explorer",
        "/tickers",
        "/health",
        "/tickers/group",
        "/tickers/name",
        "/tickers/info",
        "/tickers/refresh",
        "/analysis/top-performers",
        "/analysis/ma-scores-by-sector",
        "/analysis/volume-profile",
        "/analysis/rrg",
        "/upload/markdown",
    }
)
RUST_CORS_EXPOSE_HEADERS = (
    "x-data-source",
    "x-redis-base",
    "x-redis-raw-count",
    "x-redis-aligned",
    "x-redis-limit",
    "cf-cache-status",
    "cf-ray",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "retry-after",
)
HEALTH_SNAPSHOT_REFRESH_SECONDS = 30
RUST_QUERY_FIELDS = {
    "/tickers": {
        "symbol": "list_string",
        "interval": "optional_string",
        "start_date": "optional_string",
        "end_date": "optional_string",
        "limit": "optional_i64",
        "legacy": "bool",
        "format": "string",
        "cache": "bool",
        "mode": "mode",
        "redis": "bool",
        "ma": "bool",
        "ema": "bool",
        "snap": "bool",
    },
    "/tickers/group": {"mode": "mode"},
    "/tickers/name": {"mode": "mode"},
    "/tickers/info": {"ticker": "optional_string"},
    "/analysis/top-performers": {
        "date": "optional_string",
        "sort_by": "string",
        "limit": "optional_usize",
        "direction": "string",
        "min_volume": "optional_u64",
        "mode": "mode",
        "ema": "bool",
        "snap": "bool",
        "sector": "optional_string",
    },
    "/analysis/ma-scores-by-sector": {
        "date": "optional_string",
        "ma_period": "u32",
        "min_score": "f64",
        "above_threshold_only": "bool",
        "top_per_sector": "optional_usize",
        "mode": "mode",
        "ema": "bool",
        "snap": "bool",
    },
    "/analysis/volume-profile": {
        "symbol": "required_string",
        "date": "optional_string",
        "start_date": "optional_string",
        "end_date": "optional_string",
        "mode": "string",
        "bins": "optional_usize",
        "value_area_pct": "optional_f64",
    },
    "/analysis/rrg": {
        "benchmark": "optional_string",
        "algorithm": "rrg_algorithm",
        "period": "usize",
        "trails": "usize",
        "min_volume": "i64",
        "date": "optional_string",
        "mode": "mode",
        "ema": "bool",
        "snap": "bool",
    },
}
RUST_OPTIONAL_QUERY_FIELDS = {
    ("/tickers", "interval"),
    ("/tickers", "start_date"),
    ("/tickers", "end_date"),
    ("/tickers", "limit"),
    ("/tickers/info", "ticker"),
}
RUST_REQUIRED_QUERY_FIELDS = {"/analysis/volume-profile": ("symbol",)}
_RUST_FLOAT_QUERY = re.compile(
    r"[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?|inf(?:inity)?|nan)\Z",
    re.IGNORECASE | re.ASCII,
)
_MIME_TOKEN_CHARS = frozenset(
    "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
)
_RUST_JSON_FIELD_KINDS = {
    "/tickers/refresh": {"interval": "string", "mode": "mode", "key": "optional_string"},
}
_RUST_REFRESH_MODES = {"vn", "stock", "stocks", "crypto", "cryptos", "yahoo", "all"}
_JSON_NUMBER = re.compile(rb"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_JSON_MAX_DEPTH = 128
_JSON_DATA_ERROR = "Failed to deserialize the JSON body into the target type"
_JSON_SYNTAX_ERROR = "Failed to parse the request body as JSON"


class _SerdeJsonRejection(Exception):
    def __init__(self, kind, reason, position, field=None):
        super().__init__(reason)
        self.kind = kind
        self.reason = reason
        self.position = position
        self.field = field

    def render(self, data):
        line = data.count(b"\n", 0, self.position) + 1
        previous_newline = data.rfind(b"\n", 0, self.position)
        column = self.position - previous_newline - 1
        reason = f"{self.field}: {self.reason}" if self.field else self.reason
        return f"{reason} at line {line} column {column}"


def _serde_syntax(data, position, reason, field=None):
    raise _SerdeJsonRejection("syntax", reason, position, field)


def _serde_data(data, position, reason, field=None):
    raise _SerdeJsonRejection("data", reason, position, field)


def _serde_unexpected(data, position):
    if position >= len(data):
        return "end of input"
    first = data[position]
    if first == ord('"'):
        try:
            end, value = _consume_json_string(data, position, validate_utf8=True)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return "string"
        return f"string {json.dumps(value, ensure_ascii=False)}"
    if first == ord("{"):
        return "map"
    if first == ord("["):
        return "sequence"
    if data[position : position + 4] == b"null":
        return "null"
    if data[position : position + 4] == b"true":
        return "boolean `true`"
    if data[position : position + 5] == b"false":
        return "boolean `false`"
    match = _JSON_NUMBER.match(data, position)
    if match is not None:
        token = match.group().decode("ascii")
        if any(character in token for character in ".eE"):
            number = float(token)
            return f"floating point `{number!r}`"
        number = int(token)
        if token.startswith("-") and number == 0:
            return "floating point `-0.0`"
        if number < -(1 << 63) or number > (1 << 64) - 1:
            return f"floating point `{float(number)!r}`"
        return f"integer `{number}`"
    return "unit value"


def _reject_json_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def _parse_finite_json_float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"JSON number is out of range: {value}")
    return parsed


def _skip_json_whitespace(value, position):
    while position < len(value) and value[position] in " \t\r\n":
        position += 1
    return position


def _valid_object_value_boundary(value, position):
    position = _skip_json_whitespace(value, position)
    return position < len(value) and value[position] in ",}"


def _json_depth_exceeds(value, limit=128, base=0):
    depth = base
    in_string = False
    escaped = False
    for character in value:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > limit:
                return True
        elif character in "]}":
            depth -= 1
    return False


def _valid_surrogates(value):
    position = 0
    while position < len(value):
        codepoint = ord(value[position])
        if 0xD800 <= codepoint <= 0xDBFF:
            position += 1
            if position == len(value) or not 0xDC00 <= ord(value[position]) <= 0xDFFF:
                return False
        elif 0xDC00 <= codepoint <= 0xDFFF:
            return False
        position += 1
    return True


def _valid_json_strings(value):
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if not _valid_surrogates(item):
                return False
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return True


def _normalize_surrogate_escapes(value):
    """Combine valid JSON UTF-16 pairs so Python and Serde see the same text."""
    output = []
    in_string = False
    position = 0
    while position < len(value):
        character = value[position]
        if not in_string:
            output.append(character)
            if character == '"':
                in_string = True
            position += 1
            continue
        if character == '"':
            output.append(character)
            in_string = False
            position += 1
            continue
        if character != "\\":
            output.append(character)
            position += 1
            continue
        if position + 1 < len(value) and value[position + 1] == "u":
            digits = value[position + 2 : position + 6]
            if len(digits) == 4 and all(
                character in "0123456789abcdefABCDEF" for character in digits
            ):
                codepoint = int(digits, 16)
                if 0xD800 <= codepoint <= 0xDBFF:
                    low_start = position + 6
                    low_digits = value[low_start + 2 : low_start + 6]
                    if (
                        value[low_start : low_start + 2] == "\\u"
                        and len(low_digits) == 4
                        and all(character in "0123456789abcdefABCDEF" for character in low_digits)
                        and 0xDC00 <= int(low_digits, 16) <= 0xDFFF
                    ):
                        low = int(low_digits, 16)
                        output.append(chr(0x10000 + ((codepoint - 0xD800) << 10) + low - 0xDC00))
                        position = low_start + 6
                        continue
                    if value[low_start : low_start + 2] == "\\u":
                        raise ValueError("high surrogate followed by non-low surrogate")
                    raise ValueError("unpaired high surrogate")
                if 0xDC00 <= codepoint <= 0xDFFF:
                    raise ValueError("unpaired low surrogate")
                output.append(value[position : position + 6])
                position += 6
                continue
        output.append(value[position : position + 2])
        position += min(2, len(value) - position)
    return "".join(output)


def _skip_json_bytespace(data, position):
    while position < len(data) and data[position] in b" \t\r\n":
        position += 1
    return position


def _consume_json_string(data, position, *, validate_utf8, field=None):
    if position >= len(data) or data[position] != 0x22:
        _serde_syntax(data, min(position + 1, len(data)), "expected string", field)
    index = position + 1
    while index < len(data):
        byte = data[index]
        if byte == 0x22:
            end = index + 1
            if not validate_utf8:
                return end, None
            try:
                token = data[position:end].decode("utf-8")
                normalized = _normalize_surrogate_escapes(token)
                decoded = json.loads(normalized)
            except ValueError as exc:
                if str(exc) == "unpaired high surrogate":
                    _serde_syntax(data, end, "unexpected end of hex escape", field)
                if str(exc) in {
                    "high surrogate followed by non-low surrogate",
                    "unpaired low surrogate",
                }:
                    _serde_syntax(data, end - 1, "lone leading surrogate in hex escape", field)
                _serde_syntax(data, end, "invalid unicode code point", field)
            except (UnicodeDecodeError, json.JSONDecodeError):
                _serde_syntax(data, end, "invalid unicode code point", field)
            if not _valid_surrogates(decoded):
                _serde_syntax(data, end, "invalid unicode code point", field)
            return end, decoded
        if byte < 0x20:
            _serde_syntax(
                data,
                index + 1,
                "control character (\\u0000-\\u001F) found while parsing a string",
                field,
            )
        if byte != 0x5C:
            index += 1
            continue
        index += 1
        if index >= len(data):
            _serde_syntax(data, len(data), "EOF while parsing a string", field)
        escape = data[index]
        if escape in b'"\\\\/bfnrt':
            index += 1
            continue
        if escape != ord("u") or index + 4 >= len(data):
            if escape != ord("u"):
                _serde_syntax(data, index + 1, "invalid escape", field)
            _serde_syntax(data, len(data), "EOF while parsing a string", field)
        if any(byte not in b"0123456789abcdefABCDEF" for byte in data[index + 1 : index + 5]):
            _serde_syntax(data, index + 5, "invalid escape", field)
        index += 5
    _serde_syntax(data, len(data), "EOF while parsing a string", field)


def _consume_json_number(data, position, *, validate_number, field=None):
    match = _JSON_NUMBER.match(data, position)
    if match is None:
        _serde_syntax(data, position + 1, "invalid number", field)
    end = match.end()
    token = match.group().decode("ascii")
    if token == "0" and end < len(data) and ord("0") <= data[end] <= ord("9"):
        _serde_syntax(data, end + 1, "invalid number", field)
    if end < len(data) and data[end] in b".eE":
        if end + 1 >= len(data):
            _serde_syntax(data, len(data), "EOF while parsing a value", field)
        _serde_syntax(data, end + 2, "invalid number", field)
    if validate_number:
        token = match.group().decode("ascii")
        if "." in token or "e" in token.lower():
            if not math.isfinite(float(token)):
                _serde_syntax(data, end, "number out of range", field)
        else:
            value = int(token)
            if value < -(1 << 63) or value > (1 << 64) - 1:
                if not math.isfinite(float(token)):
                    _serde_syntax(data, end, "number out of range", field)
    return end


def _consume_json_value(data, position, *, ignored, base_depth=0, field=None):
    """Consume one JSON value; ignored values follow Serde's IgnoredAny rules."""

    def start_value(offset, parent_depth, value_field):
        offset = _skip_json_bytespace(data, offset)
        if offset >= len(data):
            _serde_syntax(data, len(data), "EOF while parsing a value", value_field)
        byte = data[offset]
        if byte == 0x22:
            return (
                _consume_json_string(data, offset, validate_utf8=not ignored, field=value_field)[0],
                None,
            )
        if byte in (ord("{"), ord("[")):
            depth = parent_depth + 1
            if not ignored and depth >= _JSON_MAX_DEPTH:
                _serde_syntax(data, offset + 1, "recursion limit exceeded", value_field)
            kind = "object" if byte == ord("{") else "array"
            state = "key_or_end" if kind == "object" else "value_or_end"
            return offset + 1, {
                "kind": kind,
                "state": state,
                "depth": depth,
                "field": value_field,
                "index": 0,
            }
        if byte == ord("t") and data[offset : offset + 4] == b"true":
            return offset + 4, None
        if byte == ord("f") and data[offset : offset + 5] == b"false":
            return offset + 5, None
        if byte == ord("n") and data[offset : offset + 4] == b"null":
            return offset + 4, None
        if byte == ord("-") or ord("0") <= byte <= ord("9"):
            return (
                _consume_json_number(data, offset, validate_number=not ignored, field=value_field),
                None,
            )
        _serde_syntax(data, offset + 1, "expected value", value_field)

    position, opened = start_value(position, base_depth, field)
    if opened is None:
        return position
    stack = [opened]
    while stack:
        frame = stack[-1]
        position = _skip_json_bytespace(data, position)
        if position >= len(data):
            detail = "object" if frame["kind"] == "object" else "array"
            _serde_syntax(data, len(data), f"EOF while parsing an {detail}", frame["field"])
        byte = data[position]
        if frame["kind"] == "object":
            state = frame["state"]
            if state in ("key_or_end", "key"):
                if byte == ord("}") and state == "key_or_end":
                    position += 1
                    stack.pop()
                    continue
                if byte == ord("}") and state == "key":
                    _serde_syntax(data, position + 1, "trailing comma", frame["field"])
                if byte != 0x22:
                    _serde_syntax(data, position + 1, "key must be a string", frame["field"])
                position, key = _consume_json_string(
                    data,
                    position,
                    validate_utf8=not ignored,
                    field=frame["field"],
                )
                frame["key"] = key
                frame["state"] = "colon"
                continue
            if state == "colon":
                if byte != ord(":"):
                    _serde_syntax(data, position + 1, "expected `:`", frame["field"])
                frame["state"] = "value"
                position += 1
                continue
            if state == "value":
                frame["state"] = "comma_or_end"
                child_field = (
                    frame["field"]
                    if ignored
                    else f"{frame['field']}.{frame['key']}"
                    if frame["field"]
                    else frame.get("key")
                )
                position, opened = start_value(position, frame["depth"], child_field)
                if opened is not None:
                    stack.append(opened)
                continue
            if byte == ord("}"):
                position += 1
                stack.pop()
                continue
            if byte != ord(","):
                _serde_syntax(data, position + 1, "expected `,` or `}`", frame["field"])
            frame["state"] = "key"
            position += 1
            continue

        state = frame["state"]
        if state in ("value_or_end", "value"):
            if byte == ord("]") and state == "value_or_end":
                position += 1
                stack.pop()
                continue
            if byte == ord("]") and state == "value":
                reason = "expected value" if ignored else "trailing comma"
                _serde_syntax(data, position + 1, reason, frame["field"])
            frame["state"] = "comma_or_end"
            child_field = (
                frame["field"]
                if ignored
                else f"{frame['field']}[{frame['index']}]"
                if frame["field"]
                else f"[{frame['index']}]"
            )
            frame["index"] += 1
            position, opened = start_value(position, frame["depth"], child_field)
            if opened is not None:
                stack.append(opened)
            continue
        if byte == ord("]"):
            position += 1
            stack.pop()
            continue
        if byte != ord(","):
            _serde_syntax(data, position + 1, "expected `,` or `]`", frame["field"])
        frame["state"] = "value"
        position += 1
    return position


def _normalize_json_number_values(value):
    if isinstance(value, int) and not isinstance(value, bool):
        if value < -(1 << 63) or value > (1 << 64) - 1:
            converted = float(value)
            if not math.isfinite(converted):
                raise ValueError("JSON integer is out of range")
            return converted
        return value
    if isinstance(value, list):
        return [_normalize_json_number_values(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_json_number_values(item) for key, item in value.items()}
    return value


def _consume_typed_json_field(kind, data, position, field):
    start = position
    first = data[position] if position < len(data) else None
    if kind == "any":
        position = _consume_json_value(data, position, ignored=False, base_depth=1, field=field)
        token = _normalize_surrogate_escapes(data[start:position].decode("utf-8"))
        parsed = json.loads(
            token,
            parse_constant=_reject_json_constant,
            parse_float=_parse_finite_json_float,
        )
        return position, _normalize_json_number_values(parsed)
    if first == 0x22:
        position, parsed = _consume_json_string(data, position, validate_utf8=True, field=field)
        if kind == "mode" and parsed not in _RUST_REFRESH_MODES:
            variants = "`stock`, `stocks`, `vn`, `crypto`, `cryptos`, `yahoo`, `all`"
            _serde_data(
                data,
                position,
                f"unknown variant `{parsed}`, expected one of {variants}",
                field,
            )
        return position, parsed
    if kind == "optional_string" and data[position : position + 4] == b"null":
        position = _consume_json_value(data, position, ignored=False, base_depth=1, field=field)
        return position, None
    if first in (ord("{"), ord("[")):
        unexpected = _serde_unexpected(data, position)
        _serde_data(
            data,
            position + 1,
            f"invalid type: {unexpected}, expected a string",
            field,
        )
    position = _consume_json_value(data, position, ignored=False, base_depth=1, field=field)
    if kind == "optional_string" and data[start:position] == b"null":
        return position, None
    unexpected = _serde_unexpected(data, start)
    _serde_data(data, position, f"invalid type: {unexpected}, expected a string", field)


def _rust_json_sequence_payload(path, data, position):
    fields = _RUST_JSON_FIELD_KINDS.get(path, {"secret": "string", "value": "any"})
    required = {"secret", "value"} if path != "/tickers/refresh" else {"interval"}
    names = list(fields)
    parsed_fields = {}
    position += 1
    index = 0
    after_comma = False
    while True:
        position = _skip_json_bytespace(data, position)
        if position >= len(data):
            _serde_syntax(data, len(data), "EOF while parsing an array")
        if data[position] == ord("]"):
            if after_comma:
                _serde_syntax(data, position + 1, "trailing comma")
            position += 1
            break
        if index >= len(names):
            # The derived visitor stops after the declared fields; Axum's final
            # deserializer.end() then rejects any unconsumed sequence values.
            _serde_syntax(data, position + 1, "trailing characters")
        name = names[index]
        position, parsed_fields[name] = _consume_typed_json_field(
            fields[name], data, position, f"[{index}]"
        )
        after_comma = False
        index += 1
        position = _skip_json_bytespace(data, position)
        if position >= len(data):
            _serde_syntax(data, len(data), "EOF while parsing an array")
        if data[position] == ord("]"):
            position += 1
            break
        if data[position] != ord(","):
            _serde_syntax(data, position + 1, "expected `,` or `]`")
        position += 1
        after_comma = True

    if required.difference(parsed_fields):
        struct_name = "RefreshQuery" if path == "/tickers/refresh" else "SyncPostBody"
        minimum = 1 if path == "/tickers/refresh" else 2
        element_word = "element" if minimum == 1 else "elements"
        _serde_data(
            data,
            position,
            f"invalid length {index}, expected struct {struct_name} with {minimum} {element_word}",
        )
    if _skip_json_bytespace(data, position) != len(data):
        trailing = _skip_json_bytespace(data, position)
        _serde_syntax(data, trailing + 1, "trailing characters")
    return json.dumps(
        parsed_fields, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _rust_json_payload(path, data):
    """Parse typed fields while skipping unknown fields like Serde IgnoredAny."""
    fields = _RUST_JSON_FIELD_KINDS.get(path, {"secret": "string", "value": "any"})
    position = _skip_json_bytespace(data, 0)
    if position >= len(data):
        _serde_syntax(data, len(data), "EOF while parsing a value")
    if data[position] == ord("["):
        return _rust_json_sequence_payload(path, data, position)
    if data[position] != ord("{"):
        end = _consume_json_value(data, position, ignored=False)
        expected = "struct RefreshQuery" if path == "/tickers/refresh" else "struct SyncPostBody"
        _serde_data(
            data,
            end,
            f"invalid type: {_serde_unexpected(data, position)}, expected {expected}",
        )

    position += 1
    seen = set()
    parsed_fields = {}
    after_comma = False
    while True:
        position = _skip_json_bytespace(data, position)
        if position >= len(data):
            _serde_syntax(data, len(data), "EOF while parsing an object")
        if data[position] == ord("}"):
            if after_comma:
                _serde_syntax(data, position + 1, "trailing comma")
            position += 1
            break
        position, name = _consume_json_string(data, position, validate_utf8=True)
        after_comma = False
        field_end = position
        position = _skip_json_bytespace(data, position)
        if position >= len(data) or data[position] != ord(":"):
            _serde_syntax(data, min(position + 1, len(data)), "expected `:`")
        position = _skip_json_bytespace(data, position + 1)
        kind = fields.get(name)
        if kind is None:
            position = _consume_json_value(data, position, ignored=True, base_depth=1, field=name)
        else:
            if name in seen:
                _serde_data(data, field_end, f"duplicate field `{name}`")
            seen.add(name)
            position, parsed_fields[name] = _consume_typed_json_field(kind, data, position, name)

        position = _skip_json_bytespace(data, position)
        if position >= len(data):
            _serde_syntax(data, len(data), "EOF while parsing an object")
        if data[position] == ord(","):
            position += 1
            after_comma = True
            continue
        if data[position] == ord("}"):
            position += 1
            break
        _serde_syntax(data, position + 1, "expected `,` or `}`")

    required = {"secret", "value"} if path != "/tickers/refresh" else {"interval"}
    if required.difference(parsed_fields):
        field_order = list(fields)
        missing = next(
            name for name in field_order if name in required and name not in parsed_fields
        )
        _serde_data(data, position, f"missing field `{missing}`")
    if _skip_json_bytespace(data, position) != len(data):
        trailing = _skip_json_bytespace(data, position)
        _serde_syntax(data, trailing + 1, "trailing characters")
    return json.dumps(
        parsed_fields, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _json_field_specs(path):
    return _RUST_JSON_FIELD_KINDS.get(path, {"secret": "string", "value": "any"})


def _early_json_data_error(path, value):
    """Catch Serde data errors encountered before a later JSON syntax error."""
    position = _skip_json_whitespace(value, 0)
    if position == len(value) or value[position] != "{":
        return False

    fields = _json_field_specs(path)
    known = set(fields)
    seen = set()
    decoder = json.JSONDecoder(
        parse_constant=_reject_json_constant, parse_float=_parse_finite_json_float
    )
    position += 1
    while True:
        position = _skip_json_whitespace(value, position)
        if position == len(value) or value[position] == "}":
            return False
        try:
            name, key_end = decoder.raw_decode(value, position)
        except (json.JSONDecodeError, ValueError, RecursionError):
            return False
        if not isinstance(name, str):
            return False
        if name in known:
            if name in seen:
                return True
            seen.add(name)

        position = _skip_json_whitespace(value, key_end)
        if position == len(value) or value[position] != ":":
            return False
        position = _skip_json_whitespace(value, position + 1)
        if position == len(value):
            return False

        kind = fields.get(name, "any")
        first = value[position]
        if kind in ("string", "mode", "optional_string") and first != '"':
            if kind == "optional_string" and first == "n":
                try:
                    parsed, end = decoder.raw_decode(value, position)
                except (json.JSONDecodeError, ValueError, RecursionError):
                    return False
                if parsed is not None:
                    return True
            elif first in "[{":
                return True
            else:
                try:
                    parsed, end = decoder.raw_decode(value, position)
                except (json.JSONDecodeError, ValueError, RecursionError):
                    return False
                if not _valid_json_strings(parsed):
                    return False
                if not _valid_object_value_boundary(value, end):
                    return False
                return True
        else:
            try:
                parsed, end = decoder.raw_decode(value, position)
            except (json.JSONDecodeError, ValueError, RecursionError):
                return False
            fragment = value[position:end]
            if _json_depth_exceeds(fragment, base=1):
                return False
            if not _valid_json_strings(parsed):
                return False
            if kind == "mode" and parsed not in _RUST_REFRESH_MODES:
                return True
            if kind == "optional_string" and parsed is not None and not isinstance(parsed, str):
                return True

        position = _skip_json_whitespace(value, end)
        if position == len(value):
            return False
        if value[position] == ",":
            position += 1
            continue
        if value[position] == "}":
            return False
        return False


def rust_json_content_type_error(request):
    """Return Axum's missing/invalid JSON media-type rejection, if applicable."""
    path = request.url.path
    if not (path == "/tickers/refresh" or re.fullmatch(r"/sync/[^/]+", path)):
        return None
    raw_content_type = next(
        (
            value
            for name, value in request.scope.get("headers", [])
            if name.lower() == b"content-type"
        ),
        b"",
    )
    if _rust_json_content_type_matches(raw_content_type):
        return None
    return PlainTextResponse("Expected request with `Content-Type: application/json`", 415)


def _rust_json_content_type_matches(raw_content_type: bytes) -> bool:
    """Match Axum 0.8.9's `mime` 0.3.17 JSON content-type check."""
    if any(byte != 9 and not 32 <= byte < 127 for byte in raw_content_type):
        # HeaderValue::to_str accepts visible ASCII and HTAB only.
        return False
    value = raw_content_type.decode("ascii")
    media_type, separator, parameters = value.partition(";")
    if media_type.count("/") != 1:
        return False
    top_level, subtype = media_type.split("/", 1)
    if (
        not top_level
        or not subtype
        or not all(char in _MIME_TOKEN_CHARS for char in top_level)
        or not all(char in _MIME_TOKEN_CHARS for char in subtype)
    ):
        return False
    if top_level.lower() != "application":
        return False
    lowered_subtype = subtype.lower()
    if lowered_subtype != "json":
        last_plus = lowered_subtype.rfind("+")
        if last_plus <= 0 or lowered_subtype[last_plus + 1 :] != "json":
            return False
    if not separator:
        return True

    # mime's parser accepts a trailing semicolon (and spaces after it), but a
    # parameter must have a nonempty token name and an immediate equals sign.
    position = 0
    while position < len(parameters):
        while position < len(parameters) and parameters[position] == " ":
            position += 1
        if position == len(parameters):
            return True

        name_start = position
        while position < len(parameters) and parameters[position] in _MIME_TOKEN_CHARS:
            position += 1
        if position == name_start or position == len(parameters) or parameters[position] != "=":
            return False
        position += 1
        if position == len(parameters):
            # mime treats a missing value at end-of-input as an empty token.
            return True

        if parameters[position] == '"':
            position += 1
            value_start = position
            while position < len(parameters) and parameters[position] != '"':
                char = parameters[position]
                if ord(char) <= 31 or ord(char) == 127:
                    return False
                position += 1
            if position == value_start or position == len(parameters):
                return False
            position += 1
            while position < len(parameters) and parameters[position] == " ":
                position += 1
        else:
            value_start = position
            while position < len(parameters) and parameters[position] in _MIME_TOKEN_CHARS:
                position += 1
            if position == value_start:
                return False

        if position == len(parameters):
            return True
        if parameters[position] != ";":
            return False
        position += 1

    return True


def _rust_header_value_to_str(request, name: bytes) -> str | None:
    """Match `http::HeaderValue::to_str` for one ASGI header field."""
    value = next(
        (
            raw_value
            for raw_name, raw_value in request.scope.get("headers", [])
            if raw_name.lower() == name
        ),
        None,
    )
    if value is None or any(byte != 9 and not 32 <= byte < 127 for byte in value):
        return None
    return value.decode("ascii")


def rust_json_body_error(request, body):
    """Return Axum's JSON extractor rejection and its normalized body bytes."""
    path = request.url.path
    if not (path == "/tickers/refresh" or re.fullmatch(r"/sync/[^/]+", path)):
        return None, body
    try:
        normalized = _rust_json_payload(path, body)
    except _SerdeJsonRejection as exc:
        prefix = _JSON_DATA_ERROR if exc.kind == "data" else _JSON_SYNTAX_ERROR
        status = 422 if exc.kind == "data" else 400
        return PlainTextResponse(f"{prefix}: {exc.render(body)}", status), body
    except TypeError:
        return PlainTextResponse(_JSON_DATA_ERROR, 422), body
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError):
        return PlainTextResponse(_JSON_SYNTAX_ERROR, 400), body
    return None, normalized


def rust_query_value_error(kind, value):
    """Return Rust's scalar query parse error for a query value."""
    if kind.startswith("optional_"):
        kind = kind.removeprefix("optional_")
    integer_ranges = {
        "u32": (0, (1 << 32) - 1),
        "u64": (0, (1 << 64) - 1),
        "usize": (0, (1 << 64) - 1),
        "i64": (-(1 << 63), (1 << 63) - 1),
    }
    if kind == "bool":
        return None if value in ("true", "false") else "provided string was not `true` or `false`"
    if kind in integer_ranges:
        if value == "":
            return "cannot parse integer from empty string"
        signed = kind == "i64"
        low, high = integer_ranges[kind]
        negative = signed and value.startswith("-")
        digits = value[1:] if value.startswith(("+", "-")) else value
        if value.startswith("-") and not signed:
            return "invalid digit found in string"
        if not digits:
            return "invalid digit found in string"
        magnitude_limit = -low if negative else high
        parsed = 0
        for character in digits:
            if character < "0" or character > "9":
                return "invalid digit found in string"
            parsed = parsed * 10 + ord(character) - ord("0")
            # Rust's FromStr reports overflow at the digit where it occurs,
            # even if a later character is also invalid.
            if parsed > magnitude_limit:
                return (
                    "number too small to fit in target type"
                    if negative
                    else "number too large to fit in target type"
                )
        return None
    if kind == "f64":
        if value == "":
            return "cannot parse float from empty string"
        if not _RUST_FLOAT_QUERY.fullmatch(value):
            return "invalid float literal"
        return None
    if kind == "mode":
        if value not in ("vn", "stock", "stocks", "crypto", "cryptos", "yahoo", "all"):
            return (
                f"unknown variant `{value}`, expected one of `stock`, `stocks`, `vn`, "
                "`crypto`, `cryptos`, `yahoo`, `all`"
            )
    if kind == "rrg_algorithm" and value not in ("jdk", "mascore"):
        return f"unknown variant `{value}`, expected `jdk` or `mascore`"
    return None


def _raw_upload_session_route(request):
    raw_path = request.scope.get("raw_path", b"").split(b"?", 1)[0]
    if not raw_path.startswith(b"/uploads/"):
        return False
    segments = raw_path.removeprefix(b"/uploads/").split(b"/")
    # Axum matches encoded separators as part of a path parameter, but literal
    # separators create a different route. This check preserves that ordering
    # before Notes query extraction runs.
    return len(segments) == 1 and bool(segments[0])


def _raw_upload_markdown_route(request):
    raw_path = request.scope.get("raw_path", b"").split(b"?", 1)[0]
    if not raw_path.startswith(b"/uploads/"):
        return False
    segments = raw_path.removeprefix(b"/uploads/").split(b"/")
    return (
        len(segments) == 3
        and bool(segments[0])
        and segments[1] == b"markdown"
        and bool(segments[2])
    )


def _raw_request_path(request):
    raw_path = request.scope.get("raw_path")
    if raw_path is None:
        raw_path = request.scope.get("path", "").encode("utf-8")
    return raw_path.split(b"?", 1)[0]


def _raw_sync_key(request):
    raw_path = _raw_request_path(request)
    prefix = b"/sync/"
    if not raw_path.startswith(prefix):
        return None
    key = raw_path[len(prefix) :]
    if not key or b"/" in key:
        return None
    return key


def _decode_axum_path_parameter(raw_value):
    return unquote_to_bytes(raw_value).decode("utf-8")


def _raw_path_rejection(request):
    """Match Axum's raw-path route lookup and Path UTF-8 rejection."""
    raw_path = _raw_request_path(request)
    path = request.scope.get("path", "")
    request_method = request.scope.get("aipa.original_method", request.method)

    # Axum matches fixed routes against the raw URI path. Starlette receives
    # the decoded ASGI path, so reject percent-encoded aliases before dispatch.
    if path in RUST_FIXED_PATHS and raw_path != path.encode("utf-8"):
        return JSONResponse({"error": "not found"}, 404)
    if path == "/public" or path.startswith("/public/"):
        if raw_path != b"/public" and not raw_path.startswith(b"/public/"):
            return JSONResponse({"error": "not found"}, 404)
        decoded_static_path = unquote_to_bytes(raw_path).split(b"/")
        # Some ASGI transports pass an already over-decoded ``scope.path``
        # while preserving the original ``raw_path``. Reject parent segments
        # in either representation so StaticFiles cannot normalize a path
        # differently from tower-http's single raw-path decode.
        decoded_scope_path = path.encode("utf-8").split(b"/")
        if b".." in decoded_static_path or b".." in decoded_scope_path:
            return Response(status_code=404)

    raw_sync_key = _raw_sync_key(request)
    if path.startswith("/sync/") and raw_sync_key is None:
        return JSONResponse({"error": "not found"}, 404)
    if raw_sync_key is not None and request_method in ("GET", "HEAD", "POST"):
        try:
            request.scope["aipa.sync_path_key"] = _decode_axum_path_parameter(raw_sync_key)
        except UnicodeDecodeError:
            return PlainTextResponse("Invalid URL: Invalid UTF-8 in `key`", 400)

    if request_method == "DELETE" and _raw_upload_session_route(request):
        raw_session = raw_path.removeprefix(b"/uploads/")
        try:
            _decode_axum_path_parameter(raw_session)
        except UnicodeDecodeError:
            return PlainTextResponse("Invalid URL: Invalid UTF-8 in `session_id`", 400)
    elif request_method in ("GET", "HEAD") and _raw_upload_markdown_route(request):
        raw_session, _literal, raw_filename = raw_path.removeprefix(b"/uploads/").split(b"/", 2)
        try:
            _decode_axum_path_parameter(raw_session)
        except UnicodeDecodeError:
            return PlainTextResponse("Invalid URL: Invalid UTF-8 in `session_id`", 400)
        try:
            _decode_axum_path_parameter(raw_filename)
        except UnicodeDecodeError:
            return PlainTextResponse("Invalid URL: Invalid UTF-8 in `filename`", 400)
    return None


def rust_route_allow_header(routes, scope, *, include_public_root=False):
    """Return Axum's ``Allow`` value for the matching router path, if any."""
    path = scope.get("path", "")
    raw_path = scope.get("raw_path", path.encode("utf-8")).split(b"?", 1)[0]
    if path in RUST_FIXED_PATHS and raw_path != path.encode("utf-8"):
        return None
    # ServeDir handles its own method behavior and does not expose Axum's
    # MethodRouter Allow header on OPTIONS requests.
    if path.startswith("/public/") or (path == "/public" and not include_public_root):
        return None

    if path.startswith("/sync/") and not raw_path.startswith(b"/sync/"):
        return None
    if path.startswith("/uploads/") and not raw_path.startswith(b"/uploads/"):
        return None
    if raw_path.startswith(b"/sync/"):
        raw_key = raw_path.removeprefix(b"/sync/")
        return "GET,HEAD,POST" if raw_key and b"/" not in raw_key else None
    if raw_path == b"/upload/markdown":
        return "POST"
    if raw_path.startswith(b"/uploads/"):
        segments = raw_path.removeprefix(b"/uploads/").split(b"/")
        if len(segments) == 1 and segments[0]:
            return "DELETE"
        if len(segments) == 3 and segments[0] and segments[1] == b"markdown" and segments[2]:
            return "GET,HEAD"
        return None

    methods = []
    for route in routes:
        if not hasattr(route, "matches") or not hasattr(route, "methods"):
            continue
        match, _ = route.matches(scope)
        if match is Match.NONE:
            continue
        for method in route.methods or ():
            if method not in methods:
                methods.append(method)
            if method == "GET" and "HEAD" not in methods:
                methods.append("HEAD")
    allow_order = {
        method: index
        for index, method in enumerate(("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"))
    }
    methods.sort(key=lambda method: (allow_order.get(method, len(allow_order)), method))
    return ",".join(methods) if methods else None


def rust_query_error(request, request_method):
    """Match Axum's query extraction failures before FastAPI coerces values."""
    path = request.url.path
    fields = RUST_QUERY_FIELDS.get(path)
    required_fields = RUST_REQUIRED_QUERY_FIELDS.get(path, ())
    urlencoded_extractor = path.startswith("/analysis/")

    if request_method not in ("GET", "HEAD"):
        if request_method == "POST" and path == "/upload/markdown":
            # Notes upload uses axum::extract::Query<UploadQuery> before the
            # multipart extractor; both required scalar query fields reject
            # repeated keys as serde_urlencoded duplicate fields.
            fields = {"session_id": "required_string", "secret": "required_string"}
            required_fields = ("session_id", "secret")
            urlencoded_extractor = True
        elif request_method == "DELETE" and _raw_upload_session_route(request):
            fields = {"secret": "required_string"}
            required_fields = ("secret",)
            urlencoded_extractor = True
        else:
            return None

    sync_route = request_method in ("GET", "HEAD") and _raw_sync_key(request) is not None
    if fields is None and sync_route:
        fields = {"secret": "required_string"}
    if fields is None:
        if request_method in ("GET", "HEAD") and _raw_upload_markdown_route(request):
            # Markdown fetch has an optional secret in Rust's extractor. The
            # web client does not send it, and Python's handler ignores it,
            # but duplicate values still fail before either handler runs.
            fields = {"secret": "optional_string"}
            urlencoded_extractor = True
        else:
            return None

    # The Rust ticker/catalog/sync handlers use axum_extra::Query, backed by
    # serde_html_form: repeated pairs are grouped and scalar duplicates fail
    # as `field: unsupported value`. The analysis and Notes handlers use Axum
    # Query, backed by serde_urlencoded: fields are decoded in pair order and
    # a second valid scalar is a root-level duplicate-field error.
    analysis_extractor = urlencoded_extractor
    grouped = OrderedDict()
    query_pairs = request.query_params.multi_items()
    if analysis_extractor:
        seen = set()
        for name, value in query_pairs:
            kind = fields.get(name)
            if kind is None:
                continue
            if name in seen:
                return PlainTextResponse(
                    f"Failed to deserialize query string: duplicate field `{name}`", 400
                )
            seen.add(name)
            error = rust_query_value_error(kind, value)
            if error is not None:
                return PlainTextResponse(
                    f"Failed to deserialize query string: {name}: {error}", 400
                )
            grouped[name] = [value]
    else:
        for name, value in query_pairs:
            grouped.setdefault(name, []).append(value)
        for name, values in grouped.items():
            kind = fields.get(name)
            if kind is None:
                continue
            if len(values) > 1 and kind != "list_string":
                return PlainTextResponse(
                    f"Failed to deserialize query string: {name}: unsupported value", 400
                )
            if kind == "list_string":
                continue
            value = values[0]
            # serde_html_form maps empty Option<T> values to None. This is
            # specific to its routes; serde_urlencoded keeps Some("").
            if (request.url.path, name) in RUST_OPTIONAL_QUERY_FIELDS and value == "":
                continue
            error = rust_query_value_error(kind, value)
            if error is not None:
                return PlainTextResponse(
                    f"Failed to deserialize query string: {name}: {error}", 400
                )

    if sync_route:
        required_fields = ("secret",)
    for name in required_fields:
        if name not in grouped:
            return PlainTextResponse(
                f"Failed to deserialize query string: missing field `{name}`", 400
            )
    return None


class RustCompatibleCORSMiddleware(CORSMiddleware):
    """Keep Starlette's simple-request handling, matching tower-http preflights."""

    def __init__(self, *args, rust_permissive=False, **kwargs):
        self.rust_permissive = rust_permissive
        self.route_allow_header = kwargs.pop("route_allow_header", None)
        super().__init__(*args, **kwargs)

    async def __call__(self, scope, receive, send):
        # tower-http short-circuits every OPTIONS request, even when the browser
        # omitted Origin or Access-Control-Request-Method. Axum adds the
        # matched MethodRouter's Allow header outside that layer; the callback
        # reproduces that raw-path-aware behavior here.
        if scope["type"] == "http" and scope["method"] == "OPTIONS":
            request_headers = Headers(scope=scope)
            origin = request_headers.get("origin")
            response_headers = {
                "vary": RUST_CORS_VARY,
                "access-control-allow-methods": "*"
                if self.rust_permissive
                else "GET,POST,DELETE,OPTIONS",
                "access-control-allow-headers": "*"
                if self.rust_permissive
                else "content-type,authorization,user-agent",
            }
            if self.rust_permissive:
                response_headers["access-control-allow-origin"] = "*"
            elif origin is not None and self.is_allowed_origin(origin):
                response_headers["access-control-allow-origin"] = origin
            if self.route_allow_header is not None:
                allow = self.route_allow_header(scope)
                if allow is not None:
                    response_headers["allow"] = allow
            response = Response(status_code=200, headers=response_headers)
            await response(scope, receive, send)
            return
        await super().__call__(scope, receive, send)

    async def send(self, message, send, request_headers):
        if message["type"] != "http.response.start":
            await send(message)
            return

        headers = MutableHeaders(scope=message)
        origin = request_headers.get("Origin")
        if self.rust_permissive:
            headers.update(self.simple_headers)
        else:
            if origin is not None and self.is_allowed_origin(origin):
                headers["Access-Control-Allow-Origin"] = origin
            # tower-http joins configured names with commas and no spaces.
            headers["Access-Control-Expose-Headers"] = ",".join(RUST_CORS_EXPOSE_HEADERS)
        headers.append(
            "Vary",
            RUST_CORS_VARY,
        )
        await send(message)


class RustStaticFileResponse(FileResponse):
    """Serve the bytes/ranges used by tower-http without Starlette's ETag."""

    async def __call__(self, scope, receive, send):
        async def strip_etag(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = [
                    (name, value) for name, value in message["headers"] if name.lower() != b"etag"
                ]
            await send(message)

        await super().__call__(scope, receive, strip_etag)


def _parse_rust_http_date(value):
    formats = (
        (
            re.compile(r"[A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT\Z"),
            "short",
        ),
        (
            re.compile(r"[A-Z][a-z]+, \d{2}-[A-Z][a-z]{2}-\d{2} \d{2}:\d{2}:\d{2} GMT\Z"),
            "long",
        ),
        (
            re.compile(
                r"[A-Z][a-z]{2} [A-Z][a-z]{2} (?: [1-9]|[12]\d|3[01]) \d{2}:\d{2}:\d{2} \d{4}\Z"
            ),
            "asctime",
        ),
    )
    style = next((kind for pattern, kind in formats if pattern.fullmatch(value)), None)
    if style is None:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        if style != "asctime":
            return None
        parsed = parsed.replace(tzinfo=UTC)
    elif parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        return None
    weekday = value.split(",", 1)[0] if style != "asctime" else value[:3]
    expected_weekday = parsed.strftime("%A" if style == "long" else "%a")
    if weekday != expected_weekday:
        return None
    return parsed.astimezone(UTC).replace(microsecond=0)


def _parse_rust_ranges(value, size):
    """Parse and validate byte ranges with http-range-header's strict rules."""
    if not value.startswith("bytes="):
        return None
    raw_ranges = value[6:]
    if not raw_ranges or raw_ranges[0].isspace():
        return None
    if size == 0 and raw_ranges == "0-":
        return [(0, 0)]
    pieces = raw_ranges.split(",")
    ranges = []
    for index, raw in enumerate(pieces):
        if index and raw and raw[0].isspace():
            raw = raw[1:]
        if not raw or any(character.isspace() for character in raw):
            return None
        match = re.fullmatch(r"(\d*)-(\d*)", raw)
        if match is None or not (match.group(1) or match.group(2)):
            return None
        start_text, end_text = match.groups()
        numbers = [number for number in (start_text, end_text) if number]
        if any(
            (len(number) > 1 and number.startswith("0")) or int(number) > (1 << 64) - 1
            for number in numbers
        ):
            return None
        if not start_text:
            suffix = int(end_text)
            if suffix == 0 or suffix > size:
                return None
            start = size - suffix
            end = size - 1
        else:
            start = int(start_text)
            end = min(int(end_text), size - 1) if end_text else size - 1
        if start > end:
            return None
        ranges.append((start, end))

    if any(
        left[0] <= right[1] and right[0] <= left[1]
        for index, left in enumerate(ranges)
        for right in ranges[index + 1 :]
    ):
        return None
    return ranges


class RustCompatibleStaticFiles(StaticFiles):
    """Match tower-http ServeDir's validators and single-range behavior."""

    async def check_config(self):
        # ServeDir accepts a missing directory and returns request-level 404s;
        # Starlette normally turns the same configuration into a startup error.
        if self.directory is not None and not os.path.exists(self.directory):
            return
        await super().check_config()

    async def get_response(self, path, scope):
        if scope["method"] not in ("GET", "HEAD"):
            return Response(status_code=405, headers={"allow": "GET,HEAD"})

        # Axum's nested service serves index.html at the exact mount root;
        # Starlette's Mount passes an empty path there and otherwise returns 404.
        if path in ("", "/", "."):
            full_path, stat_result = await asyncio.to_thread(self.lookup_path, "index.html")
            if stat_result is not None and stat.S_ISREG(stat_result.st_mode):
                return self.file_response(full_path, stat_result, scope)
            return Response(status_code=404)
        return await super().get_response(path, scope)

    def file_response(self, full_path, stat_result, scope, status_code=200):
        headers = Headers(scope=scope)
        modified_at = datetime.fromtimestamp(stat_result.st_mtime, tz=UTC).replace(microsecond=0)
        if_unmodified_since = headers.get("if-unmodified-since")
        if if_unmodified_since:
            requested_at = _parse_rust_http_date(if_unmodified_since)
            if requested_at is not None and requested_at < modified_at:
                return Response(status_code=412)

        if_modified_since = headers.get("if-modified-since")
        if if_modified_since:
            requested_at = _parse_rust_http_date(if_modified_since)
            if requested_at is not None and requested_at >= modified_at:
                return Response(status_code=304)

        # tower-http ignores If-Range. FileResponse uses it internally, so
        # remove it before handing off a valid range.
        if headers.get("if-range") is not None:
            scope["headers"] = [
                (name, value)
                for name, value in scope.get("headers", [])
                if name.lower() != b"if-range"
            ]

        raw_range_header = next(
            (value for name, value in scope.get("headers", []) if name.lower() == b"range"),
            None,
        )
        range_header = (
            headers.get("range")
            if raw_range_header is None or all(byte < 128 for byte in raw_range_header)
            else None
        )
        if range_header is not None:
            if stat_result.st_size == 0 and range_header in ("bytes=0-", "bytes=0-0"):
                content_type = mimetypes.guess_type(str(full_path))[0]
                return Response(
                    content=b"",
                    status_code=206,
                    media_type=content_type or "application/octet-stream",
                    headers={
                        "Accept-Ranges": "bytes",
                        "Content-Range": "bytes 0-0/0",
                        "Last-Modified": formatdate(stat_result.st_mtime, usegmt=True),
                    },
                )
            error_body = b""
            ranges = _parse_rust_ranges(range_header, stat_result.st_size)
            range_error = ranges is None
            if ranges is not None and len(ranges) > 1:
                error_body = b"Cannot serve multipart range requests"
                range_error = True
            if range_error:
                content_type = mimetypes.guess_type(str(full_path))[0]
                response_headers = {
                    "Accept-Ranges": "bytes",
                    "Content-Range": f"bytes */{stat_result.st_size}",
                    "Last-Modified": formatdate(stat_result.st_mtime, usegmt=True),
                }
                return Response(
                    content=error_body,
                    status_code=416,
                    media_type=content_type or "application/octet-stream",
                    headers=response_headers,
                )

        return RustStaticFileResponse(full_path, status_code=status_code, stat_result=stat_result)


def rust_header_value(value):
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    if not all(byte == 9 or 32 <= byte <= 126 or 128 <= byte <= 255 for byte in encoded):
        return None
    # ASGI/Starlette decode header bytes as Latin-1, so store configured Rust
    # HeaderValue bytes in the same representation for byte-exact comparisons.
    return encoded.decode("latin-1")


def unsigned_integer(value):
    if isinstance(value, str) and not re.fullmatch(r"\+?[0-9]+", value):
        raise ValueError("Invalid unsigned integer query value")
    return value


def empty_optional(value):
    return None if value == "" else value


def optional_signed_integer(value):
    return None if value == "" or value is None else signed_integer(value)


def optional_unsigned_integer(value):
    return None if value == "" or value is None else unsigned_integer(value)


def signed_integer(value):
    if isinstance(value, str) and not re.fullmatch(r"[+-]?[0-9]+", value):
        raise ValueError("Invalid signed integer query value")
    return value


def strict_boolean(value):
    if isinstance(value, bool):
        return value
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError("Expected 'true' or 'false'")


def symbol_query_values(value):
    if value is None:
        return None
    return value if isinstance(value, list) else [value]


def expand_comma_joined_symbols(symbols, known_symbols):
    """Accept web backtest comma lists without breaking literal ticker names."""
    if symbols is None:
        return None
    expanded = []
    for symbol in symbols:
        if "," in symbol and symbol not in known_symbols:
            expanded.extend(symbol.split(","))
        else:
            expanded.append(symbol)
    return expanded


Unsigned64 = Annotated[
    int,
    BeforeValidator(unsigned_integer),
    Query(
        ge=0,
        le=(1 << 64) - 1,
        json_schema_extra={"minimum": 0, "maximum": (1 << 64) - 1},
    ),
]
Unsigned32 = Annotated[
    int,
    BeforeValidator(unsigned_integer),
    Query(
        ge=0,
        le=(1 << 32) - 1,
        json_schema_extra={"minimum": 0, "maximum": (1 << 32) - 1},
    ),
]
Signed64 = Annotated[
    int,
    BeforeValidator(signed_integer),
    Query(
        ge=-(1 << 63),
        le=(1 << 63) - 1,
        json_schema_extra={"minimum": -(1 << 63), "maximum": (1 << 63) - 1},
    ),
]
OptionalUnsigned64 = Annotated[
    int | None,
    BeforeValidator(optional_unsigned_integer),
    Query(
        json_schema_extra={"minimum": 0, "maximum": (1 << 64) - 1},
    ),
]
OptionalSigned64 = Annotated[
    int | None,
    BeforeValidator(optional_signed_integer),
    Query(
        json_schema_extra={"minimum": -(1 << 63), "maximum": (1 << 63) - 1},
    ),
]
OptionalFloat = Annotated[float | None, BeforeValidator(empty_optional)]
OptionalString = Annotated[str | None, BeforeValidator(empty_optional)]
StrictBoolean = Annotated[bool, BeforeValidator(strict_boolean)]
SnapBoolean = Annotated[
    bool,
    BeforeValidator(strict_boolean),
    Query(
        description=("Use Rust-compatible Redis response snapshots when REDIS_URL is configured.")
    ),
]
MODE_VALUES = ["vn", "stock", "stocks", "crypto", "cryptos", "yahoo", "all"]


class TickersQuery(BaseModel):
    symbol: Annotated[list[str] | None, BeforeValidator(symbol_query_values)] = None
    interval: OptionalString = None
    start_date: OptionalString = None
    end_date: OptionalString = None
    limit: OptionalSigned64 = None
    legacy: StrictBoolean = False
    format: str = "json"
    cache: StrictBoolean = True
    mode: Annotated[str, Field(json_schema_extra={"enum": MODE_VALUES})] = "vn"
    redis: StrictBoolean = True
    ma: StrictBoolean = True
    ema: StrictBoolean = False
    snap: SnapBoolean = True

    @field_validator("symbol", mode="before")
    @classmethod
    def preserve_symbol_values(cls, value):
        return symbol_query_values(value)


class SyncPost(BaseModel):
    secret: str
    value: Any


class RefreshPost(BaseModel):
    interval: str
    mode: Literal["vn", "stock", "stocks", "crypto", "cryptos", "yahoo", "all"] = "vn"
    key: str | None = None


def selected_sources(value):
    return SOURCES if value == "all" else ("yahoo", "sjc") if value == "yahoo" else (value,)


def create_app(settings: Settings | None = None, *, redis_client=None):
    settings = settings or Settings.from_env()
    owns_redis_client = redis_client is None and bool(settings.redis_url)
    if redis_client is None:
        redis_client = create_redis_client(settings.redis_url)
    repo = Repository(settings.database)
    archive = Archive(repo, settings)
    history = History(repo, archive, settings, redis_client=redis_client)
    catalog = Catalog(settings)
    analysis = Analysis(history, catalog)
    responses = TickersResponseCache(ttl=settings.cache_ttl)
    in_flight_reads = AsyncSingleFlight()
    started = time.monotonic()
    health_snapshot = {"tickers": 0, "active_tickers": 0, "records": {}, "last_sync": {}}
    executor = None
    health_executor = None
    market_executor = None
    read_slots = None
    health_slots = None

    def parallel_market(items, function):
        """Run market reads in bounded batches and preserve input ordering."""
        items = list(items)
        results = []
        width = settings.market_read_concurrency
        for offset in range(0, len(items), width):
            futures = [
                market_executor.submit(function, item) for item in items[offset : offset + width]
            ]
            batch = []
            first_error = None
            for future in futures:
                try:
                    batch.append(future.result())
                except Exception as exc:
                    batch.append(None)
                    if first_error is None:
                        first_error = exc
            if first_error is not None:
                raise first_error
            results.extend(batch)
        return results

    analysis.parallel_map = parallel_market

    async def acquire_slot(slots, cancellation):
        """Acquire capacity or promptly unwind when its request times out."""
        if cancellation is None:
            await slots.acquire()
            return
        if cancellation.is_set():
            raise asyncio.CancelledError

        acquire_task = asyncio.create_task(slots.acquire())
        cancel_task = asyncio.create_task(cancellation.wait())
        acquired = False
        permit_released = False

        def acquisition_succeeded():
            return (
                acquire_task.done()
                and not acquire_task.cancelled()
                and acquire_task.exception() is None
            )

        try:
            await asyncio.wait((acquire_task, cancel_task), return_when=asyncio.FIRST_COMPLETED)
            if cancellation.is_set():
                acquire_task.cancel()
                await asyncio.gather(acquire_task, return_exceptions=True)
                if acquisition_succeeded():
                    slots.release()
                    permit_released = True
                cancel_task.cancel()
                await asyncio.gather(cancel_task, return_exceptions=True)
                raise asyncio.CancelledError

            acquired = acquisition_succeeded()
            if not acquired:
                raise RuntimeError("read slot acquisition ended without a permit")
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)
            if cancellation.is_set():
                slots.release()
                permit_released = True
                acquired = False
                raise asyncio.CancelledError
            acquired = False  # Transfer ownership to the caller.
        except BaseException:
            acquire_task.cancel()
            cancel_task.cancel()
            await asyncio.gather(acquire_task, cancel_task, return_exceptions=True)
            if not permit_released and (acquired or acquisition_succeeded()):
                slots.release()
            raise

    async def background(func, *args, priority=False):
        """Run bounded blocking work without releasing capacity on waiter cancellation.

        Cancelling an asyncio wrapper does not stop its worker thread. Keep the
        slot occupied until the underlying concurrent future actually finishes,
        and isolate health reads so a slow market-wide query cannot starve the
        readiness endpoint.
        """
        loop = asyncio.get_running_loop()
        pool = health_executor if priority else executor
        slots = health_slots if priority else read_slots
        cancellation = request_cancelled.get()
        await acquire_slot(slots, cancellation)
        try:
            future = pool.submit(partial(func, *args))
        except BaseException:
            slots.release()
            raise

        def release(_):
            try:
                loop.call_soon_threadsafe(slots.release)
            except RuntimeError:
                # The loop is already shutting down; the pool itself is closing.
                pass

        future.add_done_callback(release)
        return await asyncio.wrap_future(future, loop=loop)

    async def coalesced_background(key, func, *args, priority=False):
        """Share active identical read work while keeping waiter cancellation local."""

        async def execute():
            # The shared worker must not inherit one request's disconnect event:
            # other waiters may still need the same in-progress result.
            token = request_cancelled.set(None)
            try:
                return await background(func, *args, priority=priority)
            finally:
                request_cancelled.reset(token)

        return await in_flight_reads.run((key, priority), execute)

    async def refresh_health_snapshot():
        while True:
            try:
                current = await background(repo.health_summary, priority=True)
            except Exception:
                log.exception("health snapshot refresh failed")
            else:
                health_snapshot.clear()
                health_snapshot.update(current)
            await asyncio.sleep(HEALTH_SNAPSHOT_REFRESH_SECONDS)

    def prewarm_market_read_cache():
        """Prime recent EMA tails so the first web page read avoids a cold scan."""
        for source in ("vn", "yahoo", "crypto", "sjc"):
            symbols = [row["symbol"] for row in repo.tickers([source])]
            if symbols:
                history.data_loader.load_many(
                    source,
                    symbols,
                    "1D",
                    limit=601,
                    compact_tail=True,
                )

    @asynccontextmanager
    async def lifespan(app):
        nonlocal executor, health_executor, market_executor, read_slots, health_slots
        log.info(
            "api startup database=%s archive_backend=%s read_concurrency=%s",
            settings.database,
            settings.archive_backend,
            settings.read_concurrency,
        )
        executor = ThreadPoolExecutor(
            max_workers=settings.read_concurrency, thread_name_prefix="aipa-read"
        )
        market_executor = ThreadPoolExecutor(
            max_workers=settings.market_read_concurrency, thread_name_prefix="aipa-market"
        )
        health_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aipa-health")
        read_slots = asyncio.Semaphore(settings.read_concurrency)
        health_slots = asyncio.Semaphore(1)
        health_task = None
        try:
            await background(repo.initialize)
            await background(catalog.initialize, repo)
            try:
                await background(prewarm_market_read_cache)
            except Exception:
                # Cache warmup is an optimization. Keep API startup available
                # if a damaged or inaccessible market history prevents it.
                log.exception("market read cache warmup failed")
            try:
                health_snapshot.update(await background(repo.health_summary, priority=True))
            except Exception:
                log.exception("initial health snapshot refresh failed")
            health_task = asyncio.create_task(
                refresh_health_snapshot(), name="aipa-health-snapshot"
            )
            log.info("api ready")
            yield
        finally:
            log.info("api shutdown requested")
            if health_task is not None:
                health_task.cancel()
                await asyncio.gather(health_task, return_exceptions=True)
            responses.clear()
            executor.shutdown(wait=False, cancel_futures=True)
            health_executor.shutdown(wait=False, cancel_futures=True)
            market_executor.shutdown(wait=False, cancel_futures=True)
            if owns_redis_client:
                redis_client.close()
            log.info("api stopped")

    # Keep the public route surface aligned with Rust. OpenAPI remains available
    # internally through ``app.openapi()`` for contract tests, but FastAPI's
    # convenience documentation endpoints are not mounted by the Rust server.
    app = FastAPI(
        title="AIPriceAction API",
        version="0.1.0",
        default_response_class=JSONResponse,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    # The Rust router uses exact paths; `/tickers/` is a 404 rather than a 307.
    app.router.redirect_slashes = False
    app.state.settings = settings
    app.state.repo = repo
    app.state.archive = archive
    app.state.history = history
    app.state.redis_client = redis_client
    app.state.catalog = catalog
    app.state.analysis = analysis
    app.state.health_snapshot = health_snapshot
    app.include_router(uploads_router_for(settings))

    @app.middleware("http")
    async def limits_and_headers(request: Request, call_next):
        request_method = request.method
        request.scope["aipa.original_method"] = request_method
        raw_path = _raw_request_path(request)
        request.scope["aipa.public_path"] = raw_path == b"/public" or raw_path.startswith(
            b"/public/"
        )
        head_request = request_method == "HEAD"
        if head_request:
            # Axum serves HEAD through GET handlers and omits the response body.
            request.scope["method"] = "GET"
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request_id = request_id[:64].replace("\n", "").replace("\r", "")
        forwarded = request.headers.get("x-forwarded-for")
        client_ip = (
            forwarded.split(",", 1)[0].strip()[:64]
            if forwarded
            else request.client.host
            if request.client
            else "-"
        )
        began = time.monotonic()
        deadline = began + settings.query_timeout

        def finish(response):
            elapsed_ms = (time.monotonic() - began) * 1000
            status = response.status_code
            if head_request and status == 404 and request.scope["aipa.public_path"]:
                # tower-http's empty ServeDir 404 does not advertise a
                # representation length on HEAD; Starlette's empty Response
                # otherwise adds Content-Length: 0.
                if "content-length" in response.headers:
                    del response.headers["content-length"]
            level = (
                logging.ERROR
                if status >= 500
                else logging.WARNING
                if status >= 400
                else logging.INFO
            )
            log.log(
                level,
                "http response request_id=%s method=%s path=%s client_ip=%s status=%s latency_ms=%.1f",
                request_id,
                request_method,
                request.url.path,
                client_ip,
                status,
                elapsed_ms,
            )
            response.headers["x-frame-options"] = "SAMEORIGIN"
            response.headers["x-content-type-options"] = "nosniff"
            response.headers["x-xss-protection"] = "1; mode=block"
            if request.scope["aipa.public_path"]:
                content_type = response.headers.get("content-type")
                if content_type and content_type.lower().startswith("text/"):
                    response.headers["content-type"] = content_type.split(";", 1)[0]
                suffix = request.url.path.rsplit(".", 1)[-1]
                response.headers["cache-control"] = {
                    "js": "max-age=300, public",
                    "css": "max-age=3600, public",
                    "html": "no-cache, no-store, must-revalidate",
                    "png": "max-age=86400, public",
                    "jpg": "max-age=86400, public",
                    "jpeg": "max-age=86400, public",
                    "gif": "max-age=86400, public",
                    "webp": "max-age=86400, public",
                    "svg": "max-age=86400, public",
                    "ico": "max-age=86400, public",
                }.get(suffix, "max-age=3600, public")
            return response

        try:
            size = int(request.headers.get("content-length", "0"))
        except ValueError:
            return finish(JSONResponse({"error": "Invalid content-length"}, 400))
        # Axum's nested static service covers the mount root itself as well as
        # descendants, and sits outside RequestBodyLimitLayer.
        public_file_route = request.scope["aipa.public_path"]
        request_body_limit = settings.body_limit
        if (
            raw_path == b"/upload/markdown"
            or _raw_upload_session_route(request)
            or _raw_upload_markdown_route(request)
        ):
            request_body_limit += 64 * 1024
        if size > request_body_limit and not public_file_route:
            return finish(PlainTextResponse("length limit exceeded", 413))
        path_error = _raw_path_rejection(request)
        if path_error is not None:
            return finish(path_error)
        query_error = rust_query_error(request, request_method)
        if query_error is not None:
            return finish(query_error)
        if request_method == "DELETE" and _raw_upload_session_route(request):
            raw_session = (
                request.scope.get("raw_path", b"")
                .split(b"?", 1)[0]
                .removeprefix(b"/uploads/")
                .lower()
            )
            if b"%2f" in raw_session:
                return finish(
                    JSONResponse({"success": False, "error": "Invalid session_id format"}, 400)
                )
        json_body_route = request_method == "POST" and (
            request.url.path == "/tickers/refresh" or _raw_sync_key(request) is not None
        )
        upload_body_route = request_method == "POST" and request.url.path == "/upload/markdown"
        if upload_body_route:
            # Axum extracts Query<UploadQuery>, then Multipart, before entering
            # the handler. Match that order here so bad query values and bad
            # multipart media types win over a chunked body that exceeds the
            # route limit.
            content_type = _rust_header_value_to_str(request, b"content-type")
            has_boundary = False
            if content_type is not None:
                slash = content_type.find("/")
                if slash > 0:
                    top_level = content_type[:slash]
                    position = slash + 1
                    subtype_start = position
                    while (
                        position < len(content_type) and content_type[position] in _MIME_TOKEN_CHARS
                    ):
                        position += 1
                    subtype = content_type[subtype_start:position]
                    if subtype and position < len(content_type) and content_type[position] == ";":
                        parameters = content_type[position + 1 :]
                        position = 0
                        boundary_seen = False
                        valid = True
                        while position < len(parameters):
                            while position < len(parameters) and parameters[position] == " ":
                                position += 1
                            if position == len(parameters):
                                break
                            name_start = position
                            while (
                                position < len(parameters)
                                and parameters[position] in _MIME_TOKEN_CHARS
                            ):
                                position += 1
                            if (
                                position == name_start
                                or position == len(parameters)
                                or parameters[position] != "="
                            ):
                                valid = False
                                break
                            name = parameters[name_start:position]
                            position += 1
                            value_start = position
                            if position < len(parameters) and parameters[position] == '"':
                                position += 1
                                value_start = position
                                while position < len(parameters) and parameters[position] != '"':
                                    if (
                                        ord(parameters[position]) <= 31
                                        or ord(parameters[position]) == 127
                                    ):
                                        valid = False
                                        break
                                    position += 1
                                if (
                                    not valid
                                    or position == value_start
                                    or position == len(parameters)
                                ):
                                    valid = False
                                    break
                                position += 1
                                while position < len(parameters) and parameters[position] == " ":
                                    position += 1
                                if position < len(parameters) and parameters[position] != ";":
                                    valid = False
                                    break
                            else:
                                while (
                                    position < len(parameters)
                                    and parameters[position] in _MIME_TOKEN_CHARS
                                ):
                                    position += 1
                                if position < len(parameters) and parameters[position] != ";":
                                    valid = False
                                    break
                                if position == value_start and position < len(parameters):
                                    valid = False
                                    break
                            if name.lower() == "boundary" and not boundary_seen:
                                boundary_seen = True
                            if position < len(parameters):
                                position += 1
                        has_boundary = (
                            valid
                            and top_level.lower() == "multipart"
                            and subtype.lower() == "form-data"
                            and boundary_seen
                        )
            if not has_boundary:
                return finish(
                    PlainTextResponse("Invalid `boundary` for `multipart/form-data` request", 400)
                )

            session_id = request.query_params.get("session_id")
            try:
                uuid.UUID(session_id)
            except (ValueError, TypeError, AttributeError):
                return finish(
                    JSONResponse({"success": False, "error": "Invalid session_id format"}, 400)
                )
            secret = request.query_params.get("secret", "")
            if len(secret) < 8:
                return finish(
                    JSONResponse(
                        {
                            "success": False,
                            "error": "Secret must be at least 8 characters",
                        },
                        400,
                    )
                )
        if json_body_route:
            content_type_error = rust_json_content_type_error(request)
            if content_type_error is not None:
                return finish(content_type_error)
        # Rust's limited body only sees chunked bytes when an extractor consumes
        # them. Preserve that behavior for GET/405/fallback/static routes instead
        # of eagerly draining bodies that their handlers never read.
        if json_body_route or upload_body_route:
            body = bytearray()
            try:
                async with asyncio.timeout(max(0, deadline - time.monotonic())):
                    async for chunk in request.stream():
                        body.extend(chunk)
                        if len(body) > request_body_limit:
                            if upload_body_route:
                                return finish(
                                    JSONResponse(
                                        {
                                            "success": False,
                                            "error": "Invalid multipart form",
                                        },
                                        400,
                                    )
                                )
                            body_limit_message = (
                                "Failed to buffer the request body: length limit exceeded"
                                if json_body_route
                                else "length limit exceeded"
                            )
                            return finish(PlainTextResponse(body_limit_message, 413))
                body_bytes = bytes(body)
                if upload_body_route and not body_bytes:
                    # Axum's Multipart extractor attempts to read the first
                    # boundary even for an empty request body, then maps the
                    # resulting incomplete-stream error to this response.
                    # FastAPI's File extractor otherwise reports a 422 before
                    # the route can reproduce that behavior.
                    return finish(
                        JSONResponse(
                            {"success": False, "error": "Invalid multipart form"},
                            400,
                        )
                    )
                if json_body_route:
                    json_error, body_bytes = rust_json_body_error(request, body_bytes)
                    if json_error is not None:
                        return finish(json_error)
                request._body = body_bytes
            except TimeoutError:
                # Body reads use the same bounded request timeout policy.
                return finish(Response(status_code=408))
        try:
            cancellation = asyncio.Event()
            cancellation_token = request_cancelled.set(cancellation)
            try:
                async with asyncio.timeout(max(0, deadline - time.monotonic())):
                    response = await call_next(request)
            except TimeoutError:
                cancellation.set()
                # Tower HTTP's timeout layer responds with an empty 408 by default.
                response = Response(status_code=408)
            except asyncio.CancelledError:
                cancellation.set()
                raise
            finally:
                request_cancelled.reset(cancellation_token)
        except TimeoutError:
            # Tower HTTP's timeout layer responds with an empty 408 by default.
            response = Response(status_code=408)
        return finish(response)

    @app.exception_handler(DataError)
    async def data_error(request, exc):
        payload = {"error": str(exc)}
        if request.url.path.startswith("/sync/"):
            payload["success"] = False
        return JSONResponse(payload, exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Don't expose inputs, bearer tokens, or sync secrets in errors/logs.
        body = any(error["loc"][0] == "body" for error in exc.errors())
        if (
            request.url.path == "/tickers/refresh" or request.url.path.startswith("/sync/")
        ) and body:
            if any(error["type"] == "json_invalid" for error in exc.errors()):
                return PlainTextResponse("Failed to parse the request body as JSON", 400)
            return PlainTextResponse(
                "Failed to deserialize the JSON body into the target type", 422
            )
        boolean_errors = [
            error
            for error in exc.errors()
            if error["loc"][0] == "query"
            and str(error.get("ctx", {}).get("error")) == "Expected 'true' or 'false'"
        ]
        if boolean_errors:
            invalid_fields = {str(error["loc"][-1]) for error in boolean_errors}
            field = next(
                (name for name, _ in request.query_params.multi_items() if name in invalid_fields),
                str(boolean_errors[0]["loc"][-1]),
            )
            return PlainTextResponse(
                "Failed to deserialize query string: "
                f"{field}: provided string was not `true` or `false`",
                400,
            )
        payload = {"error": exc.errors()[0]["msg"] if exc.errors() else "Invalid request"}
        if request.url.path.startswith("/sync/"):
            payload["success"] = False
        return JSONResponse(payload, 422 if body else 400)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        if exc.status_code == 405:
            # Axum merges methods registered on the same path and includes
            # HEAD whenever GET is registered. Starlette's Allow header only
            # reflects one partial route. FastAPI's included upload router is
            # lazily represented in app.routes, so supply its method sets by
            # exact path after merging the directly registered routes.
            headers = dict(exc.headers or {})
            allow = rust_route_allow_header(app.routes, request.scope, include_public_root=True)
            if allow:
                headers["Allow"] = allow
            return Response(status_code=405, headers=headers)
        return JSONResponse({"error": exc.detail}, exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        # Log exception class and path, not SQL/boto credentials or query strings.
        log.exception("request exception path=%s kind=%s", request.url.path, type(exc).__name__)
        payload = {"error": "Internal server error"}
        if request.url.path.startswith("/sync/"):
            payload["success"] = False
        return JSONResponse(payload, 500)

    @app.get("/tickers")
    async def tickers(params: Annotated[TickersQuery, Query()]):
        iv = interval(params.interval or "1D")
        source_mode = mode(params.mode)
        symbols = params.symbol
        # Axum's Optional<Vec<String>> query decoder drops empty values such as
        # `?symbol=`. Match that before expanding comma-joined web symbols.
        if symbols is not None:
            symbols = [symbol for symbol in symbols if symbol != ""] or None
        if symbols is not None and any("," in symbol for symbol in symbols):
            available = repo.tickers(selected_sources(source_mode))
            known_symbols = {ticker["symbol"] for ticker in available}
            cached_tickers = catalog.redis_ticker_list(redis_client)
            if cached_tickers is not None:
                known_symbols.update(
                    row["ticker"]
                    for row in cached_tickers
                    if row["source"] in selected_sources(source_mode)
                )
            symbols = expand_comma_joined_symbols(symbols, known_symbols)
        if symbols is not None and (not symbols or all(symbol == "" for symbol in symbols)):
            # Rust returns its empty JSON map before response-cache lookup and
            # before adding x-data-source for either single-source or all mode.
            return JSONResponse({})
        try:
            start = date_bounds(params.start_date)
        except DataError:
            # The pinned Rust route silently ignores malformed optional dates.
            start = None
        try:
            end = date_bounds(params.end_date, True)
        except DataError:
            end = None
        single = symbols is not None and len(symbols) == 1
        requested_limit = params.limit
        if requested_limit is None:
            requested_limit = (settings.single_limit if params.start_date else 252) if single else 1
        limit = min(requested_limit, settings.single_limit if single else settings.max_limit)
        key = (
            source_mode,
            iv,
            tuple(sorted(symbols)) if symbols is not None else None,
            # Rust keys the cache by the raw optional date strings, even when
            # a malformed date is later ignored by its fetch path.
            params.start_date,
            params.end_date,
            limit,
            params.ma,
            params.ema,
        )
        cached = responses.get(key) if params.cache else None
        redis_meta = None
        if cached is not None:
            data = cached
            tag = "in-memory"
        elif limit == 0:
            data = {}
            tag = "empty" if source_mode == "all" else "database"
            if params.cache:
                responses.put(key, data)
        else:

            def load():
                data = {}
                batch_sql_errors = set()
                batch_sql_errors_lock = threading.Lock()
                sources = selected_sources(source_mode)
                if symbols is None:
                    cached_tickers = catalog.redis_ticker_list(redis_client)
                    tickers = (
                        [
                            (row["source"], row["ticker"])
                            for row in cached_tickers
                            if row["source"] in sources
                        ]
                        if cached_tickers is not None
                        else []
                    )
                    if not tickers:
                        tickers = [(r["source"], r["symbol"]) for r in repo.tickers(sources)]
                elif source_mode == "all":
                    database_known = {(t["source"], t["symbol"]) for t in repo.tickers(sources)}
                    cached_tickers = catalog.redis_ticker_list(redis_client)
                    if cached_tickers is not None:
                        cached_known = {(row["source"], row["ticker"]) for row in cached_tickers}
                        cached_match_count = sum(
                            any((source, symbol) in cached_known for source in sources)
                            for symbol in set(symbols)
                        )
                        known = (
                            cached_known
                            if cached_match_count == len(set(symbols))
                            else database_known
                        )
                    else:
                        known = database_known
                    priority = {"vn": 0, "yahoo": 1, "sjc": 2, "crypto": 3}
                    tickers = []
                    for sym in symbols:
                        matches = [(src, sym) for src in sources if (src, sym) in known]
                        if matches:
                            tickers.append(min(matches, key=lambda item: priority[item[0]]))
                else:
                    tickers = [(src, sym) for src in sources for sym in symbols]
                if symbols is None and source_mode == "all":
                    priority = {"vn": 0, "yahoo": 1, "sjc": 2, "crypto": 3}
                    selected = {}
                    for src, sym in tickers:
                        if sym not in selected or priority[src] < priority[selected[sym]]:
                            selected[sym] = src
                    tickers = [(src, sym) for sym, src in selected.items()]

                # Keep a mode's primary source ahead of its extra sources.
                # Rust fetches Yahoo first and appends SJC results only when a
                # symbol has not already been filled, including aggregates.
                symbol_order = {symbol: index for index, symbol in enumerate(symbols or ())}
                ordered_tickers = sorted(
                    set(tickers),
                    key=lambda pair: (
                        pair[0] != source_mode,
                        symbol_order.get(pair[1], len(symbol_order)),
                        pair,
                    ),
                )
                # Rust only takes the Redis shortcut when a Redis client is
                # configured. Keep SQLite-backed history on its normal SQL
                # lookback path when Redis is absent; otherwise an undated
                # aggregate can be incorrectly capped to the cache retention.
                redis_requested = (
                    source_mode == "all" or params.redis
                ) and redis_client is not None
                redis_by_source = {src: redis_requested for src in selected_sources(source_mode)}
                redis_covered_by_source = {}
                if source_mode == "yahoo" and base_interval(iv) != iv:
                    # Rust's aggregated Yahoo request includes SJC as an extra
                    # source, which disables its Redis shortcut for the batch.
                    redis_by_source = {src: False for src in selected_sources(source_mode)}
                elif start is not None and (
                    any(redis_by_source.values()) or base_interval(iv) != iv
                ):
                    if source_mode == "all":
                        first_by_source = {}
                        for src, sym in ordered_tickers:
                            first_by_source.setdefault(src, sym)
                        for src, sym in first_by_source.items():
                            covered = history.redis_covers_start(src, sym, iv, start)
                            redis_by_source[src] = covered
                            redis_covered_by_source[src] = covered
                    else:
                        primary_source = source_mode
                        symbols_for_check = sorted({sym for _, sym in ordered_tickers})
                        first_symbol = (
                            symbols[0]
                            if symbols
                            else symbols_for_check[0]
                            if symbols_for_check
                            else None
                        )
                        covered = bool(
                            first_symbol
                            and history.redis_covers_start(primary_source, first_symbol, iv, start)
                        )
                        for src in selected_sources(source_mode):
                            redis_by_source[src] = redis_by_source.get(src, False) and covered
                            redis_covered_by_source[src] = covered
                elif start is None:
                    # Rust's end-only and latest Redis reads choose the cache
                    # path for the whole batch without a per-symbol coverage
                    # check. A missing ticker stays missing when another
                    # ticker makes that Redis batch nonempty.
                    redis_covered_by_source = {
                        src: True for src, enabled in redis_by_source.items() if enabled
                    }

                snapshot_end_is_current = (
                    end is None
                    or datetime.fromtimestamp(end, UTC).date() >= datetime.now(UTC).date()
                )
                snapshot_eligible = (
                    redis_client is not None
                    and source_mode != "all"
                    and base_interval(iv) == iv
                    and params.snap
                    and params.ma
                    and start is None
                    and snapshot_end_is_current
                    and any(redis_by_source.values())
                )

                def fetch(pair, force_postgres=False):
                    src, sym = pair
                    if not sym:
                        return sym, None, None, False, False, False
                    history.clear_cache_read_metadata()
                    snapshot_read_attempted = False
                    snapshot_read_succeeded = False
                    if snapshot_eligible and not force_postgres and src == source_mode:
                        snapshot_read_attempted = True
                        try:
                            raw_snapshot = read_redis_snapshot(
                                redis_client,
                                source_mode,
                                sym,
                                iv,
                                limit,
                                ema=params.ema,
                            )
                        except Exception as exc:
                            # Only Redis errors that fred classifies as
                            # per-command argument/command misses keep the
                            # snapshot batch available. Auth, permission,
                            # transport, and generic server errors fall through.
                            snapshot_read_succeeded = is_snapshot_command_miss(exc)
                            log.debug(
                                "Redis snapshot read failed source=%s symbol=%s: %s",
                                src,
                                sym,
                                exc,
                            )
                        else:
                            snapshot_read_succeeded = True
                            snapshot_rows = validate_stock_data_rows(raw_snapshot)
                            if snapshot_rows is not None:
                                return (
                                    sym,
                                    snapshot_rows,
                                    None,
                                    snapshot_read_attempted,
                                    True,
                                    snapshot_read_succeeded,
                                )
                    try:
                        rows = history.query(
                            src,
                            sym,
                            iv,
                            start,
                            end,
                            limit,
                            params.ma,
                            params.ema,
                            redis=redis_by_source.get(src, False) and not force_postgres,
                            strict_source_intervals=True,
                            redis_covered=(
                                redis_covered_by_source.get(src)
                                if force_postgres and base_interval(iv) != iv
                                else False
                                if force_postgres
                                else redis_covered_by_source.get(src)
                            ),
                            ignore_pending_repairs=start is None and end is None,
                        )
                    except sqlite3.Error as exc:
                        # Rust's SQL reader is batched: one failed source query
                        # invalidates that whole source batch. Single-source
                        # Yahoo also queries its SJC extras in the same batch.
                        batch_key = src if source_mode == "all" else source_mode
                        with batch_sql_errors_lock:
                            batch_sql_errors.add(batch_key)
                        log.warning(
                            "ticker database read failed source=%s symbol=%s: %s",
                            src,
                            sym,
                            exc,
                        )
                        return (
                            sym,
                            None,
                            None,
                            snapshot_read_attempted,
                            False,
                            snapshot_read_succeeded,
                        )
                    except DataError as exc:
                        pending_repair = (
                            f"Historical adjustment repair pending for {sym} {base_interval(iv)}"
                        )
                        if (
                            start is None
                            and end is None
                            and exc.status == 503
                            and str(exc) == pending_repair
                            and not repo.read(src, sym, base_interval(iv), limit=1)
                        ):
                            # Rust's latest row-oriented endpoint omits a ticker
                            # with no live series instead of failing the batch.
                            # Only hide this specific pending-archive case;
                            # incompatible revisions and other 503s stay visible.
                            log.warning(
                                "omitting ticker %s from latest response with pending repair and no live candles",
                                sym,
                            )
                            return (
                                sym,
                                None,
                                None,
                                snapshot_read_attempted,
                                False,
                                snapshot_read_succeeded,
                            )
                        raise
                    return (
                        sym,
                        rows,
                        history.take_cache_read_metadata(),
                        snapshot_read_attempted,
                        False,
                        snapshot_read_succeeded,
                    )

                def discard_failed_sql_batches(pairs, items):
                    if not batch_sql_errors:
                        return items
                    # Rust's SQL reader is batched: one failed source query
                    # invalidates that whole source batch. Preserve valid
                    # snapshot hits, which Rust retains while retrying only
                    # the missed-symbol batch. Single-source Yahoo also
                    # queries its SJC extras in the same batch.
                    return [
                        (
                            pair[1],
                            None,
                            None,
                            False,
                            False,
                            False,
                        )
                        if (pair[0] if source_mode == "all" else source_mode) in batch_sql_errors
                        and not item[4]
                        else item
                        for pair, item in zip(pairs, items, strict=True)
                    ]

                def batch_latest_queries(pairs):
                    """Use the shared loader for undated native SQL batches."""
                    if (
                        redis_client is not None
                        or start is not None
                        or end is not None
                        or base_interval(iv) != iv
                        or limit <= 0
                    ):
                        return None
                    by_source = {}
                    for src, sym in pairs:
                        if sym:
                            by_source.setdefault(src, []).append(sym)
                    futures = {
                        src: market_executor.submit(
                            history.query_many,
                            src,
                            list(dict.fromkeys(src_symbols)),
                            iv,
                            start=start,
                            end=end,
                            limit=limit,
                            ma=params.ma,
                            ema=params.ema,
                            redis=redis_by_source.get(src, False),
                            strict_source_intervals=True,
                            ignore_pending_repairs=True,
                            return_exceptions=True,
                        )
                        for src, src_symbols in by_source.items()
                    }
                    loaded_by_source = {}
                    for src, future in futures.items():
                        try:
                            loaded_by_source[src] = future.result()
                        except sqlite3.Error as exc:
                            batch_key = src if source_mode == "all" else source_mode
                            batch_sql_errors.add(batch_key)
                            log.warning("ticker database batch read failed source=%s: %s", src, exc)
                            loaded_by_source[src] = None
                    items = []
                    for src, sym in pairs:
                        if not sym:
                            items.append((sym, None, None, False, False, False))
                            continue
                        loaded = loaded_by_source.get(src)
                        if loaded is None:
                            items.append((sym, None, None, False, False, False))
                            continue
                        rows = loaded.get(sym, [])
                        if isinstance(rows, Exception):
                            pending_repair = f"Historical adjustment repair pending for {sym} {base_interval(iv)}"
                            if (
                                isinstance(rows, DataError)
                                and rows.status == 503
                                and str(rows) == pending_repair
                                and not repo.read(src, sym, base_interval(iv), limit=1)
                            ):
                                log.warning(
                                    "omitting ticker %s from latest response with pending repair and no live candles",
                                    sym,
                                )
                                items.append((sym, None, None, False, False, False))
                                continue
                            raise rows
                        else:
                            items.append((sym, rows, None, False, False, False))
                    return items

                batched = batch_latest_queries(ordered_tickers)
                fetched = discard_failed_sql_batches(
                    ordered_tickers,
                    batched if batched is not None else parallel_market(ordered_tickers, fetch),
                )
                snapshot_miss_symbols = {
                    pair[1]
                    for pair, item in zip(ordered_tickers, fetched, strict=True)
                    if snapshot_eligible and pair[0] == source_mode and item[3] and not item[4]
                }
                snapshot_miss_has_redis_data = any(
                    item[2] is not None for item in fetched if item[0] in snapshot_miss_symbols
                )
                snapshot_sql_fallback_symbols = set()
                if snapshot_miss_symbols and not snapshot_miss_has_redis_data:
                    # Rust computes every snapshot miss as one Redis batch and
                    # falls back to SQL for that whole missed-symbol batch only
                    # when its raw Redis result is entirely empty.
                    retry_pairs = [
                        pair for pair in ordered_tickers if pair[1] in snapshot_miss_symbols
                    ]
                    snapshot_sql_fallback_symbols = set(snapshot_miss_symbols)
                    retried = discard_failed_sql_batches(
                        retry_pairs,
                        parallel_market(retry_pairs, partial(fetch, force_postgres=True)),
                    )
                    replacement = dict(zip(retry_pairs, retried, strict=True))
                    fetched = [
                        (
                            replacement[pair][0],
                            replacement[pair][1],
                            replacement[pair][2],
                            item[3] or replacement[pair][3],
                            item[4] or replacement[pair][4],
                            item[5] or replacement[pair][5],
                        )
                        if pair in replacement
                        else item
                        for pair, item in zip(ordered_tickers, fetched, strict=True)
                    ]
                # Rust falls back for an entire source batch only when the
                # Redis batch yielded no non-empty ticker map. Keep partial
                # cache results partial instead of filling missing tickers
                # independently from SQLite.
                batch_keys = {
                    pair: pair[0] if source_mode == "all" else source_mode
                    for pair in ordered_tickers
                }
                empty_redis_batches = {
                    batch_keys[pair]
                    for pair, (_, rows, _, _, _, _) in zip(ordered_tickers, fetched, strict=True)
                    if redis_by_source.get(pair[0], False)
                }
                empty_redis_batches = {
                    key
                    for key in empty_redis_batches
                    if not any(
                        rows
                        for pair, (_, rows, _, _, _, _) in zip(
                            ordered_tickers, fetched, strict=True
                        )
                        if batch_keys[pair] == key
                    )
                }
                empty_redis_batches.difference_update(batch_sql_errors)
                if empty_redis_batches:
                    retry_pairs = [
                        pair for pair in ordered_tickers if batch_keys[pair] in empty_redis_batches
                    ]
                    retried = discard_failed_sql_batches(
                        retry_pairs,
                        parallel_market(retry_pairs, partial(fetch, force_postgres=True)),
                    )
                    replacement = dict(zip(retry_pairs, retried, strict=True))
                    updated = []
                    for pair, item in zip(ordered_tickers, fetched, strict=True):
                        retry = replacement.get(pair)
                        if retry is None:
                            updated.append(item)
                        else:
                            updated.append(
                                (
                                    retry[0],
                                    retry[1],
                                    retry[2],
                                    item[3] or retry[3],
                                    item[4] or retry[4],
                                    item[5] or retry[5],
                                )
                            )
                    fetched = updated

                for sym, rows, _, _, _, _ in fetched:
                    if rows:
                        data.setdefault(sym, rows)

                # When every Redis source batch was empty, Rust retries the
                # merged Yahoo/SJC query from PostgreSQL. `redis_by_source`
                # still reflects the initial attempt, so include that fallback
                # here as well as the explicitly Redis-disabled path.
                if (
                    source_mode == "yahoo"
                    and not batch_sql_errors
                    and (
                        not any(redis_by_source.values())
                        or source_mode in empty_redis_batches
                        or snapshot_sql_fallback_symbols
                    )
                ):
                    sources_by_symbol = {}
                    for source, symbol in ordered_tickers:
                        sources_by_symbol.setdefault(symbol, []).append(source)
                    for symbol, symbol_sources in sources_by_symbol.items():
                        if len(symbol_sources) > 1 and (
                            not any(redis_by_source.values())
                            or source_mode in empty_redis_batches
                            or symbol in snapshot_sql_fallback_symbols
                        ):
                            if iv in {"1m", "1h", "1D"}:
                                rows = history.query_merged_native_sources(
                                    ("sjc", "yahoo"),
                                    symbol,
                                    iv,
                                    start,
                                    end,
                                    limit,
                                    params.ma,
                                    params.ema,
                                    skip_pending_empty=start is None and end is None,
                                    ignore_pending_repairs=start is None and end is None,
                                )
                            else:
                                rows = history.query_merged_aggregated_sources(
                                    ("sjc", "yahoo"),
                                    symbol,
                                    iv,
                                    start,
                                    end,
                                    limit,
                                    params.ma,
                                    params.ema,
                                    skip_pending_empty=start is None and end is None,
                                    ignore_pending_repairs=start is None and end is None,
                                )
                            # Rust builds the ticker map only from non-empty
                            # source results; do not emit a registered symbol
                            # as `[]` when no source has bars for this interval.
                            if rows:
                                data[symbol] = rows
                            else:
                                data.pop(symbol, None)
                successful = [item for item in fetched if item[1]]
                redis_path = any(item[2] is not None for item in successful)
                snapshot_hits = {item[0] for item in successful if item[4]}
                snapshot_read_succeeded = any(item[5] for item in successful)
                snapshot_read_symbols = {item[0] for item in fetched if item[3]}
                if snapshot_eligible and redis_client is not None:
                    for (
                        sym,
                        rows,
                        _,
                        _snapshot_read_attempted,
                        _snapshot_hit,
                        _snapshot_read_ok,
                    ) in successful:
                        if sym in snapshot_read_symbols and sym not in snapshot_hits:
                            try:
                                write_redis_snapshot(
                                    redis_client,
                                    source_mode,
                                    sym,
                                    iv,
                                    limit,
                                    rows,
                                    ema=params.ema,
                                )
                            except Exception as exc:
                                log.debug(
                                    "Redis snapshot write failed source=%s symbol=%s: %s",
                                    source_mode,
                                    sym,
                                    exc,
                                )
                first_meta = next(
                    (
                        item[2]
                        for pair, item in sorted(
                            zip(ordered_tickers, fetched, strict=True),
                            key=lambda pair_item: (
                                symbol_order.get(pair_item[0][1], len(symbol_order)),
                                pair_item[0][0] != source_mode,
                            ),
                        )
                        if item[1] and item[2] is not None
                    ),
                    None,
                )
                if source_mode == "all":
                    source_tags = {
                        "redis"
                        if item[2] is not None
                        else "redis-snap"
                        if item[5] and item[1]
                        else "postgres"
                        for item in successful
                    }
                    if not source_tags:
                        route_tag = "empty"
                    elif source_tags == {"redis"}:
                        route_tag = "cache"
                    elif "redis" in source_tags:
                        route_tag = "mixed"
                    else:
                        route_tag = "database"
                elif data and snapshot_read_succeeded:
                    route_tag = "redis-snap"
                elif data and redis_path:
                    route_tag = "cache"
                else:
                    route_tag = "database"
                if route_tag == "redis-snap":
                    first_meta = None
                return data, route_tag, first_meta

            try:
                data, tag, redis_meta = await coalesced_background(
                    ("tickers", key, params.redis, params.snap), load
                )
            except sqlite3.Error:
                # Rust's ticker SQL helpers turn PostgreSQL query failures and
                # timeouts during catalog resolution into an empty successful
                # map. mode=all labels that empty map `empty`; single-source
                # reads retain the `database` tag.
                log.warning("ticker database read failed; returning an empty result")
                data, tag, redis_meta = {}, "empty" if source_mode == "all" else "database", None
            if params.cache:
                responses.put(key, data)
        view = legacy_rows(data, source_mode) if params.legacy else data
        # Rust's mode=all fast path returns before adding its diagnostic header.
        # Preserve that cache-hit omission while keeping the normal mode cache tag.
        headers = {} if source_mode == "all" and cached is not None else {"x-data-source": tag}
        if source_mode != "all" and redis_meta is not None:
            headers.update(
                {
                    "x-redis-base": redis_meta.base_interval,
                    "x-redis-raw-count": str(redis_meta.raw_member_count),
                    "x-redis-aligned": str(redis_meta.aligned_count),
                    "x-redis-limit": str(redis_meta.requested_limit),
                }
            )
        if params.format.lower() == "csv":
            headers["content-type"] = "text/csv"
            return Response(csv_response(view), headers=headers)
        # Rust's StockDataResponse omits absent optional values instead of
        # serializing them as explicit JSON nulls. Keep CSV columns stable via
        # csv_response(), but preserve the Rust JSON shape here.
        json_view = {
            symbol: [
                {key: row[key] for key in TICKER_JSON_FIELDS if key in row and row[key] is not None}
                for row in rows
            ]
            for symbol, rows in sorted(view.items())
        }
        return JSONResponse(json_view, headers=headers)

    @app.get("/health")
    async def health():
        status = health_snapshot
        now = datetime.now(UTC)
        data = {
            "total_tickers_count": status["tickers"],
            "active_tickers_count": status["active_tickers"],
            "is_trading_hours": now.weekday() < 5 and 2 <= now.hour < 8,
            "trading_hours_timezone": "Asia/Ho_Chi_Minh",
            "uptime_secs": int(time.monotonic() - started),
            "current_system_time": now.isoformat(),
        }
        for label, iv in (("daily", "1D"), ("hourly", "1h"), ("minute", "1m")):
            data[f"{label}_records_count"] = status["records"].get(iv, 0)
            stamp = status["last_sync"].get(iv)
            data[f"{label}_last_sync"] = (
                datetime.fromtimestamp(stamp / 1e9, UTC).isoformat().replace("+00:00", "Z")
                if stamp
                else None
            )
        data.update(
            {
                name: 0.0
                if name
                in {
                    "memory_usage_mb",
                    "memory_usage_percent",
                    "disk_cache_size_mb",
                    "disk_cache_usage_percent",
                }
                else 0
                for name in (
                    "crypto_last_sync",
                    "daily_iteration_count",
                    "slow_iteration_count",
                    "crypto_iteration_count",
                    "memory_usage_bytes",
                    "memory_usage_mb",
                    "memory_limit_mb",
                    "memory_usage_percent",
                    "disk_cache_entries",
                    "disk_cache_size_bytes",
                    "disk_cache_size_mb",
                    "disk_cache_limit_mb",
                    "disk_cache_usage_percent",
                )
            }
        )
        # Rust serializes health through serde_json::Value's sorted object map.
        return dict(sorted(data.items()))

    @app.get("/tickers/group")
    async def groups(
        mode_value: Annotated[
            str,
            Query(
                alias="mode",
                json_schema_extra={"enum": MODE_VALUES},
            ),
        ] = "vn",
    ):
        # Catalog JSON is immutable for this process and cached after first
        # access. Keep this tiny metadata read off the bounded market-data pool
        # so multi-ticker bursts cannot queue it behind calculations.
        return sort_json_value_keys(catalog.groups(mode(mode_value)))

    @app.get("/tickers/name")
    async def names(
        mode_value: Annotated[
            str,
            Query(
                alias="mode",
                json_schema_extra={"enum": MODE_VALUES},
            ),
        ] = "vn",
    ):
        return sort_json_value_keys(catalog.names(mode(mode_value)))

    @app.get("/tickers/info")
    async def info(ticker: OptionalString = None):
        rows = await background(lambda: sort_json_value_keys(catalog.info))
        if ticker is None:
            return rows
        for row in rows:
            if row["ticker"].upper() == ticker.upper():
                return row
        raise DataError(f"Ticker '{ticker}' not found", 404)

    @app.post("/tickers/refresh")
    async def refresh(body: RefreshPost):
        if settings.refresh_secret is None:
            raise DataError(
                "Refresh endpoint is disabled. Set REFRESH_SECRET environment variable.", 403
            )
        if body.key is None or not hmac.compare_digest(
            body.key.encode(), settings.refresh_secret.encode()
        ):
            raise DataError("Invalid or missing key", 401)
        value = mode(body.mode)
        sources = ("vn", "crypto", "yahoo") if value == "all" else (value,)
        try:
            return await background(repo.refresh, sources, body.interval)
        except DataError:
            raise
        except RefreshScheduleError as exc:
            log.exception("refresh schedule update failed for source=%s", exc.source)
            return JSONResponse({"error": f"Database error for source {exc.source}"}, 500)
        except Exception:
            source = sources[0] if len(sources) == 1 else "all"
            log.exception("refresh schedule update failed for sources=%s", sources)
            return JSONResponse({"error": f"Database error for source {source}"}, 500)

    def authorize(request, key):
        if not settings.sync_tokens:
            raise DataError("Sync endpoint is disabled. Set SYNC_TOKEN environment variable.", 403)
        provided = _rust_header_value_to_str(request, b"authorization") or ""
        if not any(
            hmac.compare_digest(provided.encode(), ("Bearer " + token).encode())
            for token in settings.sync_tokens
        ):
            raise DataError("Invalid or missing authorization token.", 401)
        try:
            parsed = uuid.UUID(key)
            canonical = str(parsed)
            # Rust Uuid::parse_str accepts four exact shapes. Python also
            # strips malformed prefixes, braces and misplaced hyphens.
            if key.lower() not in (canonical, parsed.hex, "{" + canonical + "}") and not (
                key.startswith("urn:uuid:") and key[9:].lower() == canonical
            ):
                raise ValueError("Unsupported UUID shape")
        except ValueError as exc:
            raise DataError("Key must be a valid UUID", 400) from exc

    @app.get("/sync/{key:path}")
    async def sync_get(key: str, request: Request, secret: str):
        key = request.scope.get("aipa.sync_path_key", key)
        authorize(request, key)
        response = await background(repo.sync, key, secret)
        response["value"] = sort_json_value_keys(response["value"])
        return response

    @app.post("/sync/{key:path}")
    async def sync_post(key: str, body: SyncPost, request: Request):
        key = request.scope.get("aipa.sync_path_key", key)
        authorize(request, key)
        response = await background(repo.sync, key, body.secret, body.value, True)
        response["value"] = sort_json_value_keys(response["value"])
        return response

    @app.get("/analysis/top-performers")
    async def performers(
        mode_value: Annotated[
            str,
            Query(
                alias="mode",
                json_schema_extra={
                    "enum": ["vn", "stock", "stocks", "crypto", "cryptos", "yahoo", "all"]
                },
            ),
        ] = "vn",
        date: str | None = None,
        ema: StrictBoolean = False,
        sort_by: str = "close_changed",
        direction: str = "desc",
        limit: OptionalUnsigned64 = None,
        min_volume: OptionalUnsigned64 = None,
        sector: str | None = None,
        snap: SnapBoolean = True,
    ):
        key = (
            "analysis/top-performers",
            mode(mode_value),
            date,
            ema,
            sort_by,
            direction,
            10 if limit is None else limit,
            10000 if min_volume is None else min_volume,
            sector,
            snap,
        )
        return await coalesced_background(
            key,
            analysis.performers,
            mode(mode_value),
            date,
            ema,
            sort_by,
            direction,
            10 if limit is None else limit,
            10000 if min_volume is None else min_volume,
            sector,
            snap,
        )

    @app.get("/analysis/ma-scores-by-sector")
    async def sectors(
        mode_value: Annotated[
            str,
            Query(
                alias="mode",
                json_schema_extra={"enum": MODE_VALUES},
            ),
        ] = "vn",
        date: str | None = None,
        ema: StrictBoolean = False,
        ma_period: Unsigned32 = 20,
        min_score: float = 0,
        above_threshold_only: StrictBoolean = False,
        top_per_sector: OptionalUnsigned64 = None,
        snap: SnapBoolean = True,
    ):
        key = (
            "analysis/ma-scores-by-sector",
            mode(mode_value),
            date,
            ema,
            ma_period,
            min_score,
            above_threshold_only,
            10 if top_per_sector is None else top_per_sector,
            snap,
        )
        return await coalesced_background(
            key,
            analysis.sectors,
            mode(mode_value),
            date,
            ema,
            ma_period,
            min_score,
            above_threshold_only,
            10 if top_per_sector is None else top_per_sector,
            snap,
        )

    @app.get("/analysis/volume-profile")
    async def profile(
        symbol: str,
        mode_value: Annotated[
            str,
            Query(
                alias="mode",
                description=(
                    "Unknown values fall back to vn; only crypto and yahoo select another source."
                ),
            ),
        ] = "vn",
        date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        bins: OptionalUnsigned64 = None,
        value_area_pct: OptionalFloat = None,
    ):
        key = (
            "analysis/volume-profile",
            symbol,
            mode_value,
            date,
            start_date,
            end_date,
            50 if bins is None else bins,
            70 if value_area_pct is None else value_area_pct,
        )
        return await coalesced_background(
            key,
            analysis.profile,
            symbol,
            mode_value,
            date,
            start_date,
            end_date,
            50 if bins is None else bins,
            70 if value_area_pct is None else value_area_pct,
        )

    @app.get("/analysis/rrg")
    async def rrg(
        mode_value: Annotated[
            str,
            Query(
                alias="mode",
                json_schema_extra={"enum": MODE_VALUES},
            ),
        ] = "vn",
        date: str | None = None,
        ema: StrictBoolean = False,
        algorithm: Annotated[str, Query(json_schema_extra={"enum": ["jdk", "mascore"]})] = "jdk",
        benchmark: str | None = None,
        period: Unsigned64 = 10,
        trails: Unsigned64 = 10,
        min_volume: Signed64 = 100000,
        snap: SnapBoolean = True,
    ):
        key = (
            "analysis/rrg",
            mode(mode_value),
            date,
            ema,
            algorithm,
            benchmark,
            period,
            trails,
            min_volume,
            snap,
        )
        return await coalesced_background(
            key,
            analysis.rrg,
            mode(mode_value),
            date,
            ema,
            algorithm,
            benchmark,
            period,
            trails,
            min_volume,
            snap,
        )

    @app.get("/explorer", include_in_schema=False)
    async def explorer():
        try:
            html = (settings.public_dir / "index.html").read_text(encoding="utf-8")
        except OSError:
            return PlainTextResponse("Explorer not found", status_code=404)
        return Response(html, headers={"content-type": "text/html; charset=utf-8"})

    public_files = RustCompatibleStaticFiles(directory=settings.public_dir, check_dir=False)
    app.mount("/public", public_files, name="public")

    @app.api_route("/public", methods=["GET", "HEAD"], name="public_root", include_in_schema=False)
    async def public_root(request: Request):
        # Axum always registers ServeDir, even when its directory is missing.
        # Keep the exact mount path registered too so a missing asset directory
        # remains an empty static 404 instead of falling through to API JSON 404.
        index_path = settings.public_dir / "index.html"
        try:
            stat_result = index_path.stat()
        except OSError:
            return Response(status_code=404)
        if not stat.S_ISREG(stat_result.st_mode):
            return Response(status_code=404)
        return public_files.file_response(str(index_path), stat_result, request.scope)

    @app.exception_handler(404)
    async def not_found(request, exc):
        if request.scope.get("aipa.public_path", False):
            return Response(status_code=404)
        return JSONResponse({"error": "not found"}, 404)

    # Wrap the entire application so errors and preflight responses get CORS too.
    configured_origins = list(settings.cors_origins) or [""]
    normalized_origins = [trim_rust_whitespace(origin) for origin in configured_origins]
    if "*" in normalized_origins and normalized_origins != ["*"]:
        raise ValueError("CORS_ORIGINS cannot combine '*' with specific origins")
    cors_origins = []
    for origin in normalized_origins:
        parsed = rust_header_value(origin)
        if parsed is not None:
            cors_origins.append(parsed)
    cors_permissive = not cors_origins or cors_origins == ["*"]
    if cors_permissive:
        cors_origins = ["*"]
        cors_methods = ["*"]
        cors_headers = ["*"]
        cors_exposed_headers = ["*"]
    else:
        cors_methods = ["GET", "POST", "DELETE", "OPTIONS"]
        cors_headers = ["content-type", "authorization", "user-agent"]
        # The browser clients read these diagnostics. Same-origin Vite proxy
        # requests hide the fact that production cross-origin fetches need an
        # explicit CORS exposure list.
        cors_exposed_headers = RUST_CORS_EXPOSE_HEADERS
    app.add_middleware(
        RustCompatibleCORSMiddleware,
        route_allow_header=lambda scope: rust_route_allow_header(app.routes, scope),
        allow_origins=cors_origins,
        allow_methods=cors_methods,
        allow_headers=cors_headers,
        expose_headers=cors_exposed_headers,
        rust_permissive=cors_permissive,
    )
    # Tower's compression layer wraps routing, CORS, and early query errors.
    # Keep it outermost so its Vary header also appears on those responses.
    app.add_middleware(RustCompatibleCompressionMiddleware)
    return app
