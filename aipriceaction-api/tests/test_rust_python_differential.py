"""Opt-in HTTP differential checks against an isolated Rust/Postgres fixture.

Run with ``AIPA_RUN_RUST_DIFFERENTIAL=1 pytest -q tests/test_rust_python_differential.py``.
The harness never reads the repository's Rust ``.env`` or SQLite database. It
uses a temporary PostgreSQL cluster and temporary Python database. Host server
tools are preferred; ``AIPA_USE_DOCKER_POSTGRES=1`` opts into a cached Docker
image fallback that uses loopback networking and a temporary tmpfs. Both modes
disable every Rust worker and clean up their resources on success or failure.
Set ``AIPA_USE_LIVE_REDIS_DIFFERENTIAL=1`` to also start a loopback-only,
no-persistence Redis process and compare the same fixture through separate
Redis databases for Rust and Python.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode

import httpx
import pytest
from fastapi.testclient import TestClient

from aipriceaction_api.app import create_app
from aipriceaction_api.config import Settings
from aipriceaction_api.domain import Candle
from aipriceaction_api.uploads import _storage_usage

REPO_ROOT = Path(__file__).resolve().parents[2]
RUST_ROOT = REPO_ROOT / "aipriceaction"
RUST_BINARY = RUST_ROOT / "target/debug/aipriceaction"
DAILY_BARS = 701
LONG_HISTORY_EXTRA_BARS = 8_000
FIXTURE_END = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
    days=1
)
while FIXTURE_END.weekday() >= 5:
    FIXTURE_END -= timedelta(days=1)
FIXTURE_DATES_REVERSED = []
fixture_day = FIXTURE_END
while len(FIXTURE_DATES_REVERSED) < DAILY_BARS:
    if fixture_day.weekday() < 5:
        FIXTURE_DATES_REVERSED.append(fixture_day)
    fixture_day -= timedelta(days=1)
FIXTURE_DATES = list(reversed(FIXTURE_DATES_REVERSED))
FIXTURE_START = FIXTURE_DATES[0]
WEB_ORIGIN = "http://127.0.0.1:5173"
PRODUCTION_WEB_ORIGIN = "https://aipriceaction.com"
SYNC_TOKEN = "differential-test-token"
SYNC_SECRET = "differential-test-secret"
REFRESH_SECRET = "differential-refresh-secret"
REFRESH_SENTINEL_EPOCH = 946684800
REFRESH_SCHEDULE_COLUMNS = {
    "1D": "next_1d",
    "1h": "next_1h",
    "1m": "next_1m",
}
WEB_TICKER_CSV_COLUMNS = (
    "symbol",
    "time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "ma10",
    "ma20",
    "ma50",
    "ma100",
    "ma200",
    "ma10_score",
    "ma20_score",
    "ma50_score",
    "ma100_score",
    "ma200_score",
    "close_changed",
    "volume_changed",
    "total_money_changed",
)


def _parse_web_ticker_csv(text: str) -> tuple[list[dict[str, str]], list[list[str]]]:
    """Parse ordinary CSV rows and the Rust-compatible literal-comma symbol."""
    reader = csv.reader(text.splitlines())
    header = next(reader, [])
    errors = []
    if tuple(header) != WEB_TICKER_CSV_COLUMNS:
        errors.append(header)
        return [], errors

    rows = []
    for values in reader:
        if len(values) == len(WEB_TICKER_CSV_COLUMNS) + 1 and values[:2] == ["DUPL", "FPT"]:
            row = dict(zip(WEB_TICKER_CSV_COLUMNS[1:], values[2:], strict=True))
            row["symbol"] = "DUPL,FPT"
        elif len(values) == len(WEB_TICKER_CSV_COLUMNS):
            row = dict(zip(WEB_TICKER_CSV_COLUMNS, values, strict=True))
        else:
            errors.append(values)
            continue
        rows.append(row)
    return rows, errors


def _csv_time_epoch(value: str) -> int | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp())


def _expected_ema(values: list[float], period: int) -> float:
    if not values:
        raise ValueError("EMA oracle requires at least one value")
    seed_length = min(period, len(values))
    ema_value = sum(values[:seed_length]) / seed_length
    multiplier = 2.0 / (period + 1)
    for value in values[seed_length:]:
        ema_value = value * multiplier + ema_value * (1 - multiplier)
    return ema_value
DOCKER_POSTGRES_IMAGE = "postgres:17-bookworm"
DOCKER_POSTGRES_DATA = "/var/lib/postgresql/data"
# Health values are compared in full except for the two fields with unavoidable
# wall-clock variation. `current_system_time` must still be a valid UTC timestamp;
# `uptime_secs` must be a non-negative integer within this small startup skew.
HEALTH_UPTIME_TOLERANCE_SECS = 2
HEALTH_CLOCK_POLICY = (
    "current_system_time is validated as UTC but its exact value is volatile; "
    f"uptime_secs must be a non-negative integer within {HEALTH_UPTIME_TOLERANCE_SECS}s. "
    "Every other health field and the complete JSON shape are compared exactly."
)

# The small catalog is shared byte-for-byte by the two implementations. It
# contains a source collision to exercise mode=all precedence without importing
# either application's live catalog or any market database.
TICKERS = (
    ("vn", "DUPL", "Duplicate Corporation", 120.0, 0.73),
    ("vn", "FPT", "FPT Corporation", 100.0, 0.81),
    ("vn", "VCB", "Vietcombank", 80.0, 0.51),
    ("vn", "VNINDEX", "Vietnam Index", 1200.0, 0.29),
    ("vn", "ABC", "Three Letter Stock", 35.0, 0.13),
    ("crypto", "BTCUSDT", "Bitcoin", 50000.0, 13.0),
    ("crypto", "ETHUSDT", "Ether", 3000.0, 1.7),
    ("yahoo", "AAPL", "Apple Inc.", 180.0, 0.37),
    ("yahoo", "DUPL", "Yahoo Duplicate", 60.0, 0.19),
    ("yahoo", "COLL", "Yahoo Collision", 70.0, 0.23),
    ("sjc", "SJC", "SJC Gold", 80.0, 0.11),
    ("sjc", "COLL", "SJC Collision", 90.0, 0.07),
    ("yahoo", "FPT", "Yahoo FPT", 130.0, 0.31),
    ("yahoo", "DUPL,FPT", "Yahoo Literal Comma", 45.0, 0.17),
    ("yahoo", "CSVEDGE=F", "CSV Formatting Edge Futures", 100.0, 0.1),
    ("yahoo", "^GSPC", "S&P 500", 5000.0, 7.3),
)
SYNTHETIC_PROFILE_TICKERS = (
    ("crypto", "MICROUSDT", "Low-priced crypto fixture"),
    ("crypto", "WIDEUSDT", "Wide-range crypto fixture"),
    ("crypto", "PROFILEUSDT", "High-resolution volume profile fixture"),
)
FIXTURE_INSTRUMENTS = tuple(
    sorted(
        {(source, symbol) for source, symbol, *_ in TICKERS}
        | {(source, symbol) for source, symbol, _name in SYNTHETIC_PROFILE_TICKERS}
    )
)
FIXTURE_TICKER_COUNT = len(FIXTURE_INSTRUMENTS)


@dataclass(frozen=True)
class Snapshot:
    status: int
    headers: dict[str, str]
    body: bytes
    json_value: Any | None
    is_json: bool = False
    request_method: str | None = None


@dataclass(frozen=True)
class RequestCase:
    name: str
    method: str
    path: str
    headers: dict[str, str] | None = None
    json_body: Any | None = None
    files: dict[str, tuple[str, bytes, str]] | list[tuple[str, tuple[str, bytes, str]]] | None = (
        None
    )
    raw_body: bytes | None = None
    chunked_body: tuple[bytes, ...] | None = None
    compare_body: bool = True
    volatile_json_fields: tuple[str, ...] = ()
    health_clock_policy: bool = False
    ignored_headers: tuple[str, ...] = ()
    request_timeout: float | None = None


@dataclass(frozen=True)
class PostgresProvider:
    kind: str
    host_tools: tuple[tuple[str, str], ...] = ()
    docker_path: str | None = None
    docker_context: str | None = None

    def host_tool(self, name: str) -> str:
        return dict(self.host_tools)[name]


class _ChunkedBody(httpx.SyncByteStream):
    """A request body with no Content-Length header, yielded in chunks."""

    def __init__(self, chunks: tuple[bytes, ...]):
        self.chunks = chunks

    def __iter__(self):
        yield from self.chunks


@dataclass(frozen=True)
class PostgresHandle:
    database_url: str = field(repr=False)
    port: int
    username: str
    database_name: str
    provider: PostgresProvider = field(repr=False)
    container_name: str | None = None


def _select_postgres_provider(
    env: dict[str, str],
    host_tools: dict[str, str | None],
    docker_path: str | None,
    docker_context: str | None = None,
) -> PostgresProvider | None:
    required_host_tools = ("postgres", "initdb", "pg_ctl", "psql")
    host_entries = tuple(
        (name, tool) for name in required_host_tools if (tool := host_tools.get(name)) is not None
    )
    if len(host_entries) == len(required_host_tools):
        return PostgresProvider(
            kind="host",
            host_tools=host_entries,
        )
    if env.get("AIPA_USE_DOCKER_POSTGRES") == "1" and docker_path and docker_context:
        return PostgresProvider(
            kind="docker", docker_path=docker_path, docker_context=docker_context
        )
    return None


def _docker_environment(context: str) -> dict[str, str]:
    # DOCKER_HOST can override the selected context and point at a remote
    # daemon. Every test command pins the context explicitly through the env.
    env = dict(os.environ)
    env.pop("DOCKER_HOST", None)
    env["DOCKER_CONTEXT"] = context
    return env


def _is_local_docker_endpoint(endpoint: Any) -> bool:
    return isinstance(endpoint, str) and endpoint.startswith("unix:///")


def _local_docker_context(docker_path: str, env: dict[str, str]) -> str | None:
    # Docker context inspection is local metadata. Reject SSH/TCP endpoints so
    # the destructive create/remove lifecycle can only target a local socket.
    command_env = dict(env)
    command_env.pop("DOCKER_HOST", None)
    try:
        context = command_env.get("DOCKER_CONTEXT")
        if not context:
            result = subprocess.run(
                [docker_path, "context", "show"],
                check=True,
                capture_output=True,
                text=True,
                env=command_env,
                timeout=5,
            )
            context = result.stdout.strip()
        if not context:
            return None
        result = subprocess.run(
            [
                docker_path,
                "context",
                "inspect",
                "--format",
                "{{json .Endpoints.docker.Host}}",
                context,
            ],
            check=True,
            capture_output=True,
            text=True,
            env=command_env,
            timeout=5,
        )
        endpoint = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    if not _is_local_docker_endpoint(endpoint):
        return None
    return context


def _docker_postgres_run_command(
    docker_path: str,
    container_name: str,
    host_port: int,
    env_file: Path,
) -> list[str]:
    return [
        docker_path,
        "run",
        "--pull=never",
        "--detach",
        "--name",
        container_name,
        "--publish",
        f"127.0.0.1:{host_port}:5432",
        "--tmpfs",
        f"{DOCKER_POSTGRES_DATA}:rw,noexec,nosuid,size=1g",
        "--env-file",
        str(env_file),
        DOCKER_POSTGRES_IMAGE,
    ]


def _docker_postgres_ready_command(
    docker_path: str,
    container_name: str,
    username: str,
    database_name: str,
) -> list[str]:
    return [
        docker_path,
        "exec",
        container_name,
        "pg_isready",
        "-q",
        "-h",
        "127.0.0.1",
        "-U",
        username,
        "-d",
        database_name,
    ]


def _docker_postgres_tmpfs_command(docker_path: str, container_name: str) -> list[str]:
    return [
        docker_path,
        "inspect",
        "--format",
        "{{json .HostConfig.Tmpfs}}",
        container_name,
    ]


def _docker_postgres_mounts_command(docker_path: str, container_name: str) -> list[str]:
    return [
        docker_path,
        "inspect",
        "--format",
        "{{json .Mounts}}",
        container_name,
    ]


def _docker_postgres_seed_command(
    docker_path: str,
    container_name: str,
    username: str,
    database_name: str,
) -> list[str]:
    return [
        docker_path,
        "exec",
        "-i",
        container_name,
        "psql",
        "-h",
        "127.0.0.1",
        "-U",
        username,
        "-d",
        database_name,
        "-v",
        "ON_ERROR_STOP=1",
    ]


def _docker_postgres_remove_command(docker_path: str, container_name: str) -> list[str]:
    # The official image declares PGDATA as a VOLUME. `-v` removes only any
    # anonymous volume attached to this unique test container if Docker creates
    # one despite the explicit tmpfs mount; named volumes are never used here.
    return [docker_path, "rm", "-f", "-v", container_name]


def _health_fixture_ready(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    # Both servers initialize the health snapshot before accepting HTTP calls.
    # This predicate detects an unexpected default/empty value on first serve.
    if value.get("total_tickers_count") != FIXTURE_TICKER_COUNT:
        return False
    for key in (
        "daily_records_count",
        "hourly_records_count",
        "minute_records_count",
    ):
        count = value.get(key)
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            return False
    active_tickers = value.get("active_tickers_count")
    if (
        isinstance(active_tickers, bool)
        or not isinstance(active_tickers, int)
        or active_tickers <= 0
    ):
        return False
    daily_sync = value.get("daily_last_sync")
    if not isinstance(daily_sync, str):
        return False
    try:
        parsed = datetime.fromisoformat(daily_sync.replace("Z", "+00:00"))
    except ValueError:
        return False
    expected_sync = FIXTURE_END + timedelta(days=1)
    return parsed.utcoffset() == timedelta(0) and parsed == expected_sync


def _free_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _is_loopback_port_bind_conflict(details: str) -> bool:
    lowered = details.lower()
    return any(
        marker in lowered
        for marker in (
            "address already in use",
            "port is already allocated",
            "only one usage of each socket address",
        )
    )


def _start_redis(root: Path) -> tuple[str, subprocess.Popen[str]]:
    redis_binary = shutil.which("redis-server")
    if not redis_binary:
        _skip_or_fail_required_differential(
            "redis-server is unavailable for the live Redis differential"
        )

    import redis

    log_path = root / "redis-server.log"
    for attempt in range(3):
        port = _free_loopback_port()
        url = f"redis://127.0.0.1:{port}/0"
        child = None
        client = None
        try:
            with log_path.open("w", encoding="utf-8") as log_handle:
                child = subprocess.Popen(
                    [
                        redis_binary,
                        "--bind",
                        "127.0.0.1",
                        "--protected-mode",
                        "yes",
                        "--port",
                        str(port),
                        "--save",
                        "",
                        "--appendonly",
                        "no",
                        "--dir",
                        str(root),
                        "--logfile",
                        str(log_path),
                    ],
                    cwd=root,
                    env={"PATH": os.environ.get("PATH", ""), "HOME": str(root)},
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                )

            client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=0.2)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    break
                try:
                    if client.ping():
                        return url, child
                except redis.RedisError:
                    time.sleep(0.05)
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            raise RuntimeError(f"Temporary Redis did not become ready:\n{log_text}")
        except BaseException as startup_error:
            if child is not None:
                _stop_child_process(child)
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            if attempt < 2 and _is_loopback_port_bind_conflict(log_text):
                continue
            if isinstance(startup_error, RuntimeError):
                raise RuntimeError(f"{startup_error}:\n{log_text}") from startup_error
            raise
        finally:
            if client is not None:
                client.close()
    raise RuntimeError("Temporary Redis could not claim a loopback port after three attempts")


def _seed_redis(url: str, rows: list[Candle]) -> None:
    import redis

    retention_by_interval = {"1D": 5_000, "1h": 30_000, "1m": 20_000}
    members_by_key: dict[str, dict[str, int]] = {}
    for row in rows:
        timestamp_ms = row.time * 1000
        key = f"ohlcv:{row.source}:{row.symbol}:{row.interval}"
        member = "|".join(
            (
                str(timestamp_ms),
                format(row.open, ".17g"),
                format(row.high, ".17g"),
                format(row.low, ".17g"),
                format(row.close, ".17g"),
                str(row.volume),
            )
        )
        members_by_key.setdefault(key, {})[member] = timestamp_ms

    client = redis.Redis.from_url(url, decode_responses=True)
    try:
        for key, members in members_by_key.items():
            client.zadd(key, members)
            retention = retention_by_interval.get(key.rsplit(":", 1)[-1])
            if retention is not None and len(members) > retention:
                client.zremrangebyrank(key, 0, -(retention + 1))
    finally:
        client.close()


def _seed_redis_boundary_members(url: str) -> None:
    """Add malformed and score/member-mismatched rows for Redis bound tests."""
    import redis

    key = "ohlcv:vn:DIFF:1D"
    members = {}
    for offset, close in ((2, 10), (1, 20), (0, 30)):
        timestamp_ms = int((FIXTURE_END - timedelta(days=offset)).timestamp() * 1000)
        members[f"{timestamp_ms}|{close - 1}|{close + 1}|{close - 2}|{close}|100|1"] = timestamp_ms
    oldest_ms = int((FIXTURE_START - timedelta(days=1)).timestamp() * 1000)
    members["malformed oldest differential member"] = oldest_ms
    future_ms = int((FIXTURE_END + timedelta(days=1)).timestamp() * 1000)
    end_score_ms = int(FIXTURE_END.timestamp() * 1000)
    members[f"{future_ms}|99|100|98|99|100|1"] = end_score_ms

    client = redis.Redis.from_url(url, decode_responses=True)
    try:
        client.zadd(key, members)
    finally:
        client.close()


def _seed_analysis_snapshots(url: str) -> None:
    """Seed a complete Rust-shaped joined snapshot plus one ticker snapshot."""
    import redis

    client = redis.Redis.from_url(url, decode_responses=True)
    try:
        stamp = FIXTURE_END.isoformat().replace("+00:00", "Z")
        for source, symbol, _name, base, _slope in TICKERS:
            if source != "vn":
                continue
            close = 9876.5 if symbol == "VCB" else base + 1
            row = {
                "ticker": symbol,
                "time": stamp,
                "open": close - 0.5,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": 250_000,
                "ma20": close - 2,
                "ma20_score": None if symbol == "VCB" else base / 100,
            }
            client.hset(
                f"snap:{source}:{symbol}:1D",
                mapping={"201:sma:joined": json.dumps([row], allow_nan=False)},
            )

        ticker_row = {
            "time": FIXTURE_END.date().isoformat(),
            "open": 9800.0,
            "high": 9900.0,
            "low": 9700.0,
            "close": 9876.5,
            "volume": 250_000,
            "symbol": "VCB",
        }
        client.hset(
            "snap:vn:VCB:1D",
            mapping={"1:sma": json.dumps([ticker_row], allow_nan=False)},
        )
    finally:
        client.close()


def _analysis_snapshot_symbols() -> dict[str, tuple[str, ...]]:
    """Return the non-empty mode=all source universes used by analysis routes."""
    symbols = {
        source: list(
            dict.fromkeys(symbol for row_source, symbol, *_ in TICKERS if row_source == source)
        )
        for source in ("vn", "yahoo", "crypto")
    }
    # Rust merges the SJC group file into the Yahoo analysis universe, so the
    # joined-snapshot lookup also asks for yahoo:SJC even though the fixture's
    # canonical SJC instrument is stored under the separate `sjc` source.
    symbols["yahoo"].append("SJC")
    return {source: tuple(source_symbols) for source, source_symbols in symbols.items()}


def _mode_all_joined_snapshot_row(
    source: str, symbol: str, index: int, stamp: str
) -> dict[str, Any]:
    close = {"vn": 9876.5, "yahoo": 8765.5, "crypto": 7654.5}[source]
    score = {"vn": 90.0, "yahoo": 80.0, "crypto": 70.0}[source]
    close += index / 100
    score += index / 100
    return {
        "ticker": symbol,
        "time": stamp,
        "open": close - 0.5,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": 250_000,
        "ma10": close - 1,
        "ma20": close - 2,
        "ma50": close - 3,
        "ma100": close - 4,
        "ma200": close - 5,
        "ma10_score": score,
        "ma20_score": score,
        "ma50_score": score,
        "ma100_score": score + 1,
        "ma200_score": score,
        "close_changed": score,
        "volume_changed": score,
        "total_money_changed": score,
    }


def _seed_mode_all_joined_snapshots(client, *, tied_metrics: bool = False) -> None:
    """Seed complete SMA snapshots for every non-empty source in mode=all."""
    symbols_by_source = _analysis_snapshot_symbols()
    for source, symbols in symbols_by_source.items():
        if symbols:
            client.delete(*(f"snap:{source}:{symbol}:1D" for symbol in symbols))

    stamp = FIXTURE_END.isoformat().replace("+00:00", "Z")
    for source, symbols in symbols_by_source.items():
        for index, symbol in enumerate(symbols):
            row = _mode_all_joined_snapshot_row(source, symbol, index, stamp)
            if tied_metrics:
                # All sources and symbols share identical performer and MA
                # metrics so the Rust symbol/source tie-breaks are exercised.
                row.update(
                    close_changed=7.0,
                    volume_changed=7.0,
                    total_money_changed=7.0,
                    ma10_score=7.0,
                    ma20_score=7.0,
                    ma50_score=7.0,
                    ma100_score=7.0,
                    ma200_score=7.0,
                )
            serialized = json.dumps(
                [row],
                allow_nan=False,
            )
            client.hset(
                f"snap:{source}:{symbol}:1D",
                mapping={
                    "201:sma:joined": serialized,
                    "101:sma:joined": serialized,
                },
            )


def _prepare_live_redis_case(urls: tuple[str, str], case_name: str, rows: list[Candle]) -> None:
    """Apply identical isolated Redis edge state immediately before one case."""
    import redis

    for url in urls:
        client = redis.Redis.from_url(url, decode_responses=True)
        try:
            if case_name in {
                "top-performers-default-snapshot-option",
                "ma-scores-by-sector-default-snapshot-option",
            } or case_name.startswith("production-origin-data-top-performers"):
                # Earlier raw analysis requests write joined snapshots as a
                # side effect. Restore synthetic rows immediately before the
                # default-snapshot cases so they prove this cache path without
                # depending on test order.
                _seed_analysis_snapshots(url)
            elif case_name in {
                "ticker-stale-snapshot-hit",
                "ticker-incomplete-snapshot-miss",
                "ticker-malformed-snapshot-miss",
                "ticker-snapshot-hash-error-fallback",
            }:
                if case_name == "ticker-stale-snapshot-hit":
                    stamp = (FIXTURE_END - timedelta(days=30)).date().isoformat()
                    close = 9876.5
                    row = {
                        "time": stamp,
                        "open": close - 1,
                        "high": close + 1,
                        "low": close - 2,
                        "close": close,
                        "volume": 250_000,
                        "symbol": "VCB",
                    }
                    client.hset(
                        "snap:vn:VCB:1D",
                        "1:sma",
                        json.dumps([row], allow_nan=False),
                    )
                elif case_name == "ticker-incomplete-snapshot-miss":
                    row = {
                        "time": FIXTURE_END.date().isoformat(),
                        "open": 9876.0,
                        "high": 9877.5,
                        "low": 9875.5,
                        "close": 9876.5,
                        "volume": 250_000,
                        "symbol": "VCB",
                    }
                    client.hset(
                        "snap:vn:VCB:1D",
                        "2:sma",
                        json.dumps([row], allow_nan=False),
                    )
                    client.delete("snap:vn:FPT:1D")
                elif case_name == "ticker-snapshot-hash-error-fallback":
                    client.set("snap:vn:FPT:1D", "wrong redis key type")
                else:
                    client.hset("snap:vn:FPT:1D", "3:sma", '[{"time":"wrong-shape"}]')
            elif case_name == "rrg-mascore-duplicate-member-raw-limit-seed-depth":
                vcb_rows = sorted(
                    (
                        row
                        for row in rows
                        if row.source == "vn" and row.symbol == "VCB" and row.interval == "1D"
                    ),
                    key=lambda row: row.time,
                )[-201:]
                key = "ohlcv:vn:VCB:1D"
                members = {}
                for index, row in enumerate(vcb_rows):
                    if index == len(vcb_rows) - 100:
                        close = 10_000.0
                    elif index == len(vcb_rows) - 1:
                        close = 80.0
                    elif index >= len(vcb_rows) - 99:
                        close = 100.0
                    else:
                        close = row.close
                    timestamp_ms = row.time * 1000
                    member = "|".join(
                        (
                            str(timestamp_ms),
                            format(close - 0.5, ".17g"),
                            format(close + 1.25, ".17g"),
                            format(close - 1.5, ".17g"),
                            format(close, ".17g"),
                            str(row.volume),
                        )
                    )
                    members[member] = timestamp_ms

                latest = vcb_rows[-1]
                timestamp_ms = latest.time * 1000
                for crawl_index in range(102):
                    close = 110.0 if crawl_index == 101 else 90.0
                    member = "|".join(
                        (
                            str(timestamp_ms),
                            format(close - 0.5, ".17g"),
                            format(close + 1.25, ".17g"),
                            format(close - 1.5, ".17g"),
                            format(close, ".17g"),
                            str(latest.volume),
                            str(10_000_000_000_000 + crawl_index),
                        )
                    )
                    members[member] = timestamp_ms

                client.delete(key)
                client.zadd(key, members)
            elif case_name in {
                "rrg-mascore-valid-stale-joined-snapshot",
                "rrg-mascore-null-required-ohlc-joined-snapshot",
            }:
                vcb_rows = [
                    row
                    for row in rows
                    if row.source == "vn" and row.symbol == "VCB" and row.interval == "1D"
                ]
                client.delete("ohlcv:vn:VCB:1D")
                _seed_redis(url, vcb_rows)
                if case_name == "rrg-mascore-null-required-ohlc-joined-snapshot":
                    latest = max(vcb_rows, key=lambda row: row.time)
                    timestamp_ms = latest.time * 1000
                    redis_close = 4321.25
                    member = "|".join(
                        (
                            str(timestamp_ms),
                            format(redis_close - 1, ".17g"),
                            format(redis_close + 1, ".17g"),
                            format(redis_close - 2, ".17g"),
                            format(redis_close, ".17g"),
                            str(latest.volume),
                            "9999999999999",
                        )
                    )
                    client.zadd("ohlcv:vn:VCB:1D", {member: timestamp_ms})
                for source, symbol, _name, base, _slope in TICKERS:
                    if source != "vn":
                        continue
                    row = {
                        "ticker": symbol,
                        "time": FIXTURE_START.isoformat().replace("+00:00", "Z"),
                        "open": base - 0.5,
                        "high": base + 1.25,
                        "low": base - 1.5,
                        "close": 9876.5 if symbol == "VCB" else base + 1,
                        "volume": 250_000,
                        "ma20_score": 1234.5,
                        "ma100_score": 2345.5,
                    }
                    if (
                        case_name == "rrg-mascore-null-required-ohlc-joined-snapshot"
                        and symbol == "VCB"
                    ):
                        row["close"] = None
                    client.hset(
                        f"snap:{source}:{symbol}:1D",
                        "101:sma:joined",
                        json.dumps([row], allow_nan=False),
                    )
            elif case_name.startswith("analysis-mode-all-joined-snapshot-"):
                fixture_keys = {f"ohlcv:{row.source}:{row.symbol}:{row.interval}" for row in rows}
                if fixture_keys:
                    client.delete(*fixture_keys)
                _seed_redis(url, rows)
                _seed_mode_all_joined_snapshots(client)
                if case_name.endswith("incomplete-yahoo-fallback"):
                    # One missing Yahoo snapshot invalidates only that source's
                    # joined set. Also remove AAPL's raw cache row so the paired
                    # request proves its per-ticker SQL fallback.
                    client.hdel(
                        "snap:yahoo:AAPL:1D",
                        "201:sma:joined",
                        "101:sma:joined",
                    )
                    client.delete("ohlcv:yahoo:AAPL:1D")
            elif case_name.startswith("analysis-mode-all-tie-"):
                fixture_keys = {f"ohlcv:{row.source}:{row.symbol}:{row.interval}" for row in rows}
                if fixture_keys:
                    client.delete(*fixture_keys)
                _seed_redis(url, rows)
                _seed_mode_all_joined_snapshots(client, tied_metrics=True)
            elif case_name == "top-performers-stale-joined-snapshot":
                fixture_keys = {f"ohlcv:{row.source}:{row.symbol}:{row.interval}" for row in rows}
                if fixture_keys:
                    client.delete(*fixture_keys)
                _seed_redis(url, rows)
                _seed_mode_all_joined_snapshots(client)
                stale_stamp = (FIXTURE_END - timedelta(days=30)).isoformat().replace("+00:00", "Z")
                stale_row = _mode_all_joined_snapshot_row("vn", "VCB", 0, stale_stamp)
                serialized = json.dumps([stale_row], allow_nan=False)
                client.hset(
                    "snap:vn:VCB:1D",
                    mapping={
                        "201:sma:joined": serialized,
                        "101:sma:joined": serialized,
                    },
                )
            elif case_name in {
                "volume-profile-partial-tail-overlap",
                "volume-profile-tail-no-overlap",
                "volume-profile-restore-tail-after-partial-cases",
            }:
                minute_key = "ohlcv:vn:FPT:1m"
                minute_rows = [
                    row
                    for row in rows
                    if row.source == "vn" and row.symbol == "FPT" and row.interval == "1m"
                ]
                client.delete(minute_key)
                if case_name == "volume-profile-partial-tail-overlap":
                    # SQL has all 30 current-day rows; Redis exposes only an
                    # overlapping slice to prove both handlers use the retained
                    # tail without completing it from SQL.
                    selected = [
                        row
                        for row in minute_rows
                        if FIXTURE_END.replace(hour=2, minute=10)
                        <= datetime.fromtimestamp(row.time, UTC)
                        < FIXTURE_END.replace(hour=2, minute=18)
                    ]
                else:
                    # Keep only current-day Redis rows. The no-overlap request
                    # targets the prior fixture day, whose zero-volume row is
                    # available only through SQL; the restore case puts the full
                    # fixture tail back before the history-gap assertion below.
                    selected = [
                        row
                        for row in minute_rows
                        if datetime.fromtimestamp(row.time, UTC).date() == FIXTURE_END.date()
                    ]
                    if case_name == "volume-profile-restore-tail-after-partial-cases":
                        selected = minute_rows
                _seed_redis(url, selected)
            elif case_name in {
                "ticker-redis-ticker-list-subset",
                "ticker-redis-ticker-list-source-priority",
                "ticker-redis-ticker-list-absent",
                "ticker-redis-ticker-list-malformed",
                "ticker-redis-ticker-list-empty",
                "ticker-redis-ticker-list-incomplete-explicit",
                "ticker-redis-ticker-list-all-empty",
            }:
                fixture_keys = {f"ohlcv:{row.source}:{row.symbol}:{row.interval}" for row in rows}
                if fixture_keys:
                    client.delete(*fixture_keys)
                _seed_redis(url, rows)
                client.delete("meta:ticker_list")
                if case_name == "ticker-redis-ticker-list-subset":
                    # No-symbol listing should follow a valid Redis identity cache,
                    # even when PostgreSQL/SQLite know additional fixture tickers.
                    client.set(
                        "meta:ticker_list",
                        json.dumps(
                            [
                                {"source": "vn", "ticker": "VCB"},
                                {"source": "crypto", "ticker": "BTCUSDT"},
                            ],
                            separators=(",", ":"),
                        ),
                    )
                elif case_name in {
                    "ticker-redis-ticker-list-source-priority",
                    "ticker-redis-ticker-list-incomplete-explicit",
                }:
                    client.set(
                        "meta:ticker_list",
                        json.dumps([{"source": "yahoo", "ticker": "DUPL"}], separators=(",", ":")),
                    )
                elif case_name == "ticker-redis-ticker-list-malformed":
                    client.set("meta:ticker_list", "{malformed")
                elif case_name == "ticker-redis-ticker-list-empty":
                    client.set("meta:ticker_list", "[]")
                elif case_name == "ticker-redis-ticker-list-all-empty":
                    client.set("meta:ticker_list", "[]")
            elif case_name == "ticker-partial-redis-batch":
                client.delete("ohlcv:vn:FPT:1D")
            elif case_name == "ticker-empty-redis-batch":
                client.delete("ohlcv:vn:VCB:1D", "ohlcv:vn:FPT:1D")

            if case_name in {
                "ticker-incomplete-snapshot-miss",
                "ticker-malformed-snapshot-miss",
                "ticker-snapshot-hash-error-fallback",
            }:
                # Distinguish a raw Redis fallback from SQL fallback. Both
                # stores have the same fixture except for this latest candle.
                latest_fpt = max(
                    (
                        row
                        for row in rows
                        if row.source == "vn" and row.symbol == "FPT" and row.interval == "1D"
                    ),
                    key=lambda row: row.time,
                )
                timestamp_ms = latest_fpt.time * 1000
                redis_key = "ohlcv:vn:FPT:1D"
                client.zremrangebyscore(redis_key, timestamp_ms, timestamp_ms)
                sentinel_close = 4321.25
                member = "|".join(
                    (
                        str(timestamp_ms),
                        format(sentinel_close - 1, ".17g"),
                        format(sentinel_close + 1, ".17g"),
                        format(sentinel_close - 2, ".17g"),
                        format(sentinel_close, ".17g"),
                        str(latest_fpt.volume),
                    )
                )
                client.zadd(redis_key, {member: timestamp_ms})
        finally:
            client.close()


def _snapshot(
    response: httpx.Response,
    ignored_headers: tuple[str, ...] = (),
    request_method: str | None = None,
) -> Snapshot:
    content_type = response.headers.get("content-type", "")
    is_json = "json" in content_type.lower()
    try:
        value = response.json() if is_json else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        value = None
        is_json = False
    # Drop hop-by-hop and server-generated transport values. All application,
    # CORS, cache, security, content-length (for identity responses), and
    # x-data-source headers remain in the comparison.
    ignored = {
        "connection",
        "date",
        "keep-alive",
        "server",
        "transfer-encoding",
        *ignored_headers,
    }
    # The body exposed by HTTPX is decompressed, so encoded lengths can vary
    # between valid compressors. The comparator still checks header presence.
    headers = {
        key.lower(): value for key, value in response.headers.items() if key.lower() not in ignored
    }
    return Snapshot(
        response.status_code,
        headers,
        response.content,
        value,
        is_json,
        request_method.upper() if request_method else None,
    )


def _format_parity_failures(failures: list[str], *, max_items: int = 80) -> str:
    """Keep matrix failures readable when an unexpected body is very large."""
    rendered = [f"- {failure[:1_200]}" for failure in failures[:max_items]]
    if len(failures) > max_items:
        rendered.append(f"- ... {len(failures) - max_items} additional failures omitted")
    return "\n".join(rendered)


def _normalize_top_level_json_body(body: bytes, volatile_fields: tuple[str, ...]) -> bytes:
    """Replace only selected top-level JSON values, preserving all other wire bytes."""
    if not volatile_fields:
        return body
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body

    decoder = json.JSONDecoder()
    position = 0

    def skip_whitespace(offset: int) -> int:
        while offset < len(text) and text[offset] in " \t\r\n":
            offset += 1
        return offset

    position = skip_whitespace(position)
    if position >= len(text) or text[position] != "{":
        return body
    position += 1
    replacement_spans = []
    fields = set(volatile_fields)
    seen_volatile_fields = set()
    while position < len(text):
        position = skip_whitespace(position)
        if position >= len(text) or text[position] == "}":
            break
        try:
            key, position = decoder.raw_decode(text, position)
        except json.JSONDecodeError:
            return body
        if not isinstance(key, str):
            return body
        position = skip_whitespace(position)
        if position >= len(text) or text[position] != ":":
            return body
        value_start = skip_whitespace(position + 1)
        try:
            _value, value_end = decoder.raw_decode(text, value_start)
        except json.JSONDecodeError:
            return body
        if key in fields:
            if key in seen_volatile_fields:
                # A JSON object with duplicate volatile keys has ambiguous
                # semantics. Keep the original bytes so a differing earlier
                # value cannot disappear behind Python's last-key-wins parser.
                return body
            seen_volatile_fields.add(key)
            replacement_spans.append((value_start, value_end))
        position = skip_whitespace(value_end)
        if position < len(text) and text[position] == ",":
            position += 1
            continue
        if position < len(text) and text[position] == "}":
            break
        return body

    marker = json.dumps("<volatile>", separators=(",", ":"))
    for start, end in reversed(replacement_spans):
        text = text[:start] + marker + text[end:]
    return text.encode("utf-8")


def _body_wire_difference(rust_body: bytes, python_body: bytes, label: str) -> str:
    offset = next(
        (
            index
            for index, (rust_byte, python_byte) in enumerate(
                zip(rust_body, python_body, strict=False)
            )
            if rust_byte != python_byte
        ),
        min(len(rust_body), len(python_body)),
    )
    context_start = max(0, offset - 48)
    context_end = offset + 80
    rust_context = rust_body[context_start:context_end].decode("utf-8", errors="replace")
    python_context = python_body[context_start:context_end].decode("utf-8", errors="replace")
    return (
        f"{label} wire bytes differ at offset {offset} "
        f"(Rust length={len(rust_body)}, Python length={len(python_body)}); "
        f"Rust={rust_context!r} Python={python_context!r}"
    )


def _is_finite_json_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _json_differences(left: Any, right: Any, path: str = "$", limit: int = 12) -> list[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        differences = []
        left_keys = list(left)
        right_keys = list(right)
        if left_keys != right_keys:
            differences.append(f"{path}: object key order Rust={left_keys!r} Python={right_keys!r}")
        for key in dict.fromkeys([*left_keys, *right_keys]):
            child = f"{path}.{key}"
            if key not in left:
                differences.append(f"{child}: missing from Rust")
            elif key not in right:
                differences.append(f"{child}: missing from Python")
            else:
                differences.extend(_json_differences(left[key], right[key], child, limit))
            if len(differences) >= limit:
                break
        return differences
    if isinstance(left, list) and isinstance(right, list):
        differences = []
        if len(left) != len(right):
            differences.append(f"{path}: length Rust={len(left)} Python={len(right)}")
        for index, (left_item, right_item) in enumerate(zip(left, right, strict=False)):
            differences.extend(_json_differences(left_item, right_item, f"{path}[{index}]", limit))
            if len(differences) >= limit:
                break
        return differences
    # Python considers True == 1 and 1 == 1.0. Rust JSON values distinguish
    # those scalar kinds, so a plain `!=` would hide wire-contract drift.
    if type(left) is not type(right):
        return [
            f"{path}: Rust={left!r} ({type(left).__name__}) Python={right!r} ({type(right).__name__})"
        ]
    if left != right:
        left_text = repr(left)
        right_text = repr(right)
        if len(left_text) > 160:
            left_text = left_text[:157] + "..."
        if len(right_text) > 160:
            right_text = right_text[:157] + "..."
        return [f"{path}: Rust={left_text} Python={right_text}"]
    return []


def _replace_volatile_fields(value: Any, fields: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        return {key: "<volatile>" if key in fields else item for key, item in value.items()}
    return value


def _volatile_timestamp_differences(
    rust_value: Any,
    python_value: Any,
    fields: tuple[str, ...],
    max_skew: timedelta = timedelta(seconds=5),
) -> list[str]:
    differences = []
    parsed_values = []
    for label, value in (("Rust", rust_value), ("Python", python_value)):
        if not isinstance(value, dict):
            differences.append(f"{label} response is not an object while validating timestamps")
            parsed_values.append({})
            continue
        parsed = {}
        for field_name in fields:
            if field_name not in value:
                differences.append(f"{label} {field_name} is missing")
                continue
            timestamp = value[field_name]
            if not isinstance(timestamp, str):
                differences.append(f"{label} {field_name} is not a string")
                continue
            try:
                parsed[field_name] = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                differences.append(f"{label} {field_name} is not ISO-8601")
            else:
                if parsed[field_name].utcoffset() != timedelta(0):
                    differences.append(f"{label} {field_name} is not UTC")
        created = parsed.get("created_at")
        updated = parsed.get("updated_at")
        if created is not None and updated is not None and created > updated:
            differences.append(f"{label} created_at is later than updated_at")
        parsed_values.append(parsed)

    for field_name in fields:
        rust_timestamp = parsed_values[0].get(field_name)
        python_timestamp = parsed_values[1].get(field_name)
        if rust_timestamp is None or python_timestamp is None:
            continue
        skew = abs(rust_timestamp - python_timestamp)
        if skew > max_skew:
            differences.append(
                f"{field_name} differs between Rust and Python by {skew.total_seconds():.3f}s "
                f"(maximum {max_skew.total_seconds():.3f}s)"
            )
    return differences


def _health_clock_differences(rust_value: Any, python_value: Any) -> list[str]:
    if not isinstance(rust_value, dict) or not isinstance(python_value, dict):
        return _json_differences(rust_value, python_value)

    differences = []
    parsed_times = []
    for label, value in (("Rust", rust_value), ("Python", python_value)):
        timestamp = value.get("current_system_time")
        if not isinstance(timestamp, str):
            differences.append(f"{label} health current_system_time is not a string")
            parsed_times.append(None)
        else:
            try:
                parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                differences.append(f"{label} health current_system_time is not ISO-8601")
                parsed_times.append(None)
            else:
                if parsed.utcoffset() != timedelta(0):
                    differences.append(f"{label} health current_system_time is not UTC")
                parsed_times.append(parsed)

        uptime = value.get("uptime_secs")
        if isinstance(uptime, bool) or not isinstance(uptime, int) or uptime < 0:
            differences.append(f"{label} health uptime_secs is not a non-negative integer")

    rust_uptime = rust_value.get("uptime_secs")
    python_uptime = python_value.get("uptime_secs")
    if (
        isinstance(rust_uptime, int)
        and not isinstance(rust_uptime, bool)
        and rust_uptime >= 0
        and isinstance(python_uptime, int)
        and not isinstance(python_uptime, bool)
        and python_uptime >= 0
        and abs(rust_uptime - python_uptime) > HEALTH_UPTIME_TOLERANCE_SECS
    ):
        differences.append(
            "health uptime_secs exceeds documented tolerance "
            f"({rust_uptime} vs {python_uptime}; policy: {HEALTH_CLOCK_POLICY})"
        )

    if parsed_times[0] is not None and parsed_times[1] is not None:
        skew = abs(parsed_times[0] - parsed_times[1])
        if skew > timedelta(seconds=5):
            differences.append(
                "health current_system_time differs between Rust and Python by "
                f"{skew.total_seconds():.3f}s (maximum 5.000s)"
            )

    normalized = [
        _replace_volatile_fields(value, ("current_system_time", "uptime_secs"))
        for value in (rust_value, python_value)
    ]
    differences.extend(_json_differences(normalized[0], normalized[1]))
    return differences


def _differences(
    rust: Snapshot,
    python: Snapshot,
    compare_body: bool = True,
    volatile_json_fields: tuple[str, ...] = (),
    health_clock_policy: bool = False,
) -> list[str]:
    differences = []
    if rust.status != python.status:
        differences.append(f"status Rust={rust.status} Python={python.status}")
    volatile_fields = (
        ("current_system_time", "uptime_secs")
        if health_clock_policy
        else volatile_json_fields
    )
    volatile_json_objects = (
        compare_body
        and bool(volatile_fields)
        and rust.is_json
        and python.is_json
        and isinstance(rust.json_value, dict)
        and isinstance(python.json_value, dict)
    )
    volatile_health_head_length = (
        health_clock_policy
        and "HEAD" in {rust.request_method, python.request_method}
        and {rust.request_method, python.request_method}.issubset({"GET", "HEAD"})
    )
    for runtime, snapshot in (("Rust", rust), ("Python", python)):
        content_length = snapshot.headers.get("content-length")
        if content_length is not None:
            if not re.fullmatch(r"[0-9]+", content_length):
                differences.append(
                    f"{runtime} Content-Length={content_length!r} must contain only decimal digits"
                )
            elif (
                "content-encoding" not in snapshot.headers
                and snapshot.request_method != "HEAD"
                and snapshot.status != 304
            ):
                declared_length = int(content_length)
                if declared_length != len(snapshot.body):
                    differences.append(
                        f"{runtime} Content-Length={content_length!r} does not match "
                        f"identity body length={len(snapshot.body)}"
                    )
    for key in sorted(set(rust.headers) | set(python.headers)):
        if key == "content-length" and (
            "content-encoding" in rust.headers or "content-encoding" in python.headers
        ):
            if (key in rust.headers) != (key in python.headers):
                differences.append(
                    f"header {key}: Rust={'present' if key in rust.headers else 'missing'} "
                    f"Python={'present' if key in python.headers else 'missing'}"
                )
            continue
        if key == "content-length" and (volatile_json_objects or volatile_health_head_length):
            if rust.headers.get(key) is None or python.headers.get(key) is None:
                if rust.headers.get(key) != python.headers.get(key):
                    differences.append(
                        f"header {key}: Rust={rust.headers.get(key)!r} "
                        f"Python={python.headers.get(key)!r}"
                    )
            continue
        if rust.headers.get(key) != python.headers.get(key):
            differences.append(
                f"header {key}: Rust={rust.headers.get(key)!r} Python={python.headers.get(key)!r}"
            )
    if compare_body:
        if (
            rust.is_json or rust.json_value is not None
        ) and (python.is_json or python.json_value is not None):
            volatile_fields = volatile_json_fields
            if health_clock_policy:
                differences.extend(_health_clock_differences(rust.json_value, python.json_value))
                volatile_fields = ("current_system_time", "uptime_secs")
            else:
                if volatile_json_fields:
                    differences.extend(
                        _volatile_timestamp_differences(
                            rust.json_value, python.json_value, volatile_json_fields
                        )
                    )
                differences.extend(
                    _json_differences(
                        _replace_volatile_fields(rust.json_value, volatile_json_fields),
                        _replace_volatile_fields(python.json_value, volatile_json_fields),
                    )
                )
            rust_wire = _normalize_top_level_json_body(rust.body, volatile_fields)
            python_wire = _normalize_top_level_json_body(python.body, volatile_fields)
            if rust_wire != python_wire:
                differences.append(
                    _body_wire_difference(rust_wire, python_wire, "JSON body")
                )
        elif rust.body != python.body:
            offset = next(
                (
                    index
                    for index, (rust_byte, python_byte) in enumerate(
                        zip(rust.body, python.body, strict=False)
                    )
                    if rust_byte != python_byte
                ),
                min(len(rust.body), len(python.body)),
            )
            start = max(0, offset - 48)
            end = offset + 96
            rust_context = rust.body[start:end].decode("utf-8", errors="replace")
            python_context = python.body[start:end].decode("utf-8", errors="replace")
            rust_lines = rust.body.decode("utf-8", errors="replace").splitlines()
            python_lines = python.body.decode("utf-8", errors="replace").splitlines()
            line_difference = next(
                (
                    (index, rust_line, python_line)
                    for index, (rust_line, python_line) in enumerate(
                        zip(rust_lines, python_lines, strict=False)
                    )
                    if rust_line != python_line
                ),
                None,
            )
            line_detail = (
                f"; first differing line {line_difference[0]}: "
                f"Rust={line_difference[1]!r} Python={line_difference[2]!r}"
                if line_difference is not None
                else ""
            )
            differences.append(
                f"body differs at byte {offset} (Rust length={len(rust.body)}, "
                f"Python length={len(python.body)}): Rust[{start}:{end}]={rust_context!r} "
                f"Python[{start}:{end}]={python_context!r}{line_detail}"
            )
    return differences[:16]


def _find_pg_tool(name: str) -> str | None:
    candidate = shutil.which(name)
    if candidate and os.access(candidate, os.X_OK):
        return candidate
    pg_config = shutil.which("pg_config")
    if pg_config:
        bindir = subprocess.run(
            [pg_config, "--bindir"], check=True, capture_output=True, text=True
        ).stdout.strip()
        path = Path(bindir) / name
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def _postgres_provider(env: dict[str, str] | None = None) -> PostgresProvider | None:
    current_env = dict(os.environ) if env is None else env
    host_tools = {name: _find_pg_tool(name) for name in ("postgres", "initdb", "pg_ctl", "psql")}
    docker_path = shutil.which("docker")
    docker_context = (
        _local_docker_context(docker_path, current_env)
        if docker_path and current_env.get("AIPA_USE_DOCKER_POSTGRES") == "1"
        else None
    )
    return _select_postgres_provider(current_env, host_tools, docker_path, docker_context)


def _postgres_skip_reason(env: dict[str, str] | None = None) -> str:
    current_env = dict(os.environ) if env is None else env
    if current_env.get("AIPA_USE_DOCKER_POSTGRES") == "1":
        return (
            "Neither a complete host PostgreSQL toolchain nor the Docker CLI is available; "
            "Docker fallback requires opt-in, the cached postgres:17-bookworm image, and a "
            "verified local Unix-socket context."
        )
    return (
        "Executable PostgreSQL server tools are unavailable. Install PostgreSQL or opt into "
        "the cached Docker fallback with AIPA_USE_DOCKER_POSTGRES=1."
    )


def _skip_or_fail_required_differential(reason: str) -> None:
    if os.environ.get("AIPA_REQUIRE_RUST_DIFFERENTIAL") == "1":
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


def _write_shared_catalog(root: Path) -> Path:
    catalog = root / "catalog"
    catalog.mkdir()
    vn_groups = {
        # The duplicate source member exercises Rust's map deduplication in
        # MASCORE and its symbol-list behavior in JDK.
        "Banking": ["VCB", "VCB"],
        "Duplicate": ["DUPL"],
        "Indices": ["VNINDEX"],
        "Technology": ["FPT"],
        "Microcap": ["ABC"],
    }
    market_files = {
        "ticker_group.json": vn_groups,
        "binance_tickers.json": {
            "data": [
                {"symbol": "BTCUSDT", "name": "Bitcoin", "category": "Crypto"},
                {"symbol": "ETHUSDT", "name": "Ether", "category": "Crypto"},
            ]
        },
        "global_tickers.json": {
            "data": [
                {"symbol": "AAPL", "name": "Apple Inc.", "category": "Technology"},
                {"symbol": "DUPL", "name": "Yahoo Duplicate", "category": "Duplicate"},
                {"symbol": "FPT", "name": "Yahoo FPT", "category": "Technology"},
                {"symbol": "DUPL,FPT", "name": "Yahoo Literal Comma", "category": "Fixture"},
                {"symbol": "COLL", "name": "Yahoo Collision", "category": "Global Gold"},
                {"symbol": "^GSPC", "name": "S&P 500", "category": "Indices"},
            ]
        },
        "sjc_tickers.json": {
            "data": [
                {"symbol": "SJC", "name": "SJC Gold", "category": "Commodities"},
                {"symbol": "COLL", "name": "SJC Collision", "category": "Commodities"},
            ]
        },
    }
    for name, value in market_files.items():
        (catalog / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    with (catalog / "vn.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("ticker", "organ_name", "en_organ_name", "exchange", "type"))
        writer.writerows(
            (
                ("DUPL", "Duplicate Corporation", "Duplicate Corporation", "HOSE", "stock"),
                ("FPT", "FPT Corporation", "FPT Corporation", "HOSE", "stock"),
                (" VCB ", " Vietcombank ", " Vietcombank English Name ", " HOSE ", " stock "),
                ("VNINDEX", "Vietnam Index", "Vietnam Index", "HOSE", "index"),
                ("ABC", "Three Letter Stock", "Three Letter Stock", "HOSE", "stock"),
            )
        )
    group_by_symbol = {
        symbol: group
        for group, symbols in vn_groups.items()
        for symbol in symbols
    }
    stock_rows = []
    with (catalog / "vn.csv").open(encoding="utf-8", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle), start=1):
            if row["type"].strip().lower() != "stock":
                continue
            symbol = row["ticker"].strip().upper()
            stock_rows.append(
                {
                    "symbol": symbol,
                    "name": row["organ_name"].strip(),
                    "en_name": row["en_organ_name"].strip(),
                    "short_name": row["organ_name"].strip(),
                    "en_short_name": row["en_organ_name"].strip(),
                    "exchange": row["exchange"].strip(),
                    "listing_board": row["exchange"].strip(),
                    "listing_status": "listed",
                    "type": "stock",
                    "icb_level2_code": str(index),
                    "icb_level2_name": group_by_symbol[symbol],
                    "icb_level2_name_en": group_by_symbol[symbol],
                    "market_cap_vnd": 1_000_000_000,
                    "market_cap_as_of": "2026-10-20T00:00:00Z",
                    "market_cap_price_vnd": 10_000,
                    "market_cap_price_basis": "last_trade",
                    "shares_listed": 100_000,
                }
            )
    (catalog / "vn_tickers.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-10-20T00:00:00Z",
                "stock_count": len(stock_rows),
                "market_cap_known_count": len(stock_rows),
                "market_cap_unknown_count": 0,
                "stocks": stock_rows,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (catalog / "watchlist.json").write_text(
        json.dumps({"vn": [{"symbol": "VNINDEX", "asset_type": "index", "name": "Vietnam Index"}]}),
        encoding="utf-8",
    )
    company_info = [
        {"ticker": symbol, "company_name": name, "sector": sector, "fixture": True}
        for (source, symbol, name, _base, _slope), sector in zip(
            (row for row in TICKERS if row[0] == "vn"),
            ("Duplicate", "Technology", "Banking", "Indices", "Microcap"),
            strict=True,
        )
    ]
    (catalog / "company_info.json").write_text(
        json.dumps(company_info, ensure_ascii=False), encoding="utf-8"
    )
    return catalog


def _candle_rows() -> list[Candle]:
    rows = []
    updated_at = int((FIXTURE_END + timedelta(days=1)).timestamp())
    csv_edge_closes = (
        0.0,
        -0.000005,
        0.000009,
        0.00001,
        0.0005,
        0.001,
        0.9999,
        1.0,
        99.999,
        100.0,
        -100.0,
    )
    for source, symbol, _name, base, slope in TICKERS:
        for index in range(DAILY_BARS):
            if source == "yahoo" and symbol == "CSVEDGE=F" and index >= len(csv_edge_closes):
                break
            csv_edge = source == "yahoo" and symbol == "CSVEDGE=F"
            close = (
                csv_edge_closes[index] if csv_edge else base + index * slope + (index % 7) * 0.25
            )
            stamp = FIXTURE_DATES[index]
            rows.append(
                Candle(
                    source,
                    symbol,
                    "1D",
                    int(stamp.timestamp()),
                    close - 0.5 if not csv_edge else close + 0.1234567,
                    close + 1.25 if not csv_edge else close + 0.9876543,
                    close - 1.5 if not csv_edge else close - 0.7654321,
                    close,
                    500
                    if symbol in {"DUPL", "ABC"}
                    else 200_000 + index * 100 + len(symbol) * 1_000,
                    "differential-fixture",
                    "fixture-v1",
                    updated_at,
                )
            )

    # Keep the main endpoint corpus compact, while giving the aggregate
    # latest/end-only checks enough history to exceed the normal 5,000-row
    # daily Redis retention. These older rows belong only to VN FPT so they do
    # not change any other source's fixture semantics.
    long_history_dates_reversed = []
    long_history_day = FIXTURE_START - timedelta(days=1)
    while len(long_history_dates_reversed) < LONG_HISTORY_EXTRA_BARS:
        if long_history_day.weekday() < 5:
            long_history_dates_reversed.append(long_history_day)
        long_history_day -= timedelta(days=1)
    for index, stamp in enumerate(reversed(long_history_dates_reversed)):
        offset = index - LONG_HISTORY_EXTRA_BARS
        close = 100.0 + offset * 0.01 + (offset % 7) * 0.001
        rows.append(
            Candle(
                "vn",
                "FPT",
                "1D",
                int(stamp.timestamp()),
                close - 0.5,
                close + 1.25,
                close - 1.5,
                close,
                200_000 + (offset % 1_000) * 100,
                "differential-fixture",
                "fixture-v1",
                updated_at,
            )
        )

    minute_start = FIXTURE_END.replace(hour=2)
    for index in range(30):
        close = 100.0 + index * 0.3 + (index % 4) * 0.1
        rows.append(
            Candle(
                "vn",
                "FPT",
                "1m",
                int((minute_start + timedelta(minutes=index)).timestamp()),
                close - 0.1,
                close + 0.5,
                close - 0.4,
                close,
                1_000 + index * 10,
                "differential-fixture",
                "fixture-v1",
                updated_at,
            )
        )
        for source, symbol, base, step in (
            ("crypto", "ETHUSDT", 3_000.0, 1.0),
            ("yahoo", "AAPL", 180.0, 0.1),
        ):
            close = base + index * step + (index % 4) * step / 10
            rows.append(
                Candle(
                    source,
                    symbol,
                    "1m",
                    int((minute_start + timedelta(minutes=index)).timestamp()),
                    close - step / 2,
                    close + step,
                    close - step,
                    close,
                    100 + index,
                    "differential-fixture",
                    "fixture-v1",
                    updated_at,
                )
            )
    # Adjacent crypto ticks are below Rust's one-cent POC lookup tolerance.
    # This fixture exposes the value-area seed behavior for low-priced assets.
    for index, (price, volume) in enumerate(((0.0001, 1), (0.0002, 100), (0.0003, 1))):
        rows.append(
            Candle(
                "crypto",
                "MICROUSDT",
                "1m",
                int((minute_start + timedelta(minutes=40 + index)).timestamp()),
                price,
                price,
                price,
                price,
                volume,
                "differential-fixture",
                "fixture-v1",
                updated_at,
            )
        )
    # This profile spans more than 200 crypto ticks while staying under the
    # resource cap. It makes the 200-bin clamp and 60% value-area boundary
    # observable instead of merely testing that the query parses.
    for index in range(30):
        close = 0.0015 + index * 0.0009
        rows.append(
            Candle(
                "crypto",
                "PROFILEUSDT",
                "1m",
                int((minute_start + timedelta(minutes=100 + index)).timestamp()),
                close - 0.0004,
                close + 0.0004,
                close - 0.0004,
                close,
                100 + index * 10,
                "differential-fixture",
                "fixture-v1",
                updated_at,
            )
        )
    # Zero-volume rows lower the tick-size reference without expanding the
    # profile, making the wide row exceed the shared one-million-step limit.
    for index in range(1_000):
        rows.append(
            Candle(
                "crypto",
                "WIDEUSDT",
                "1m",
                int((minute_start + timedelta(minutes=index)).timestamp()),
                0.00001,
                0.00001,
                0.00001,
                0.00001,
                0,
                "differential-fixture",
                "fixture-v1",
                updated_at,
            )
        )
    rows.append(
        Candle(
            "crypto",
            "WIDEUSDT",
            "1m",
            int((minute_start + timedelta(minutes=1_000)).timestamp()),
            50.0,
            100.0,
            0.00001,
            50.0,
            100,
            "differential-fixture",
            "fixture-v1",
            updated_at,
        )
    )
    zero_volume_minute = FIXTURE_DATES[-2].replace(hour=2, minute=0, second=0, microsecond=0)
    rows.append(
        Candle(
            "vn",
            "FPT",
            "1m",
            int(zero_volume_minute.timestamp()),
            100.0,
            100.5,
            99.5,
            100.25,
            0,
            "differential-fixture",
            "fixture-v1",
            updated_at,
        )
    )
    older_minute = FIXTURE_DATES[-3].replace(hour=2, minute=0, second=0, microsecond=0)
    rows.append(
        Candle(
            "vn",
            "FPT",
            "1m",
            int(older_minute.timestamp()),
            95.0,
            96.0,
            94.0,
            95.5,
            600,
            "differential-fixture",
            "fixture-v1",
            updated_at,
        )
    )
    hourly_start = FIXTURE_END.replace(hour=2)
    for index in range(24):
        close = 100.0 + index * 0.7 + (index % 5) * 0.2
        rows.append(
            Candle(
                "vn",
                "FPT",
                "1h",
                int((hourly_start + timedelta(hours=index)).timestamp()),
                close - 0.2,
                close + 0.8,
                close - 0.9,
                close,
                10_000 + index * 100,
                "differential-fixture",
                "fixture-v1",
                updated_at,
            )
        )
    for source, symbol, _name, base, slope in TICKERS:
        if source == "vn" and symbol == "FPT":
            continue
        for index in range(DAILY_BARS - 21, DAILY_BARS):
            close = base + index * slope + (index % 7) * 0.25 + 0.1
            rows.append(
                Candle(
                    source,
                    symbol,
                    "1h",
                    int(FIXTURE_DATES[index].replace(hour=2).timestamp()),
                    close - 0.2,
                    close + 0.8,
                    close - 0.9,
                    close,
                    10_000 + index * 100 + len(symbol) * 10,
                    "differential-fixture",
                    "fixture-v1",
                    updated_at,
                )
            )
    return rows


def _write_postgres_seed(
    path: Path,
    rows: list[Candle],
    extra_tickers: tuple[tuple[str, str, str], ...] = (),
) -> None:
    statements = [
        "INSERT INTO tickers (source,ticker,name,status) VALUES "
        + ",".join(
            "(" + ",".join("'" + value.replace("'", "''") + "'" for value in values) + ")"
            for values in sorted(
                [(source, symbol, name, "ready") for source, symbol, name, *_ in TICKERS]
                + [(source, symbol, name, "ready") for source, symbol, name in SYNTHETIC_PROFILE_TICKERS]
                + [
                    (source, symbol, name, "ready")
                    for source, symbol, name in extra_tickers
                ]
            )
        )
        + " ON CONFLICT (source,ticker) DO UPDATE SET name=EXCLUDED.name,status='ready';"
    ]
    statements.extend(
        (
            "ALTER TABLE ohlcv_daily SET (autovacuum_enabled = false);",
            r"""DO $$
            DECLARE partition RECORD;
            BEGIN
                FOR partition IN
                    SELECT relid::regclass AS relation
                    FROM pg_partition_tree('ohlcv_hourly') WHERE isleaf
                LOOP
                    EXECUTE format(
                        'ALTER TABLE %s SET (autovacuum_enabled = false)',
                        partition.relation
                    );
                END LOOP;
                FOR partition IN
                    SELECT relid::regclass AS relation
                    FROM pg_partition_tree('ohlcv_minute') WHERE isleaf
                LOOP
                    EXECUTE format(
                        'ALTER TABLE %s SET (autovacuum_enabled = false)',
                        partition.relation
                    );
                END LOOP;
            END $$;""",
        )
    )
    values = []
    for row in rows:
        stamp = datetime.fromtimestamp(row.time, UTC).isoformat()
        updated = datetime.fromtimestamp(row.updated_at, UTC).isoformat()
        ticker_id = (
            "(SELECT id FROM tickers WHERE source='"
            + row.source
            + "' AND ticker='"
            + row.symbol.replace("'", "''")
            + "')"
        )
        fields = (
            ticker_id,
            "'" + row.interval + "'",
            "'" + stamp + "'::timestamptz",
            repr(row.open),
            repr(row.high),
            repr(row.low),
            repr(row.close),
            str(row.volume),
            "'" + updated + "'::timestamptz",
        )
        values.append("(" + ",".join(fields) + ")")
    # Keep each statement bounded. A single VALUES list for all symbols and
    # intervals makes PostgreSQL's parser/planner hold an unnecessarily large
    # query tree and can exhaust the memory budget of the isolated Docker DB.
    batch_size = 250
    for start in range(0, len(values), batch_size):
        statements.append(
            "INSERT INTO ohlcv "
            "(ticker_id,interval,time,open,high,low,close,volume,updated_at) VALUES "
            + ",".join(values[start : start + batch_size])
            + " ON CONFLICT (ticker_id,interval,time) DO UPDATE SET "
            "open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,close=EXCLUDED.close,"
            "volume=EXCLUDED.volume,updated_at=EXCLUDED.updated_at;"
        )
    statements.extend(
        (
            r"""DO $$
            DECLARE
                sample ohlcv%ROWTYPE;
            BEGIN
                SELECT * INTO sample FROM ohlcv WHERE interval = '1D' LIMIT 1;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'health reconciliation fixture has no daily candle';
                END IF;
                UPDATE ohlcv_health_count_state
                SET reconciled = FALSE WHERE ticker_id = sample.ticker_id;
                DELETE FROM ohlcv_health_counts WHERE ticker_id = sample.ticker_id;
                DELETE FROM ohlcv
                WHERE ticker_id = sample.ticker_id AND interval = sample.interval
                  AND time = sample.time;
                INSERT INTO ohlcv
                    (ticker_id, interval, time, open, high, low, close, volume, updated_at)
                VALUES
                    (sample.ticker_id, sample.interval, sample.time, sample.open,
                     sample.high, sample.low, sample.close, sample.volume, sample.updated_at);
            END $$;""",
            "CALL reconcile_ohlcv_health_counts();",
            "ANALYZE tickers;",
            r"""DO $$
            DECLARE
                actual_count BIGINT;
                estimated_count REAL;
                partition RECORD;
            BEGIN
                SELECT COUNT(*) INTO actual_count FROM ohlcv_daily;
                SELECT reltuples INTO estimated_count
                FROM pg_class WHERE oid = 'ohlcv_daily'::regclass;
                IF actual_count > 0 AND estimated_count >= 0 THEN
                    RAISE EXCEPTION 'daily statistics unexpectedly analyzed: %', estimated_count;
                END IF;

                FOR partition IN
                    SELECT relid::regclass AS relation
                    FROM pg_partition_tree('ohlcv_hourly') WHERE isleaf
                LOOP
                    EXECUTE format('SELECT COUNT(*) FROM %s', partition.relation)
                    INTO actual_count;
                    EXECUTE 'SELECT reltuples FROM pg_class WHERE oid = $1'
                    INTO estimated_count USING partition.relation::oid;
                    IF actual_count > 0 AND estimated_count >= 0 THEN
                        RAISE EXCEPTION 'hourly statistics unexpectedly analyzed for %: %',
                            partition.relation, estimated_count;
                    END IF;
                END LOOP;

                FOR partition IN
                    SELECT relid::regclass AS relation
                    FROM pg_partition_tree('ohlcv_minute') WHERE isleaf
                LOOP
                    EXECUTE format('SELECT COUNT(*) FROM %s', partition.relation)
                    INTO actual_count;
                    EXECUTE 'SELECT reltuples FROM pg_class WHERE oid = $1'
                    INTO estimated_count USING partition.relation::oid;
                    IF actual_count > 0 AND estimated_count >= 0 THEN
                        RAISE EXCEPTION 'minute statistics unexpectedly analyzed for %: %',
                            partition.relation, estimated_count;
                    END IF;
                END LOOP;

                IF COALESCE((SELECT SUM(row_count) FROM ohlcv_health_counts
                             WHERE interval = '1D'), 0)
                   <> (SELECT COUNT(*) FROM ohlcv_daily)
                   OR COALESCE((SELECT SUM(row_count) FROM ohlcv_health_counts
                                WHERE interval = '1h'), 0)
                   <> (SELECT COUNT(*) FROM ohlcv_hourly)
                   OR COALESCE((SELECT SUM(row_count) FROM ohlcv_health_counts
                                WHERE interval = '1m'), 0)
                   <> (SELECT COUNT(*) FROM ohlcv_minute) THEN
                    RAISE EXCEPTION 'health counters do not match exact candle counts';
                END IF;
            END $$;""",
            "BEGIN;",
            r"""DO $$
            DECLARE
                sample ohlcv%ROWTYPE;
                current_interval TEXT;
                moved_ticker_id INT;
                exact_count BIGINT;
                indexed_count BIGINT;
            BEGIN
                FOR current_interval IN
                    SELECT unnest(ARRAY['1D', '1h', '1m']::TEXT[])
                LOOP
                    SELECT * INTO sample FROM ohlcv
                    WHERE interval = current_interval LIMIT 1;
                    IF NOT FOUND THEN
                        RAISE EXCEPTION 'health trigger fixture has no % candle',
                            current_interval;
                    END IF;

                    DELETE FROM ohlcv
                    WHERE ticker_id = sample.ticker_id
                      AND interval = sample.interval AND time = sample.time;
                    SELECT COUNT(*) INTO exact_count FROM ohlcv
                    WHERE interval = current_interval;
                    SELECT COALESCE(SUM(row_count), 0) INTO indexed_count
                    FROM ohlcv_health_counts WHERE interval = current_interval;
                    IF exact_count <> indexed_count THEN
                        RAISE EXCEPTION '% delete health counter mismatch: % vs %',
                            current_interval, indexed_count, exact_count;
                    END IF;

                    INSERT INTO ohlcv
                        (ticker_id, interval, time, open, high, low, close, volume, updated_at)
                    VALUES
                        (sample.ticker_id, sample.interval, sample.time, sample.open,
                         sample.high, sample.low, sample.close, sample.volume, sample.updated_at);
                    SELECT COUNT(*) INTO exact_count FROM ohlcv
                    WHERE interval = current_interval;
                    SELECT COALESCE(SUM(row_count), 0) INTO indexed_count
                    FROM ohlcv_health_counts WHERE interval = current_interval;
                    IF exact_count <> indexed_count THEN
                        RAISE EXCEPTION '% insert health counter mismatch: % vs %',
                            current_interval, indexed_count, exact_count;
                    END IF;

                    UPDATE ohlcv SET close = close
                    WHERE ticker_id = sample.ticker_id
                      AND interval = sample.interval AND time = sample.time;
                    SELECT COUNT(*) INTO exact_count FROM ohlcv
                    WHERE interval = current_interval;
                    SELECT COALESCE(SUM(row_count), 0) INTO indexed_count
                    FROM ohlcv_health_counts WHERE interval = current_interval;
                    IF exact_count <> indexed_count THEN
                        RAISE EXCEPTION '% update health counter mismatch: % vs %',
                            current_interval, indexed_count, exact_count;
                    END IF;
                END LOOP;

                SELECT * INTO sample FROM ohlcv WHERE interval = '1D' LIMIT 1;
                INSERT INTO tickers (source, ticker, name)
                VALUES ('vn', 'HEALTHMOVE', 'Health trigger test')
                RETURNING id INTO moved_ticker_id;
                UPDATE ohlcv SET ticker_id = moved_ticker_id, interval = '1h'
                WHERE ticker_id = sample.ticker_id
                  AND interval = sample.interval AND time = sample.time;
                SELECT COUNT(*) INTO exact_count FROM ohlcv WHERE interval = '1D';
                SELECT COALESCE(SUM(row_count), 0) INTO indexed_count
                FROM ohlcv_health_counts WHERE interval = '1D';
                IF exact_count <> indexed_count THEN
                    RAISE EXCEPTION 'ticker/interval move daily counter mismatch: % vs %',
                        indexed_count, exact_count;
                END IF;
                SELECT COUNT(*) INTO exact_count FROM ohlcv WHERE interval = '1h';
                SELECT COALESCE(SUM(row_count), 0) INTO indexed_count
                FROM ohlcv_health_counts WHERE interval = '1h';
                IF exact_count <> indexed_count THEN
                    RAISE EXCEPTION 'ticker/interval move hourly counter mismatch: % vs %',
                        indexed_count, exact_count;
                END IF;
            END $$;""",
            "ROLLBACK;",
        )
    )
    path.write_text("\n".join(statements), encoding="utf-8")


def _start_postgres(root: Path, provider: PostgresProvider) -> PostgresHandle:
    if provider.kind == "docker":
        return _start_docker_postgres(root, provider)

    username = os.environ.get("USER") or os.environ.get("LOGNAME") or "postgres"
    database_name = "aipa_differential"
    handle = None
    initdb = provider.host_tool("initdb")
    pg_ctl = provider.host_tool("pg_ctl")
    psql = provider.host_tool("psql")
    data_dir = root / "postgres"
    socket_dir = root / "postgres-socket"
    socket_dir.mkdir()
    try:
        subprocess.run(
            [
                initdb,
                "-D",
                str(data_dir),
                "--auth-local=trust",
                "--auth-host=trust",
                "--no-locale",
                "--encoding=UTF8",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        log_path = root / "postgres.log"
        for attempt in range(3):
            database_port = _free_loopback_port()
            handle = PostgresHandle(
                database_url=(
                    f"postgresql://{username}@127.0.0.1:{database_port}/{database_name}"
                ),
                port=database_port,
                username=username,
                database_name=database_name,
                provider=provider,
            )
            log_path.unlink(missing_ok=True)
            try:
                subprocess.run(
                    [
                        pg_ctl,
                        "-D",
                        str(data_dir),
                        "-l",
                        str(log_path),
                        "-o",
                        f"-h 127.0.0.1 -p {database_port} -k {socket_dir} -F -c listen_addresses=127.0.0.1",
                        "-w",
                        "start",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as startup_error:
                server_log = log_path.read_text(encoding="utf-8", errors="replace")
                details = (
                    f"{startup_error.stdout or ''}\n{startup_error.stderr or ''}\n{server_log}"
                )
                if attempt == 2 or not _is_loopback_port_bind_conflict(details):
                    raise
                _stop_postgres(root, handle)
            else:
                break
        subprocess.run(
            [
                psql,
                "-h",
                "127.0.0.1",
                "-p",
                str(database_port),
                "-U",
                username,
                "-d",
                "postgres",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                "CREATE DATABASE aipa_differential",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return handle
    except BaseException as startup_error:
        try:
            _stop_postgres(root, handle)
        except Exception as cleanup_error:
            startup_error.add_note(f"PostgreSQL startup cleanup also failed: {cleanup_error}")
        raise


def _start_docker_postgres(root: Path, provider: PostgresProvider) -> PostgresHandle:
    assert provider.docker_path is not None
    assert provider.docker_context is not None
    docker_path = provider.docker_path
    docker_env = _docker_environment(provider.docker_context)
    image = subprocess.run(
        [docker_path, "image", "inspect", DOCKER_POSTGRES_IMAGE],
        check=False,
        capture_output=True,
        text=True,
        env=docker_env,
        timeout=10,
    )
    if image.returncode:
        image_error = (image.stderr or image.stdout).strip()
        if (
            "cannot connect" in image_error.lower()
            or "is the docker daemon running" in image_error.lower()
        ):
            raise RuntimeError(
                f"Docker is unavailable for the opt-in PostgreSQL fallback: {image_error}"
            )
        _skip_or_fail_required_differential(
            f"Docker image {DOCKER_POSTGRES_IMAGE} is not cached; fallback never pulls images."
        )

    identity = uuid.uuid4().hex
    container_name = f"aipa-differential-pg-{identity}"
    username = f"aipa_{identity[:12]}"
    database_name = f"aipa_{identity[12:24]}"
    password = uuid.uuid4().hex
    handle = None
    env_file = root / f"{container_name}.env"
    file_descriptor = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    launch_attempted = False
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as file_handle:
            file_handle.write(
                f"POSTGRES_USER={username}\n"
                f"POSTGRES_PASSWORD={password}\n"
                f"POSTGRES_DB={database_name}\n"
            )

        for attempt in range(3):
            database_port = _free_loopback_port()
            handle = PostgresHandle(
                database_url=(
                    f"postgresql://{username}:{password}@127.0.0.1:{database_port}/{database_name}"
                ),
                port=database_port,
                username=username,
                database_name=database_name,
                provider=provider,
                container_name=container_name,
            )
            launch_attempted = True
            try:
                subprocess.run(
                    _docker_postgres_run_command(
                        docker_path, container_name, database_port, env_file
                    ),
                    check=True,
                    capture_output=True,
                    text=True,
                    env=docker_env,
                    timeout=20,
                )
            except subprocess.CalledProcessError as startup_error:
                details = f"{startup_error.stdout or ''}\n{startup_error.stderr or ''}"
                if attempt == 2 or not _is_loopback_port_bind_conflict(details):
                    raise
                _remove_docker_postgres(provider, container_name)
                launch_attempted = False
            else:
                break
        tmpfs = subprocess.run(
            _docker_postgres_tmpfs_command(docker_path, container_name),
            check=True,
            capture_output=True,
            text=True,
            env=docker_env,
            timeout=10,
        )
        mounts = subprocess.run(
            _docker_postgres_mounts_command(docker_path, container_name),
            check=True,
            capture_output=True,
            text=True,
            env=docker_env,
            timeout=10,
        )
        _assert_docker_postgres_tmpfs_only(tmpfs.stdout, mounts.stdout)
        deadline = time.monotonic() + 45
        ready_command = _docker_postgres_ready_command(
            docker_path, container_name, username, database_name
        )
        while time.monotonic() < deadline:
            ready = subprocess.run(
                ready_command,
                check=False,
                capture_output=True,
                text=True,
                env=docker_env,
                timeout=5,
            )
            if ready.returncode == 0:
                return handle
            time.sleep(0.2)
        raise RuntimeError("Dockerized PostgreSQL did not become ready within 45 seconds")
    except BaseException as startup_error:
        if launch_attempted:
            try:
                _remove_docker_postgres(provider, container_name)
            except Exception as cleanup_error:
                startup_error.add_note(f"Docker PostgreSQL cleanup also failed: {cleanup_error}")
        raise
    finally:
        env_file.unlink(missing_ok=True)


def _assert_docker_postgres_tmpfs_only(tmpfs_json: str, mounts_json: str) -> None:
    tmpfs = json.loads(tmpfs_json)
    mounts = json.loads(mounts_json)
    expected_tmpfs = {DOCKER_POSTGRES_DATA: "rw,noexec,nosuid,size=1g"}
    unexpected_mounts = [
        {"Type": mount.get("Type"), "Destination": mount.get("Destination")}
        for mount in mounts
        if mount.get("Type") != "tmpfs" or mount.get("Destination") != DOCKER_POSTGRES_DATA
    ]
    if tmpfs != expected_tmpfs or unexpected_mounts:
        raise RuntimeError(
            "Docker PostgreSQL container must use the requested PGDATA tmpfs and no "
            "persistent mounts; "
            f"observed tmpfs config: {tmpfs!r}, unexpected mounts: {unexpected_mounts!r}"
        )


def _remove_docker_postgres(provider: PostgresProvider, container_name: str) -> None:
    assert provider.docker_path is not None
    assert provider.docker_context is not None
    result = subprocess.run(
        _docker_postgres_remove_command(provider.docker_path, container_name),
        check=False,
        capture_output=True,
        text=True,
        env=_docker_environment(provider.docker_context),
        timeout=10,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        if "no such container" in detail.lower() or "no such object" in detail.lower():
            return
        raise RuntimeError(f"docker rm -f failed for {container_name}: {detail}")


def _stop_postgres(root: Path, handle: PostgresHandle | None = None) -> None:
    if handle is not None and handle.provider.kind == "docker":
        assert handle.provider.docker_context is not None
        assert handle.container_name is not None
        _remove_docker_postgres(handle.provider, handle.container_name)
        return

    pg_ctl = handle.provider.host_tool("pg_ctl") if handle is not None else _find_pg_tool("pg_ctl")
    data_dir = root / "postgres"
    pid_file = data_dir / "postmaster.pid"
    if not pg_ctl or not pid_file.is_file():
        return
    graceful = subprocess.run(
        [pg_ctl, "-D", str(data_dir), "-m", "fast", "-w", "stop"],
        check=False,
        capture_output=True,
        text=True,
    )
    if pid_file.exists():
        immediate = subprocess.run(
            [pg_ctl, "-D", str(data_dir), "-m", "immediate", "-w", "stop"],
            check=False,
            capture_output=True,
            text=True,
        )
        if pid_file.exists():
            details = (
                immediate.stderr or immediate.stdout or graceful.stderr or graceful.stdout
            ).strip()
            raise RuntimeError(
                f"Could not stop isolated PostgreSQL cluster in {data_dir}: {details}"
            )


def _seed_postgres(handle: PostgresHandle, seed_file: Path) -> None:
    try:
        if handle.provider.kind == "host":
            subprocess.run(
                [
                    handle.provider.host_tool("psql"),
                    handle.database_url,
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-f",
                    str(seed_file),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            return

        assert handle.provider.docker_path is not None
        assert handle.container_name is not None
        ready_command = _docker_postgres_ready_command(
            handle.provider.docker_path,
            handle.container_name,
            handle.username,
            handle.database_name,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            ready = subprocess.run(
                ready_command,
                check=False,
                capture_output=True,
                text=True,
                env=_docker_environment(handle.provider.docker_context),
                timeout=5,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("Isolated Docker PostgreSQL became unavailable before seeding")
        subprocess.run(
            _docker_postgres_seed_command(
                handle.provider.docker_path,
                handle.container_name,
                handle.username,
                handle.database_name,
            ),
            check=True,
            capture_output=True,
            text=True,
            env=_docker_environment(handle.provider.docker_context),
            timeout=30,
            input=seed_file.read_text(encoding="utf-8"),
        )
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or "psql returned no diagnostic output").strip()
        if handle.provider.kind == "docker":
            assert handle.provider.docker_path is not None
            assert handle.provider.docker_context is not None
            assert handle.container_name is not None
            logs = subprocess.run(
                [handle.provider.docker_path, "logs", handle.container_name],
                check=False,
                capture_output=True,
                text=True,
                env=_docker_environment(handle.provider.docker_context),
                timeout=10,
            )
            container_logs = (logs.stdout or "") + (logs.stderr or "")
            if container_logs.strip():
                details += "\nTemporary PostgreSQL container log tail:\n" + "\n".join(
                    container_logs.splitlines()[-60:]
                )
        raise RuntimeError(f"Could not seed isolated PostgreSQL fixture: {details}") from exc


def _run_postgres_sql(handle: PostgresHandle, sql: str) -> str:
    if handle.provider.kind == "host":
        command = [
            handle.provider.host_tool("psql"),
            handle.database_url,
            "-X",
            "-q",
            "-A",
            "-t",
            "-F",
            "|",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ]
        env = None
    else:
        assert handle.provider.docker_path is not None
        assert handle.provider.docker_context is not None
        assert handle.container_name is not None
        command = [
            handle.provider.docker_path,
            "exec",
            handle.container_name,
            "psql",
            "-X",
            "-q",
            "-A",
            "-t",
            "-F",
            "|",
            "-h",
            "127.0.0.1",
            "-U",
            handle.username,
            "-d",
            handle.database_name,
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ]
        env = _docker_environment(handle.provider.docker_context)
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        details = getattr(exc, "stderr", None) or str(exc)
        raise RuntimeError(f"Could not inspect isolated PostgreSQL fixture: {details}") from exc
    return result.stdout.strip()


def _refresh_schedule_states(
    postgres: PostgresHandle, repo
) -> tuple[
    dict[tuple[str, str], tuple[int, int, int]], dict[tuple[str, str], tuple[int, int, int]]
]:
    columns = tuple(REFRESH_SCHEDULE_COLUMNS.values())
    pg_rows = _run_postgres_sql(
        postgres,
        "SELECT source,ticker,"
        + ",".join(f"extract(epoch from {column})::bigint" for column in columns)
        + " FROM tickers ORDER BY source,ticker",
    )
    postgres_state = {}
    for line in pg_rows.splitlines():
        source, symbol, *values = line.split("|")
        postgres_state[(source, symbol)] = tuple(int(value) for value in values)
    with repo.connect() as con:
        sqlite_state = {
            (row["source"], row["symbol"]): tuple(int(row[column]) for column in columns)
            for row in con.execute(
                "SELECT source,symbol,next_1d,next_1h,next_1m FROM tickers ORDER BY source,symbol"
            )
        }
    return postgres_state, sqlite_state


def _prime_refresh_schedules(
    postgres: PostgresHandle, repo
) -> tuple[
    dict[tuple[str, str], tuple[int, int, int]], dict[tuple[str, str], tuple[int, int, int]]
]:
    sentinel = REFRESH_SENTINEL_EPOCH
    assignments = ",".join(
        f"{column}=to_timestamp({sentinel})" for column in REFRESH_SCHEDULE_COLUMNS.values()
    )
    _run_postgres_sql(postgres, f"UPDATE tickers SET {assignments}")
    with repo.connect() as con:
        con.execute(
            "UPDATE tickers SET next_1d=?,next_1h=?,next_1m=?",
            (sentinel, sentinel, sentinel),
        )
    rust_state, python_state = _refresh_schedule_states(postgres, repo)
    expected = {
        identity: (sentinel, sentinel, sentinel) for identity in FIXTURE_INSTRUMENTS
    }
    if rust_state != expected or python_state != expected:
        raise AssertionError(
            "Refresh schedule sentinel did not cover both fixtures: "
            f"PostgreSQL={rust_state!r}, SQLite={python_state!r}"
        )
    return rust_state, python_state


def _assert_refresh_result(
    case: RequestCase,
    rust_response: httpx.Response,
    python_response: httpx.Response,
    before_states: tuple[
        dict[tuple[str, str], tuple[int, int, int]],
        dict[tuple[str, str], tuple[int, int, int]],
    ],
    postgres: PostgresHandle,
    repo,
) -> list[str]:
    failures = []
    if case.name.startswith("refresh-valid-"):
        request = case.json_body
        mode_value = request["mode"]
        expected_sources = ["vn", "crypto", "yahoo"] if mode_value == "all" else [mode_value]
        interval = request["interval"]
        selected_column = tuple(REFRESH_SCHEDULE_COLUMNS).index(interval)
        expected_body = {
            "updated": sum(
                source in expected_sources for source, _symbol in FIXTURE_INSTRUMENTS
            ),
            "interval": REFRESH_SCHEDULE_COLUMNS[interval],
            "sources": expected_sources,
        }
        expected_status = 200
    else:
        expected_status = {
            "refresh-invalid-interval": 400,
            "refresh-missing-key": 401,
            "refresh-wrong-key": 401,
        }.get(case.name)
        expected_body = {
            "refresh-invalid-interval": {"error": "Invalid interval. Must be one of: 1D, 1h, 1m"},
            "refresh-missing-key": {"error": "Invalid or missing key"},
            "refresh-wrong-key": {"error": "Invalid or missing key"},
        }.get(case.name)
        selected_column = None

    for runtime, response in (("Rust", rust_response), ("Python", python_response)):
        if expected_status is not None and response.status_code != expected_status:
            failures.append(
                f"{case.name}: expected {runtime} HTTP {expected_status}, got {response.status_code}"
            )
        if expected_body is not None:
            try:
                actual_body = response.json()
            except ValueError:
                actual_body = None
            if actual_body != expected_body:
                failures.append(
                    f"{case.name}: {runtime} body {actual_body!r} did not match independent "
                    f"expectation {expected_body!r}"
                )

    after_states = _refresh_schedule_states(postgres, repo)
    expected_sources = (
        ["vn", "crypto", "yahoo"]
        if case.name.startswith("refresh-valid-") and case.json_body["mode"] == "all"
        else [case.json_body["mode"]]
        if case.name.startswith("refresh-valid-")
        else []
    )
    for runtime, before_state, after_state in zip(
        ("Rust", "Python"), before_states, after_states, strict=True
    ):
        refreshed_at = {}
        if before_state.keys() != after_state.keys():
            failures.append(
                f"{case.name}: {runtime} fixture tickers changed during refresh: "
                f"before={sorted(before_state)!r}, after={sorted(after_state)!r}"
            )
        for identity, before_values in before_state.items():
            source, _symbol = identity
            after_values = after_state.get(identity)
            if after_values is None:
                continue
            for index, (before_value, after_value) in enumerate(
                zip(before_values, after_values, strict=True)
            ):
                should_update = (
                    selected_column is not None
                    and index == selected_column
                    and source in expected_sources
                )
                if should_update:
                    if after_value <= REFRESH_SENTINEL_EPOCH:
                        failures.append(
                            f"{case.name}: expected {runtime} refreshed schedule for {identity} "
                            f"column {tuple(REFRESH_SCHEDULE_COLUMNS.values())[index]}, "
                            f"got {after_value}"
                        )
                    else:
                        refreshed_at[identity] = after_value
                        now = int(time.time())
                        if not now - 10 <= after_value <= now + 1:
                            failures.append(
                                f"{case.name}: {runtime} schedule for {identity} is not fresh: "
                                f"{after_value}, current epoch {now}"
                            )
                elif after_value != before_value:
                    failures.append(
                        f"{case.name}: unexpected {runtime} schedule mutation for {identity} "
                        f"column {tuple(REFRESH_SCHEDULE_COLUMNS.values())[index]}: "
                        f"{before_value} -> {after_value}"
                    )
        expected_updated = {
            (source, symbol)
            for source, symbol in FIXTURE_INSTRUMENTS
            if selected_column is not None and source in expected_sources
        }
        if set(refreshed_at) != expected_updated:
            failures.append(
                f"{case.name}: {runtime} refreshed schedule identities "
                f"{sorted(refreshed_at)!r}, expected {sorted(expected_updated)!r}"
            )
        if runtime == "Rust":
            rust_refreshed_at = refreshed_at
        else:
            python_refreshed_at = refreshed_at

    if case.name.startswith("refresh-valid-"):
        for identity in sorted(rust_refreshed_at.keys() & python_refreshed_at.keys()):
            rust_time = rust_refreshed_at[identity]
            python_time = python_refreshed_at[identity]
            if abs(rust_time - python_time) > 1:
                failures.append(
                    f"{case.name}: Rust/Python schedule timestamp for {identity} differs by "
                    f"more than one second ({rust_time} vs {python_time})"
                )
    return failures


def _fixture_daily_close(source: str, symbol: str, index: int) -> float:
    ticker = next(row for row in TICKERS if row[0] == source and row[1] == symbol)
    _source, _symbol, _name, base, slope = ticker
    return base + index * slope + (index % 7) * 0.25


def _fixture_daily_volume(source: str, symbol: str, index: int) -> int:
    if symbol in {"DUPL", "ABC"}:
        return 500
    return 200_000 + index * 100 + len(symbol) * 1_000


def _expected_fixture_ema(
    source: str,
    symbol: str,
    period: int,
    bar_count: int = DAILY_BARS,
    start_index: int = 0,
) -> float:
    closes = [
        _fixture_daily_close(source, symbol, index)
        for index in range(start_index, start_index + bar_count)
    ]
    seed_length = min(period, len(closes))
    ema_value = sum(closes[:seed_length]) / seed_length
    multiplier = 2.0 / (period + 1)
    for close in closes[seed_length:]:
        ema_value = close * multiplier + ema_value * (1 - multiplier)
    return ema_value


def _expected_fixture_analysis_ma(
    source: str, symbol: str, period: int, *, ema: bool
) -> float:
    # Analysis handlers request the latest row plus their configured MA seed
    # window (200 SMA bars or 600 EMA bars), rather than the entire fixture.
    bar_count = 601 if ema else 201
    start_index = DAILY_BARS - bar_count
    closes = [
        _fixture_daily_close(source, symbol, index)
        for index in range(start_index, DAILY_BARS)
    ]
    if ema:
        return _expected_fixture_ema(
            source, symbol, period, bar_count=bar_count, start_index=start_index
        )
    total = 0.0
    for close in closes[-period:]:
        total += close
    return total / period


def _expected_fixture_performer_metrics(source: str, symbol: str, *, ema: bool) -> dict[str, float]:
    index = DAILY_BARS - 1
    close = _fixture_daily_close(source, symbol, index)
    previous_close = _fixture_daily_close(source, symbol, index - 1)
    volume = _fixture_daily_volume(source, symbol, index)
    previous_volume = _fixture_daily_volume(source, symbol, index - 1)
    values = {
        "close": close,
        "volume": volume,
        "close_changed": ((close - previous_close) / previous_close) * 100,
        "volume_changed": ((volume - previous_volume) / previous_volume) * 100,
        "total_money_changed": (close - previous_close) * volume,
    }
    for period in (10, 20, 50, 100, 200):
        ma_value = _expected_fixture_analysis_ma(source, symbol, period, ema=ema)
        values[f"ma{period}"] = ma_value
        values[f"ma{period}_score"] = ((close - ma_value) / ma_value) * 100
    return values


def _expected_fixture_ticker_metrics(
    source: str,
    symbol: str,
    index: int,
    *,
    ema: bool,
    output_limit: int,
) -> dict[str, float | int]:
    """Independently calculate the web-visible daily fields for one fixture row."""
    close = _fixture_daily_close(source, symbol, index)
    volume = _fixture_daily_volume(source, symbol, index)
    previous_close = _fixture_daily_close(source, symbol, index - 1)
    previous_volume = _fixture_daily_volume(source, symbol, index - 1)
    result: dict[str, float | int] = {
        "close": close,
        "volume": volume,
        "close_changed": (close - previous_close) / previous_close * 100,
        "volume_changed": (volume - previous_volume) / previous_volume * 100,
    }
    # GET /tickers requests the output rows plus this MA seed buffer before
    # calculating indicators, then trims back to output_limit rows.
    seed_start = max(0, DAILY_BARS - output_limit - (600 if ema else 200))
    closes = [_fixture_daily_close(source, symbol, row_index) for row_index in range(index + 1)]
    for period in (10, 20, 50, 100, 200):
        if ema:
            ma = _expected_ema(closes[seed_start : index + 1], period)
        else:
            ma = sum(closes[max(0, index - period + 1) : index + 1]) / min(
                period, index + 1
            )
        result[f"ma{period}"] = ma
        result[f"ma{period}_score"] = (close - ma) / ma * 100 if ma else 0.0
    return result


def _expected_indicator_series(values: list[float], period: int, *, ema: bool) -> list[float]:
    """Calculate one Rust-compatible MA series without using API code."""
    result = [0.0] * len(values)
    if not values:
        return result
    if ema:
        seed = min(period, len(values))
        result[seed - 1] = sum(values[:seed]) / seed
        alpha = 2 / (period + 1)
        for index in range(seed, len(values)):
            result[index] = values[index] * alpha + result[index - 1] * (1 - alpha)
    elif len(values) < period:
        for index in range(len(values)):
            result[index] = sum(values[: index + 1]) / (index + 1)
    else:
        for index in range(period - 1, len(values)):
            result[index] = sum(values[index + 1 - period : index + 1]) / period
    return result


def _expected_csv_candle_rows(
    candles: list[Candle], *, interval: str, ema: bool
) -> list[dict[str, float | int | str | None]]:
    """Independently derive every web CSV candle and indicator field."""
    ordered = sorted(candles, key=lambda candle: candle.time)
    closes = [candle.close for candle in ordered]
    indicators = {
        period: _expected_indicator_series(closes, period, ema=ema)
        for period in (10, 20, 50, 100, 200)
    }
    result = []
    for index, candle in enumerate(ordered):
        stamp = datetime.fromtimestamp(candle.time, UTC)
        daily = interval in {"1D", "1W", "2W", "1M"}
        row: dict[str, float | int | str | None] = {
            "symbol": candle.symbol,
            "time": stamp.strftime("%Y-%m-%d" if daily else "%Y-%m-%dT%H:%M:%S"),
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
        }
        for period, values in indicators.items():
            ma = values[index]
            row[f"ma{period}"] = ma if ma > 0 else None
            row[f"ma{period}_score"] = (candle.close - ma) / ma * 100 if ma > 0 else None
        if index:
            previous = ordered[index - 1]
            row["close_changed"] = (
                (candle.close - previous.close) / previous.close * 100
                if previous.close > 0
                else None
            )
            row["volume_changed"] = (
                (candle.volume - previous.volume) / previous.volume * 100
                if previous.volume > 0
                else None
            )
            row["total_money_changed"] = (candle.close - previous.close) * candle.volume
        else:
            row["close_changed"] = None
            row["volume_changed"] = None
            row["total_money_changed"] = None
        result.append(row)
    return result


def _expected_weekly_candles(
    daily_rows: list[Candle], *, end: datetime
) -> list[Candle]:
    """Aggregate the retained source history into Monday buckets for the Playground chart."""
    source_rows = [row for row in daily_rows if row.time <= int(end.timestamp())]
    groups: dict[int, list[Candle]] = {}
    for row in source_rows:
        stamp = datetime.fromtimestamp(row.time, UTC)
        monday = stamp.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
            days=stamp.weekday()
        )
        groups.setdefault(int(monday.timestamp()), []).append(row)
    aggregated = []
    for bucket_time, grouped in sorted(groups.items()):
        grouped.sort(key=lambda row: row.time)
        first, last = grouped[0], grouped[-1]
        aggregated.append(
            Candle(
                first.source,
                first.symbol,
                "1W",
                bucket_time,
                first.open,
                max(row.high for row in grouped),
                min(row.low for row in grouped),
                last.close,
                sum(row.volume for row in grouped),
                first.provider,
                first.revision,
                first.updated_at,
            )
        )
    return aggregated


def _expected_jdk_rrg(
    rows: list[Candle],
    *,
    source: str,
    symbol: str,
    benchmark: str,
    period: int,
    ema: bool,
    end_date: str | None,
    trails: int,
    benchmark_source: str | None = None,
) -> dict[str, Any] | None:
    """Independent JdK oracle over the raw daily fixture candles."""
    period = max(4, min(50, period))
    benchmark_source = benchmark_source or source
    end_time = (
        int(datetime.fromisoformat(end_date).replace(tzinfo=UTC).timestamp()) + 86399
        if end_date
        else None
    )
    fetch_limit = 250 + (600 if ema else 200)

    def selected(ticker_source: str, ticker: str) -> list[Candle]:
        matching = sorted(
            (
                row
                for row in rows
                if row.source == ticker_source
                and row.symbol == ticker
                and row.interval == "1D"
                and (end_time is None or row.time <= end_time)
            ),
            key=lambda row: row.time,
        )
        return matching[-fetch_limit:]

    security_rows = selected(source, symbol)
    benchmark_rows = selected(benchmark_source, benchmark)
    benchmark_by_time = {row.time: row for row in benchmark_rows if row.close != 0}
    aligned = [
        (row, benchmark_by_time[row.time])
        for row in security_rows
        if row.time in benchmark_by_time
    ]
    if len(aligned) < 3 * period + 1:
        return None
    raw_rs = [security.close / bench.close for security, bench in aligned]
    ratio = [0.0] * len(raw_rs)
    weight_sum = period * (period + 1) / 2
    first_wma = [0.0] * len(raw_rs)
    if len(raw_rs) >= period:
        for index in range(period - 1, len(raw_rs)):
            first_wma[index] = sum(
                value * weight
                for weight, value in enumerate(raw_rs[index + 1 - period : index + 1], 1)
            ) / weight_sum
        for index in range(period - 1, len(raw_rs)):
            ratio[index] = sum(
                value * weight
                for weight, value in enumerate(first_wma[index + 1 - period : index + 1], 1)
            ) / weight_sum
    momentum = [
        (ratio[index] - ratio[index - 1]) / ratio[index - 1] if ratio[index - 1] else 0.0
        for index in range(1, len(ratio))
    ]

    def normalize(series: list[float]) -> list[float]:
        normalized = [100.0] * len(series)
        for index in range(period - 1, len(series)):
            window = series[index + 1 - period : index + 1]
            mean = sum(window) / period
            variance = sum((value - mean) ** 2 for value in window) / period
            deviation = math.sqrt(variance)
            if deviation:
                normalized[index] = 100 + 10 * (series[index] - mean) / deviation
        return normalized

    ratio_norm = normalize(ratio)
    momentum_norm = normalize(momentum)
    points = [
        {
            "date": datetime.fromtimestamp(aligned[index + 1][0].time, UTC).date().isoformat(),
            "rs_ratio": ratio_norm[index + 1],
            "rs_momentum": momentum_norm[index],
        }
        for index in range(period - 1, len(momentum_norm))
    ]
    return {
        "close": aligned[-1][0].close,
        "volume": aligned[-1][0].volume,
        "raw_rs": raw_rs[-1],
        "rs_ratio": points[-1]["rs_ratio"],
        "rs_momentum": points[-1]["rs_momentum"],
        "trails": points[-max(1, min(trails, 120)) :] if trails > 0 else None,
    }


def _expected_volume_profile(
    rows: list[Candle],
    *,
    source: str,
    symbol: str,
    bins: int,
    value_area_pct: float,
) -> dict[str, Any]:
    """Independent volume-by-price oracle over source OHLCV fixture rows."""
    if not rows:
        raise AssertionError("volume-profile oracle requires fixture rows")
    average = sum((row.high + row.low) / 2 for row in rows) / len(rows)
    if source == "vn":
        tick = 0.01 if symbol in {"VNINDEX", "VN30", "VN30F1M", "HNX30"} else (
            10 if average < 10000 else 50 if average < 50000 else 100
        )
    else:
        tick = 0.0001 if average < 1 else 0.01 if average < 100 else 0.1 if average < 1000 else 1
    distribution: dict[int, float] = {}
    positive = [row for row in rows if row.volume > 0]
    for row in sorted(rows, key=lambda item: item.time, reverse=True):
        if row.volume <= 0:
            continue
        low_index = math.floor(row.low / tick + 0.5)
        high_index = math.floor(row.high / tick + 0.5)
        if high_index < low_index:
            continue
        per_step = row.volume / (high_index - low_index + 1)
        for price_index in range(low_index, high_index + 1):
            distribution[price_index] = distribution.get(price_index, 0.0) + per_step
    levels = [(index * tick, volume) for index, volume in sorted(distribution.items())]
    total = sum(volume for _, volume in levels)
    bin_count = max(2, min(200, bins))
    if len(levels) > bin_count:
        minimum, maximum = levels[0][0], levels[-1][0]
        size = (maximum - minimum) / bin_count
        grouped = [0.0] * bin_count
        for price, volume in levels:
            index = min(bin_count - 1, int((price - minimum) / size))
            grouped[index] += volume
        levels = [
            (minimum + (index + 0.5) * size, volume)
            for index, volume in enumerate(grouped)
            if volume > 0
        ]
    peak = max(range(len(levels)), key=lambda index: (levels[index][1], index))
    target = max(60, min(90, value_area_pct)) / 100
    poc_price = levels[peak][0]
    # Mirror Rust's POC-to-index lookup, including its one-cent tolerance.
    poc_index = next(
        (index for index, (price, _volume) in enumerate(levels) if abs(price - poc_price) < 0.01),
        0,
    )
    low = high = poc_index
    area_volume = levels[poc_index][1]
    while area_volume < total * target and (low > 0 or high < len(levels) - 1):
        below = levels[low - 1][1] if low else 0
        above = levels[high + 1][1] if high + 1 < len(levels) else 0
        if below > above or high == len(levels) - 1:
            low -= 1
            area_volume += levels[low][1]
        else:
            high += 1
            area_volume += levels[high][1]
    running = 0.0
    profile = []
    for price, volume in levels:
        percentage = volume / total * 100
        running += percentage
        profile.append(
            {
                "price": price,
                "volume": volume,
                "percentage": percentage,
                "cumulative_percentage": running,
            }
        )
    mean = sum(price * volume for price, volume in levels) / total
    cumulative = 0.0
    median = levels[0][0]
    for price, volume in levels:
        cumulative += volume
        if cumulative >= total / 2:
            median = price
            break
    variance = sum((price - mean) ** 2 * volume for price, volume in levels) / total
    deviation = math.sqrt(variance)
    skewness = (
        sum((price - mean) ** 3 * volume for price, volume in levels)
        / total
        / (deviation**3)
        if deviation
        else 0.0
    )
    poc_price, poc_volume = levels[peak]
    return {
        "total_volume": sum(row.volume for row in rows),
        "total_minutes": len(rows),
        "price_range": {
            "low": min(row.low for row in positive),
            "high": max(row.high for row in positive),
        },
        "poc": {"price": poc_price, "volume": poc_volume, "percentage": poc_volume / total * 100},
        "value_area": {
            "low": levels[low][0],
            "high": levels[high][0],
            "volume": area_volume,
            "percentage": area_volume / total * 100,
        },
        "profile": profile,
        "statistics": {
            "mean_price": mean,
            "median_price": median,
            "std_deviation": deviation,
            "skewness": skewness,
        },
    }


def _mode_all_fixture_tickers() -> tuple[tuple[str, str], ...]:
    """Return all-mode ticker identities after Rust's source precedence."""
    source_priority = {"vn": 0, "yahoo": 1, "sjc": 2, "crypto": 3}
    selected = {}
    for source, symbol, *_ in TICKERS:
        if (
            symbol not in selected
            or source_priority[source] < source_priority[selected[symbol]]
        ):
            selected[symbol] = source
    return tuple(sorted((source, symbol) for symbol, source in selected.items()))


def _assert_home_csv_expectations(
    case: RequestCase, response: httpx.Response, runtime: str
) -> list[str]:
    failures = []
    prefix = f"{case.name}: {runtime}"
    if response.status_code != 200:
        return [f"{prefix} expected HTTP 200, got {response.status_code}"]
    if not response.headers.get("content-type", "").startswith("text/csv"):
        failures.append(
            f"{prefix} Content-Type {response.headers.get('content-type')!r} is not text/csv"
        )
    lines = response.text.splitlines()
    if not lines:
        return [f"{prefix} returned an empty CSV body"]
    header = next(csv.reader([lines[0]]))
    if tuple(header) != WEB_TICKER_CSV_COLUMNS:
        failures.append(
            f"{prefix} CSV header {tuple(header)!r} did not match the web ticker contract"
        )

    mode_value = case.path.split("mode=", 1)[1].split("&", 1)[0]
    accepted_sources = ("yahoo", "sjc") if mode_value == "yahoo" else (mode_value,)
    expected_symbols = sorted(
        {symbol for source, symbol, *_ in TICKERS if source in accepted_sources}
    )
    data_lines = lines[1:]
    observed_symbols = []
    parsed_rows = {}
    for line in data_lines:
        if line.startswith("DUPL,FPT,"):
            # Rust's CSV contract emits symbol and time without quoting. Decode
            # this synthetic comma-symbol row with its known two-cell prefix so
            # its remaining fields still receive the same independent checks.
            values = next(csv.reader([line]))
            if len(values) != len(WEB_TICKER_CSV_COLUMNS) + 1 or values[:2] != [
                "DUPL",
                "FPT",
            ]:
                failures.append(
                    f"{prefix} synthetic comma-symbol row has an unexpected shape: {values!r}"
                )
                continue
            observed_symbols.append("DUPL,FPT")
            row = dict(zip(WEB_TICKER_CSV_COLUMNS[1:], values[2:], strict=True))
            row["symbol"] = "DUPL,FPT"
            parsed_rows["DUPL,FPT"] = row
            continue
        values = next(csv.reader([line]))
        if not values:
            failures.append(f"{prefix} returned a blank data row")
            continue
        observed_symbols.append(values[0])
        if len(values) != len(WEB_TICKER_CSV_COLUMNS):
            failures.append(
                f"{prefix} row for {values[0]!r} has {len(values)} cells, "
                f"expected {len(WEB_TICKER_CSV_COLUMNS)}"
            )
            continue
        parsed_rows[values[0]] = dict(zip(WEB_TICKER_CSV_COLUMNS, values, strict=True))

    if sorted(observed_symbols) != expected_symbols:
        failures.append(
            f"{prefix} returned symbols {sorted(observed_symbols)!r}, expected "
            f"one row for each configured {mode_value} symbol {expected_symbols!r}"
        )
    if len(data_lines) != len(expected_symbols):
        failures.append(
            f"{prefix} returned {len(data_lines)} data rows, expected {len(expected_symbols)}"
        )

    end_date = FIXTURE_END.date().isoformat()
    expected_ma = "ma=false" not in case.path
    for symbol, row in parsed_rows.items():
        matching_tickers = [
            item for item in TICKERS if item[0] in accepted_sources and item[1] == symbol
        ]
        if not matching_tickers:
            continue
        expected_date = FIXTURE_DATES[10].date().isoformat() if symbol == "CSVEDGE=F" else end_date
        if row["time"] != expected_date:
            failures.append(
                f"{prefix} row {symbol} time {row['time']!r}, expected latest fixture date "
                f"{expected_date!r}"
            )
        if symbol != "CSVEDGE=F" and len(matching_tickers) == 1:
            ticker = matching_tickers[0]
            _source, _symbol, _name, base, slope = ticker
            index = DAILY_BARS - 1
            expected_close = base + index * slope + (index % 7) * 0.25
            if not math.isclose(float(row["close"]), expected_close, abs_tol=0.0051):
                failures.append(
                    f"{prefix} row {symbol} close {row['close']!r}, expected latest fixture "
                    f"close {expected_close:.4f} within CSV precision"
                )

        ma_columns = WEB_TICKER_CSV_COLUMNS[7:17]
        if not expected_ma and any(row[column] for column in ma_columns):
            failures.append(f"{prefix} row {symbol} populated MA fields despite ma=false")
        elif (
            expected_ma and symbol != "CSVEDGE=F" and any(not row[column] for column in ma_columns)
        ):
            failures.append(f"{prefix} row {symbol} omitted an MA value or score despite ma=true")

    if expected_ma:
        expected_ema_symbol = {"vn": "FPT", "crypto": "ETHUSDT", "yahoo": "AAPL"}[mode_value]
        row = parsed_rows.get(expected_ema_symbol)
        if row is not None:
            expected_ema10 = _expected_fixture_ema(mode_value, expected_ema_symbol, 10)
            if not math.isclose(float(row["ma10"]), expected_ema10, abs_tol=0.0051):
                failures.append(
                    f"{prefix} {expected_ema_symbol} EMA10 {row['ma10']!r}, expected "
                    f"{expected_ema10:.6f} within CSV precision"
                )
    return failures


def _export_rust_ticker_identity_order(handle: PostgresHandle, path: Path) -> None:
    """Export actual fixture IDs so Python can reproduce Rust's identity order."""
    copy_command = (
        r"\copy (SELECT id, source, ticker AS symbol FROM tickers ORDER BY id) "
        r"TO STDOUT WITH CSV HEADER"
    )
    if handle.provider.kind == "host":
        command = [
            handle.provider.host_tool("psql"),
            handle.database_url,
            "-X",
            "-q",
            "-t",
            "-A",
            "-c",
            copy_command,
        ]
        env = None
    else:
        assert handle.provider.docker_path is not None
        assert handle.provider.docker_context is not None
        assert handle.container_name is not None
        command = [
            handle.provider.docker_path,
            "exec",
            handle.container_name,
            "psql",
            "-X",
            "-q",
            "-t",
            "-A",
            "-h",
            "127.0.0.1",
            "-U",
            handle.username,
            "-d",
            handle.database_name,
            "-c",
            copy_command,
        ]
        env = _docker_environment(handle.provider.docker_context)
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        details = getattr(exc, "stderr", None) or str(exc)
        raise RuntimeError(f"Could not export Rust ticker identity order: {details}") from exc
    content = result.stdout
    if not content.startswith("id,source,symbol\n"):
        raise RuntimeError("Rust ticker identity export did not contain the expected CSV header")
    path.write_text(content, encoding="utf-8")


def _stop_child_process(child: subprocess.Popen[str]) -> None:
    if child.poll() is None:
        try:
            child.terminate()
        except ProcessLookupError:
            pass
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            child.kill()
        except ProcessLookupError:
            pass
        child.wait(timeout=5)


def _start_rust(
    root: Path,
    database_url: str,
    redis_url: str = "",
    refresh_secret: str | None = REFRESH_SECRET,
    sync_token: str | None = SYNC_TOKEN,
    uploads_path: str | None = None,
) -> tuple[httpx.Client, subprocess.Popen[str]]:
    if not RUST_BINARY.is_file():
        raise RuntimeError(f"Current Rust server binary was not built: {RUST_BINARY}")

    # Start in a fresh working directory so dotenvy cannot read the repository
    # .env. Static catalog files are symlinked read-only into that directory.
    for filename in (
        "ticker_group.json",
        "binance_tickers.json",
        "global_tickers.json",
        "sjc_tickers.json",
        "vn.csv",
        "company_info.json",
    ):
        (root / filename).symlink_to(root / "catalog" / filename)
    shutil.copytree(RUST_ROOT / "public", root / "public")
    (root / "public" / "_rust_empty_range_fixture.txt").touch()

    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", str(root)),
        "TMPDIR": str(root),
        "LANG": "C.UTF-8",
        "DATABASE_URL": database_url,
        "UPLOADS_PATH": uploads_path if uploads_path is not None else str(root / "rust-uploads"),
        "UPLOADS_MAX_BYTES": str(6 * 1024 * 1024),
        "UPLOADS_MAX_SESSIONS": "4",
        "REDIS_URL": redis_url,
        "REDIS_DAILY_MAX_SIZE": "5000",
        "REDIS_HOURLY_MAX_SIZE": "30000",
        "REDIS_MINUTE_MAX_SIZE": "20000",
        "CORS_ORIGINS": f"{WEB_ORIGIN},{PRODUCTION_WEB_ORIGIN}",
        "RUST_LOG": "error",
        "VCI_WORKERS": "false",
        "VCI_DIVIDEND_WORKER": "false",
        "BINANCE_WORKERS": "false",
        "YAHOO_WORKERS": "false",
        "SJC_WORKERS": "false",
        "REDIS_WORKERS": "false",
        "S3_ARCHIVE_WORKER": "false",
        "OTEL_SDK_DISABLED": "true",
    }
    if refresh_secret is not None:
        env["REFRESH_SECRET"] = refresh_secret
    if sync_token is not None:
        env["SYNC_TOKEN"] = sync_token
    log_path = root / "rust-server.log"
    for attempt in range(3):
        port = _free_loopback_port()
        base_url = f"http://127.0.0.1:{port}"
        log_handle = log_path.open("w", encoding="utf-8")
        child = None
        client = None
        try:
            child = subprocess.Popen(
                [str(RUST_BINARY), "serve", "--host", "127.0.0.1", "--port", str(port)],
                cwd=root,
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
            client = httpx.Client(base_url=base_url, timeout=8.0, trust_env=False)
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError("Rust server exited during startup")
                try:
                    response = client.get("/health", headers={"accept-encoding": "identity"})
                    if response.status_code == 200:
                        try:
                            health_value = response.json()
                        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                            raise RuntimeError(
                                "Rust returned invalid JSON from /health before startup completed"
                            ) from exc
                        else:
                            if not _health_fixture_ready(health_value):
                                raise RuntimeError(
                                    "Rust served an uninitialized /health snapshot: "
                                    f"{health_value!r}"
                                )
                            log_handle.close()
                            return client, child
                except httpx.HTTPError:
                    time.sleep(0.1)
                    continue
                time.sleep(0.1)
            raise RuntimeError(
                "Rust server did not accept HTTP requests before the startup deadline"
            )
        except BaseException as startup_error:
            if client is not None:
                client.close()
            if child is not None and child.poll() is None:
                _stop_child_process(child)
            if not log_handle.closed:
                log_handle.close()
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            if attempt < 2 and _is_loopback_port_bind_conflict(log_text):
                continue
            if isinstance(startup_error, RuntimeError):
                raise RuntimeError(f"{startup_error}:\n{log_text}") from startup_error
            raise
    raise RuntimeError("Rust server could not claim a loopback port after three attempts")


def _apply_rust_migrations(root: Path, database_url: str) -> None:
    """Apply embedded Rust migrations without starting workers or the API."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", str(root)),
        "TMPDIR": str(root),
        "LANG": "C.UTF-8",
        "DATABASE_URL": database_url,
        "REDIS_URL": "",
        "OTEL_SDK_DISABLED": "true",
    }
    result = subprocess.run(
        [str(RUST_BINARY), "status"],
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(
            "Rust status command could not apply migrations to the temporary database:\n"
            + result.stdout
            + result.stderr
        )


def _build_current_rust_binary() -> None:
    env = {**os.environ, "SQLX_OFFLINE": "true", "CARGO_TARGET_DIR": str(RUST_ROOT / "target")}
    env.pop("CARGO_BUILD_TARGET", None)
    try:
        result = subprocess.run(
            ["cargo", "build", "--locked", "--bin", "aipriceaction"],
            cwd=RUST_ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.fail(f"Could not build Rust API from the current checkout: {exc}", pytrace=False)
    if result.returncode:
        pytest.fail(
            "Could not build Rust API from the current checkout:\n"
            + result.stdout[-12000:]
            + result.stderr[-12000:],
            pytrace=False,
        )


def _seed_catalog_symbols(repo) -> None:
    for source, symbol, name, _base, _slope in TICKERS:
        repo.register(source, symbol, name, enabled=True)
    for source, symbol, name in SYNTHETIC_PROFILE_TICKERS:
        repo.register(source, symbol, name, enabled=True)


def _seed_legacy_notes(root: Path, session_id: str) -> None:
    """Seed an old plaintext-secret Rust record in both isolated upload roots."""
    metadata = {
        "session_id": session_id,
        "secret": "legacy-notes-secret",
        "is_public": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    for implementation in ("rust-uploads", "python-uploads"):
        session = root / implementation / session_id
        markdown = session / "markdown"
        markdown.mkdir(parents=True)
        (session / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        (markdown / "content.md").write_text("legacy Notes fixture\n", encoding="utf-8")


def _seed_head_notes(root: Path, session_id: str) -> None:
    """Seed a standalone Notes session that remains available for HEAD probes."""
    metadata = _notes_metadata(session_id)
    for implementation in ("rust-uploads", "python-uploads"):
        session = root / implementation / session_id
        markdown = session / "markdown"
        markdown.mkdir(parents=True)
        (session / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        (markdown / "content.md").write_text("HEAD Notes fixture\n", encoding="utf-8")


def _notes_metadata(session_id: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "secret_sha256": hashlib.sha256(SYNC_SECRET.encode("utf-8")).hexdigest(),
        "is_public": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _seed_symlinked_notes(root: Path, session_ids: dict[str, str]) -> None:
    """Create isolated symlink fixtures under each runtime's temporary uploads root."""
    for implementation in ("rust-uploads", "python-uploads"):
        uploads = root / implementation
        uploads.mkdir(parents=True, exist_ok=True)

        external_session = root / f"outside-session-{implementation}"
        external_session.mkdir()
        (external_session / "outside.marker").write_text("keep", encoding="utf-8")
        (external_session / "metadata.json").write_text(
            json.dumps(_notes_metadata(session_ids["session"])), encoding="utf-8"
        )
        (uploads / session_ids["session"]).symlink_to(external_session, target_is_directory=True)

        markdown_session = uploads / session_ids["markdown"]
        markdown_session.mkdir()
        (markdown_session / "metadata.json").write_text(
            json.dumps(_notes_metadata(session_ids["markdown"])), encoding="utf-8"
        )
        external_markdown = root / f"outside-markdown-{implementation}"
        external_markdown.mkdir()
        (markdown_session / "markdown").symlink_to(external_markdown, target_is_directory=True)

        metadata_session = uploads / session_ids["metadata"]
        metadata_session.mkdir()
        (metadata_session / "metadata.json").symlink_to(external_session / "metadata.json")
        (metadata_session / "markdown").mkdir()

        content_session = uploads / session_ids["content"]
        content_markdown = content_session / "markdown"
        content_markdown.mkdir(parents=True)
        (content_session / "metadata.json").write_text(
            json.dumps(_notes_metadata(session_ids["content"])), encoding="utf-8"
        )
        external_content = root / f"outside-content-{implementation}.md"
        external_content.write_text("must remain outside the session", encoding="utf-8")
        (content_markdown / "content.md").symlink_to(external_content)

        populated_session = uploads / session_ids["populated_markdown"]
        populated_session.mkdir()
        (populated_session / "metadata.json").write_text(
            json.dumps(_notes_metadata(session_ids["populated_markdown"])), encoding="utf-8"
        )
        external_populated_markdown = root / f"outside-populated-markdown-{implementation}"
        external_populated_markdown.mkdir()
        (external_populated_markdown / "content.md").write_text(
            "must remain outside the session", encoding="utf-8"
        )
        (populated_session / "markdown").symlink_to(
            external_populated_markdown, target_is_directory=True
        )


def _cases(
    session_id: str,
    legacy_session_id: str,
    head_notes_session_id: str,
    byte_limited_session_id: str,
    max_file_session_id: str,
    oversized_file_session_id: str,
    quota_session_id: str,
    session_limited_session_id: str,
    symlink_session_id: str,
    symlink_markdown_id: str,
    symlink_metadata_id: str,
    symlink_content_id: str,
    symlink_populated_markdown_id: str,
) -> list[RequestCase]:
    """Cases mounted by both routers; Python-only Notes edges also live in test_notes_contract."""
    end_date = FIXTURE_END.date().isoformat()
    csv_edge_end_date = FIXTURE_DATES[10].date().isoformat()
    mid_fixture = FIXTURE_DATES[650]
    mid_fixture_start = mid_fixture.date().isoformat()
    mid_fixture_end = FIXTURE_DATES[654].date().isoformat()
    date_control_start = (FIXTURE_END.date() - timedelta(days=10)).isoformat()
    date_control_end = (FIXTURE_END.date() + timedelta(days=10)).isoformat()
    widget_fallback_date = FIXTURE_DATES[-2].date().isoformat()
    chrono_date = next(
        day.date() for day in reversed(FIXTURE_DATES) if day.month < 10 or day.day < 10
    )
    chrono_date_text = f"{chrono_date.year}-{chrono_date.month}-{chrono_date.day}"
    origin_headers = {"origin": WEB_ORIGIN, "accept-encoding": "identity"}
    home_modes = ("vn", "crypto", "yahoo")
    production_origin_headers = {
        "origin": PRODUCTION_WEB_ORIGIN,
        "accept-encoding": "identity",
    }
    oversized_request_body = b"x" * (5 * 1024 * 1024 + 1)
    notes_request_body_limit = 5 * 1024 * 1024 + 64 * 1024
    oversized_notes_chunk = b"x" * (notes_request_body_limit + 1)
    oversized_notes_multipart_chunks = (
        b'--x\r\nContent-Disposition: form-data; name="file"; filename="content.md"\r\n'
        b"Content-Type: text/markdown\r\n\r\n",
        oversized_notes_chunk,
        b"\r\n--x--\r\n",
    )
    many_symbol_pairs = [
        ("symbol", f"NOPE{index:04d}") for index in range(501)
    ] + [("symbol", "FPT")]
    many_symbol_pairs.extend(
        (
            ("interval", "1D"),
            ("limit", "1"),
            ("mode", "vn"),
            ("ma", "false"),
            ("format", "json"),
            ("redis", "false"),
            ("snap", "false"),
            ("cache", "false"),
        )
    )
    more_than_500_symbols_path = f"/tickers?{urlencode(many_symbol_pairs)}"
    cases = [
        RequestCase("health", "GET", "/health", origin_headers, health_clock_policy=True),
        RequestCase(
            "health-no-origin-cors-expose-headers",
            "GET",
            "/health",
            {"accept-encoding": "identity"},
            health_clock_policy=True,
        ),
        RequestCase(
            "health-disallowed-origin-cors-expose-headers",
            "GET",
            "/health",
            {"origin": "https://not-allowed.example", "accept-encoding": "identity"},
            health_clock_policy=True,
        ),
        RequestCase(
            "production-origin-health",
            "GET",
            "/health",
            production_origin_headers,
            health_clock_policy=True,
        ),
        RequestCase(
            "home-vn-csv-gzip",
            "GET",
            "/tickers?limit=1&ma=true&ema=true&mode=vn&format=csv",
            {**origin_headers, "accept-encoding": "gzip"},
        ),
        RequestCase(
            "analysis-top-performers-gzip",
            "GET",
            f"/analysis/top-performers?date={end_date}&mode=vn&limit=10&min_volume=0&snap=false",
            {**origin_headers, "accept-encoding": "gzip"},
        ),
    ]
    cases.extend(
        RequestCase(
            f"home-{mode}-csv-ma-ema",
            "GET",
            f"/tickers?limit=1&ma=true&ema=true&mode={mode}&format=csv",
            origin_headers,
        )
        for mode in home_modes
    )
    cases.extend(
        RequestCase(
            f"home-{mode}-csv-no-ma",
            "GET",
            f"/tickers?limit=1&ma=false&mode={mode}&format=csv",
            origin_headers,
        )
        for mode in home_modes
    )
    cases.extend(
        RequestCase(
            f"home-{mode}-csv-no-ma-with-end-date",
            "GET",
            f"/tickers?limit=1&end_date={end_date}&ma=false&mode={mode}&format=csv",
            origin_headers,
        )
        for mode in home_modes
    )
    cases.append(
        RequestCase(
            "csv-price-format-thresholds-zero-negative",
            "GET",
            f"/tickers?symbol=CSVEDGE=F&start_date={FIXTURE_START.date()}&end_date={csv_edge_end_date}&limit=20&ma=false&mode=yahoo&format=csv&redis=false&snap=false&cache=false",
            origin_headers,
        )
    )
    cases.extend(
        RequestCase(
            f"home-{mode}-csv-ma-ema-with-end-date",
            "GET",
            f"/tickers?limit=1&end_date={end_date}&ma=true&ema=true&mode={mode}&format=csv",
            origin_headers,
        )
        for mode in home_modes
    )
    cases.extend(
        [
            RequestCase(
                "web-date-control-vnindex-range-csv",
                "GET",
                f"/tickers?symbol=VNINDEX&start_date={date_control_start}&end_date={date_control_end}&mode=vn&format=csv",
                origin_headers,
            ),
            RequestCase(
                "ticker-weekly-latest-long-history",
                "GET",
                "/tickers?symbol=FPT&interval=1W&limit=1002&mode=vn&ma=true&redis=true&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-weekly-end-only-long-history",
                "GET",
                f"/tickers?symbol=FPT&interval=1W&limit=1002&end_date={end_date}&mode=vn&ma=true&redis=true&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "web-mixed-watchlist-mode-all-csv",
                "GET",
                f"/tickers?symbol=FPT&symbol=ETHUSDT&symbol=AAPL&symbol=VNINDEX&symbol=BTCUSDT&mode=all&interval=1D&end_date={end_date}&limit=21&ema=true&format=csv",
                origin_headers,
            ),
            RequestCase(
                "web-mixed-watchlist-mode-all-sma-csv",
                "GET",
                f"/tickers?symbol=FPT&symbol=ETHUSDT&symbol=AAPL&symbol=VNINDEX&symbol=BTCUSDT&mode=all&interval=1D&end_date={end_date}&limit=21&format=csv",
                origin_headers,
            ),
            RequestCase(
                "web-mixed-watchlist-mode-all-hourly-csv",
                "GET",
                f"/tickers?symbol=FPT&symbol=ETHUSDT&symbol=AAPL&symbol=VNINDEX&symbol=BTCUSDT&mode=all&interval=1h&end_date={end_date}&limit=21&ema=true&format=csv",
                origin_headers,
            ),
            RequestCase(
                "web-trend-signal-table-mode-all-hourly-csv",
                "GET",
                f"/tickers?mode=all&interval=1h&end_date={end_date}&limit=40&ema=true&format=csv",
                origin_headers,
            ),
            RequestCase(
                "web-playground-native-hourly-range-csv",
                "GET",
                f"/tickers?symbol=FPT&interval=1h&start_date={FIXTURE_DATES[DAILY_BARS - 500].date()}&end_date={end_date}&mode=vn&ema=true&format=csv",
                origin_headers,
            ),
            RequestCase(
                "web-playground-native-weekly-range-csv",
                "GET",
                f"/tickers?symbol=FPT&interval=1W&start_date={FIXTURE_DATES[DAILY_BARS - 500].date()}&end_date={end_date}&mode=vn&ema=true&format=csv",
                origin_headers,
            ),
            RequestCase(
                "web-playground-native-weekly-range-csv-postgres",
                "GET",
                f"/tickers?symbol=FPT&interval=1W&start_date={FIXTURE_DATES[DAILY_BARS - 500].date()}&end_date={end_date}&mode=vn&ema=true&format=csv&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "web-playground-lazy-minute-range-csv",
                "GET",
                f"/tickers?symbol=FPT&interval=1m&mode=vn&ema=true&start_date={(FIXTURE_END.date() - timedelta(days=3)).isoformat()}&limit=3000&format=csv",
                origin_headers,
            ),
            RequestCase(
                "web-alert-context-mode-all-no-ma-csv",
                "GET",
                "/tickers?mode=all&interval=1D&limit=4&ma=false&format=csv",
                origin_headers,
            ),
            RequestCase(
                "web-alert-context-max-lookback-mode-all-no-ma-csv",
                "GET",
                "/tickers?mode=all&interval=1D&limit=110&ma=false&format=csv",
                origin_headers,
            ),
            RequestCase(
                "web-market-matrix-all-vn-limit-21-enddate-csv",
                "GET",
                f"/tickers?interval=1D&end_date={end_date}&limit=21&mode=vn&ema=true&format=csv",
                origin_headers,
            ),
            RequestCase(
                "web-trend-signal-table-mode-all-csv",
                "GET",
                f"/tickers?mode=all&interval=1D&end_date={end_date}&limit=3&ema=true&format=csv",
                origin_headers,
            ),
            RequestCase(
                "web-volume-profile-widget-date-discovery-csv",
                "GET",
                "/tickers?symbol=ETHUSDT&limit=1&mode=crypto&ema=true&format=csv",
                origin_headers,
            ),
            RequestCase(
                "web-volume-profile-widget-dated-daily-csv",
                "GET",
                f"/tickers?symbol=ETHUSDT&end_date={end_date}&limit=1&mode=crypto&ema=true&format=csv",
                origin_headers,
            ),
        ]
    )
    cases.extend(
        RequestCase(
            f"production-origin-data-ticker-{mode}",
            "GET",
            f"/tickers?limit=1&ma=true&ema=true&mode={mode}&format=csv",
            production_origin_headers,
        )
        for mode in home_modes
    )
    cases.extend(
        RequestCase(
            f"production-origin-bootstrap-ticker-{mode}",
            "GET",
            f"/tickers?limit=1&end_date={end_date}&ma=false&mode={mode}&format=csv",
            production_origin_headers,
        )
        for mode in home_modes
    )
    cases.extend(
        [
            RequestCase(
                "production-origin-data-ticker-json",
                "GET",
                "/tickers?symbol=FPT&limit=1&mode=vn&format=json",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-data-ticker-all-json",
                "GET",
                "/tickers?symbol=FPT&limit=1&ma=false&mode=all&format=json",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-data-top-performers-raw",
                "GET",
                f"/analysis/top-performers?date={end_date}&mode=vn&limit=5&min_volume=0&snap=false",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-data-top-performers",
                "GET",
                f"/analysis/top-performers?date={end_date}&mode=vn&limit=5&min_volume=0",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-data-ma-scores-raw",
                "GET",
                f"/analysis/ma-scores-by-sector?date={end_date}&mode=vn&ma_period=20&min_score=0&top_per_sector=5&snap=false",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-data-ma-scores",
                "GET",
                f"/analysis/ma-scores-by-sector?date={end_date}&mode=vn&ma_period=20&min_score=0&top_per_sector=5",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-data-rrg",
                "GET",
                f"/analysis/rrg?algorithm=jdk&mode=vn&trails=5&min_volume=1000&benchmark=VNINDEX&period=10&date={end_date}&ema=true",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-data-rrg-crypto",
                "GET",
                f"/analysis/rrg?algorithm=jdk&mode=crypto&trails=5&min_volume=1000&benchmark=BTCUSDT&period=10&date={end_date}&ema=true",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-data-rrg-yahoo",
                "GET",
                f"/analysis/rrg?algorithm=jdk&mode=yahoo&trails=5&min_volume=0&benchmark=%5EGSPC&period=10&date={end_date}&ema=true",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-data-rrg-mascore",
                "GET",
                f"/analysis/rrg?algorithm=mascore&mode=vn&trails=5&min_volume=1000&date={end_date}&ema=true",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-data-rrg-mascore-crypto",
                "GET",
                f"/analysis/rrg?algorithm=mascore&mode=crypto&trails=5&min_volume=1000&date={end_date}&ema=true",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-data-rrg-mascore-yahoo",
                "GET",
                f"/analysis/rrg?algorithm=mascore&mode=yahoo&trails=5&min_volume=0&date={end_date}&ema=true",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-data-rrg-jdk-all",
                "GET",
                f"/analysis/rrg?algorithm=jdk&mode=all&trails=5&min_volume=1000&benchmark=VNINDEX&period=10&date={end_date}&ema=true",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-data-rrg-jdk-all-raw",
                "GET",
                f"/analysis/rrg?algorithm=jdk&mode=all&trails=5&min_volume=1000&benchmark=VNINDEX&period=10&date={end_date}&ema=true&snap=false",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-data-rrg-mascore-all",
                "GET",
                f"/analysis/rrg?algorithm=mascore&mode=all&trails=5&min_volume=1000&date={end_date}&ema=true",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-data-rrg-mascore-all-raw",
                "GET",
                f"/analysis/rrg?algorithm=mascore&mode=all&trails=5&min_volume=1000&date={end_date}&ema=true&snap=false",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-data-volume-profile",
                "GET",
                f"/analysis/volume-profile?symbol=FPT&mode=vn&date={end_date}&bins=10",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-data-volume-profile-range",
                "GET",
                f"/analysis/volume-profile?symbol=FPT&mode=vn&start_date={(FIXTURE_END - timedelta(days=30)).date()}&end_date={end_date}&bins=10",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-groups",
                "GET",
                "/tickers/group",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-names",
                "GET",
                "/tickers/name",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-groups-all",
                "GET",
                "/tickers/group?mode=all",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-names-all",
                "GET",
                "/tickers/name?mode=all",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-groups-crypto",
                "GET",
                "/tickers/group?mode=crypto",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-groups-yahoo",
                "GET",
                "/tickers/group?mode=yahoo",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-names-crypto",
                "GET",
                "/tickers/name?mode=crypto",
                production_origin_headers,
            ),
            RequestCase(
                "production-origin-names-yahoo",
                "GET",
                "/tickers/name?mode=yahoo",
                production_origin_headers,
            ),
            RequestCase(
                "home-vn-daily-interval",
                "GET",
                "/tickers?interval=1D&limit=1&mode=vn&ema=true&format=csv",
                origin_headers,
            ),
            RequestCase(
                "chart-vn-daily-default-csv-cache-flags",
                "GET",
                f"/tickers?symbol=FPT&interval=1D&end_date={end_date}&limit=1&mode=vn&ema=true&format=csv",
                origin_headers,
            ),
            RequestCase(
                "chart-crypto-daily-default-csv-cache-flags",
                "GET",
                f"/tickers?symbol=ETHUSDT&interval=1D&end_date={end_date}&limit=1&mode=crypto&ema=true&format=csv",
                origin_headers,
            ),
            RequestCase(
                "chart-yahoo-daily-default-csv-cache-flags",
                "GET",
                f"/tickers?symbol=AAPL&interval=1D&end_date={end_date}&limit=1&mode=yahoo&ema=true&format=csv",
                origin_headers,
            ),
            RequestCase(
                "chart-vn-hourly-default-csv-cache-flags",
                "GET",
                f"/tickers?symbol=FPT&interval=1h&end_date={end_date}&limit=1&mode=vn&ema=true&format=csv",
                origin_headers,
            ),
            RequestCase(
                "chart-vn-minute-default-csv-cache-flags",
                "GET",
                f"/tickers?symbol=FPT&interval=1m&end_date={end_date}&limit=1&mode=vn&ema=true&format=csv",
                origin_headers,
            ),
            RequestCase(
                "ticker-json-ema",
                "GET",
                "/tickers?symbol=FPT&limit=5&ma=true&ema=true&mode=vn&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-default-query-options",
                "GET",
                "/tickers?symbol=FPT&limit=1",
                origin_headers,
            ),
            RequestCase(
                "top-performers-default-query-options",
                "GET",
                "/analysis/top-performers",
                origin_headers,
            ),
            RequestCase(
                "ma-scores-default-query-options",
                "GET",
                "/analysis/ma-scores-by-sector",
                origin_headers,
            ),
            RequestCase(
                "rrg-default-query-options",
                "GET",
                "/analysis/rrg",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-default-query-options",
                "GET",
                f"/analysis/volume-profile?symbol=FPT&date={end_date}",
                origin_headers,
            ),
            RequestCase(
                "ticker-redis-end-bounded-zset",
                "GET",
                f"/tickers?symbol=FPT&end_date={end_date}&limit=5&ma=false&mode=vn&format=json&redis=true&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-redis-snapshot-cross-backend",
                "GET",
                "/tickers?symbol=VCB&limit=3&ma=true&ema=true&mode=vn&format=json&redis=true&snap=true&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-redis-multi-symbol-snapshot-outage",
                "GET",
                "/tickers?symbol=VCB%2CFPT&limit=3&ma=true&ema=true&mode=vn&format=json&redis=true&snap=true&cache=false",
                origin_headers,
                request_timeout=20.0,
            ),
            RequestCase(
                "ticker-stock-mode-alias",
                "GET",
                "/tickers?symbol=FPT&limit=1&mode=stocks&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase("ticker-empty-symbol", "GET", "/tickers?symbol=&limit=1", origin_headers),
            RequestCase(
                "ticker-signed-limit-negative-no-ma-sql",
                "GET",
                "/tickers?symbol=FPT&limit=-1&ma=false&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-empty-symbol-mode-all-csv",
                "GET",
                "/tickers?symbol=&mode=all&format=csv",
                origin_headers,
            ),
            RequestCase(
                "ticker-zero-limit-single-source",
                "GET",
                "/tickers?symbol=FPT&limit=0&mode=vn&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-zero-limit-mode-all",
                "GET",
                "/tickers?symbol=FPT&limit=0&mode=all&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-unknown-symbol-single-source",
                "GET",
                "/tickers?symbol=NOPE&limit=3&mode=vn&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-unknown-symbol-mode-all",
                "GET",
                "/tickers?symbol=NOPE&limit=3&mode=all&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-mode-all-catalog-universe",
                "GET",
                "/tickers?limit=1&mode=all&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-daily-chart-window",
                "GET",
                f"/tickers?symbol=FPT&interval=1D&start_date={FIXTURE_START.date()}&end_date={end_date}&limit=5&mode=vn&ma=true&ema=true&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-daily-chart-window-with-prior-ma-context",
                "GET",
                f"/tickers?symbol=FPT&interval=1D&start_date={mid_fixture_start}&end_date={mid_fixture_end}&limit=5&mode=vn&ma=true&ema=true&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-one-sided-start-date",
                "GET",
                f"/tickers?symbol=FPT&start_date={mid_fixture_start}&limit=5&mode=vn&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-one-sided-end-date",
                "GET",
                f"/tickers?symbol=FPT&end_date={mid_fixture_end}&limit=5&mode=vn&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-invalid-date-ignored",
                "GET",
                "/tickers?symbol=FPT&start_date=not-a-date&end_date=2026-13-40&limit=5&mode=vn&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-chrono-unpadded-date",
                "GET",
                f"/tickers?symbol=FPT&interval=1D&start_date={chrono_date_text}&end_date={chrono_date_text}&limit=5&mode=vn&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-invalid-interval-fallback",
                "GET",
                f"/tickers?symbol=FPT&interval=not-a-real-interval&start_date={end_date}&end_date={end_date}&limit=5&mode=vn&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-multi-symbol-limit-cap",
                "GET",
                "/tickers?symbol=FPT&symbol=VCB&limit=41&mode=vn&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-single-symbol-limit-cap",
                "GET",
                "/tickers?symbol=FPT&limit=10001&mode=vn&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-limit-i64-overflow",
                "GET",
                "/tickers?limit=9223372036854775808",
                origin_headers,
            ),
            RequestCase(
                "ticker-duplicate-boolean-query",
                "GET",
                "/tickers?symbol=FPT&ma=true&ma=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-aggregated-yahoo-sjc-collision",
                "GET",
                f"/tickers?symbol=COLL&interval=1W&start_date={FIXTURE_START.date()}&end_date={end_date}&limit=3&mode=yahoo&ma=true&ema=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-yahoo-sjc-collision-native-daily-identity-order",
                "GET",
                "/tickers?symbol=COLL&interval=1D&limit=3&mode=yahoo&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-yahoo-sjc-collision-2w-identity-order",
                "GET",
                "/tickers?symbol=COLL&interval=2W&limit=3&mode=yahoo&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-yahoo-sjc-collision-1m-identity-order",
                "GET",
                "/tickers?symbol=COLL&interval=1M&limit=3&mode=yahoo&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-aggregated-yahoo-sjc-latest-no-ma",
                "GET",
                "/tickers?symbol=COLL&interval=1W&limit=3&mode=yahoo&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-ambiguous-literal-comma-symbol",
                "GET",
                "/tickers?symbol=DUPL%2CFPT&limit=3&mode=yahoo&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-hourly-chart-window",
                "GET",
                f"/tickers?symbol=FPT&interval=1h&start_date={end_date}&end_date={end_date}&limit=20&mode=vn&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-registered-symbol-no-bars-for-requested-interval",
                "GET",
                "/tickers?symbol=COLL&interval=1h&limit=3&mode=yahoo&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-minute-chart-window",
                "GET",
                f"/tickers?symbol=FPT&interval=1m&start_date={end_date}&end_date={end_date}&limit=20&mode=vn&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-repeated-symbols",
                "GET",
                "/tickers?symbol=FPT&symbol=VCB&limit=5&mode=vn&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-comma-joined-multiple-symbols",
                "GET",
                f"/tickers?symbol=FPT,VCB&interval=1D&start_date={FIXTURE_START.date()}&end_date={end_date}&limit=5&mode=vn&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "web-backtest-current-prices-comma-joined-csv",
                "GET",
                "/tickers?symbol=FPT,VCB&limit=1&mode=vn&format=csv",
                origin_headers,
            ),
            RequestCase(
                "ticker-legacy-scaling",
                "GET",
                "/tickers?symbol=FPT&limit=3&mode=vn&legacy=true&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-daily-interval-alias",
                "GET",
                f"/tickers?symbol=FPT&interval=daily&start_date={end_date}&end_date={end_date}&limit=5&mode=vn&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            *[
                RequestCase(
                    f"ticker-derived-{derived_interval}",
                    "GET",
                    f"/tickers?symbol=FPT&interval={derived_interval}&start_date={end_date}&end_date={end_date}&limit=10&mode=vn&ma=false&format=json&redis=false&snap=false&cache=false",
                    origin_headers,
                )
                for derived_interval in ("5m", "15m", "30m")
            ],
            RequestCase(
                "ticker-derived-4h",
                "GET",
                f"/tickers?symbol=FPT&interval=4h&start_date={end_date}&end_date={end_date}&limit=10&mode=vn&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            *[
                RequestCase(
                    f"ticker-derived-{derived_interval}",
                    "GET",
                    f"/tickers?symbol=FPT&interval={derived_interval}&start_date={FIXTURE_START.date()}&end_date={end_date}&limit=10&mode=vn&ma=false&format=json&redis=false&snap=false&cache=false",
                    origin_headers,
                )
                for derived_interval in ("1W", "2W", "1M")
            ],
            RequestCase(
                "ticker-sjc-in-all-mode",
                "GET",
                "/tickers?symbol=SJC&limit=3&mode=all&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-yahoo-sjc-duplicate-symbol",
                "GET",
                "/tickers?symbol=COLL&limit=3&mode=yahoo&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-yahoo-sjc-collision-dated-ema",
                "GET",
                f"/tickers?symbol=COLL&interval=1D&start_date={mid_fixture_start}&end_date={mid_fixture_end}&limit=3&mode=yahoo&ma=true&ema=true&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-yahoo-sjc-collision-negative-limit",
                "GET",
                "/tickers?symbol=COLL&limit=-1&mode=yahoo&ma=true&ema=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-aggregated-weekly-negative-limit-ma",
                "GET",
                "/tickers?symbol=FPT&interval=1W&limit=-1&mode=vn&ma=true&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-more-than-500-symbol-parameters",
                "GET",
                more_than_500_symbols_path,
                origin_headers,
            ),
            RequestCase(
                "ticker-mode-all-source-collision",
                "GET",
                "/tickers?symbol=DUPL&limit=3&mode=all&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-mode-all-cache-miss",
                "GET",
                "/tickers?symbol=DUPL&limit=3&mode=all&ma=false&format=json&redis=false&snap=false&cache=true",
                origin_headers,
            ),
            RequestCase(
                "ticker-mode-all-cache-hit",
                "GET",
                "/tickers?symbol=DUPL&limit=3&mode=all&ma=false&format=json&redis=false&snap=false&cache=true",
                origin_headers,
            ),
            RequestCase(
                "ticker-cache-comma-literal-first",
                "GET",
                "/tickers?symbol=DUPL%2CFPT&limit=13&mode=yahoo&ma=false&format=json&redis=false&snap=false&cache=true",
                origin_headers,
            ),
            RequestCase(
                "ticker-cache-comma-repeated-second",
                "GET",
                "/tickers?symbol=DUPL&symbol=FPT&limit=13&mode=yahoo&ma=false&format=json&redis=false&snap=false&cache=true",
                origin_headers,
            ),
            RequestCase(
                "ticker-cache-comma-repeated-first",
                "GET",
                "/tickers?symbol=DUPL&symbol=FPT&limit=14&mode=yahoo&ma=false&format=json&redis=false&snap=false&cache=true",
                origin_headers,
            ),
            RequestCase(
                "ticker-cache-comma-literal-second",
                "GET",
                "/tickers?symbol=DUPL%2CFPT&limit=14&mode=yahoo&ma=false&format=json&redis=false&snap=false&cache=true",
                origin_headers,
            ),
            RequestCase("names-vn", "GET", "/tickers/name", origin_headers),
            RequestCase("names-crypto", "GET", "/tickers/name?mode=crypto", origin_headers),
            RequestCase(
                "names-cryptos-mode-alias", "GET", "/tickers/name?mode=cryptos", origin_headers
            ),
            RequestCase("names-yahoo", "GET", "/tickers/name?mode=yahoo", origin_headers),
            RequestCase("names-all", "GET", "/tickers/name?mode=all", origin_headers),
            RequestCase(
                "names-duplicate-mode",
                "GET",
                "/tickers/name?mode=vn&mode=crypto",
                origin_headers,
            ),
            RequestCase(
                "ticker-duplicate-optional-interval",
                "GET",
                "/tickers?symbol=FPT&interval=1D&interval=1h",
                origin_headers,
            ),
            RequestCase(
                "notes-upload-bad-name",
                "POST",
                f"/upload/markdown?session_id={session_id}&secret={SYNC_SECRET}",
                origin_headers,
                files={"file": ("wrong.md", b"bad name", "text/markdown")},
            ),
            RequestCase(
                "notes-upload-wrong-field-name",
                "POST",
                f"/upload/markdown?session_id={session_id}&secret={SYNC_SECRET}",
                origin_headers,
                files={"wrong_field": ("content.md", b"wrong field", "text/markdown")},
            ),
            RequestCase(
                "notes-upload-multiple-files",
                "POST",
                f"/upload/markdown?session_id={session_id}&secret={SYNC_SECRET}",
                origin_headers,
                files=[
                    ("file", ("content.md", b"first", "text/markdown")),
                    ("file", ("content.md", b"second", "text/markdown")),
                ],
            ),
            RequestCase(
                "notes-upload-invalid-utf8",
                "POST",
                f"/upload/markdown?session_id={session_id}&secret={SYNC_SECRET}",
                origin_headers,
                files={"file": ("content.md", b"\xff", "text/markdown")},
            ),
            RequestCase(
                "notes-upload-empty-multipart",
                "POST",
                f"/upload/markdown?session_id={session_id}&secret={SYNC_SECRET}",
                {**origin_headers, "content-type": "multipart/form-data; boundary=x"},
                raw_body=b"--x--\r\n",
            ),
            RequestCase(
                "notes-upload-zero-byte-multipart-body",
                "POST",
                f"/upload/markdown?session_id={session_id}&secret={SYNC_SECRET}",
                {**origin_headers, "content-type": "multipart/form-data; boundary=x"},
                raw_body=b"",
            ),
            RequestCase(
                "notes-upload-zero-byte-empty-multipart-boundary",
                "POST",
                f"/upload/markdown?session_id={session_id}&secret={SYNC_SECRET}",
                {**origin_headers, "content-type": "multipart/form-data; boundary="},
                raw_body=b"",
            ),
            RequestCase(
                "notes-upload-chunked-over-limit-invalid-content-type",
                "POST",
                f"/upload/markdown?session_id={session_id}&secret={SYNC_SECRET}",
                {**origin_headers, "content-type": "text/plain"},
                chunked_body=(oversized_notes_chunk,),
            ),
            RequestCase(
                "notes-upload-chunked-over-limit-valid-multipart-content-type",
                "POST",
                f"/upload/markdown?session_id={session_id}&secret={SYNC_SECRET}",
                {**origin_headers, "content-type": "multipart/form-data; boundary=x"},
                chunked_body=oversized_notes_multipart_chunks,
            ),
            RequestCase(
                "notes-upload-chunked-over-limit-empty-multipart-boundary",
                "POST",
                f"/upload/markdown?session_id={session_id}&secret={SYNC_SECRET}",
                {**origin_headers, "content-type": "multipart/form-data; boundary="},
                chunked_body=(oversized_notes_chunk,),
            ),
            RequestCase(
                "notes-upload-chunked-over-limit-invalid-session-id",
                "POST",
                f"/upload/markdown?session_id=not-a-uuid&secret={SYNC_SECRET}",
                {**origin_headers, "content-type": "multipart/form-data; boundary=x"},
                chunked_body=oversized_notes_multipart_chunks,
            ),
            RequestCase(
                "notes-upload-chunked-over-limit-short-secret",
                "POST",
                f"/upload/markdown?session_id={session_id}&secret=short",
                {**origin_headers, "content-type": "multipart/form-data; boundary=x"},
                chunked_body=oversized_notes_multipart_chunks,
            ),
            RequestCase(
                "notes-invalid-attempts-no-session",
                "GET",
                f"/uploads/{session_id}/markdown/content.md",
                origin_headers,
            ),
            RequestCase(
                "notes-fetch-legacy-plaintext-secret",
                "GET",
                f"/uploads/{legacy_session_id}/markdown/content.md",
                origin_headers,
            ),
            RequestCase(
                "notes-fetch-invalid-secret",
                "GET",
                f"/uploads/{legacy_session_id}/markdown/content.md?secret=incorrect-secret",
                origin_headers,
            ),
            RequestCase(
                "notes-fetch-invalid-session-id",
                "GET",
                "/uploads/not-a-uuid/markdown/content.md",
                origin_headers,
            ),
            RequestCase(
                "notes-fetch-duplicate-optional-secret",
                "GET",
                f"/uploads/{legacy_session_id}/markdown/content.md?secret=legacy-notes-secret&secret=legacy-notes-secret",
                origin_headers,
            ),
            RequestCase(
                "notes-upload-markdown",
                "POST",
                f"/upload/markdown?session_id={session_id}&secret={SYNC_SECRET}",
                production_origin_headers,
                files={
                    "file": (
                        "content.md",
                        b"# Notes parity\\n\\nShared Rust/Python route fixture.\\n",
                        "text/markdown",
                    )
                },
            ),
            RequestCase(
                "notes-upload-invalid-session-id",
                "POST",
                f"/upload/markdown?session_id=not-a-uuid&secret={SYNC_SECRET}",
                origin_headers,
                files={"file": ("content.md", b"invalid session", "text/markdown")},
            ),
            RequestCase(
                "notes-upload-existing-session-wrong-secret",
                "POST",
                f"/upload/markdown?session_id={legacy_session_id}&secret=incorrect-secret",
                origin_headers,
                files={"file": ("content.md", b"wrong secret", "text/markdown")},
            ),
            RequestCase(
                "notes-upload-duplicate-required-session-id",
                "POST",
                f"/upload/markdown?session_id={session_id}&session_id={session_id}&secret={SYNC_SECRET}",
                origin_headers,
                files={"file": ("content.md", b"duplicate query probe", "text/markdown")},
            ),
            RequestCase(
                "notes-upload-duplicate-required-secret",
                "POST",
                f"/upload/markdown?session_id={session_id}&secret={SYNC_SECRET}&secret={SYNC_SECRET}",
                origin_headers,
                files={"file": ("content.md", b"duplicate query probe", "text/markdown")},
            ),
            RequestCase(
                "notes-fetch-markdown",
                "GET",
                f"/uploads/{session_id}/markdown/content.md",
                production_origin_headers,
            ),
            RequestCase(
                "notes-fetch-leading-dot-filename",
                "GET",
                f"/uploads/{legacy_session_id}/markdown/.hidden.md",
                origin_headers,
            ),
            RequestCase(
                "notes-fetch-doubledot-filename",
                "GET",
                f"/uploads/{legacy_session_id}/markdown/content..md",
                origin_headers,
            ),
            RequestCase(
                "notes-fetch-encoded-dotdot-filename",
                "GET",
                f"/uploads/{legacy_session_id}/markdown/%2E%2E",
                origin_headers,
            ),
            RequestCase(
                "notes-fetch-encoded-backslash-filename",
                "GET",
                f"/uploads/{legacy_session_id}/markdown/%5C",
                origin_headers,
            ),
            RequestCase(
                "notes-fetch-encoded-slash-filename",
                "GET",
                f"/uploads/{legacy_session_id}/markdown/%2F",
                origin_headers,
            ),
            RequestCase(
                "notes-fetch-del-control-filename",
                "GET",
                f"/uploads/{legacy_session_id}/markdown/%7F",
                origin_headers,
            ),
            RequestCase(
                "notes-fetch-c1-control-filename",
                "GET",
                f"/uploads/{legacy_session_id}/markdown/%C2%80",
                origin_headers,
            ),
            RequestCase(
                "notes-fetch-encoded-session-slash",
                "GET",
                f"/uploads/{legacy_session_id}%2Fextra/markdown/content.md",
                origin_headers,
            ),
            RequestCase(
                "notes-fetch-literal-nested-session",
                "GET",
                f"/uploads/{legacy_session_id}/extra/markdown/content.md",
                origin_headers,
            ),
            RequestCase(
                "notes-fetch-encoded-fixed-slash",
                "GET",
                f"/uploads/{legacy_session_id}/markdown%2Fcontent.md",
                origin_headers,
            ),
            RequestCase(
                "notes-fetch-encoded-fixed-letter",
                "GET",
                f"/uploads/{legacy_session_id}/mark%64own/content.md",
                origin_headers,
            ),
            RequestCase(
                "notes-fetch-empty-filename",
                "GET",
                f"/uploads/{legacy_session_id}/markdown/",
                origin_headers,
            ),
            RequestCase(
                "notes-fetch-duplicate-secret-encoded-slash-filename",
                "GET",
                f"/uploads/{legacy_session_id}/markdown/%2F?secret=first-secret&secret=second-secret",
                origin_headers,
            ),
            RequestCase(
                "notes-fetch-duplicate-secret-encoded-fixed-slash",
                "GET",
                f"/uploads/{legacy_session_id}/markdown%2Fcontent.md?secret=first-secret&secret=second-secret",
                origin_headers,
            ),
            RequestCase(
                "notes-fetch-literal-nested-filename",
                "GET",
                f"/uploads/{legacy_session_id}/markdown/sub/path.md",
                origin_headers,
            ),
            RequestCase(
                "notes-upload-maximum-markdown-size",
                "POST",
                f"/upload/markdown?session_id={max_file_session_id}&secret={SYNC_SECRET}",
                origin_headers,
                files={
                    "file": (
                        "content.md",
                        b"x" * (5 * 1024 * 1024),
                        "text/markdown",
                    )
                },
            ),
            RequestCase(
                "notes-fetch-maximum-markdown-size",
                "GET",
                f"/uploads/{max_file_session_id}/markdown/content.md",
                origin_headers,
            ),
            RequestCase(
                "notes-upload-over-maximum-markdown-size",
                "POST",
                f"/upload/markdown?session_id={oversized_file_session_id}&secret={SYNC_SECRET}",
                origin_headers,
                files={
                    "file": (
                        "content.md",
                        b"x" * (5 * 1024 * 1024 + 1),
                        "text/markdown",
                    )
                },
            ),
            RequestCase(
                "notes-quota-byte-limit",
                "POST",
                f"/upload/markdown?session_id={byte_limited_session_id}&secret={SYNC_SECRET}",
                origin_headers,
                files={"file": ("content.md", b"x" * 450, "text/markdown")},
            ),
            RequestCase(
                "notes-quota-fourth-session",
                "POST",
                f"/upload/markdown?session_id={quota_session_id}&secret={SYNC_SECRET}",
                origin_headers,
                files={"file": ("content.md", b"x", "text/markdown")},
            ),
            RequestCase(
                "notes-quota-session-limit",
                "POST",
                f"/upload/markdown?session_id={session_limited_session_id}&secret={SYNC_SECRET}",
                origin_headers,
                files={"file": ("content.md", b"x", "text/markdown")},
            ),
            RequestCase(
                "notes-delete-session",
                "DELETE",
                f"/uploads/{session_id}?secret={SYNC_SECRET}",
                production_origin_headers,
            ),
            RequestCase(
                "notes-quota-exact-byte-limit",
                "POST",
                f"/upload/markdown?session_id={session_limited_session_id}&secret={SYNC_SECRET}",
                origin_headers,
                files={"file": ("content.md", b"x", "text/markdown")},
            ),
            RequestCase(
                "notes-quota-one-byte-over-limit",
                "POST",
                f"/upload/markdown?session_id={byte_limited_session_id}&secret={SYNC_SECRET}",
                origin_headers,
                files={"file": ("content.md", b"x", "text/markdown")},
            ),
            RequestCase(
                "notes-delete-duplicate-required-secret",
                "DELETE",
                f"/uploads/{session_id}?secret={SYNC_SECRET}&secret={SYNC_SECRET}",
                origin_headers,
            ),
            RequestCase(
                "notes-delete-encoded-session-slash",
                "DELETE",
                f"/uploads/{legacy_session_id}%2Fextra?secret=legacy-notes-secret",
                origin_headers,
            ),
            RequestCase(
                "notes-delete-encoded-session-slash-duplicate-secret",
                "DELETE",
                f"/uploads/{legacy_session_id}%2Fextra?secret=first-secret&secret=second-secret",
                origin_headers,
            ),
            RequestCase(
                "notes-delete-literal-nested-session",
                "DELETE",
                f"/uploads/{legacy_session_id}/extra?secret=legacy-notes-secret",
                origin_headers,
            ),
            RequestCase(
                "notes-delete-third-session",
                "DELETE",
                f"/uploads/{quota_session_id}?secret={SYNC_SECRET}",
                origin_headers,
            ),
            RequestCase(
                "notes-delete-legacy-session",
                "DELETE",
                f"/uploads/{legacy_session_id}?secret=legacy-notes-secret",
                origin_headers,
            ),
            RequestCase(
                "notes-delete-existing-session-wrong-secret",
                "DELETE",
                f"/uploads/{legacy_session_id}?secret=incorrect-secret",
                origin_headers,
            ),
            RequestCase(
                "notes-delete-invalid-session-id",
                "DELETE",
                "/uploads/not-a-uuid?secret=incorrect-secret",
                origin_headers,
            ),
            RequestCase(
                "notes-upload-symlinked-populated-markdown-directory",
                "POST",
                f"/upload/markdown?session_id={symlink_populated_markdown_id}&secret={SYNC_SECRET}",
                origin_headers,
                files={"file": ("content.md", b"must not overwrite", "text/markdown")},
            ),
            RequestCase(
                "notes-upload-symlinked-session",
                "POST",
                f"/upload/markdown?session_id={symlink_session_id}&secret={SYNC_SECRET}",
                origin_headers,
                files={"file": ("content.md", b"must not escape", "text/markdown")},
            ),
            RequestCase(
                "notes-fetch-symlinked-session",
                "GET",
                f"/uploads/{symlink_session_id}/markdown/content.md",
                origin_headers,
            ),
            RequestCase(
                "notes-delete-symlinked-session",
                "DELETE",
                f"/uploads/{symlink_session_id}?secret={SYNC_SECRET}",
                origin_headers,
            ),
            RequestCase(
                "notes-upload-symlinked-markdown-directory",
                "POST",
                f"/upload/markdown?session_id={symlink_markdown_id}&secret={SYNC_SECRET}",
                origin_headers,
                files={"file": ("content.md", b"must not escape", "text/markdown")},
            ),
            RequestCase(
                "notes-fetch-symlinked-markdown-directory",
                "GET",
                f"/uploads/{symlink_markdown_id}/markdown/content.md",
                origin_headers,
            ),
            RequestCase(
                "notes-delete-symlinked-markdown-directory",
                "DELETE",
                f"/uploads/{symlink_markdown_id}?secret={SYNC_SECRET}",
                origin_headers,
            ),
            RequestCase(
                "notes-upload-symlinked-metadata-file",
                "POST",
                f"/upload/markdown?session_id={symlink_metadata_id}&secret={SYNC_SECRET}",
                origin_headers,
                files={"file": ("content.md", b"must not overwrite metadata", "text/markdown")},
            ),
            RequestCase(
                "notes-fetch-symlinked-metadata-file",
                "GET",
                f"/uploads/{symlink_metadata_id}/markdown/content.md",
                origin_headers,
            ),
            RequestCase(
                "notes-delete-symlinked-metadata-file",
                "DELETE",
                f"/uploads/{symlink_metadata_id}?secret={SYNC_SECRET}",
                origin_headers,
            ),
            RequestCase(
                "notes-fetch-symlinked-content-file",
                "GET",
                f"/uploads/{symlink_content_id}/markdown/content.md",
                origin_headers,
            ),
            RequestCase(
                "notes-delete-symlinked-content-file",
                "DELETE",
                f"/uploads/{symlink_content_id}?secret={SYNC_SECRET}",
                origin_headers,
            ),
            RequestCase(
                "top-performers",
                "GET",
                f"/analysis/top-performers?date={end_date}&mode=vn&limit=10&min_volume=0&snap=false",
                origin_headers,
            ),
            RequestCase(
                "top-performers-stock-mode-alias",
                "GET",
                f"/analysis/top-performers?date={end_date}&mode=stocks&limit=10&min_volume=0&snap=false",
                origin_headers,
            ),
            RequestCase(
                "top-performers-invalid-date-fallback",
                "GET",
                "/analysis/top-performers?date=not-a-date&mode=vn&limit=10&min_volume=0&snap=false",
                origin_headers,
            ),
            RequestCase(
                "top-performers-with-hour-requested",
                "GET",
                f"/analysis/top-performers?date={end_date}&mode=vn&limit=10&min_volume=0&with_hour=true&snap=false",
                origin_headers,
            ),
            RequestCase(
                "top-performers-with-hour-false",
                "GET",
                f"/analysis/top-performers?date={end_date}&mode=vn&limit=10&min_volume=0&with_hour=false&snap=false",
                origin_headers,
            ),
            RequestCase(
                "top-performers-with-hour-invalid",
                "GET",
                f"/analysis/top-performers?date={end_date}&mode=vn&limit=10&min_volume=0&with_hour=on&snap=false",
                origin_headers,
            ),
            RequestCase(
                "top-performers-with-hour-duplicate",
                "GET",
                f"/analysis/top-performers?date={end_date}&mode=vn&limit=10&min_volume=0&with_hour=true&with_hour=false&snap=false",
                origin_headers,
            ),
            RequestCase(
                "top-performers-default-snapshot-option",
                "GET",
                f"/analysis/top-performers?date={end_date}&mode=vn&limit=10&min_volume=0",
                origin_headers,
            ),
            RequestCase(
                "top-performers-mode-all-volume-ascending",
                "GET",
                f"/analysis/top-performers?date={end_date}&mode=all&sort_by=volume&direction=asc&limit=10&min_volume=0&snap=false",
                origin_headers,
            ),
            RequestCase(
                "top-performers-sector-filter",
                "GET",
                f"/analysis/top-performers?date={end_date}&mode=vn&sort_by=ma20_score&direction=desc&limit=10&min_volume=0&sector=Technology&snap=false",
                origin_headers,
            ),
            RequestCase(
                "top-performers-default-minimum-volume",
                "GET",
                f"/analysis/top-performers?date={end_date}&mode=vn&sort_by=close_changed&direction=desc&limit=20&snap=false",
                origin_headers,
            ),
            RequestCase(
                "top-performers-zero-limit-sort-fallback",
                "GET",
                f"/analysis/top-performers?date={end_date}&mode=vn&sort_by=unknown&direction=sideways&limit=0&min_volume=0&snap=false",
                origin_headers,
            ),
            RequestCase(
                "top-performers-minimum-volume-overflow",
                "GET",
                "/analysis/top-performers?min_volume=18446744073709551616",
                origin_headers,
            ),
            RequestCase(
                "top-performers-limit-overflow",
                "GET",
                "/analysis/top-performers?limit=18446744073709551616",
                origin_headers,
            ),
            RequestCase(
                "top-performers-negative-limit",
                "GET",
                "/analysis/top-performers?limit=-1",
                origin_headers,
            ),
            RequestCase(
                "top-performers-negative-minimum-volume",
                "GET",
                "/analysis/top-performers?min_volume=-1",
                origin_headers,
            ),
            RequestCase(
                "top-performers-duplicate-optional-sector",
                "GET",
                "/analysis/top-performers?sector=Technology&sector=Energy",
                origin_headers,
            ),
            RequestCase(
                "top-performers-duplicate-mode",
                "GET",
                "/analysis/top-performers?mode=vn&mode=crypto",
                origin_headers,
            ),
            RequestCase(
                "ma-scores-by-sector",
                "GET",
                f"/analysis/ma-scores-by-sector?date={end_date}&mode=vn&ma_period=20&min_score=0&top_per_sector=10&snap=false",
                origin_headers,
            ),
            RequestCase(
                "ma-scores-invalid-date-fallback",
                "GET",
                "/analysis/ma-scores-by-sector?date=not-a-date&mode=vn&ma_period=20&min_score=0&top_per_sector=10&snap=false",
                origin_headers,
            ),
            RequestCase(
                "ma-scores-stock-mode-alias",
                "GET",
                f"/analysis/ma-scores-by-sector?date={end_date}&mode=stock&ma_period=20&min_score=0&top_per_sector=10&snap=false",
                origin_headers,
            ),
            RequestCase(
                "ma-scores-by-sector-default-snapshot-option",
                "GET",
                f"/analysis/ma-scores-by-sector?date={end_date}&mode=vn&ma_period=20&min_score=0&top_per_sector=10",
                origin_headers,
            ),
            RequestCase(
                "ma-scores-by-sector-mode-all-threshold",
                "GET",
                f"/analysis/ma-scores-by-sector?date={end_date}&mode=all&ema=true&ma_period=10&min_score=0&above_threshold_only=true&top_per_sector=2&snap=false",
                origin_headers,
            ),
            RequestCase(
                "ma-scores-invalid-period",
                "GET",
                "/analysis/ma-scores-by-sector?ma_period=9&min_score=0",
                origin_headers,
            ),
            RequestCase(
                "ma-scores-period-u32-overflow",
                "GET",
                "/analysis/ma-scores-by-sector?ma_period=4294967296",
                origin_headers,
            ),
            RequestCase(
                "ma-scores-top-per-sector-usize-overflow",
                "GET",
                "/analysis/ma-scores-by-sector?top_per_sector=18446744073709551616",
                origin_headers,
            ),
            RequestCase(
                "ma-scores-negative-period",
                "GET",
                "/analysis/ma-scores-by-sector?ma_period=-1",
                origin_headers,
            ),
            RequestCase(
                "ma-scores-negative-top-per-sector",
                "GET",
                "/analysis/ma-scores-by-sector?top_per_sector=-1",
                origin_headers,
            ),
            RequestCase(
                "ma-scores-duplicate-float-threshold",
                "GET",
                "/analysis/ma-scores-by-sector?min_score=1.25&min_score=2.5",
                origin_headers,
            ),
            RequestCase(
                "ma-scores-duplicate-mode",
                "GET",
                "/analysis/ma-scores-by-sector?mode=vn&mode=crypto",
                origin_headers,
            ),
            RequestCase(
                "ma-scores-zero-limit-and-high-threshold",
                "GET",
                f"/analysis/ma-scores-by-sector?date={end_date}&mode=vn&ma_period=10&min_score=100&above_threshold_only=true&top_per_sector=0&snap=false",
                origin_headers,
            ),
            RequestCase(
                "ma-scores-nan-threshold",
                "GET",
                f"/analysis/ma-scores-by-sector?date={end_date}&mode=vn&ma_period=20&min_score=NaN&snap=false",
                origin_headers,
            ),
            RequestCase(
                "ma-scores-positive-infinity-threshold",
                "GET",
                f"/analysis/ma-scores-by-sector?date={end_date}&mode=vn&ma_period=20&min_score=Infinity&snap=false",
                origin_headers,
            ),
            RequestCase(
                "ma-scores-negative-infinity-threshold",
                "GET",
                f"/analysis/ma-scores-by-sector?date={end_date}&mode=vn&ma_period=20&min_score=-Infinity&snap=false",
                origin_headers,
            ),
            RequestCase(
                "rrg-jdk",
                "GET",
                f"/analysis/rrg?algorithm=jdk&mode=vn&trails=5&min_volume=1000&benchmark=VNINDEX&period=10&date={end_date}&ema=true",
                origin_headers,
            ),
            RequestCase(
                "rrg-stock-mode-alias",
                "GET",
                f"/analysis/rrg?algorithm=jdk&mode=stocks&trails=5&min_volume=1000&benchmark=VNINDEX&period=10&date={end_date}&ema=true",
                origin_headers,
            ),
            RequestCase(
                "rrg-jdk-chrono-unpadded-date",
                "GET",
                f"/analysis/rrg?algorithm=jdk&mode=vn&trails=5&min_volume=1000&benchmark=VNINDEX&period=10&date={chrono_date_text}&ema=false&snap=false",
                origin_headers,
            ),
            RequestCase(
                "rrg-jdk-widget-default-date-after-fixture",
                "GET",
                f"/analysis/rrg?algorithm=jdk&mode=vn&trails=5&min_volume=1000&benchmark=VNINDEX&period=10&date={(FIXTURE_END + timedelta(days=1)).date()}&ema=true",
                origin_headers,
            ),
            RequestCase(
                "rrg-jdk-widget-default-date-after-fixture-crypto",
                "GET",
                f"/analysis/rrg?algorithm=jdk&mode=crypto&trails=5&min_volume=1000&benchmark=BTCUSDT&period=10&date={(FIXTURE_END + timedelta(days=1)).date()}&ema=true",
                origin_headers,
            ),
            RequestCase(
                "rrg-jdk-widget-default-date-after-fixture-yahoo",
                "GET",
                f"/analysis/rrg?algorithm=jdk&mode=yahoo&trails=5&min_volume=0&benchmark=%5EGSPC&period=10&date={(FIXTURE_END + timedelta(days=1)).date()}&ema=true",
                origin_headers,
            ),
            RequestCase(
                "rrg-invalid-date",
                "GET",
                "/analysis/rrg?algorithm=jdk&mode=vn&trails=5&min_volume=0&benchmark=VNINDEX&period=10&date=not-a-date&ema=false&snap=false",
                origin_headers,
            ),
            RequestCase(
                "rrg-mascore",
                "GET",
                f"/analysis/rrg?algorithm=mascore&mode=vn&trails=5&min_volume=1000&date={end_date}&ema=true&snap=false",
                origin_headers,
            ),
            RequestCase(
                "rrg-jdk-mode-all",
                "GET",
                f"/analysis/rrg?algorithm=jdk&mode=all&trails=5&min_volume=0&benchmark=VNINDEX&period=10&date={end_date}&ema=false&snap=false",
                origin_headers,
            ),
            RequestCase(
                "rrg-jdk-mode-all-duplicate-benchmark",
                "GET",
                f"/analysis/rrg?algorithm=jdk&mode=all&trails=5&min_volume=0&benchmark=DUPL&period=10&date={end_date}&ema=false&snap=false",
                origin_headers,
            ),
            RequestCase(
                "rrg-jdk-mode-all-yahoo-benchmark",
                "GET",
                f"/analysis/rrg?algorithm=jdk&mode=all&trails=5&min_volume=0&benchmark=%5EGSPC&period=10&date={end_date}&ema=false&snap=false",
                origin_headers,
            ),
            RequestCase(
                "rrg-jdk-mode-all-crypto-benchmark",
                "GET",
                f"/analysis/rrg?algorithm=jdk&mode=all&trails=5&min_volume=0&benchmark=BTCUSDT&period=10&date={end_date}&ema=false&snap=false",
                origin_headers,
            ),
            RequestCase(
                "rrg-mascore-no-trails",
                "GET",
                f"/analysis/rrg?algorithm=mascore&mode=vn&trails=0&min_volume=0&benchmark=VNINDEX&period=10&date={end_date}&ema=false&snap=false",
                origin_headers,
            ),
            RequestCase(
                "rrg-boundary-period-and-trails",
                "GET",
                f"/analysis/rrg?algorithm=jdk&mode=vn&trails=120&min_volume=0&benchmark=VNINDEX&period=50&date={end_date}&ema=false&snap=false",
                origin_headers,
            ),
            RequestCase(
                "rrg-above-period-and-trails-clamps",
                "GET",
                f"/analysis/rrg?algorithm=jdk&mode=vn&trails=121&min_volume=0&benchmark=VNINDEX&period=51&date={end_date}&ema=false&snap=false",
                origin_headers,
            ),
            RequestCase(
                "rrg-below-period-minimum-clamp",
                "GET",
                f"/analysis/rrg?algorithm=jdk&mode=vn&trails=5&min_volume=0&benchmark=VNINDEX&period=3&date={end_date}&ema=false&snap=false",
                origin_headers,
            ),
            RequestCase(
                "rrg-zero-period-and-negative-volume",
                "GET",
                f"/analysis/rrg?algorithm=jdk&mode=vn&trails=0&min_volume=-1&benchmark=VNINDEX&period=0&date={end_date}&ema=false&snap=false",
                origin_headers,
            ),
            RequestCase(
                "rrg-period-usize-overflow",
                "GET",
                "/analysis/rrg?period=18446744073709551616",
                origin_headers,
            ),
            RequestCase(
                "rrg-negative-period",
                "GET",
                "/analysis/rrg?period=-1",
                origin_headers,
            ),
            RequestCase(
                "rrg-trails-usize-overflow",
                "GET",
                "/analysis/rrg?trails=18446744073709551616",
                origin_headers,
            ),
            RequestCase(
                "rrg-negative-trails",
                "GET",
                "/analysis/rrg?trails=-1",
                origin_headers,
            ),
            RequestCase(
                "rrg-min-volume-i64-overflow",
                "GET",
                "/analysis/rrg?min_volume=9223372036854775808",
                origin_headers,
            ),
            RequestCase(
                "rrg-duplicate-optional-benchmark",
                "GET",
                "/analysis/rrg?benchmark=VNINDEX&benchmark=VCB",
                origin_headers,
            ),
            RequestCase(
                "volume-profile",
                "GET",
                f"/analysis/volume-profile?symbol=FPT&mode=vn&date={end_date}&bins=20&value_area_pct=70",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-widget-vn-default-bins",
                "GET",
                f"/analysis/volume-profile?symbol=FPT&date={end_date}&bins=10&mode=vn",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-widget-crypto-default-bins",
                "GET",
                f"/analysis/volume-profile?symbol=ETHUSDT&date={end_date}&bins=10&mode=crypto",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-low-priced-crypto-poc-tolerance",
                "GET",
                f"/analysis/volume-profile?symbol=MICROUSDT&mode=crypto&date={end_date}&bins=10&value_area_pct=70",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-crypto-resource-step-limit",
                "GET",
                f"/analysis/volume-profile?symbol=WIDEUSDT&mode=crypto&date={end_date}&bins=10",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-widget-yahoo-default-bins",
                "GET",
                f"/analysis/volume-profile?symbol=AAPL&date={end_date}&bins=10&mode=yahoo",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-widget-fallback-empty-profile",
                "GET",
                f"/analysis/volume-profile?symbol=FPT&date={widget_fallback_date}&bins=10&mode=vn",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-widget-fallback-daily-csv",
                "GET",
                f"/tickers?symbol=FPT&end_date={widget_fallback_date}&limit=1&mode=vn&ema=true&format=csv",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-range",
                "GET",
                f"/analysis/volume-profile?symbol=FPT&mode=vn&start_date={(FIXTURE_END - timedelta(days=30)).date()}&end_date={end_date}&bins=10",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-date-overrides-range",
                "GET",
                f"/analysis/volume-profile?symbol=FPT&mode=vn&date={end_date}&start_date={FIXTURE_START.date()}&end_date={FIXTURE_START.date()}&bins=20&value_area_pct=70",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-start-only",
                "GET",
                f"/analysis/volume-profile?symbol=FPT&mode=vn&start_date={end_date}&bins=20&value_area_pct=70",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-end-only",
                "GET",
                f"/analysis/volume-profile?symbol=FPT&mode=vn&end_date={end_date}&bins=20&value_area_pct=70",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-invalid-date",
                "GET",
                "/analysis/volume-profile?symbol=FPT&mode=vn&date=2026-13-40&bins=20&value_area_pct=70",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-invalid-end-date",
                "GET",
                f"/analysis/volume-profile?symbol=FPT&mode=vn&start_date={end_date}&end_date=2026-13-40&bins=20&value_area_pct=70",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-unknown-mode-vn-fallback",
                "GET",
                f"/analysis/volume-profile?symbol=FPT&mode=unknown&date={end_date}&bins=20&value_area_pct=70",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-clamped-low-bounds",
                "GET",
                f"/analysis/volume-profile?symbol=PROFILEUSDT&mode=crypto&date={end_date}&bins=0&value_area_pct=0",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-clamped-high-bounds",
                "GET",
                f"/analysis/volume-profile?symbol=PROFILEUSDT&mode=crypto&date={end_date}&bins=201&value_area_pct=100",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-bins-usize-overflow",
                "GET",
                "/analysis/volume-profile?symbol=FPT&bins=18446744073709551616",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-negative-bins",
                "GET",
                "/analysis/volume-profile?symbol=FPT&bins=-1",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-duplicate-required-symbol",
                "GET",
                "/analysis/volume-profile?symbol=FPT&symbol=VCB",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-duplicate-float-value-area",
                "GET",
                "/analysis/volume-profile?symbol=FPT&value_area_pct=60&value_area_pct=70",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-duplicate-string-mode",
                "GET",
                "/analysis/volume-profile?symbol=FPT&mode=vn&mode=crypto",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-nan-value-area",
                "GET",
                f"/analysis/volume-profile?symbol=FPT&mode=vn&date={end_date}&bins=20&value_area_pct=NaN",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-positive-infinity-value-area",
                "GET",
                f"/analysis/volume-profile?symbol=FPT&mode=vn&date={end_date}&bins=20&value_area_pct=Infinity",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-negative-infinity-value-area",
                "GET",
                f"/analysis/volume-profile?symbol=FPT&mode=vn&date={end_date}&bins=20&value_area_pct=-Infinity",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-invalid-range",
                "GET",
                f"/analysis/volume-profile?symbol=FPT&mode=vn&start_date={end_date}&end_date={FIXTURE_START.date()}&bins=20&value_area_pct=70",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-no-minute-history",
                "GET",
                f"/analysis/volume-profile?symbol=BTCUSDT&mode=crypto&date={end_date}&bins=20&value_area_pct=70",
                origin_headers,
            ),
            RequestCase("explorer", "GET", "/explorer", origin_headers),
            RequestCase(
                "static-js",
                "GET",
                "/public/js/app.js",
                origin_headers,
                ignored_headers=("last-modified",),
            ),
            RequestCase(
                "static-js-if-none-match-wildcard",
                "GET",
                "/public/js/app.js",
                {**origin_headers, "if-none-match": "*"},
                ignored_headers=("last-modified",),
            ),
            RequestCase(
                "static-js-if-modified-since-future",
                "GET",
                "/public/js/app.js",
                {**origin_headers, "if-modified-since": "Tue, 01 Jan 2030 00:00:00 GMT"},
                ignored_headers=("last-modified",),
            ),
            RequestCase(
                "static-js-if-modified-since-invalid-zone",
                "GET",
                "/public/js/app.js",
                {**origin_headers, "if-modified-since": "Thu, 01 Jan 2030 00:00:00"},
                ignored_headers=("last-modified",),
            ),
            RequestCase(
                "static-js-if-unmodified-since-invalid-zone",
                "GET",
                "/public/js/app.js",
                {**origin_headers, "if-unmodified-since": "Thu, 01 Jan 1970 00:00:00"},
                ignored_headers=("last-modified",),
            ),
            RequestCase(
                "static-js-if-unmodified-since-past",
                "GET",
                "/public/js/app.js",
                {**origin_headers, "if-unmodified-since": "Thu, 01 Jan 1970 00:00:00 GMT"},
                ignored_headers=("last-modified",),
            ),
            RequestCase(
                "static-js-if-unmodified-since-future",
                "GET",
                "/public/js/app.js",
                {**origin_headers, "if-unmodified-since": "Thu, 01 Jan 2030 00:00:00 GMT"},
                ignored_headers=("last-modified",),
            ),
            RequestCase(
                "static-js-range",
                "GET",
                "/public/js/app.js",
                {**origin_headers, "range": "bytes=0-31"},
                ignored_headers=("last-modified",),
            ),
            RequestCase(
                "static-js-unsatisfiable-range",
                "GET",
                "/public/js/app.js",
                {**origin_headers, "range": "bytes=999999999-"},
                ignored_headers=("last-modified",),
            ),
            RequestCase(
                "static-js-malformed-range",
                "GET",
                "/public/js/app.js",
                {**origin_headers, "range": "bytes=bad"},
                ignored_headers=("last-modified",),
            ),
            RequestCase(
                "static-js-leading-zero-range",
                "GET",
                "/public/js/app.js",
                {**origin_headers, "range": "bytes=00-1"},
                ignored_headers=("last-modified",),
            ),
            RequestCase(
                "static-js-oversized-suffix-range",
                "GET",
                "/public/js/app.js",
                {**origin_headers, "range": "bytes=-999999999"},
                ignored_headers=("last-modified",),
            ),
            RequestCase(
                "static-js-malformed-multipart-range",
                "GET",
                "/public/js/app.js",
                {**origin_headers, "range": "bytes=bad,"},
                ignored_headers=("last-modified",),
            ),
            RequestCase(
                "static-js-multiple-ranges",
                "GET",
                "/public/js/app.js",
                {**origin_headers, "range": "bytes=0-1,5-6"},
                ignored_headers=("last-modified",),
            ),
            RequestCase(
                "static-js-multiple-ranges-one-leading-tab",
                "GET",
                "/public/js/app.js",
                {**origin_headers, "range": "bytes=0-1,\t5-6"},
                ignored_headers=("last-modified",),
            ),
            RequestCase(
                "static-empty-file-open-range",
                "GET",
                "/public/_rust_empty_range_fixture.txt",
                {**origin_headers, "range": "bytes=0-"},
                ignored_headers=("last-modified",),
            ),
            RequestCase(
                "static-empty-file-closed-range",
                "GET",
                "/public/_rust_empty_range_fixture.txt",
                {**origin_headers, "range": "bytes=0-0"},
                ignored_headers=("last-modified",),
            ),
            RequestCase(
                "static-js-if-range-date",
                "GET",
                "/public/js/app.js",
                {
                    **origin_headers,
                    "if-range": "Thu, 01 Jan 2030 00:00:00 GMT",
                    "range": "bytes=0-31",
                },
                ignored_headers=("last-modified",),
            ),
            RequestCase(
                "static-js-if-range-stale-date",
                "GET",
                "/public/js/app.js",
                {
                    **origin_headers,
                    "if-range": "Thu, 01 Jan 1970 00:00:00 GMT",
                    "range": "bytes=0-31",
                },
                ignored_headers=("last-modified",),
            ),
            RequestCase(
                "static-js-if-range-etag",
                "GET",
                "/public/js/app.js",
                {
                    **origin_headers,
                    "if-range": '"starlette-etag"',
                    "range": "bytes=0-31",
                },
                ignored_headers=("last-modified",),
            ),
            RequestCase(
                "head-health", "HEAD", "/health", origin_headers, health_clock_policy=True
            ),
            RequestCase(
                "head-tickers-data-csv",
                "HEAD",
                "/tickers?symbol=FPT&limit=1&ma=false&mode=vn&format=csv&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "head-health-gzip",
                "HEAD",
                "/health",
                {**origin_headers, "accept-encoding": "gzip"},
                health_clock_policy=True,
            ),
            RequestCase(
                "health-zstd-and-gzip",
                "GET",
                "/health",
                {**origin_headers, "accept-encoding": "zstd, gzip"},
                health_clock_policy=True,
            ),
            RequestCase(
                "health-zstd-only",
                "GET",
                "/health",
                {**origin_headers, "accept-encoding": "zstd"},
                health_clock_policy=True,
            ),
            RequestCase(
                "head-static-js",
                "HEAD",
                "/public/js/app.js",
                origin_headers,
                ignored_headers=("last-modified",),
            ),
            RequestCase("head-public-mount-root", "HEAD", "/public", origin_headers),
            RequestCase(
                "head-public-mount-root-gzip",
                "HEAD",
                "/public",
                {**origin_headers, "accept-encoding": "gzip"},
            ),
            *[
                RequestCase(
                    f"static-js-encoding-{encoding}",
                    "GET",
                    "/public/js/app.js",
                    {**origin_headers, "accept-encoding": accept_encoding},
                    ignored_headers=("last-modified",),
                )
                for encoding, accept_encoding in (
                    ("gzip", "gzip"),
                    ("br", "br"),
                    ("deflate", "deflate"),
                    ("weighted", "gzip;q=0.5, br;q=0.8"),
                )
            ],
            RequestCase("invalid-ticker-limit", "GET", "/tickers?limit=nope", origin_headers),
            RequestCase("invalid-ticker-mode", "GET", "/tickers?mode=bad", origin_headers),
            RequestCase(
                "invalid-ticker-group-mode", "GET", "/tickers/group?mode=bad", origin_headers
            ),
            RequestCase(
                "invalid-ticker-name-mode", "GET", "/tickers/name?mode=bad", origin_headers
            ),
            RequestCase(
                "invalid-top-performers-mode",
                "GET",
                "/analysis/top-performers?mode=bad",
                origin_headers,
            ),
            RequestCase(
                "invalid-ma-scores-mode",
                "GET",
                "/analysis/ma-scores-by-sector?mode=bad",
                origin_headers,
            ),
            RequestCase("invalid-rrg-mode", "GET", "/analysis/rrg?mode=bad", origin_headers),
            RequestCase("slash-tickers", "GET", "/tickers/", origin_headers),
            RequestCase("slash-health", "GET", "/health/", origin_headers),
            RequestCase(
                "invalid-rrg-algorithm", "GET", "/analysis/rrg?algorithm=nope", origin_headers
            ),
            RequestCase(
                "duplicate-ticker-limit", "GET", "/tickers?limit=1&limit=2", origin_headers
            ),
            RequestCase(
                "unknown-route",
                "GET",
                "/__differential_not_found__",
                origin_headers,
            ),
            # The packaged SDK still exposes getRawData(), but both current
            # servers have removed the legacy proxy route. Keep this explicit
            # so a future Python-only route or silent Rust restoration cannot
            # drift from the actual Rust contract unnoticed.
            RequestCase(
                "legacy-sdk-raw-route-not-mounted",
                "GET",
                "/raw/ticker_group.json",
                origin_headers,
            ),
            RequestCase("encoded-fixed-health-path", "GET", "/%68ealth", origin_headers),
            RequestCase(
                "encoded-fixed-ticker-group-path", "GET", "/tickers%2Fgroup", origin_headers
            ),
            RequestCase(
                "encoded-fixed-analysis-path", "GET", "/analysis%2Frrg", origin_headers
            ),
            RequestCase(
                "encoded-static-mount-prefix", "GET", "/public%2Fjs/app.js", origin_headers
            ),
            RequestCase(
                "encoded-static-parent-component",
                "GET",
                "/public/js/%2e%2e/index.html",
                origin_headers,
            ),
            RequestCase(
                "encoded-static-parent-component-head",
                "HEAD",
                "/public/js/%2e%2e/index.html",
                origin_headers,
            ),
            *[
                RequestCase(
                    f"fastapi-only-docs-route-{method.lower()}-{path.strip('/').replace('/', '-')}",
                    method,
                    path,
                    {
                        **origin_headers,
                        **({"content-type": "application/json"} if method == "POST" else {}),
                    },
                    json_body={} if method == "POST" else None,
                )
                for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect")
                for method in ("GET", "HEAD", "POST")
            ],
            RequestCase(
                "method-not-allowed",
                "POST",
                "/tickers",
                {**origin_headers, "content-type": "application/json"},
                json_body={},
            ),
            RequestCase(
                "oversized-encoded-fixed-path-checks-body-limit-first",
                "POST",
                "/tickers%2Fgroup",
                {**origin_headers, "content-type": "application/octet-stream"},
                raw_body=oversized_request_body,
            ),
            RequestCase(
                "oversized-sync-extra-segment-checks-body-limit-first",
                "POST",
                "/sync/key/extra",
                {**origin_headers, "content-type": "application/octet-stream"},
                raw_body=oversized_request_body,
            ),
            RequestCase(
                "notes-get-uses-notes-body-limit",
                "GET",
                f"/uploads/{session_id}/markdown/content.md",
                origin_headers,
                raw_body=oversized_request_body,
            ),
            RequestCase(
                "notes-delete-uses-notes-body-limit",
                "DELETE",
                f"/uploads/{session_id}?secret={SYNC_SECRET}",
                origin_headers,
                raw_body=oversized_request_body,
            ),
            RequestCase(
                "cors-preflight",
                "OPTIONS",
                "/tickers",
                {
                    **origin_headers,
                    "access-control-request-method": "GET",
                    "access-control-request-headers": "content-type",
                },
            ),
            RequestCase(
                "bare-options",
                "OPTIONS",
                "/tickers",
            ),
            RequestCase(
                "sync-options-preflight",
                "OPTIONS",
                "/sync/example",
                {
                    **origin_headers,
                    "access-control-request-method": "POST",
                    "access-control-request-headers": "authorization,content-type",
                },
            ),
            RequestCase(
                "notes-delete-options-preflight",
                "OPTIONS",
                f"/uploads/{session_id}",
                {
                    **origin_headers,
                    "access-control-request-method": "DELETE",
                },
            ),
            RequestCase("options-sync-literal-extra-segment", "OPTIONS", "/sync/foo/bar"),
            RequestCase("options-sync-encoded-slash-segment", "OPTIONS", "/sync/foo%2Fbar"),
            RequestCase("options-encoded-sync-prefix", "OPTIONS", "/%73ync/foo"),
            RequestCase("options-notes-literal-extra-session-segment", "OPTIONS", "/uploads/foo/bar"),
            RequestCase("options-notes-encoded-session-slash", "OPTIONS", "/uploads/foo%2Fbar"),
            RequestCase("options-encoded-uploads-prefix", "OPTIONS", f"/%75ploads/{session_id}"),
            RequestCase(
                "options-notes-literal-extra-filename-segment",
                "OPTIONS",
                f"/uploads/{session_id}/markdown/file/extra",
            ),
            RequestCase(
                "options-encoded-notes-markdown-segment",
                "OPTIONS",
                f"/uploads/{session_id}/%6darkdown/file.md",
            ),
            RequestCase(
                "options-notes-encoded-filename-slash",
                "OPTIONS",
                f"/uploads/{session_id}/markdown/file%2Fextra",
            ),
            RequestCase(
                "production-origin-notes-delete-options-preflight",
                "OPTIONS",
                f"/uploads/{session_id}?secret={SYNC_SECRET}",
                {
                    **production_origin_headers,
                    "access-control-request-method": "DELETE",
                },
            ),
            RequestCase("options-public-file", "OPTIONS", "/public/js/app.js", origin_headers),
            RequestCase(
                "static-post-file-method-not-allowed", "POST", "/public/js/app.js", origin_headers
            ),
            RequestCase(
                "static-put-file-method-not-allowed", "PUT", "/public/js/app.js", origin_headers
            ),
            RequestCase("options-malformed-sync-path", "OPTIONS", "/sync/", origin_headers),
            RequestCase(
                "production-origin-refresh-options-preflight",
                "OPTIONS",
                "/tickers/refresh",
                {
                    **production_origin_headers,
                    "access-control-request-method": "POST",
                    "access-control-request-headers": "content-type",
                },
            ),
            RequestCase(
                "production-origin-head-options-preflight",
                "OPTIONS",
                "/health",
                {
                    **production_origin_headers,
                    "access-control-request-method": "HEAD",
                    "access-control-request-headers": "authorization",
                },
            ),
            RequestCase(
                "production-origin-sync-get-options-preflight",
                "OPTIONS",
                f"/sync/{session_id}?secret={SYNC_SECRET}",
                {
                    **production_origin_headers,
                    "access-control-request-method": "GET",
                    "access-control-request-headers": "authorization",
                },
            ),
            RequestCase(
                "production-origin-sync-post-options-preflight",
                "OPTIONS",
                f"/sync/{session_id}",
                {
                    **production_origin_headers,
                    "access-control-request-method": "POST",
                    "access-control-request-headers": "authorization,content-type",
                },
            ),
            RequestCase(
                "production-origin-notes-upload-options-preflight",
                "OPTIONS",
                f"/upload/markdown?session_id={session_id}&secret={SYNC_SECRET}",
                {
                    **production_origin_headers,
                    "access-control-request-method": "POST",
                },
            ),
            *[
                RequestCase(
                    f"refresh-valid-{mode_value}-{interval_value}",
                    "POST",
                    "/tickers/refresh",
                    {**origin_headers, "content-type": "application/json"},
                    json_body={
                        "interval": interval_value,
                        "mode": mode_value,
                        "key": REFRESH_SECRET,
                    },
                )
                for mode_value in ("vn", "crypto", "yahoo", "all")
                for interval_value in ("1D", "1h", "1m")
            ],
            RequestCase(
                "refresh-invalid-interval",
                "POST",
                "/tickers/refresh",
                {**origin_headers, "content-type": "application/json"},
                json_body={"interval": "daily", "mode": "vn", "key": REFRESH_SECRET},
            ),
            RequestCase(
                "refresh-missing-key",
                "POST",
                "/tickers/refresh",
                {**origin_headers, "content-type": "application/json"},
                json_body={"interval": "1D", "mode": "vn"},
            ),
            RequestCase(
                "refresh-wrong-key",
                "POST",
                "/tickers/refresh",
                {**origin_headers, "content-type": "application/json"},
                json_body={"interval": "1D", "mode": "vn", "key": "incorrect-refresh-key"},
            ),
            *[
                RequestCase(
                    f"refresh-json-{name}",
                    "POST",
                    "/tickers/refresh",
                    {**origin_headers, "content-type": "application/json"},
                    raw_body=payload,
                )
                for name, payload in (
                    ("malformed", b"{"),
                    ("empty", b""),
                    ("invalid-utf8", b"\xff"),
                    ("invalid-utf8-ignored-field", b'{"interval":"1D","ignored":"\xff"}'),
                    ("missing-object-end", b'{"interval":"1D"'),
                    ("missing-value", b'{"interval":}'),
                    ("trailing-comma", b'{"interval":"1D",}'),
                    ("trailing-data", b'{"interval":"1D"}{}'),
                    ("missing-required", b'{"mode":"vn"}'),
                    ("wrong-interval-type", b'{"interval":1}'),
                    ("invalid-mode-enum", b'{"interval":"1D","mode":"bad"}'),
                    ("duplicate-field", b'{"interval":"1D","interval":"1h"}'),
                    ("duplicate-before-malformed-value", b'{"interval":"1D","interval":01}'),
                    ("wrong-interval-type-before-malformed-value", b'{"interval":1,'),
                    ("wrong-key-type-before-malformed-value", b'{"interval":"1D","key":1,'),
                    ("invalid-unicode-escape", b'{"interval":"1D","key":"\\u12XZ"}'),
                    ("raw-control-in-string", b'{"interval":"1D","key":"\x01"}'),
                    ("unknown-nested-field", b'{"interval":"1D","ignored":{"x":[1,true]}}'),
                    (
                        "unknown-field-malformed-nested-value",
                        b'{"interval":"1D","ignored":{"x":[1,]}}',
                    ),
                )
            ],
            RequestCase(
                "refresh-json-wrong-content-type",
                "POST",
                "/tickers/refresh",
                {**origin_headers, "content-type": "text/plain"},
                raw_body=b'{"interval":"1D"}',
            ),
            RequestCase(
                "refresh-json-sequence-wrong-type",
                "POST",
                "/tickers/refresh",
                {**origin_headers, "content-type": "application/json"},
                raw_body=b"[1]",
            ),
            RequestCase(
                "refresh-oversized-valid-json",
                "POST",
                "/tickers/refresh",
                {**origin_headers, "content-type": "application/json"},
                raw_body=(b'{"interval":"1D","ignored":"' + b"x" * (5 * 1024 * 1024) + b'"}'),
            ),
            RequestCase(
                "refresh-json-missing-content-type",
                "POST",
                "/tickers/refresh",
                origin_headers,
                raw_body=b'{"interval":"1D"}',
            ),
            RequestCase(
                "sync-write-isolated",
                "POST",
                f"/sync/{session_id}",
                {
                    **production_origin_headers,
                    "authorization": f"Bearer {SYNC_TOKEN}",
                    "content-type": "application/json",
                },
                json_body={
                    "secret": SYNC_SECRET,
                    "value": {
                        "watchlists": {"My List": "ACB,VCB,TCB"},
                        "alerts": [
                            {
                                "id": "alert-fixture",
                                "ticker": "VCB",
                                "created_at": "2026-09-01T00:00:00.000Z",
                                "target_price": 100000,
                                "alert_type": "price_hits",
                                "triggered": False,
                                "note": "web caller fixture",
                                "price_at_creation": 95000,
                                "last_checked_bar_time": "2026-09-01",
                            }
                        ],
                        "chartLines": [
                            {
                                "id": "line-fixture",
                                "ticker": "VCB",
                                "price": 95000,
                                "note": "web caller fixture",
                                "created_at": "2026-09-01T00:00:00.000Z",
                            }
                        ],
                        "exportedAt": "2026-09-01T00:00:00.000Z",
                    },
                },
                volatile_json_fields=("created_at", "updated_at"),
            ),
            RequestCase(
                "sync-write-existing-wrong-secret",
                "POST",
                f"/sync/{session_id}",
                {
                    **production_origin_headers,
                    "authorization": f"Bearer {SYNC_TOKEN}",
                    "content-type": "application/json",
                },
                json_body={"secret": "incorrect-secret", "value": {"overwrote": True}},
            ),
            RequestCase(
                "sync-write-compact-uuid",
                "POST",
                "/sync/c5a32924b1d54c64a114b91a4becc860",
                {
                    **origin_headers,
                    "authorization": f"Bearer {SYNC_TOKEN}",
                    "content-type": "application/json",
                },
                json_body={"secret": SYNC_SECRET, "value": {"canonical": True}},
                volatile_json_fields=("created_at", "updated_at"),
            ),
            RequestCase(
                "sync-read-canonical-uuid",
                "GET",
                f"/sync/c5a32924-b1d5-4c64-a114-b91a4becc860?secret={SYNC_SECRET}",
                {**origin_headers, "authorization": f"Bearer {SYNC_TOKEN}"},
                volatile_json_fields=("created_at", "updated_at"),
            ),
            RequestCase(
                "sync-read-isolated",
                "GET",
                f"/sync/{session_id}?secret={SYNC_SECRET}",
                {**production_origin_headers, "authorization": f"Bearer {SYNC_TOKEN}"},
                volatile_json_fields=("created_at", "updated_at"),
            ),
            RequestCase(
                "sync-read-missing-record",
                "GET",
                f"/sync/fefefefe-fefe-4efe-8efe-fefefefefefe?secret={SYNC_SECRET}",
                {
                    **production_origin_headers,
                    "authorization": f"Bearer {SYNC_TOKEN}",
                },
            ),
            RequestCase(
                "sync-read-wrong-secret",
                "GET",
                f"/sync/{session_id}?secret=incorrect-secret",
                {**origin_headers, "authorization": f"Bearer {SYNC_TOKEN}"},
            ),
            RequestCase(
                "sync-read-wrong-token",
                "GET",
                f"/sync/{session_id}?secret={SYNC_SECRET}",
                {**origin_headers, "authorization": "Bearer incorrect-token"},
            ),
            RequestCase(
                "sync-read-invalid-uuid",
                "GET",
                f"/sync/not-a-uuid?secret={SYNC_SECRET}",
                {**origin_headers, "authorization": f"Bearer {SYNC_TOKEN}"},
            ),
            RequestCase(
                "sync-read-encoded-slash-in-key",
                "GET",
                f"/sync/{session_id}%2Fextra?secret={SYNC_SECRET}",
                {**origin_headers, "authorization": f"Bearer {SYNC_TOKEN}"},
            ),
            RequestCase(
                "sync-read-invalid-utf8-key",
                "GET",
                f"/sync/%FF?secret={SYNC_SECRET}",
                {**origin_headers, "authorization": f"Bearer {SYNC_TOKEN}"},
            ),
            RequestCase(
                "sync-read-duplicate-required-secret",
                "GET",
                f"/sync/{session_id}?secret={SYNC_SECRET}&secret={SYNC_SECRET}",
                {**origin_headers, "authorization": f"Bearer {SYNC_TOKEN}"},
            ),
            RequestCase(
                "sync-read-missing-required-secret",
                "GET",
                f"/sync/{session_id}",
                {**origin_headers, "authorization": f"Bearer {SYNC_TOKEN}"},
            ),
            RequestCase(
                "sync-unauthorized",
                "GET",
                f"/sync/{session_id}?secret={SYNC_SECRET}",
                origin_headers,
            ),
            RequestCase(
                "notes-invalid-utf8-session-path",
                "GET",
                "/uploads/%FF/markdown/content.md",
                origin_headers,
            ),
            RequestCase(
                "notes-invalid-utf8-filename-path",
                "GET",
                f"/uploads/{session_id}/markdown/%FF",
                origin_headers,
            ),
            RequestCase(
                "notes-invalid-utf8-delete-path",
                "DELETE",
                "/uploads/%FF?secret=notes-test-secret",
                origin_headers,
            ),
            *[
                RequestCase(
                    f"sync-json-{name}",
                    "POST",
                    f"/sync/{session_id}",
                    {
                        **origin_headers,
                        "authorization": f"Bearer {SYNC_TOKEN}",
                        "content-type": "application/json",
                    },
                    raw_body=payload,
                )
                for name, payload in (
                    ("malformed", b"{"),
                    ("invalid-utf8", b"\xff"),
                    ("missing-object-end", b'{"secret":"s","value":{}'),
                    ("trailing-comma", b'{"secret":"s","value":{},}'),
                    ("trailing-data", b'{"secret":"s","value":{}}[]'),
                    ("missing-required", b'{"secret":"s"}'),
                    ("wrong-secret-type", b'{"secret":1,"value":{}}'),
                    ("float-canonicalization", b'{"secret":1.00,"value":{}}'),
                    ("large-integer-as-float", b'{"secret":18446744073709551616,"value":{}}'),
                    ("negative-zero-as-float", b'{"secret":-0,"value":{}}'),
                    ("duplicate-field", b'{"secret":"s","secret":"x","value":{}}'),
                    ("duplicate-before-malformed-value", b'{"secret":"s","value":{},"value":01}'),
                    ("wrong-secret-type-before-malformed-value", b'{"secret":1,'),
                    ("valid-scalar-value", b'{"secret":"s","value":null}'),
                    ("too-large-float", b'{"secret":"s","value":1e400}'),
                    ("unpaired-surrogate", b'{"secret":"s","value":"\\ud800"}'),
                    ("high-surrogate-before-non-low", b'{"secret":"s","value":"\\ud800\\u0061"}'),
                    ("lone-low-surrogate", b'{"secret":"s","value":"\\udc00"}'),
                    ("malformed-number", b'{"secret":1e,'),
                    ("leading-zero", b'{"secret":"s","value":01}'),
                    ("exponent-at-eof", b'{"secret":"s","value":1e'),
                    ("decimal-at-eof", b'{"secret":"s","value":1.'),
                    (
                        "excessive-depth",
                        b'{"secret":"s","value":' + b"[" * 130 + b"0" + b"]" * 130 + b"}",
                    ),
                    ("empty-sequence", b"[]"),
                    ("sequence-trailing", b'["s",{}]x'),
                    ("sequence-wrong-secret-type", b"[1,{}]"),
                    ("wrong-root-trailing", b"null x"),
                )
            ],
            RequestCase(
                "sync-oversized-wrong-content-type",
                "POST",
                f"/sync/{session_id}",
                {**origin_headers, "content-type": "text/plain"},
                raw_body=b"x" * (5 * 1024 * 1024 + 1),
            ),
            RequestCase(
                "sync-chunked-oversized-json",
                "POST",
                f"/sync/{session_id}",
                {
                    **origin_headers,
                    "authorization": f"Bearer {SYNC_TOKEN}",
                    "content-type": "application/json",
                },
                chunked_body=(
                    b'{"secret":"s","value":"',
                    b"x" * (5 * 1024 * 1024),
                    b'"}',
                ),
            ),
            RequestCase(
                "health-oversized-declared-body",
                "GET",
                "/health",
                origin_headers,
                raw_body=b"x" * (5 * 1024 * 1024 + 1),
            ),
            RequestCase(
                "health-chunked-oversized-body",
                "GET",
                "/health",
                origin_headers,
                chunked_body=(b"x" * (5 * 1024 * 1024 + 1),),
                health_clock_policy=True,
            ),
            RequestCase(
                "ticker-method-not-allowed-chunked-oversized-body",
                "POST",
                "/tickers",
                origin_headers,
                chunked_body=(b"x" * (5 * 1024 * 1024 + 1),),
            ),
            RequestCase(
                "static-oversized-declared-body",
                "GET",
                "/public/js/app.js",
                origin_headers,
                raw_body=b"x" * (5 * 1024 * 1024 + 1),
                ignored_headers=("last-modified",),
            ),
            RequestCase(
                "static-mount-root-oversized-declared-body",
                "GET",
                "/public",
                origin_headers,
                raw_body=b"x" * (5 * 1024 * 1024 + 1),
            ),
            RequestCase("static-mount-root-trailing-slash", "GET", "/public/", origin_headers),
            RequestCase(
                "sync-json-problem-content-type",
                "POST",
                f"/sync/{session_id}",
                {
                    **origin_headers,
                    "authorization": f"Bearer {SYNC_TOKEN}",
                    "content-type": "application/problem+json",
                },
                raw_body=b'{"secret":"s","value":{"fixture":true}}',
            ),
            RequestCase(
                "sync-json-quoted-parameter-escape-content-type",
                "POST",
                f"/sync/{session_id}",
                {
                    **origin_headers,
                    "authorization": f"Bearer {SYNC_TOKEN}",
                    "content-type": r'application/json;fixture="x\"y"',
                },
                raw_body=b'{"secret":"s","value":{"fixture":true}}',
            ),
            RequestCase(
                "sync-json-empty-quoted-parameter-content-type",
                "POST",
                f"/sync/{session_id}",
                {
                    **origin_headers,
                    "authorization": f"Bearer {SYNC_TOKEN}",
                    "content-type": 'application/json;fixture=""',
                },
                raw_body=b'{"secret":"s","value":{"fixture":true}}',
            ),
            RequestCase(
                "sync-json-empty-terminal-parameter-content-type",
                "POST",
                f"/sync/{session_id}",
                {
                    **origin_headers,
                    "authorization": f"Bearer {SYNC_TOKEN}",
                    "content-type": "application/json;fixture=",
                },
                raw_body=b'{"secret":"s","value":{"fixture":true}}',
            ),
            RequestCase(
                "sync-json-odd-json-suffix-content-type",
                "POST",
                f"/sync/{session_id}",
                {
                    **origin_headers,
                    "authorization": f"Bearer {SYNC_TOKEN}",
                    "content-type": "application/foo++json",
                },
                raw_body=b'{"secret":"s","value":{"fixture":true}}',
            ),
        ]
    )
    cases.extend(
        RequestCase(
            f"top-performers-sort-{metric}-{direction}",
            "GET",
            f"/analysis/top-performers?date={end_date}&mode=vn&ema=true&sort_by={metric}&direction={direction}&limit=7&min_volume=0&snap=false",
            origin_headers,
        )
        for metric in (
            "close_changed",
            "volume",
            "volume_changed",
            "ma10_score",
            "ma20_score",
            "ma50_score",
            "ma100_score",
            "ma200_score",
            "total_money_changed",
        )
        for direction in ("asc", "desc")
    )
    cases.extend(
        RequestCase(
            f"top-performers-latest-{source}-ema",
            "GET",
            f"/analysis/top-performers?mode={source}&ema=true&sort_by=total_money_changed&direction=desc&limit=7&min_volume=0&snap=false",
            origin_headers,
        )
        for source in ("crypto", "yahoo")
    )
    cases.extend(
        RequestCase(
            f"ma-scores-period-{period}-ema-{str(ema).lower()}",
            "GET",
            f"/analysis/ma-scores-by-sector?date={end_date}&mode=vn&ema={str(ema).lower()}&ma_period={period}&min_score=0&top_per_sector=5&snap=false",
            origin_headers,
        )
        for period in (10, 20, 50, 100, 200)
        for ema in (False, True)
    )
    cases.extend(
        RequestCase(
            f"ma-scores-latest-{source}-period-{period}",
            "GET",
            f"/analysis/ma-scores-by-sector?mode={source}&ema=true&ma_period={period}&min_score=0&top_per_sector=5&snap=false",
            origin_headers,
        )
        for source in ("crypto", "yahoo")
        for period in (20, 100, 200)
    )
    cases.extend(
        RequestCase(
            f"rrg-latest-{algorithm}-{source}",
            "GET",
            f"/analysis/rrg?algorithm={algorithm}&mode={source}&trails=5&min_volume=0&benchmark={benchmark}&period=10&ema=true&snap=false",
            origin_headers,
        )
        for algorithm in ("jdk", "mascore")
        for source, benchmark in (("vn", "VNINDEX"), ("crypto", "BTCUSDT"), ("yahoo", "AAPL"))
    )
    cases.extend(
        [
            RequestCase(
                "rrg-latest-jdk-all",
                "GET",
                "/analysis/rrg?algorithm=jdk&mode=all&trails=5&min_volume=0&benchmark=VNINDEX&period=10&ema=false&snap=false",
                origin_headers,
            ),
            RequestCase(
                "rrg-latest-mascore-all",
                "GET",
                "/analysis/rrg?algorithm=mascore&mode=all&trails=5&min_volume=0&benchmark=VNINDEX&period=10&ema=false&snap=false",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-crypto-success",
                "GET",
                f"/analysis/volume-profile?symbol=ETHUSDT&mode=crypto&date={end_date}&bins=20&value_area_pct=70",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-yahoo-success",
                "GET",
                f"/analysis/volume-profile?symbol=AAPL&mode=yahoo&date={end_date}&bins=20&value_area_pct=70",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-yahoo-range",
                "GET",
                f"/analysis/volume-profile?symbol=AAPL&mode=yahoo&start_date={mid_fixture_start}&end_date={mid_fixture_end}&bins=20&value_area_pct=70",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-missing-symbol",
                "GET",
                f"/analysis/volume-profile?mode=vn&date={end_date}",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-missing-date",
                "GET",
                "/analysis/volume-profile?symbol=FPT&mode=vn",
                origin_headers,
            ),
        ]
    )
    method_contract_paths = [
        ("health", "POST", "/health", {**origin_headers, "content-type": "application/json"}),
        ("tickers", "POST", "/tickers", {**origin_headers, "content-type": "application/json"}),
        (
            "groups",
            "POST",
            "/tickers/group",
            {**origin_headers, "content-type": "application/json"},
        ),
        ("names", "POST", "/tickers/name", {**origin_headers, "content-type": "application/json"}),
        (
            "info",
            "POST",
            "/tickers/info?ticker=FPT",
            {**origin_headers, "content-type": "application/json"},
        ),
        ("refresh", "GET", "/tickers/refresh", origin_headers),
        (
            "top-performers",
            "POST",
            "/analysis/top-performers",
            {**origin_headers, "content-type": "application/json"},
        ),
        (
            "ma-scores",
            "POST",
            "/analysis/ma-scores-by-sector",
            {**origin_headers, "content-type": "application/json"},
        ),
        (
            "volume-profile",
            "POST",
            "/analysis/volume-profile?symbol=ETHUSDT&mode=crypto&date=" + end_date,
            {**origin_headers, "content-type": "application/json"},
        ),
        ("rrg", "POST", "/analysis/rrg", {**origin_headers, "content-type": "application/json"}),
        ("explorer", "POST", "/explorer", {**origin_headers, "content-type": "application/json"}),
        (
            "sync",
            "DELETE",
            f"/sync/{session_id}?secret={SYNC_SECRET}",
            {**origin_headers, "authorization": f"Bearer {SYNC_TOKEN}"},
        ),
        ("upload", "GET", "/upload/markdown", origin_headers),
        (
            "notes-markdown",
            "POST",
            f"/uploads/{session_id}/markdown/content.md",
            {**origin_headers, "content-type": "application/json"},
        ),
        ("notes-session", "GET", f"/uploads/{session_id}?secret={SYNC_SECRET}", origin_headers),
        (
            "public-root-post",
            "POST",
            "/public",
            {**origin_headers, "content-type": "application/json"},
        ),
        (
            "public-root-put",
            "PUT",
            "/public",
            {**origin_headers, "content-type": "application/json"},
        ),
        ("public-root-delete", "DELETE", "/public", origin_headers),
        ("public-root-patch", "PATCH", "/public", origin_headers),
        ("public-file-delete", "DELETE", "/public/js/app.js", origin_headers),
        ("public-file-patch", "PATCH", "/public/js/app.js", origin_headers),
    ]
    cases.extend(
        RequestCase(f"method-not-allowed-{name}", method, path, headers)
        for name, method, path, headers in method_contract_paths
    )
    head_contract_paths = [
        ("explorer", "/explorer", origin_headers),
        ("refresh-post-only", "/tickers/refresh", origin_headers),
        ("upload-post-only", "/upload/markdown", origin_headers),
        ("notes-session-delete-only", f"/uploads/{head_notes_session_id}", origin_headers),
        ("groups", "/tickers/group", origin_headers),
        ("names", "/tickers/name", origin_headers),
        ("info", "/tickers/info?ticker=FPT", origin_headers),
        (
            "notes-markdown",
            f"/uploads/{head_notes_session_id}/markdown/content.md",
            origin_headers,
        ),
        ("public-mount-root", "/public", origin_headers),
        (
            "top-performers",
            f"/analysis/top-performers?date={end_date}&mode=vn&limit=10&min_volume=0&snap=false",
            origin_headers,
        ),
        (
            "ma-scores",
            f"/analysis/ma-scores-by-sector?date={end_date}&mode=vn&ma_period=20&min_score=0&top_per_sector=5&snap=false",
            origin_headers,
        ),
        (
            "volume-profile",
            f"/analysis/volume-profile?symbol=ETHUSDT&mode=crypto&date={end_date}&bins=20&value_area_pct=70",
            origin_headers,
        ),
        (
            "rrg",
            f"/analysis/rrg?algorithm=jdk&mode=vn&trails=5&min_volume=0&benchmark=VNINDEX&period=10&date={end_date}&ema=true&snap=false",
            origin_headers,
        ),
        (
            "sync",
            f"/sync/{session_id}?secret={SYNC_SECRET}",
            {**origin_headers, "authorization": f"Bearer {SYNC_TOKEN}"},
        ),
    ]
    cases.extend(
        RequestCase(
            f"head-contract-{name}",
            "HEAD",
            path,
            headers,
            volatile_json_fields=("created_at", "updated_at") if name == "sync" else (),
        )
        for name, path, headers in head_contract_paths
    )

    # Adversarial query-extractor inputs are paired against Rust too. Local
    # Python-only validation tests cannot prove Axum/FastAPI rejection parity.
    invalid_boolean_queries = [
        ("ticker-legacy", "/tickers?symbol=FPT&legacy=yes"),
        ("ticker-cache", "/tickers?symbol=FPT&cache=yes"),
        ("ticker-redis", "/tickers?symbol=FPT&redis=yes"),
        ("ticker-ma", "/tickers?symbol=FPT&ma=yes"),
        ("ticker-ema", "/tickers?symbol=FPT&ema=yes"),
        ("ticker-ma-uppercase", "/tickers?symbol=FPT&ma=TRUE"),
        ("ticker-snap", "/tickers?symbol=FPT&snap=yes"),
        ("top-performers-ema", "/analysis/top-performers?ema=yes"),
        ("top-performers-snap", "/analysis/top-performers?snap=yes"),
        ("ma-scores-ema", "/analysis/ma-scores-by-sector?ema=yes"),
        (
            "ma-scores-above-threshold-only",
            "/analysis/ma-scores-by-sector?above_threshold_only=yes",
        ),
        ("ma-scores-snap", "/analysis/ma-scores-by-sector?snap=yes"),
        ("rrg-ema", "/analysis/rrg?ema=yes"),
        ("rrg-snap", "/analysis/rrg?snap=yes"),
        ("rrg-snap-uppercase", "/analysis/rrg?snap=False"),
    ]
    cases.extend(
        RequestCase(f"invalid-boolean-{name}", "GET", path, origin_headers)
        for name, path in invalid_boolean_queries
    )
    blank_numeric_queries = [
        ("ticker-limit", "/tickers?limit="),
        ("top-performers-limit", "/analysis/top-performers?limit="),
        ("top-performers-min-volume", "/analysis/top-performers?min_volume="),
        ("ma-scores-period", "/analysis/ma-scores-by-sector?ma_period="),
        ("ma-scores-min-score", "/analysis/ma-scores-by-sector?min_score="),
        ("ma-scores-top-per-sector", "/analysis/ma-scores-by-sector?top_per_sector="),
        ("rrg-period", "/analysis/rrg?period="),
        ("rrg-trails", "/analysis/rrg?trails="),
        ("rrg-min-volume", "/analysis/rrg?min_volume="),
        ("volume-profile-bins", "/analysis/volume-profile?symbol=FPT&bins="),
        (
            "volume-profile-value-area",
            "/analysis/volume-profile?symbol=FPT&value_area_pct=",
        ),
    ]
    cases.extend(
        RequestCase(f"blank-numeric-{name}", "GET", path, origin_headers)
        for name, path in blank_numeric_queries
    )
    cases.extend(
        [
            RequestCase(
                "ticker-limit-i64-minimum",
                "GET",
                "/tickers?symbol=FPT&limit=-9223372036854775808&mode=vn&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-limit-i64-maximum",
                "GET",
                "/tickers?symbol=FPT&limit=9223372036854775807&mode=vn&ma=false&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "top-performers-limit-usize-maximum",
                "GET",
                "/analysis/top-performers?mode=vn&limit=18446744073709551615&min_volume=0",
                origin_headers,
            ),
            RequestCase(
                "top-performers-min-volume-u64-maximum",
                "GET",
                "/analysis/top-performers?mode=vn&limit=10&min_volume=18446744073709551615",
                origin_headers,
            ),
            RequestCase(
                "ma-scores-period-u32-maximum",
                "GET",
                "/analysis/ma-scores-by-sector?mode=vn&ma_period=4294967295&top_per_sector=5",
                origin_headers,
            ),
            RequestCase(
                "ma-scores-top-per-sector-usize-maximum",
                "GET",
                "/analysis/ma-scores-by-sector?mode=vn&ma_period=20&top_per_sector=18446744073709551615",
                origin_headers,
            ),
            RequestCase(
                "rrg-period-usize-maximum-clamp",
                "GET",
                "/analysis/rrg?algorithm=jdk&mode=vn&benchmark=VNINDEX&period=18446744073709551615&trails=5",
                origin_headers,
            ),
            RequestCase(
                "rrg-trails-usize-maximum-clamp",
                "GET",
                "/analysis/rrg?algorithm=jdk&mode=vn&benchmark=VNINDEX&period=10&trails=18446744073709551615",
                origin_headers,
            ),
            RequestCase(
                "rrg-min-volume-i64-minimum",
                "GET",
                "/analysis/rrg?algorithm=jdk&mode=vn&benchmark=VNINDEX&period=10&trails=5&min_volume=-9223372036854775808",
                origin_headers,
            ),
            RequestCase(
                "rrg-min-volume-i64-maximum-filter",
                "GET",
                "/analysis/rrg?algorithm=jdk&mode=vn&benchmark=VNINDEX&period=10&trails=5&min_volume=9223372036854775807",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-bins-usize-maximum-clamp",
                "GET",
                f"/analysis/volume-profile?symbol=PROFILEUSDT&mode=crypto&date={end_date}&bins=18446744073709551615&value_area_pct=70",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-exact-low-boundaries",
                "GET",
                f"/analysis/volume-profile?symbol=PROFILEUSDT&mode=crypto&date={end_date}&bins=2&value_area_pct=70",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-exact-high-boundaries",
                "GET",
                f"/analysis/volume-profile?symbol=PROFILEUSDT&mode=crypto&date={end_date}&bins=200&value_area_pct=90",
                origin_headers,
            ),
            RequestCase(
                "volume-profile-value-area-minimum-boundary",
                "GET",
                f"/analysis/volume-profile?symbol=PROFILEUSDT&mode=crypto&date={end_date}&bins=200&value_area_pct=60",
                origin_headers,
            ),
            RequestCase(
                "ticker-csv-format-uppercase",
                "GET",
                "/tickers?symbol=FPT&limit=1&mode=vn&ma=false&format=CSV",
                origin_headers,
            ),
            RequestCase(
                "ticker-csv-format-mixed-case",
                "GET",
                "/tickers?symbol=FPT&limit=1&mode=vn&ma=false&format=cSv",
                origin_headers,
            ),
            RequestCase(
                "ticker-json-format-uppercase",
                "GET",
                "/tickers?symbol=FPT&limit=1&mode=vn&ma=false&format=JSON",
                origin_headers,
            ),
            RequestCase(
                "ticker-ema-ignored-when-ma-disabled",
                "GET",
                "/tickers?symbol=FPT&limit=3&mode=vn&ma=false&ema=true&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
            RequestCase(
                "ticker-legacy-scaled-with-ma-enabled",
                "GET",
                "/tickers?symbol=FPT&limit=3&mode=vn&legacy=true&ma=true&ema=true&format=json&redis=false&snap=false&cache=false",
                origin_headers,
            ),
        ]
    )
    return cases


@pytest.mark.skipif(
    os.environ.get("AIPA_RUN_RUST_DIFFERENTIAL") != "1"
    and os.environ.get("AIPA_REQUIRE_RUST_DIFFERENTIAL") != "1",
    reason="set AIPA_RUN_RUST_DIFFERENTIAL=1 to run isolated Rust/Postgres HTTP differential checks",
)
def test_rust_and_python_http_endpoints_match_on_shared_fixture():
    if (
        os.environ.get("AIPA_REQUIRE_RUST_DIFFERENTIAL") == "1"
        and os.environ.get("AIPA_RUN_RUST_DIFFERENTIAL") != "1"
    ):
        pytest.fail(
            "AIPA_REQUIRE_RUST_DIFFERENTIAL=1 requires AIPA_RUN_RUST_DIFFERENTIAL=1",
            pytrace=False,
        )
    _build_current_rust_binary()
    provider = _postgres_provider()
    if provider is None:
        _skip_or_fail_required_differential(_postgres_skip_reason())

    with tempfile.TemporaryDirectory(prefix="aipa-rust-python-diff-") as temp_name:
        root = Path(temp_name)
        catalog = _write_shared_catalog(root)
        rows = _candle_rows()
        web_date_control_start = (FIXTURE_END.date() - timedelta(days=10)).isoformat()
        web_date_control_end = (FIXTURE_END.date() + timedelta(days=10)).isoformat()
        mid_fixture_start = FIXTURE_DATES[650].date().isoformat()
        mid_fixture_end = FIXTURE_DATES[654].date().isoformat()
        web_date_control_dates = [
            stamp.date().isoformat()
            for stamp in FIXTURE_DATES
            if web_date_control_start
            <= stamp.date().isoformat()
            <= web_date_control_end
        ]
        current_daily_candles = {
            (row.source, row.symbol): row
            for row in rows
            if row.interval == "1D" and row.time == int(FIXTURE_END.timestamp())
        }
        fixture_daily_by_key = {
            (
                row.source,
                row.symbol,
                datetime.fromtimestamp(row.time, UTC).date().isoformat(),
            ): row
            for row in rows
            if row.interval == "1D"
        }
        use_live_redis = os.environ.get("AIPA_USE_LIVE_REDIS_DIFFERENTIAL") == "1"
        use_unreachable_redis = (
            os.environ.get("AIPA_USE_UNREACHABLE_REDIS_DIFFERENTIAL") == "1"
        )
        use_permission_error_redis = (
            os.environ.get("AIPA_USE_REDIS_PERMISSION_ERROR_DIFFERENTIAL") == "1"
        )
        if sum((use_live_redis, use_unreachable_redis, use_permission_error_redis)) > 1:
            raise RuntimeError("Redis differential modes cannot be enabled together")
        rust_redis_url = ""
        python_redis_url = ""
        redis_process = None
        seed_file = root / "seed.sql"
        _write_postgres_seed(seed_file, rows)
        rust_client = None
        rust_process = None
        postgres_handle = None
        try:
            postgres_handle = _start_postgres(root, provider)
            _apply_rust_migrations(root, postgres_handle.database_url)
            _seed_postgres(postgres_handle, seed_file)
            if use_live_redis:
                rust_redis_url, redis_process = _start_redis(root)
                python_redis_url = rust_redis_url.rsplit("/", 1)[0] + "/1"
                _seed_redis(rust_redis_url, rows)
                _seed_redis(python_redis_url, rows)
                _seed_redis_boundary_members(rust_redis_url)
                _seed_redis_boundary_members(python_redis_url)
                _seed_analysis_snapshots(rust_redis_url)
                _seed_analysis_snapshots(python_redis_url)
            elif use_unreachable_redis:
                # Port zero is reserved for kernel-selected ephemeral binds and
                # cannot be claimed as a listening destination. This keeps an
                # unrelated local service from making the outage case reachable.
                rust_redis_url = "redis://127.0.0.1:0/0"
                python_redis_url = "redis://127.0.0.1:0/1"
            elif use_permission_error_redis:
                import redis

                admin_url, redis_process = _start_redis(root)
                username = "aipa-parity-denied"
                password = "aipa-parity-secret"
                admin = redis.Redis.from_url(admin_url)
                try:
                    admin.execute_command(
                        "ACL",
                        "SETUSER",
                        username,
                        "on",
                        f">{password}",
                        "allkeys",
                        "-@all",
                        "+ping",
                        "+hello",
                        "+select",
                        "+client|setinfo",
                    )
                finally:
                    admin.close()
                authenticated_url = admin_url.replace(
                    "redis://", f"redis://{username}:{password}@", 1
                )
                rust_redis_url = authenticated_url
                python_redis_url = authenticated_url.rsplit("/", 1)[0] + "/1"
            identity_order_path = root / "rust-ticker-identity-order.csv"
            _export_rust_ticker_identity_order(postgres_handle, identity_order_path)
            with identity_order_path.open(encoding="utf-8", newline="") as handle:
                identities = {
                    (row["source"], row["symbol"]): int(row["id"]) for row in csv.DictReader(handle)
                }
            assert identities[("sjc", "COLL")] < identities[("yahoo", "COLL")]
            rust_client, rust_process = _start_rust(
                root, postgres_handle.database_url, redis_url=rust_redis_url
            )
            python_public_dir = root / "python-public"
            shutil.copytree(Settings().public_dir, python_public_dir)
            (python_public_dir / "_rust_empty_range_fixture.txt").touch()

            settings = replace(
                Settings(),
                database=root / "python.sqlite3",
                cache_dir=root / "python-cache",
                object_dir=root / "python-objects",
                uploads_dir=root / "python-uploads",
                uploads_max_bytes=6 * 1024 * 1024,
                uploads_max_sessions=4,
                rust_ticker_identity_order=identity_order_path,
                archive_backend="filesystem",
                catalog_dir=catalog,
                company_info=catalog / "company_info.json",
                public_dir=python_public_dir,
                sync_tokens=(SYNC_TOKEN,),
                refresh_secret=REFRESH_SECRET,
                cors_origins=(WEB_ORIGIN, PRODUCTION_WEB_ORIGIN),
                max_limit=40,
                single_limit=10000,
                redis_url=python_redis_url or None,
                # Keep normal retention settings in both runs. The HTTP route
                # must select the cache only when a real Redis client exists.
                redis_daily_max_size=5000,
                redis_hourly_max_size=30000,
                redis_minute_max_size=20000,
            )
            app = create_app(settings)
            session_id = str(uuid.uuid4())
            legacy_session_id = str(uuid.uuid4())
            head_notes_session_id = str(uuid.uuid4())
            byte_limited_session_id = str(uuid.uuid4())
            max_file_session_id = str(uuid.uuid4())
            oversized_file_session_id = str(uuid.uuid4())
            quota_session_id = str(uuid.uuid4())
            session_limited_session_id = str(uuid.uuid4())
            symlink_session_id = str(uuid.uuid4())
            symlink_markdown_id = str(uuid.uuid4())
            symlink_metadata_id = str(uuid.uuid4())
            symlink_content_id = str(uuid.uuid4())
            symlink_populated_markdown_id = str(uuid.uuid4())
            _seed_legacy_notes(root, legacy_session_id)
            symlinked_notes = {
                "session": symlink_session_id,
                "markdown": symlink_markdown_id,
                "metadata": symlink_metadata_id,
                "content": symlink_content_id,
                "populated_markdown": symlink_populated_markdown_id,
            }
            failures = []
            with TestClient(app) as python_client:
                _seed_catalog_symbols(app.state.repo)
                app.state.repo.put(rows)
                # The repository stamps imported rows with wall-clock ingest
                # time. Seed the same fixed ingest timestamp as PostgreSQL so
                # health last-sync values compare the same fixture revision.
                fixture_updated_at_ns = int(
                    (FIXTURE_END + timedelta(days=1)).timestamp() * 1_000_000_000
                )
                with app.state.repo.connect() as connection:
                    connection.execute("UPDATE candles SET updated_at=?", (fixture_updated_at_ns,))
                    connection.execute(
                        "UPDATE candle_health SET last_sync=?", (fixture_updated_at_ns,)
                    )
                app.state.health_snapshot.update(app.state.repo.health_summary())
                rust_public_dir = RUST_ROOT / "public"
                rust_assets = {
                    path.relative_to(rust_public_dir).as_posix(): path.read_bytes()
                    for path in rust_public_dir.rglob("*")
                    if path.is_file()
                }
                python_assets = {
                    path.relative_to(python_public_dir).as_posix(): path.read_bytes()
                    for path in python_public_dir.rglob("*")
                    if path.is_file() and path.name != "_rust_empty_range_fixture.txt"
                }
                if rust_assets != python_assets:
                    failures.append(
                        "packaged Python public assets differ from the Rust public tree"
                    )
                cases = _cases(
                    session_id,
                    legacy_session_id,
                    head_notes_session_id,
                    byte_limited_session_id,
                    max_file_session_id,
                    oversized_file_session_id,
                    quota_session_id,
                    session_limited_session_id,
                    symlink_session_id,
                    symlink_markdown_id,
                    symlink_metadata_id,
                    symlink_content_id,
                    symlink_populated_markdown_id,
                )
                sync_case_values = {
                    case.name: case.json_body["value"]
                    for case in cases
                    if case.name in {"sync-write-isolated", "sync-write-compact-uuid"}
                    and isinstance(case.json_body, dict)
                    and isinstance(case.json_body.get("value"), dict)
                }
                sync_case_values["sync-read-isolated"] = sync_case_values["sync-write-isolated"]
                sync_case_values["sync-read-canonical-uuid"] = {"canonical": True}
                if use_live_redis:
                    cases = (
                        [
                            RequestCase(
                                "ticker-explicit-snapshot-hit",
                                "GET",
                                "/tickers?symbol=VCB&limit=1&ma=true&ema=false&mode=vn&format=json&redis=true&snap=true&cache=false",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "top-performers-explicit-joined-snapshot-hit",
                                "GET",
                                "/analysis/top-performers?mode=vn&sort_by=close_changed&direction=desc&limit=10&min_volume=0&snap=true",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "ma-scores-explicit-null-indicator-snapshot-hit",
                                "GET",
                                "/analysis/ma-scores-by-sector?mode=vn&ma_period=20&min_score=0&top_per_sector=10&snap=true",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "ticker-redis-signed-limit-negative-no-end",
                                "GET",
                                "/tickers?symbol=DIFF&limit=-2&ma=false&mode=vn&format=json&redis=true&snap=false&cache=false",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "ticker-redis-signed-limit-zero-no-end",
                                "GET",
                                "/tickers?symbol=DIFF&limit=-1&ma=false&mode=vn&format=json&redis=true&snap=false&cache=false",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "ticker-redis-signed-limit-negative-end",
                                "GET",
                                f"/tickers?symbol=DIFF&end_date={FIXTURE_END.date()}&limit=-2&ma=false&mode=vn&format=json&redis=true&snap=false&cache=false",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                        ]
                        + cases
                        + [
                            RequestCase(
                                "ticker-stale-snapshot-hit",
                                "GET",
                                "/tickers?symbol=VCB&limit=1&ma=true&ema=false&mode=vn&format=json&redis=true&snap=true&cache=false",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "ticker-incomplete-snapshot-miss",
                                "GET",
                                "/tickers?symbol=VCB&symbol=FPT&limit=2&ma=true&mode=vn&format=json&redis=true&snap=true&cache=false",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "ticker-malformed-snapshot-miss",
                                "GET",
                                "/tickers?symbol=FPT&limit=3&ma=true&mode=vn&format=json&redis=true&snap=true&cache=false",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "ticker-snapshot-hash-error-fallback",
                                "GET",
                                "/tickers?symbol=FPT&limit=3&ma=true&mode=vn&format=json&redis=true&snap=true&cache=false",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "ticker-redis-ticker-list-subset",
                                "GET",
                                "/tickers?limit=1&mode=all&ma=false&format=json&snap=false&cache=false",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "ticker-redis-ticker-list-source-priority",
                                "GET",
                                "/tickers?symbol=DUPL&limit=1&mode=all&ma=false&format=json&snap=false&cache=false",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "ticker-redis-ticker-list-absent",
                                "GET",
                                "/tickers?limit=1&mode=vn&ma=false&format=json&redis=true&snap=false&cache=false",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "ticker-redis-ticker-list-malformed",
                                "GET",
                                "/tickers?limit=1&mode=vn&ma=false&format=json&redis=true&snap=false&cache=false",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "ticker-redis-ticker-list-empty",
                                "GET",
                                "/tickers?limit=1&mode=vn&ma=false&format=json&redis=true&snap=false&cache=false",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "ticker-redis-ticker-list-all-empty",
                                "GET",
                                "/tickers?limit=1&mode=all&ma=false&format=json&snap=false&cache=false",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "ticker-redis-ticker-list-incomplete-explicit",
                                "GET",
                                "/tickers?symbol=DUPL&symbol=FPT&limit=1&mode=all&ma=false&format=json&snap=false&cache=false",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "ticker-partial-redis-batch",
                                "GET",
                                "/tickers?symbol=VCB&symbol=FPT&limit=1&ma=false&mode=vn&format=json&redis=true&snap=false&cache=false",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "ticker-empty-redis-batch",
                                "GET",
                                "/tickers?symbol=VCB&symbol=FPT&limit=1&ma=false&mode=vn&format=json&redis=true&snap=false&cache=false",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "rrg-mascore-valid-stale-joined-snapshot",
                                "GET",
                                "/analysis/rrg?algorithm=mascore&mode=vn&trails=0&min_volume=0&ema=false&snap=true",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "rrg-mascore-null-required-ohlc-joined-snapshot",
                                "GET",
                                "/analysis/rrg?algorithm=mascore&mode=vn&trails=0&min_volume=0&ema=false&snap=true",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "rrg-mascore-duplicate-member-raw-limit-seed-depth",
                                "GET",
                                f"/analysis/rrg?algorithm=mascore&mode=vn&trails=0&min_volume=0&date={FIXTURE_END.date()}&ema=false&snap=false",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "top-performers-stale-joined-snapshot",
                                "GET",
                                "/analysis/top-performers?mode=vn&sort_by=close_changed&direction=desc&limit=50&min_volume=0&snap=true",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "analysis-mode-all-joined-snapshot-top-performers-complete",
                                "GET",
                                "/analysis/top-performers?mode=all&sort_by=close_changed&direction=desc&limit=50&min_volume=0&snap=true",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "analysis-mode-all-joined-snapshot-top-performers-incomplete-yahoo-fallback",
                                "GET",
                                "/analysis/top-performers?mode=all&sort_by=close_changed&direction=desc&limit=50&min_volume=0&snap=true",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "analysis-mode-all-joined-snapshot-ma-scores-complete",
                                "GET",
                                "/analysis/ma-scores-by-sector?mode=all&ma_period=20&min_score=0&top_per_sector=50&snap=true",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "analysis-mode-all-joined-snapshot-ma-scores-incomplete-yahoo-fallback",
                                "GET",
                                "/analysis/ma-scores-by-sector?mode=all&ma_period=20&min_score=0&top_per_sector=50&snap=true",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "analysis-mode-all-joined-snapshot-rrg-complete",
                                "GET",
                                "/analysis/rrg?algorithm=mascore&mode=all&trails=0&min_volume=0&ema=false&snap=true",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "analysis-mode-all-joined-snapshot-rrg-incomplete-yahoo-fallback",
                                "GET",
                                "/analysis/rrg?algorithm=mascore&mode=all&trails=0&min_volume=0&ema=false&snap=true",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "volume-profile-partial-tail-overlap",
                                "GET",
                                f"/analysis/volume-profile?symbol=FPT&mode=vn&date={FIXTURE_END.date()}&bins=20&value_area_pct=70",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "volume-profile-tail-no-overlap",
                                "GET",
                                f"/analysis/volume-profile?symbol=FPT&mode=vn&date={FIXTURE_DATES[-2].date()}&bins=20&value_area_pct=70",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "volume-profile-restore-tail-after-partial-cases",
                                "GET",
                                f"/analysis/volume-profile?symbol=FPT&mode=vn&date={FIXTURE_END.date()}&bins=20&value_area_pct=70",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "analysis-mode-all-tie-top-performers",
                                "GET",
                                "/analysis/top-performers?mode=all&sort_by=close_changed&direction=desc&limit=50&min_volume=0&snap=true",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "analysis-mode-all-tie-ma-scores",
                                "GET",
                                "/analysis/ma-scores-by-sector?mode=all&ma_period=20&min_score=0&top_per_sector=50&snap=true",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                        ]
                    )
                    cases.extend(
                        [
                            RequestCase(
                                "web-playground-native-weekly-range-csv",
                                "GET",
                                f"/tickers?symbol=FPT&interval=1W&start_date={FIXTURE_DATES[DAILY_BARS - 500].date()}&end_date={FIXTURE_END.date()}&mode=vn&ema=true&format=csv",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "web-playground-native-weekly-range-csv-postgres",
                                "GET",
                                f"/tickers?symbol=FPT&interval=1W&start_date={FIXTURE_DATES[DAILY_BARS - 500].date()}&end_date={FIXTURE_END.date()}&mode=vn&ema=true&format=csv&redis=false&snap=false&cache=false",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "ticker-cache-comma-literal-first",
                                "GET",
                                "/tickers?symbol=DUPL%2CFPT&limit=13&mode=yahoo&ma=false&format=json&redis=false&snap=false&cache=true",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "ticker-cache-comma-repeated-second",
                                "GET",
                                "/tickers?symbol=DUPL&symbol=FPT&limit=13&mode=yahoo&ma=false&format=json&redis=false&snap=false&cache=true",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "ticker-cache-comma-repeated-first",
                                "GET",
                                "/tickers?symbol=DUPL&symbol=FPT&limit=14&mode=yahoo&ma=false&format=json&redis=false&snap=false&cache=true",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                            RequestCase(
                                "ticker-cache-comma-literal-second",
                                "GET",
                                "/tickers?symbol=DUPL%2CFPT&limit=14&mode=yahoo&ma=false&format=json&redis=false&snap=false&cache=true",
                                {"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            ),
                        ]
                    )
                elif use_unreachable_redis:
                    unreachable_redis_cases = {
                        "ticker-redis-end-bounded-zset",
                        "ticker-redis-snapshot-cross-backend",
                        "ticker-redis-multi-symbol-snapshot-outage",
                        "production-origin-data-ticker-json",
                        "production-origin-data-top-performers",
                        "production-origin-data-ma-scores",
                        "production-origin-data-rrg",
                    }
                    cases = [case for case in cases if case.name in unreachable_redis_cases]
                elif use_permission_error_redis:
                    permission_error_cases = {
                        "ticker-redis-snapshot-cross-backend",
                        "ticker-redis-multi-symbol-snapshot-outage",
                    }
                    cases = [case for case in cases if case.name in permission_error_cases]
                expected_notes_status = {
                    "notes-upload-chunked-over-limit-invalid-content-type": 400,
                    "notes-upload-chunked-over-limit-valid-multipart-content-type": 400,
                    "notes-upload-chunked-over-limit-empty-multipart-boundary": 400,
                    "notes-upload-chunked-over-limit-invalid-session-id": 400,
                    "notes-upload-chunked-over-limit-short-secret": 400,
                    "notes-upload-bad-name": 400,
                    "notes-upload-wrong-field-name": 422,
                    "notes-upload-multiple-files": 400,
                    "notes-upload-invalid-utf8": 415,
                    "notes-upload-empty-multipart": 422,
                    "notes-upload-zero-byte-multipart-body": 400,
                    "notes-upload-zero-byte-empty-multipart-boundary": 400,
                    "notes-invalid-attempts-no-session": 404,
                    "notes-fetch-legacy-plaintext-secret": 200,
                    "notes-fetch-duplicate-optional-secret": 400,
                    "notes-upload-markdown": 200,
                    "notes-fetch-leading-dot-filename": 400,
                    "notes-fetch-doubledot-filename": 400,
                    "notes-fetch-encoded-dotdot-filename": 400,
                    "notes-fetch-encoded-backslash-filename": 400,
                    "notes-fetch-encoded-slash-filename": 400,
                    "notes-fetch-del-control-filename": 400,
                    "notes-fetch-c1-control-filename": 400,
                    "notes-fetch-encoded-session-slash": 400,
                    "notes-fetch-literal-nested-session": 404,
                    "notes-fetch-encoded-fixed-slash": 404,
                    "notes-fetch-encoded-fixed-letter": 404,
                    "notes-fetch-empty-filename": 404,
                    "notes-fetch-duplicate-secret-encoded-slash-filename": 400,
                    "notes-fetch-duplicate-secret-encoded-fixed-slash": 404,
                    "notes-fetch-literal-nested-filename": 404,
                    "notes-upload-maximum-markdown-size": 200,
                    "notes-fetch-maximum-markdown-size": 200,
                    "notes-upload-over-maximum-markdown-size": 413,
                    "notes-upload-duplicate-required-session-id": 400,
                    "notes-upload-duplicate-required-secret": 400,
                    "notes-fetch-markdown": 200,
                    "notes-quota-byte-limit": 507,
                    "notes-quota-fourth-session": 200,
                    "notes-quota-session-limit": 507,
                    "notes-delete-session": 200,
                    "notes-delete-duplicate-required-secret": 400,
                    "notes-delete-encoded-session-slash": 400,
                    "notes-delete-encoded-session-slash-duplicate-secret": 400,
                    "notes-delete-literal-nested-session": 404,
                    "notes-quota-exact-byte-limit": 200,
                    "notes-quota-one-byte-over-limit": 507,
                    "notes-delete-third-session": 200,
                    "notes-delete-legacy-session": 200,
                    "notes-upload-symlinked-populated-markdown-directory": 500,
                    "notes-upload-symlinked-session": 500,
                    "notes-fetch-symlinked-session": 500,
                    "notes-delete-symlinked-session": 500,
                    "notes-upload-symlinked-markdown-directory": 500,
                    "notes-fetch-symlinked-markdown-directory": 500,
                    "notes-delete-symlinked-markdown-directory": 200,
                    "notes-upload-symlinked-metadata-file": 500,
                    "notes-fetch-symlinked-metadata-file": 404,
                    "notes-delete-symlinked-metadata-file": 404,
                    "notes-fetch-symlinked-content-file": 500,
                    "notes-delete-symlinked-content-file": 200,
                }
                expected_analysis_query_status = {
                    "top-performers": 200,
                    "top-performers-default-query-options": 200,
                    "top-performers-invalid-date-fallback": 200,
                    "top-performers-default-snapshot-option": 200,
                    "analysis-top-performers-gzip": 200,
                    "ma-scores-by-sector": 200,
                    "ma-scores-default-query-options": 200,
                    "ma-scores-by-sector-default-snapshot-option": 200,
                    "ma-scores-invalid-date-fallback": 200,
                    "rrg-jdk": 200,
                    "rrg-default-query-options": 200,
                    "rrg-mascore": 200,
                    "rrg-below-period-minimum-clamp": 200,
                    "rrg-jdk-mode-all": 200,
                    "rrg-jdk-mode-all-duplicate-benchmark": 200,
                    "rrg-jdk-mode-all-yahoo-benchmark": 200,
                    "rrg-jdk-mode-all-crypto-benchmark": 200,
                    "volume-profile": 200,
                    "volume-profile-default-query-options": 200,
                    "volume-profile-widget-vn-default-bins": 200,
                    "volume-profile-widget-crypto-default-bins": 200,
                    "volume-profile-low-priced-crypto-poc-tolerance": 200,
                    "volume-profile-widget-yahoo-default-bins": 200,
                    "production-origin-data-top-performers-raw": 200,
                    "production-origin-data-top-performers": 200,
                    "production-origin-data-ma-scores-raw": 200,
                    "production-origin-data-ma-scores": 200,
                    "production-origin-data-rrg": 200,
                    "production-origin-data-rrg-crypto": 200,
                    "production-origin-data-rrg-yahoo": 200,
                    "production-origin-data-rrg-mascore": 200,
                    "production-origin-data-rrg-mascore-crypto": 200,
                    "production-origin-data-rrg-mascore-yahoo": 200,
                    "production-origin-data-rrg-jdk-all": 200,
                    "production-origin-data-rrg-jdk-all-raw": 200,
                    "production-origin-data-rrg-mascore-all": 200,
                    "production-origin-data-rrg-mascore-all-raw": 200,
                    "rrg-jdk-widget-default-date-after-fixture": 200,
                    "rrg-jdk-widget-default-date-after-fixture-crypto": 200,
                    "rrg-jdk-widget-default-date-after-fixture-yahoo": 200,
                    "production-origin-data-volume-profile": 200,
                    "production-origin-data-volume-profile-range": 200,
                    "top-performers-limit-overflow": 400,
                    "top-performers-negative-limit": 400,
                    "top-performers-negative-minimum-volume": 400,
                    "top-performers-duplicate-optional-sector": 400,
                    "top-performers-duplicate-mode": 400,
                    "ma-scores-period-u32-overflow": 400,
                    "ma-scores-top-per-sector-usize-overflow": 400,
                    "ma-scores-negative-period": 400,
                    "ma-scores-negative-top-per-sector": 400,
                    "ma-scores-duplicate-float-threshold": 400,
                    "ma-scores-duplicate-mode": 400,
                    "rrg-period-usize-overflow": 400,
                    "rrg-trails-usize-overflow": 400,
                    "rrg-negative-period": 400,
                    "rrg-negative-trails": 400,
                    "rrg-min-volume-i64-overflow": 400,
                    "rrg-duplicate-optional-benchmark": 400,
                    "volume-profile-bins-usize-overflow": 400,
                    "volume-profile-negative-bins": 400,
                    "volume-profile-crypto-resource-step-limit": 400,
                    "volume-profile-duplicate-required-symbol": 400,
                    "volume-profile-duplicate-float-value-area": 400,
                    "volume-profile-duplicate-string-mode": 400,
                    "ma-scores-positive-infinity-threshold": 200,
                    "ma-scores-negative-infinity-threshold": 200,
                    "volume-profile-invalid-date": 400,
                    "volume-profile-invalid-end-date": 400,
                    "volume-profile-date-overrides-range": 200,
                    "volume-profile-start-only": 200,
                    "volume-profile-end-only": 400,
                    "volume-profile-no-minute-history": 404,
                    "invalid-top-performers-mode": 400,
                    "invalid-ma-scores-mode": 400,
                    "invalid-rrg-mode": 400,
                    "volume-profile-positive-infinity-value-area": 200,
                    "volume-profile-negative-infinity-value-area": 200,
                    "volume-profile-clamped-low-bounds": 200,
                    "volume-profile-clamped-high-bounds": 200,
                }
                expected_analysis_query_status.update(
                    {
                        case.name: 200
                        for case in cases
                        if case.name.startswith("top-performers-sort-")
                        or case.name.startswith("top-performers-with-hour-")
                        or case.name.startswith("top-performers-latest-")
                        or re.fullmatch(r"ma-scores-period-\d+-ema-(true|false)", case.name)
                        or case.name.startswith("ma-scores-latest-")
                        or case.name.startswith("rrg-latest-")
                        or case.name
                        in {"volume-profile-crypto-success", "volume-profile-yahoo-success"}
                    }
                )
                expected_analysis_query_status.update(
                    {
                        case.name: 400
                        for case in cases
                        if case.name.startswith("invalid-boolean-")
                        and case.path.startswith("/analysis/")
                    }
                )
                expected_analysis_query_status.update(
                    {
                        case.name: 400
                        for case in cases
                        if case.name.startswith("blank-numeric-")
                        and case.path.startswith("/analysis/")
                    }
                )
                expected_analysis_query_status.update(
                    {
                        "top-performers-limit-usize-maximum": 200,
                        "top-performers-min-volume-u64-maximum": 200,
                        "ma-scores-period-u32-maximum": 400,
                        "ma-scores-top-per-sector-usize-maximum": 200,
                        "rrg-period-usize-maximum-clamp": 200,
                        "rrg-trails-usize-maximum-clamp": 200,
                        "rrg-min-volume-i64-minimum": 200,
                        "rrg-min-volume-i64-maximum-filter": 200,
                        "volume-profile-bins-usize-maximum-clamp": 200,
                        "volume-profile-exact-low-boundaries": 200,
                        "volume-profile-exact-high-boundaries": 200,
                        "volume-profile-value-area-minimum-boundary": 200,
                    }
                )
                expected_analysis_query_status["volume-profile-yahoo-range"] = 404
                expected_query_status = {
                    "ticker-default-query-options": 200,
                    "ticker-redis-end-bounded-zset": 200,
                    "ticker-redis-snapshot-cross-backend": 200,
                    "ticker-redis-multi-symbol-snapshot-outage": 200,
                    "ticker-cache-comma-literal-first": 200,
                    "ticker-cache-comma-repeated-second": 200,
                    "ticker-cache-comma-repeated-first": 200,
                    "ticker-cache-comma-literal-second": 200,
                    "production-origin-data-ticker-json": 200,
                    "production-origin-data-ticker-all-json": 200,
                    "sync-write-compact-uuid": 200,
                    "sync-read-canonical-uuid": 200,
                    "production-origin-groups": 200,
                    "production-origin-names": 200,
                    "production-origin-groups-all": 200,
                    "production-origin-names-all": 200,
                    "production-origin-groups-crypto": 200,
                    "production-origin-groups-yahoo": 200,
                    "production-origin-names-crypto": 200,
                    "production-origin-names-yahoo": 200,
                    "invalid-ticker-mode": 400,
                    "invalid-ticker-group-mode": 400,
                    "invalid-ticker-name-mode": 400,
                    "ticker-duplicate-optional-interval": 400,
                    "groups-duplicate-mode": 400,
                    "names-duplicate-mode": 400,
                    "info-duplicate-optional-ticker": 400,
                    "sync-read-duplicate-required-secret": 400,
                    "sync-read-invalid-uuid": 400,
                    "sync-write-existing-wrong-secret": 403,
                    "sync-read-missing-record": 404,
                    "blank-numeric-ticker-limit": 200,
                    "ticker-limit-i64-minimum": 200,
                    "ticker-limit-i64-maximum": 200,
                    "ticker-csv-format-uppercase": 200,
                    "ticker-csv-format-mixed-case": 200,
                    "ticker-json-format-uppercase": 200,
                    "ticker-ema-ignored-when-ma-disabled": 200,
                    "ticker-legacy-scaled-with-ma-enabled": 200,
                }
                expected_query_status.update(
                    {
                        case.name: 400
                        for case in cases
                        if case.name.startswith("invalid-boolean-ticker-")
                    }
                )
                expected_sync_bodies = {
                    "sync-write-existing-wrong-secret": {
                        "success": False,
                        "error": "Invalid secret",
                    },
                    "sync-read-missing-record": {
                        "success": False,
                        "error": "Key not found",
                    },
                    "sync-read-encoded-slash-in-key": {
                        "success": False,
                        "error": "Key must be a valid UUID",
                    },
                }
                expected_route_status = {
                    "legacy-sdk-raw-route-not-mounted": 404,
                    "encoded-fixed-health-path": 404,
                    "encoded-fixed-ticker-group-path": 404,
                    "encoded-fixed-analysis-path": 404,
                    "encoded-static-mount-prefix": 404,
                    "sync-read-encoded-slash-in-key": 400,
                    "sync-read-invalid-utf8-key": 400,
                    "notes-invalid-utf8-session-path": 400,
                    "notes-invalid-utf8-filename-path": 400,
                    "notes-invalid-utf8-delete-path": 400,
                }
                expected_plain_bodies = {
                    "sync-read-invalid-utf8-key": b"Invalid URL: Invalid UTF-8 in `key`",
                    "notes-invalid-utf8-session-path": (
                        b"Invalid URL: Invalid UTF-8 in `session_id`"
                    ),
                    "notes-invalid-utf8-filename-path": (
                        b"Invalid URL: Invalid UTF-8 in `filename`"
                    ),
                    "notes-invalid-utf8-delete-path": (
                        b"Invalid URL: Invalid UTF-8 in `session_id`"
                    ),
                }
                expected_success_analysis_types = {
                    "top-performers": "top_performers",
                    "top-performers-default-query-options": "top_performers",
                    "top-performers-default-snapshot-option": "top_performers",
                    "analysis-top-performers-gzip": "top_performers",
                    "top-performers-invalid-date-fallback": "top_performers",
                    "ma-scores-by-sector": "ma_scores_by_sector",
                    "ma-scores-default-query-options": "ma_scores_by_sector",
                    "ma-scores-by-sector-default-snapshot-option": "ma_scores_by_sector",
                    "ma-scores-invalid-date-fallback": "ma_scores_by_sector",
                    "production-origin-data-top-performers-raw": "top_performers",
                    "production-origin-data-ma-scores-raw": "ma_scores_by_sector",
                    "rrg-jdk": "rrg",
                    "rrg-default-query-options": "rrg",
                    "rrg-mascore": "rrg",
                    "rrg-below-period-minimum-clamp": "rrg",
                    "rrg-jdk-mode-all": "rrg",
                    "rrg-jdk-mode-all-duplicate-benchmark": "rrg",
                    "rrg-jdk-mode-all-yahoo-benchmark": "rrg",
                    "rrg-jdk-mode-all-crypto-benchmark": "rrg",
                    "rrg-jdk-widget-default-date-after-fixture": "rrg",
                    "rrg-jdk-widget-default-date-after-fixture-crypto": "rrg",
                    "rrg-jdk-widget-default-date-after-fixture-yahoo": "rrg",
                    "volume-profile": "volume_profile",
                    "volume-profile-range": "volume_profile",
                    "volume-profile-default-query-options": "volume_profile",
                    "volume-profile-widget-vn-default-bins": "volume_profile",
                    "volume-profile-widget-crypto-default-bins": "volume_profile",
                    "volume-profile-low-priced-crypto-poc-tolerance": "volume_profile",
                    "volume-profile-widget-yahoo-default-bins": "volume_profile",
                    "volume-profile-date-overrides-range": "volume_profile",
                    "volume-profile-start-only": "volume_profile",
                    "volume-profile-positive-infinity-value-area": "volume_profile",
                    "volume-profile-negative-infinity-value-area": "volume_profile",
                    "production-origin-data-top-performers": "top_performers",
                    "production-origin-data-ma-scores": "ma_scores_by_sector",
                    "production-origin-data-rrg": "rrg",
                    "production-origin-data-rrg-crypto": "rrg",
                    "production-origin-data-rrg-yahoo": "rrg",
                    "production-origin-data-rrg-mascore": "rrg",
                    "production-origin-data-rrg-mascore-crypto": "rrg",
                    "production-origin-data-rrg-mascore-yahoo": "rrg",
                    "production-origin-data-rrg-jdk-all": "rrg",
                    "production-origin-data-rrg-jdk-all-raw": "rrg",
                    "production-origin-data-rrg-mascore-all": "rrg",
                    "production-origin-data-rrg-mascore-all-raw": "rrg",
                    "production-origin-data-volume-profile": "volume_profile",
                    "production-origin-data-volume-profile-range": "volume_profile",
                }
                expected_success_analysis_types.update(
                    {
                        case.name: "top_performers"
                        for case in cases
                        if case.name.startswith("top-performers-latest-")
                        or case.name.startswith("top-performers-with-hour-")
                    }
                )
                expected_success_analysis_types.update(
                    {
                        case.name: "ma_scores_by_sector"
                        for case in cases
                        if case.name.startswith("ma-scores-latest-")
                    }
                )
                expected_success_analysis_types.update(
                    {
                        case.name: "rrg"
                        for case in cases
                        if case.name.startswith("rrg-latest-")
                    }
                )
                expected_success_analysis_types.update(
                    {
                        "volume-profile-crypto-success": "volume_profile",
                        "volume-profile-yahoo-success": "volume_profile",
                        "volume-profile-bins-usize-maximum-clamp": "volume_profile",
                        "volume-profile-exact-low-boundaries": "volume_profile",
                        "volume-profile-exact-high-boundaries": "volume_profile",
                        "volume-profile-value-area-minimum-boundary": "volume_profile",
                        "volume-profile-clamped-low-bounds": "volume_profile",
                        "volume-profile-clamped-high-bounds": "volume_profile",
                    }
                )
                expected_success_analysis_types.update(
                    {
                        "top-performers-limit-usize-maximum": "top_performers",
                        "top-performers-min-volume-u64-maximum": "top_performers",
                        "ma-scores-top-per-sector-usize-maximum": "ma_scores_by_sector",
                        "rrg-period-usize-maximum-clamp": "rrg",
                        "rrg-trails-usize-maximum-clamp": "rrg",
                        "rrg-min-volume-i64-minimum": "rrg",
                        "rrg-min-volume-i64-maximum-filter": "rrg",
                    }
                )
                expected_analysis_query_bodies = {
                    "ma-scores-period-u32-maximum": {
                        "error": "Invalid MA period. Must be one of: 10, 20, 50, 100, 200"
                    },
                    "volume-profile-end-only": {
                        "error": "Either 'date' or 'start_date' parameter is required (YYYY-MM-DD format)"
                    },
                    "volume-profile-invalid-date": {
                        "error": "Invalid start date format. Use YYYY-MM-DD"
                    },
                    "volume-profile-invalid-end-date": {
                        "error": "Invalid end date format. Use YYYY-MM-DD"
                    },
                    "volume-profile-crypto-resource-step-limit": {
                        "error": "Minute price range exceeds volume-profile resource limit"
                    },
                    "volume-profile-yahoo-range": {
                        "error": (
                            "No minute data found for AAPL on "
                            f"{mid_fixture_start} to {mid_fixture_end}"
                        )
                    },
                    "volume-profile-no-minute-history": {
                        "error": (
                            "No minute data found for BTCUSDT on "
                            f"{FIXTURE_END.date().isoformat()}"
                        )
                    },
                }
                expected_analysis_dates = {
                    "top-performers-invalid-date-fallback": "not-a-date",
                    "ma-scores-invalid-date-fallback": "not-a-date",
                    "top-performers-limit-usize-maximum": "latest",
                    "top-performers-min-volume-u64-maximum": "latest",
                    "ma-scores-top-per-sector-usize-maximum": "latest",
                    "rrg-period-usize-maximum-clamp": "latest",
                    "rrg-trails-usize-maximum-clamp": "latest",
                    "rrg-min-volume-i64-minimum": "latest",
                    "rrg-min-volume-i64-maximum-filter": "latest",
                    "volume-profile-bins-usize-maximum-clamp": FIXTURE_END.date().isoformat(),
                    "volume-profile-exact-low-boundaries": FIXTURE_END.date().isoformat(),
                    "volume-profile-exact-high-boundaries": FIXTURE_END.date().isoformat(),
                    "volume-profile-value-area-minimum-boundary": FIXTURE_END.date().isoformat(),
                    "volume-profile-clamped-low-bounds": FIXTURE_END.date().isoformat(),
                    "volume-profile-clamped-high-bounds": FIXTURE_END.date().isoformat(),
                    "rrg-jdk": FIXTURE_END.date().isoformat(),
                    "rrg-jdk-mode-all": FIXTURE_END.date().isoformat(),
                    "rrg-below-period-minimum-clamp": FIXTURE_END.date().isoformat(),
                    "rrg-jdk-mode-all-duplicate-benchmark": FIXTURE_END.date().isoformat(),
                    "rrg-jdk-mode-all-yahoo-benchmark": FIXTURE_END.date().isoformat(),
                    "rrg-jdk-mode-all-crypto-benchmark": FIXTURE_END.date().isoformat(),
                    "rrg-mascore": FIXTURE_END.date().isoformat(),
                    "rrg-jdk-widget-default-date-after-fixture": (FIXTURE_END + timedelta(days=1))
                    .date()
                    .isoformat(),
                    "rrg-jdk-widget-default-date-after-fixture-crypto": (
                        FIXTURE_END + timedelta(days=1)
                    )
                    .date()
                    .isoformat(),
                    "rrg-jdk-widget-default-date-after-fixture-yahoo": (
                        FIXTURE_END + timedelta(days=1)
                    )
                    .date()
                    .isoformat(),
                    "volume-profile-range": (
                        f"{(FIXTURE_END - timedelta(days=30)).date()} "
                        f"to {FIXTURE_END.date()}"
                    ),
                    "production-origin-data-top-performers": FIXTURE_END.date().isoformat(),
                    "production-origin-data-top-performers-raw": FIXTURE_END.date().isoformat(),
                    "production-origin-data-ma-scores": FIXTURE_END.date().isoformat(),
                    "production-origin-data-ma-scores-raw": FIXTURE_END.date().isoformat(),
                    "production-origin-data-rrg": FIXTURE_END.date().isoformat(),
                    "production-origin-data-rrg-crypto": FIXTURE_END.date().isoformat(),
                    "production-origin-data-rrg-yahoo": FIXTURE_END.date().isoformat(),
                    "production-origin-data-rrg-mascore": FIXTURE_END.date().isoformat(),
                    "production-origin-data-rrg-mascore-crypto": FIXTURE_END.date().isoformat(),
                    "production-origin-data-rrg-mascore-yahoo": FIXTURE_END.date().isoformat(),
                    "production-origin-data-rrg-jdk-all": FIXTURE_END.date().isoformat(),
                    "production-origin-data-rrg-jdk-all-raw": FIXTURE_END.date().isoformat(),
                    "production-origin-data-rrg-mascore-all": FIXTURE_END.date().isoformat(),
                    "production-origin-data-rrg-mascore-all-raw": FIXTURE_END.date().isoformat(),
                    "production-origin-data-volume-profile": FIXTURE_END.date().isoformat(),
                    "production-origin-data-volume-profile-range": (
                        f"{(FIXTURE_END - timedelta(days=30)).date()} "
                        f"to {FIXTURE_END.date()}"
                    ),
                }
                expected_analysis_dates.update(
                    {
                        case.name: "latest"
                        for case in cases
                        if case.name.startswith("top-performers-latest-")
                        or case.name.startswith("ma-scores-latest-")
                        or case.name.startswith("rrg-latest-")
                    }
                )
                expected_analysis_dates.update(
                    {
                        "volume-profile-crypto-success": FIXTURE_END.date().isoformat(),
                        "volume-profile-yahoo-success": FIXTURE_END.date().isoformat(),
                    }
                )
                widget_fallback_date = FIXTURE_DATES[-2].date().isoformat()
                expected_rrg_metadata = {
                    "rrg-jdk": ("jdk", "VNINDEX", 10),
                    "rrg-below-period-minimum-clamp": ("jdk", "VNINDEX", 4),
                    "rrg-jdk-mode-all": ("jdk", "VNINDEX", 10),
                    "rrg-jdk-mode-all-duplicate-benchmark": ("jdk", "DUPL", 10),
                    "rrg-jdk-mode-all-yahoo-benchmark": ("jdk", "^GSPC", 10),
                    "rrg-jdk-mode-all-crypto-benchmark": ("jdk", "BTCUSDT", 10),
                    "rrg-jdk-widget-default-date-after-fixture": ("jdk", "VNINDEX", 10),
                    "rrg-jdk-widget-default-date-after-fixture-crypto": (
                        "jdk",
                        "BTCUSDT",
                        10,
                    ),
                    "rrg-jdk-widget-default-date-after-fixture-yahoo": (
                        "jdk",
                        "^GSPC",
                        10,
                    ),
                    "rrg-mascore": ("mascore", None, None),
                    "production-origin-data-rrg": ("jdk", "VNINDEX", 10),
                    "production-origin-data-rrg-crypto": ("jdk", "BTCUSDT", 10),
                    "production-origin-data-rrg-yahoo": ("jdk", "^GSPC", 10),
                    "production-origin-data-rrg-mascore": ("mascore", None, None),
                    "production-origin-data-rrg-mascore-crypto": ("mascore", None, None),
                    "production-origin-data-rrg-mascore-yahoo": ("mascore", None, None),
                    "production-origin-data-rrg-jdk-all": ("jdk", "VNINDEX", 10),
                    "production-origin-data-rrg-jdk-all-raw": ("jdk", "VNINDEX", 10),
                    "production-origin-data-rrg-mascore-all": ("mascore", None, None),
                    "production-origin-data-rrg-mascore-all-raw": ("mascore", None, None),
                    "rrg-period-usize-maximum-clamp": ("jdk", "VNINDEX", 50),
                    "rrg-trails-usize-maximum-clamp": ("jdk", "VNINDEX", 10),
                    "rrg-min-volume-i64-minimum": ("jdk", "VNINDEX", 10),
                    "rrg-min-volume-i64-maximum-filter": ("jdk", "VNINDEX", 10),
                }
                expected_rrg_symbols = {
                    "rrg-jdk": ("FPT", "vn"),
                    "rrg-below-period-minimum-clamp": ("FPT", "vn"),
                    "rrg-jdk-mode-all": ("FPT", "vn"),
                    "rrg-jdk-mode-all-duplicate-benchmark": ("FPT", "vn"),
                    "rrg-jdk-widget-default-date-after-fixture": ("FPT", "vn"),
                    "rrg-jdk-widget-default-date-after-fixture-crypto": (
                        "ETHUSDT",
                        "crypto",
                    ),
                    "rrg-jdk-widget-default-date-after-fixture-yahoo": ("AAPL", "yahoo"),
                    "rrg-mascore": ("FPT", "vn"),
                    "production-origin-data-rrg": ("FPT", "vn"),
                    "production-origin-data-rrg-crypto": ("ETHUSDT", "crypto"),
                    "production-origin-data-rrg-yahoo": ("AAPL", "yahoo"),
                    "production-origin-data-rrg-mascore": ("FPT", "vn"),
                    "production-origin-data-rrg-mascore-crypto": ("ETHUSDT", "crypto"),
                    "production-origin-data-rrg-mascore-yahoo": ("AAPL", "yahoo"),
                    "production-origin-data-rrg-jdk-all": ("FPT", "vn"),
                    "production-origin-data-rrg-jdk-all-raw": ("FPT", "vn"),
                    "production-origin-data-rrg-mascore-all": ("FPT", "vn"),
                    "production-origin-data-rrg-mascore-all-raw": ("FPT", "vn"),
                    "rrg-period-usize-maximum-clamp": ("FPT", "vn"),
                    "rrg-trails-usize-maximum-clamp": ("FPT", "vn"),
                    "rrg-min-volume-i64-minimum": ("FPT", "vn"),
                }
                expected_rrg_mixed_identities = {
                    "rrg-jdk": Counter({("FPT", "vn"): 1, ("VCB", "vn"): 2}),
                    "rrg-min-volume-i64-minimum": Counter(
                        {
                            ("ABC", "vn"): 1,
                            ("DUPL", "vn"): 1,
                            ("FPT", "vn"): 1,
                            ("VCB", "vn"): 2,
                        }
                    ),
                    "production-origin-data-rrg": Counter(
                        {("FPT", "vn"): 1, ("VCB", "vn"): 2}
                    ),
                    "production-origin-data-rrg-crypto": Counter(
                        {("ETHUSDT", "crypto"): 1}
                    ),
                    "production-origin-data-rrg-yahoo": Counter(
                        {
                            ("AAPL", "yahoo"): 1,
                            ("DUPL", "yahoo"): 1,
                            ("FPT", "yahoo"): 1,
                            ("DUPL,FPT", "yahoo"): 1,
                            ("COLL", "yahoo"): 2,
                        }
                    ),
                    "rrg-stock-mode-alias": Counter(
                        {("FPT", "vn"): 1, ("VCB", "vn"): 2}
                    ),
                    "rrg-jdk-chrono-unpadded-date": Counter(
                        {("FPT", "vn"): 1, ("VCB", "vn"): 2}
                    ),
                    "rrg-jdk-widget-default-date-after-fixture": Counter(
                        {("FPT", "vn"): 1, ("VCB", "vn"): 2}
                    ),
                    "rrg-jdk-widget-default-date-after-fixture-crypto": Counter(
                        {("ETHUSDT", "crypto"): 1}
                    ),
                    "rrg-jdk-widget-default-date-after-fixture-yahoo": Counter(
                        {
                            ("AAPL", "yahoo"): 1,
                            ("DUPL", "yahoo"): 1,
                            ("FPT", "yahoo"): 1,
                            ("DUPL,FPT", "yahoo"): 1,
                            ("COLL", "yahoo"): 2,
                        }
                    ),
                    "rrg-jdk-mode-all": Counter(
                        {
                            ("FPT", "vn"): 1,
                            ("VCB", "vn"): 2,
                            ("DUPL", "vn"): 1,
                            ("ABC", "vn"): 1,
                            ("AAPL", "yahoo"): 1,
                            ("DUPL", "yahoo"): 1,
                            ("FPT", "yahoo"): 1,
                            ("DUPL,FPT", "yahoo"): 1,
                            ("COLL", "yahoo"): 2,
                            ("^GSPC", "yahoo"): 1,
                            ("BTCUSDT", "crypto"): 1,
                            ("ETHUSDT", "crypto"): 1,
                        }
                    ),
                    "rrg-jdk-mode-all-duplicate-benchmark": Counter(
                        {
                            ("FPT", "vn"): 1,
                            ("VCB", "vn"): 2,
                            ("ABC", "vn"): 1,
                            ("AAPL", "yahoo"): 1,
                            ("FPT", "yahoo"): 1,
                            ("DUPL,FPT", "yahoo"): 1,
                            ("COLL", "yahoo"): 2,
                            ("^GSPC", "yahoo"): 1,
                            ("BTCUSDT", "crypto"): 1,
                            ("ETHUSDT", "crypto"): 1,
                        }
                    ),
                    "rrg-latest-jdk-vn": Counter(
                        {
                            ("FPT", "vn"): 1,
                            ("VCB", "vn"): 2,
                            ("DUPL", "vn"): 1,
                            ("ABC", "vn"): 1,
                        }
                    ),
                    "rrg-latest-jdk-crypto": Counter({("ETHUSDT", "crypto"): 1}),
                    "rrg-latest-jdk-yahoo": Counter(
                        {
                            ("DUPL", "yahoo"): 1,
                            ("FPT", "yahoo"): 1,
                            ("DUPL,FPT", "yahoo"): 1,
                            ("COLL", "yahoo"): 2,
                            ("^GSPC", "yahoo"): 1,
                        }
                    ),
                    "production-origin-data-rrg-jdk-all": Counter(
                        {
                            ("FPT", "vn"): 1,
                            ("VCB", "vn"): 2,
                            ("AAPL", "yahoo"): 1,
                            ("FPT", "yahoo"): 1,
                            ("DUPL,FPT", "yahoo"): 1,
                            ("COLL", "yahoo"): 2,
                            ("^GSPC", "yahoo"): 1,
                            ("BTCUSDT", "crypto"): 1,
                            ("ETHUSDT", "crypto"): 1,
                        }
                    ),
                    "production-origin-data-rrg-jdk-all-raw": Counter(
                        {
                            ("FPT", "vn"): 1,
                            ("VCB", "vn"): 2,
                            ("AAPL", "yahoo"): 1,
                            ("FPT", "yahoo"): 1,
                            ("DUPL,FPT", "yahoo"): 1,
                            ("COLL", "yahoo"): 2,
                            ("^GSPC", "yahoo"): 1,
                            ("BTCUSDT", "crypto"): 1,
                            ("ETHUSDT", "crypto"): 1,
                        }
                    ),
                    "production-origin-data-rrg-mascore-all": Counter(
                        {
                            ("FPT", "vn"): 1,
                            ("VCB", "vn"): 1,
                            ("AAPL", "yahoo"): 1,
                            ("FPT", "yahoo"): 1,
                            ("DUPL,FPT", "yahoo"): 1,
                            ("COLL", "yahoo"): 1,
                            ("^GSPC", "yahoo"): 1,
                            ("SJC", "sjc"): 1,
                            ("COLL", "sjc"): 1,
                            ("BTCUSDT", "crypto"): 1,
                            ("ETHUSDT", "crypto"): 1,
                        }
                    ),
                    "production-origin-data-rrg-mascore-all-raw": Counter(
                        {
                            ("FPT", "vn"): 1,
                            ("VCB", "vn"): 1,
                            ("AAPL", "yahoo"): 1,
                            ("FPT", "yahoo"): 1,
                            ("DUPL,FPT", "yahoo"): 1,
                            ("COLL", "yahoo"): 1,
                            ("^GSPC", "yahoo"): 1,
                            ("SJC", "sjc"): 1,
                            ("COLL", "sjc"): 1,
                            ("BTCUSDT", "crypto"): 1,
                            ("ETHUSDT", "crypto"): 1,
                        }
                    ),
                }
                expected_latest_analysis_symbols = {}
                latest_source_symbol = {
                    "crypto": ("ETHUSDT", "crypto"),
                    "yahoo": ("AAPL", "yahoo"),
                }
                expected_latest_analysis_symbols[
                    "top-performers-limit-usize-maximum"
                ] = ("FPT", "vn")
                expected_empty_analysis_cases = {
                    f"ma-scores-latest-crypto-period-{period}"
                    for period in (20, 100, 200)
                }
                expected_empty_analysis_cases.update(
                    {"rrg-min-volume-i64-maximum-filter"}
                )
                expected_latest_analysis_symbols[
                    "top-performers-min-volume-u64-maximum"
                ] = ("DUPL", "vn")
                for case in cases:
                    if case.name.startswith("top-performers-latest-"):
                        source = case.name.split("-")[3]
                        expected_latest_analysis_symbols[case.name] = latest_source_symbol[source]
                    elif case.name.startswith("ma-scores-latest-"):
                        source = case.name.split("-")[3]
                        if source == "yahoo":
                            # Single-source MA-by-sector keeps VN sector
                            # membership and looks those symbols up in Yahoo.
                            expected_latest_analysis_symbols[case.name] = ("FPT", "yahoo")
                    elif case.name.startswith("rrg-latest-"):
                        parts = case.name.split("-")
                        algorithm, source = parts[2], parts[3]
                        if source == "all":
                            benchmark = "VNINDEX"
                            expected_latest_analysis_symbols[case.name] = ("FPT", "vn")
                        else:
                            symbol_source = {
                                "vn": ("FPT", "vn"),
                                "crypto": ("ETHUSDT", "crypto"),
                                # RRG excludes its benchmark symbol from the
                                # plotted members, so use another Yahoo row.
                                "yahoo": ("FPT", "yahoo"),
                            }
                            benchmark = {
                                "vn": "VNINDEX",
                                "crypto": "BTCUSDT",
                                "yahoo": "AAPL",
                            }[source]
                            expected_latest_analysis_symbols[case.name] = symbol_source[source]
                        expected_rrg_metadata[case.name] = (
                            ("jdk", benchmark, 10)
                            if algorithm == "jdk"
                            else ("mascore", None, None)
                        )
                        expected_rrg_symbols[case.name] = expected_latest_analysis_symbols[
                            case.name
                        ]

                expected_latest_analysis_symbols.update(
                    {
                        "top-performers-latest-crypto-ema": ("ETHUSDT", "crypto"),
                        "top-performers-latest-yahoo-ema": ("AAPL", "yahoo"),
                        "ma-scores-latest-yahoo-period-20": ("FPT", "yahoo"),
                        "ma-scores-latest-yahoo-period-100": ("FPT", "yahoo"),
                        "ma-scores-latest-yahoo-period-200": ("FPT", "yahoo"),
                    }
                )
                nonfinite_threshold_cases = {
                    "ma-scores-positive-infinity-threshold",
                    "ma-scores-negative-infinity-threshold",
                }
                rejected_upload_sessions = {
                    "notes-upload-bad-name": session_id,
                    "notes-upload-wrong-field-name": session_id,
                    "notes-upload-multiple-files": session_id,
                    "notes-upload-invalid-utf8": session_id,
                    "notes-upload-empty-multipart": session_id,
                    "notes-quota-byte-limit": byte_limited_session_id,
                    "notes-quota-one-byte-over-limit": byte_limited_session_id,
                    "notes-upload-over-maximum-markdown-size": oversized_file_session_id,
                    "notes-quota-session-limit": session_limited_session_id,
                }
                successful_upload_sessions = {
                    "notes-upload-markdown": session_id,
                    "notes-upload-maximum-markdown-size": max_file_session_id,
                    "notes-quota-fourth-session": quota_session_id,
                    "notes-quota-exact-byte-limit": session_limited_session_id,
                }
                top_performers_ignored_query_bodies = {"Rust": {}, "Python": {}}

                for case in cases:
                    refresh_before_states = None
                    quota_fillers = []
                    if case.name == "head-contract-notes-markdown":
                        # Seed only when the late HEAD probes run, after the
                        # quota lifecycle has completed. This keeps the oracle
                        # session out of the session-count quota fixture.
                        _seed_head_notes(root, head_notes_session_id)
                        for implementation in ("rust-uploads", "python-uploads"):
                            notes_path = (
                                root
                                / implementation
                                / head_notes_session_id
                                / "markdown"
                                / "content.md"
                            )
                            if not notes_path.is_file():
                                failures.append(
                                    f"{case.name}: seeded {implementation} fixture is missing "
                                    f"before request: {notes_path}"
                                )
                    if case.method == "POST" and case.path == "/tickers/refresh":
                        refresh_before_states = _prime_refresh_schedules(
                            postgres_handle, app.state.repo
                        )
                    if case.name == "notes-quota-byte-limit":
                        # The same matrix also accepts a 5 MiB markdown file,
                        # so make the byte-quota rejection independent of that
                        # file-size boundary by reserving the configured quota
                        # in an existing temporary Notes session.
                        for implementation in ("rust-uploads", "python-uploads"):
                            filler = (
                                root
                                / implementation
                                / legacy_session_id
                                / "quota-filler.bin"
                            )
                            with filler.open("wb") as handle:
                                handle.truncate(settings.uploads_max_bytes)
                            quota_fillers.append(filler)
                    if case.name in {
                        "notes-quota-exact-byte-limit",
                        "notes-quota-one-byte-over-limit",
                    }:
                        # Set the aggregate stored-byte count to the exact
                        # boundary before each one-byte upload. The first must
                        # fit; the next must exceed the same boundary.
                        target_before_upload = settings.uploads_max_bytes - (
                            1 if case.name == "notes-quota-exact-byte-limit" else 0
                        )
                        for implementation in ("rust-uploads", "python-uploads"):
                            current_bytes, _ = _storage_usage(root / implementation)
                            filler_size = target_before_upload - current_bytes
                            if filler_size < 0:
                                failures.append(
                                    f"{case.name}: {implementation} fixture already uses "
                                    f"{current_bytes} bytes, above boundary "
                                    f"{target_before_upload}"
                                )
                                continue
                            filler = (
                                root
                                / implementation
                                / legacy_session_id
                                / "quota-filler.bin"
                            )
                            with filler.open("wb") as handle:
                                handle.truncate(filler_size)
                            quota_fillers.append(filler)
                    if use_live_redis and case.name.startswith(
                        (
                            "top-performers-default-snapshot-option",
                            "production-origin-data-top-performers",
                            "ma-scores-by-sector-default-snapshot-option",
                            "ticker-stale-snapshot-",
                            "ticker-incomplete-snapshot-",
                            "ticker-malformed-snapshot-",
                            "ticker-redis-ticker-list-",
                            "ticker-partial-redis-",
                            "ticker-empty-redis-",
                            "rrg-mascore-valid-stale-joined-snapshot",
                            "rrg-mascore-null-required-ohlc-joined-snapshot",
                            "rrg-mascore-duplicate-member-raw-limit-seed-depth",
                            "top-performers-stale-joined-snapshot",
                            "analysis-mode-all-joined-snapshot-",
                            "analysis-mode-all-tie-",
                            "volume-profile-partial-tail-overlap",
                            "volume-profile-tail-no-overlap",
                            "volume-profile-restore-tail",
                        )
                    ):
                        _prepare_live_redis_case(
                            (rust_redis_url, python_redis_url), case.name, rows
                        )
                    kwargs = {}
                    if case.json_body is not None:
                        kwargs["json"] = case.json_body
                    elif case.files is not None:
                        kwargs["files"] = case.files
                    elif case.raw_body is not None:
                        kwargs["content"] = case.raw_body
                    elif case.chunked_body is not None:
                        kwargs["content"] = _ChunkedBody(case.chunked_body)
                    try:
                        request_options = (
                            {"timeout": case.request_timeout}
                            if case.request_timeout is not None
                            else {}
                        )
                        python_response = python_client.request(
                            case.method,
                            case.path,
                            headers=case.headers,
                            **kwargs,
                        )
                        rust_response = rust_client.request(
                            case.method,
                            case.path,
                            headers=case.headers,
                            **request_options,
                            **kwargs,
                        )
                    except Exception as exc:
                        for filler in quota_fillers:
                            filler.unlink(missing_ok=True)
                        failures.append(f"{case.name}: request failed: {type(exc).__name__}: {exc}")
                        continue
                    if case.name in {
                        "notes-quota-exact-byte-limit",
                        "notes-quota-one-byte-over-limit",
                    }:
                        for implementation in ("rust-uploads", "python-uploads"):
                            current_bytes, _ = _storage_usage(root / implementation)
                            if current_bytes != settings.uploads_max_bytes:
                                failures.append(
                                    f"{case.name}: {implementation} stored {current_bytes} "
                                    f"bytes before temporary filler cleanup, expected exact "
                                    f"quota {settings.uploads_max_bytes}"
                                )
                    for filler in quota_fillers:
                        filler.unlink(missing_ok=True)
                    differences = _differences(
                        _snapshot(
                            rust_response,
                            case.ignored_headers,
                            request_method=case.method,
                        ),
                        _snapshot(
                            python_response,
                            case.ignored_headers,
                            request_method=case.method,
                        ),
                        compare_body=case.compare_body,
                        volatile_json_fields=case.volatile_json_fields,
                        health_clock_policy=case.health_clock_policy,
                    )
                    if differences:
                        failures.append(f"{case.name}: " + "; ".join(differences))

                    if case.name in {
                        "top-performers",
                        "top-performers-with-hour-requested",
                        "top-performers-with-hour-false",
                        "top-performers-with-hour-invalid",
                        "top-performers-with-hour-duplicate",
                    }:
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            top_performers_ignored_query_bodies[runtime][case.name] = (
                                response.content
                            )

                    if case.name in {"home-vn-csv-gzip", "analysis-top-performers-gzip"}:
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            if response.headers.get("content-encoding") != "gzip":
                                failures.append(
                                    f"{case.name}: {runtime} did not gzip the API response"
                                )
                            if "accept-encoding" not in response.headers.get("vary", "").lower():
                                failures.append(
                                    f"{case.name}: {runtime} omitted Accept-Encoding from Vary"
                                )

                    if (use_unreachable_redis or use_permission_error_redis) and case.name in {
                        "ticker-redis-end-bounded-zset",
                        "ticker-redis-snapshot-cross-backend",
                        "ticker-redis-multi-symbol-snapshot-outage",
                    }:
                        expected_symbols = {
                            "ticker-redis-end-bounded-zset": ("FPT",),
                            "ticker-redis-snapshot-cross-backend": ("VCB",),
                            "ticker-redis-multi-symbol-snapshot-outage": ("VCB", "FPT"),
                        }[case.name]
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            try:
                                payload = response.json()
                            except ValueError:
                                payload = None
                            if response.status_code != 200 or not isinstance(payload, dict):
                                failures.append(
                                    f"{case.name}: {runtime} failed to serve a ticker response "
                                    "with Redis unavailable or permission denied"
                                )
                            else:
                                missing_symbols = [
                                    symbol for symbol in expected_symbols if symbol not in payload
                                ]
                                if missing_symbols:
                                    failures.append(
                                        f"{case.name}: {runtime} database fallback omitted "
                                        f"{', '.join(missing_symbols)}"
                                    )
                            if response.headers.get("x-data-source") != "database":
                                failures.append(
                                    f"{case.name}: {runtime} did not identify its database fallback"
                                )
                            if any(header.startswith("x-redis-") for header in response.headers):
                                failures.append(
                                    f"{case.name}: {runtime} exposed Redis diagnostics without "
                                    "a successful Redis read"
                                )

                    if case.name == "ticker-signed-limit-negative-no-ma-sql":
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            if response.status_code != 200 or response.json() != {}:
                                failures.append(
                                    f"{case.name}: {runtime} should return an empty 200 response "
                                    "when SQL binds the signed no-MA limit as zero"
                                )

                    if case.name in {
                        "ticker-weekly-latest-long-history",
                        "ticker-weekly-end-only-long-history",
                        "ticker-aggregated-weekly-negative-limit-ma",
                    }:
                        expected_raw_daily = sorted(
                            (
                                row
                                for row in rows
                                if row.source == "vn"
                                and row.symbol == "FPT"
                                and row.interval == "1D"
                            ),
                            key=lambda row: row.time,
                        )
                        if case.name == "ticker-aggregated-weekly-negative-limit-ma":
                            # Rust computes -1 * 5 daily bars + the 5,000-row
                            # MA context, then its signed-to-usize output trim
                            # retains every weekly bucket from that raw window.
                            expected_raw_daily = expected_raw_daily[-4_995:]
                        elif use_live_redis:
                            expected_raw_daily = expected_raw_daily[-5_000:]
                        expected_weekly_all = _expected_weekly_candles(
                            expected_raw_daily, end=FIXTURE_END
                        )
                        expected_weekly_rows = _expected_csv_candle_rows(
                            expected_weekly_all, interval="1W", ema=False
                        )
                        if case.name != "ticker-aggregated-weekly-negative-limit-ma":
                            expected_weekly_rows = expected_weekly_rows[-1_002:]
                        expected_weekly_count = len(expected_weekly_rows)
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            try:
                                weekly_rows = response.json().get("FPT", [])
                            except (json.JSONDecodeError, AttributeError):
                                weekly_rows = []
                            if len(weekly_rows) != expected_weekly_count:
                                failures.append(
                                    f"{case.name}: {runtime} returned {len(weekly_rows)} weekly "
                                    f"rows, expected {expected_weekly_count} from independently "
                                    "aggregated fixture history and configured Redis retention"
                                )
                                continue
                            mismatches = []
                            for row_index, (actual_row, expected_row) in enumerate(
                                zip(weekly_rows, expected_weekly_rows, strict=True)
                            ):
                                for field_name, expected_value in expected_row.items():
                                    actual_value = actual_row.get(field_name)
                                    if expected_value is None:
                                        # Rust omits absent Option fields from JSON;
                                        # an explicit null is a wire-shape mismatch.
                                        matches = field_name not in actual_row
                                    elif field_name in {"symbol", "time"}:
                                        matches = actual_value == expected_value
                                    elif field_name == "volume":
                                        matches = actual_value == expected_value
                                    else:
                                        matches = (
                                            isinstance(actual_value, (int, float))
                                            and not isinstance(actual_value, bool)
                                            and math.isclose(
                                                actual_value,
                                                float(expected_value),
                                                rel_tol=0,
                                                abs_tol=1e-8,
                                            )
                                        )
                                    if not matches:
                                        mismatches.append(
                                            f"row {row_index} {field_name}={actual_value!r}, "
                                            f"expected {expected_value!r} at {expected_row['time']}"
                                        )
                                        if len(mismatches) >= 8:
                                            break
                                if len(mismatches) >= 8:
                                    break
                            if mismatches:
                                failures.append(
                                    f"{case.name}: {runtime} weekly fixture oracle mismatch: "
                                    + "; ".join(mismatches)
                                )

                    query = parse_qs(
                        case.path.partition("?")[2], keep_blank_values=True
                    )
                    is_csv_request = case.method == "GET" and case.path.startswith(
                        "/tickers"
                    ) and any(
                        value.lower() == "csv" for value in query.get("format", ())
                    )
                    if is_csv_request:
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            if response.status_code != 200:
                                failures.append(
                                    f"{case.name}: {runtime} CSV request returned "
                                    f"{response.status_code}, expected 200"
                                )
                                continue
                            if not response.headers.get("content-type", "").startswith(
                                "text/csv"
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} returned "
                                    f"Content-Type {response.headers.get('content-type')!r}, "
                                    "expected text/csv"
                                )
                            lines = response.text.splitlines()
                            header = next(csv.reader(lines[:1]), [])
                            if tuple(header) != WEB_TICKER_CSV_COLUMNS:
                                failures.append(
                                    f"{case.name}: {runtime} CSV header {tuple(header)!r} "
                                    "did not match the web SDK column contract"
                                )
                            if len(lines) < 2:
                                failures.append(
                                    f"{case.name}: {runtime} returned no CSV data rows"
                                )

                    playground_start = datetime.fromtimestamp(
                        FIXTURE_DATES[DAILY_BARS - 500].timestamp(), UTC
                    )
                    playground_end = FIXTURE_END.replace(
                        hour=23, minute=59, second=59, microsecond=0
                    )
                    playground_hourly_candles = [
                        row
                        for row in rows
                        if row.source == "vn"
                        and row.symbol == "FPT"
                        and row.interval == "1h"
                        and playground_start.timestamp() <= row.time <= playground_end.timestamp()
                    ]
                    playground_daily = [
                        row
                        for row in rows
                        if row.source == "vn" and row.symbol == "FPT" and row.interval == "1D"
                    ]
                    playground_weekly_sql_source = [
                        row
                        for row in playground_daily
                        if playground_start.timestamp()
                        <= row.time
                        <= playground_end.timestamp()
                    ]
                    playground_weekly_sql_all = _expected_weekly_candles(
                        playground_weekly_sql_source,
                        end=playground_end,
                    )
                    playground_weekly_uncovered_sql_expected = _expected_csv_candle_rows(
                        playground_weekly_sql_all,
                        interval="1W",
                        ema=True,
                    )
                    playground_weekly_source = (
                        playground_daily if use_live_redis else playground_weekly_sql_source
                    )
                    playground_weekly_all = _expected_weekly_candles(
                        playground_weekly_source,
                        end=playground_end,
                    )
                    playground_weekly_candles = playground_weekly_all
                    if use_live_redis:
                        # A covered Redis window aggregates retained history,
                        # then filters by the aggregate bucket labels.
                        playground_weekly_candles = [
                            row
                            for row in playground_weekly_all
                            if playground_start.timestamp()
                            <= row.time
                            <= playground_end.timestamp()
                        ]
                    playground_minute_start = datetime.combine(
                        FIXTURE_END.date() - timedelta(days=3),
                        datetime.min.time(),
                        tzinfo=UTC,
                    )
                    playground_minute_context = [
                        row
                        for row in rows
                        if row.source == "vn"
                        and row.symbol == "FPT"
                        and row.interval == "1m"
                    ]
                    if not use_live_redis:
                        # With no Redis, SQL starts its indicator/change window
                        # at start_date instead of using the retained minute tail.
                        playground_minute_context = [
                            row
                            for row in playground_minute_context
                            if row.time >= int(playground_minute_start.timestamp())
                        ]
                    playground_minute_candles = [
                        row
                        for row in playground_minute_context
                        if row.time >= int(playground_minute_start.timestamp())
                    ]
                    playground_minute_expected = _expected_csv_candle_rows(
                        playground_minute_context, interval="1m", ema=True
                    )
                    playground_minute_expected = [
                        row
                        for row in playground_minute_expected
                        if row["time"] >= playground_minute_start.strftime("%Y-%m-%dT%H:%M:%S")
                    ]
                    playground_weekly_expected = _expected_csv_candle_rows(
                        playground_weekly_all, interval="1W", ema=True
                    )
                    if use_live_redis:
                        playground_weekly_expected = [
                            row
                            for row in playground_weekly_expected
                            if playground_start.strftime("%Y-%m-%d") <= row["time"]
                            <= playground_end.strftime("%Y-%m-%d")
                        ]
                    expected_web_csv_symbols = {
                        "web-date-control-vnindex-range-csv": [
                            "VNINDEX"
                        ] * len(web_date_control_dates),
                        "web-mixed-watchlist-mode-all-csv": sorted(
                            symbol
                            for symbol in ("AAPL", "BTCUSDT", "ETHUSDT", "FPT", "VNINDEX")
                            for _ in range(21)
                        ),
                        "web-mixed-watchlist-mode-all-sma-csv": sorted(
                            symbol
                            for symbol in ("AAPL", "BTCUSDT", "ETHUSDT", "FPT", "VNINDEX")
                            for _ in range(21)
                        ),
                        "web-mixed-watchlist-mode-all-hourly-csv": sorted(
                            symbol
                            for symbol in ("AAPL", "BTCUSDT", "ETHUSDT", "FPT", "VNINDEX")
                            for _ in range(21)
                        ),
                        "web-alert-context-max-lookback-mode-all-no-ma-csv": sorted(
                            symbol
                            for _source, symbol in _mode_all_fixture_tickers()
                            for _ in range(
                                min(110, 40, 11 if symbol == "CSVEDGE=F" else DAILY_BARS)
                            )
                        ),
                        "web-market-matrix-all-vn-limit-21-enddate-csv": sorted(
                            symbol
                            for source, symbol, *_ in TICKERS
                            if source == "vn"
                            for _ in range(21)
                        ),
                        "web-trend-signal-table-mode-all-hourly-csv": sorted(
                            symbol
                            for source, symbol in _mode_all_fixture_tickers()
                            for _ in sorted(
                                (
                                    row
                                    for row in rows
                                    if row.source == source
                                    and row.symbol == symbol
                                    and row.interval == "1h"
                                    and row.time
                                    <= int(
                                        FIXTURE_END.replace(
                                            hour=23, minute=59, second=59
                                        ).timestamp()
                                    )
                                ),
                                key=lambda row: row.time,
                            )[-40:]
                        ),
                        "web-playground-native-hourly-range-csv": [
                            "FPT" for _ in playground_hourly_candles
                        ],
                        "web-playground-native-weekly-range-csv": [
                            "FPT" for _ in playground_weekly_candles
                        ],
                        "web-playground-lazy-minute-range-csv": [
                            "FPT" for _ in playground_minute_candles
                        ],
                        "web-volume-profile-widget-date-discovery-csv": ["ETHUSDT"],
                        "web-volume-profile-widget-dated-daily-csv": ["ETHUSDT"],
                        "web-backtest-current-prices-comma-joined-csv": ["FPT", "VCB"],
                    }.get(case.name)
                    if expected_web_csv_symbols is not None:
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            csv_rows, parse_errors = _parse_web_ticker_csv(response.text)
                            actual_symbols = sorted(row["symbol"] for row in csv_rows)
                            if actual_symbols != expected_web_csv_symbols:
                                failures.append(
                                    f"{case.name}: {runtime} CSV symbols "
                                    f"{actual_symbols!r}, expected {expected_web_csv_symbols!r}"
                                )
                            if parse_errors:
                                failures.append(
                                    f"{case.name}: {runtime} CSV parse errors {parse_errors!r}"
                                )
                            if case.name == "web-date-control-vnindex-range-csv":
                                observed_dates = sorted(row["time"] for row in csv_rows)
                                if observed_dates != web_date_control_dates:
                                    failures.append(
                                        f"{case.name}: {runtime} returned dates "
                                        f"{observed_dates!r}, expected exact inclusive range "
                                        f"{web_date_control_dates!r}"
                                    )
                            elif case.name == "web-market-matrix-all-vn-limit-21-enddate-csv":
                                market_matrix_symbols = {
                                    symbol for source, symbol, *_ in TICKERS if source == "vn"
                                }
                                expected_market_matrix_rows = Counter(
                                    (symbol, stamp.date().isoformat())
                                    for symbol in market_matrix_symbols
                                    for stamp in FIXTURE_DATES[-21:]
                                )
                                observed_market_matrix_rows = Counter(
                                    (row["symbol"], row["time"]) for row in csv_rows
                                )
                                if observed_market_matrix_rows != expected_market_matrix_rows:
                                    failures.append(
                                        f"{case.name}: {runtime} returned symbol/date rows "
                                        f"{observed_market_matrix_rows!r}, expected "
                                        f"{expected_market_matrix_rows!r}"
                                    )
                                for row in csv_rows:
                                    symbol = row["symbol"]
                                    date_index = next(
                                        (
                                            index
                                            for index, stamp in enumerate(FIXTURE_DATES)
                                            if stamp.date().isoformat() == row["time"]
                                        ),
                                        -1,
                                    )
                                    expected_values = _expected_fixture_ticker_metrics(
                                        "vn",
                                        symbol,
                                        date_index,
                                        ema=True,
                                        output_limit=21,
                                    )
                                    for field, expected_value in expected_values.items():
                                        try:
                                            actual_value = (
                                                int(row[field])
                                                if field == "volume"
                                                else float(row[field])
                                            )
                                        except (KeyError, ValueError):
                                            actual_value = math.nan
                                        if not math.isclose(
                                            actual_value,
                                            expected_value,
                                            rel_tol=0,
                                            abs_tol=0.0051,
                                        ):
                                            failures.append(
                                                f"{case.name}: {runtime} {symbol} {field} "
                                                f"{actual_value!r}, expected {expected_value!r} "
                                                f"at {row['time']!r}"
                                            )
                                    expected_candle = fixture_daily_by_key.get(
                                        ("vn", symbol, row["time"])
                                    )
                                    try:
                                        actual_ohlcv = {
                                            field: float(row[field])
                                            for field in ("open", "high", "low")
                                        }
                                        actual_volume = int(row["volume"])
                                    except (KeyError, ValueError):
                                        actual_ohlcv = {
                                            field: math.nan for field in ("open", "high", "low")
                                        }
                                        actual_volume = -1
                                    if (
                                        expected_candle is None
                                        or any(
                                            not math.isclose(
                                                actual_ohlcv[field],
                                                getattr(expected_candle, field),
                                                rel_tol=0,
                                                abs_tol=0.0051,
                                            )
                                            for field in ("open", "high", "low")
                                        )
                                        or actual_volume
                                        != (expected_candle.volume if expected_candle else -1)
                                    ):
                                        failures.append(
                                            f"{case.name}: {runtime} {symbol}/{row['time']} "
                                            f"open/high/low/volume {actual_ohlcv!r}/"
                                            f"{actual_volume!r}, expected fixture candle "
                                            f"{expected_candle!r}"
                                        )
                            elif case.name in {
                                "web-mixed-watchlist-mode-all-csv",
                                "web-mixed-watchlist-mode-all-sma-csv",
                                "web-mixed-watchlist-mode-all-hourly-csv",
                                "web-trend-signal-table-mode-all-hourly-csv",
                            }:
                                selected_sources = {
                                    "AAPL": "yahoo",
                                    "BTCUSDT": "crypto",
                                    "ETHUSDT": "crypto",
                                    "FPT": "vn",
                                    "VNINDEX": "vn",
                                }
                                if case.name in {
                                    "web-mixed-watchlist-mode-all-csv",
                                    "web-mixed-watchlist-mode-all-sma-csv",
                                }:
                                    expected_mixed_rows = Counter(
                                        (symbol, stamp.date().isoformat())
                                        for symbol in selected_sources
                                        for stamp in FIXTURE_DATES[-21:]
                                    )
                                    observed_mixed_rows = Counter(
                                        (row["symbol"], row["time"])
                                        for row in csv_rows
                                    )
                                    if observed_mixed_rows != expected_mixed_rows:
                                        failures.append(
                                            f"{case.name}: {runtime} returned symbol/date rows "
                                            f"{observed_mixed_rows!r}, expected "
                                            f"{expected_mixed_rows!r}"
                                        )
                                    for row in csv_rows:
                                        symbol = row["symbol"]
                                        source = selected_sources.get(symbol)
                                        date_index = next(
                                            (
                                                index
                                                for index, stamp in enumerate(FIXTURE_DATES)
                                                if stamp.date().isoformat() == row["time"]
                                            ),
                                            -1,
                                        )
                                        if source is None or date_index < 1:
                                            failures.append(
                                                f"{case.name}: {runtime} unexpected fixture identity/date "
                                                f"{symbol!r}/{row.get('time')!r}"
                                            )
                                            continue
                                        expected_values = _expected_fixture_ticker_metrics(
                                            source,
                                            symbol,
                                            date_index,
                                            ema=case.name == "web-mixed-watchlist-mode-all-csv",
                                            output_limit=21,
                                        )
                                        for field, expected_value in expected_values.items():
                                            try:
                                                actual_value = (
                                                    int(row[field])
                                                    if field == "volume"
                                                    else float(row[field])
                                                )
                                            except (KeyError, ValueError):
                                                actual_value = math.nan
                                            if not math.isclose(
                                                actual_value,
                                                expected_value,
                                                rel_tol=0,
                                                abs_tol=0.0051,
                                            ):
                                                failures.append(
                                                    f"{case.name}: {runtime} {symbol} {field} "
                                                    f"{actual_value!r}, expected {expected_value!r} "
                                                    f"at {row['time']!r}"
                                                )
                                else:
                                    hourly_end = int(
                                        FIXTURE_END.replace(
                                            hour=23, minute=59, second=59
                                        ).timestamp()
                                    )
                                    hourly_limit = (
                                        40
                                        if case.name
                                        == "web-trend-signal-table-mode-all-hourly-csv"
                                        else 21
                                    )
                                    identities = (
                                        _mode_all_fixture_tickers()
                                        if case.name
                                        == "web-trend-signal-table-mode-all-hourly-csv"
                                        else tuple(
                                            (source, symbol)
                                            for symbol, source in selected_sources.items()
                                        )
                                    )
                                    expected_hourly_by_symbol = {}
                                    for source, symbol in identities:
                                        all_symbol_rows = sorted(
                                            (
                                                candle
                                                for candle in rows
                                                if candle.source == source
                                                and candle.symbol == symbol
                                                and candle.interval == "1h"
                                                and candle.time <= hourly_end
                                            ),
                                            key=lambda candle: candle.time,
                                        )
                                        expected_hourly_by_symbol[symbol] = all_symbol_rows
                                    expected_hourly_rows = {
                                        (symbol, candle.time): candle
                                        for symbol, symbol_rows in expected_hourly_by_symbol.items()
                                        for candle in symbol_rows[-hourly_limit:]
                                    }
                                    actual_hourly_keys = Counter()
                                    for row in csv_rows:
                                        stamp = _csv_time_epoch(row["time"])
                                        if stamp is None:
                                            failures.append(
                                                f"{case.name}: {runtime} invalid hourly time "
                                                f"{row['time']!r}"
                                            )
                                            continue
                                        actual_hourly_keys[(row["symbol"], stamp)] += 1
                                    if actual_hourly_keys != Counter(expected_hourly_rows.keys()):
                                        failures.append(
                                            f"{case.name}: {runtime} hourly symbol/time rows "
                                            f"{actual_hourly_keys!r}, expected "
                                            f"{Counter(expected_hourly_rows.keys())!r}"
                                        )
                                    for row in csv_rows:
                                        stamp = _csv_time_epoch(row["time"])
                                        candle = expected_hourly_rows.get((row["symbol"], stamp))
                                        if candle is None:
                                            continue
                                        try:
                                            actual_ohlcv = {
                                                field: float(row[field])
                                                for field in ("open", "high", "low", "close")
                                            }
                                            actual_volume = int(row["volume"])
                                        except (KeyError, ValueError):
                                            failures.append(
                                                f"{case.name}: {runtime} malformed OHLCV row "
                                                f"{row!r}"
                                            )
                                            continue
                                        if (
                                            any(
                                                not math.isclose(
                                                    actual_ohlcv[field],
                                                    getattr(candle, field),
                                                    rel_tol=0,
                                                    abs_tol=0.0051,
                                                )
                                                for field in ("open", "high", "low", "close")
                                            )
                                            or actual_volume != candle.volume
                                        ):
                                            failures.append(
                                                f"{case.name}: {runtime} {row['symbol']} "
                                                f"{row['time']} OHLCV {actual_ohlcv!r}/"
                                                f"{actual_volume!r}, expected "
                                                f"{candle.open, candle.high, candle.low, candle.close, candle.volume!r}"
                                            )

                                    for symbol, symbol_rows in expected_hourly_by_symbol.items():
                                        if not symbol_rows:
                                            failures.append(
                                                f"{case.name}: fixture has no hourly bars for "
                                                f"{symbol}"
                                            )
                                            continue
                                        latest_candle = symbol_rows[-1]
                                        latest_row = next(
                                            (
                                                row
                                                for row in csv_rows
                                                if row["symbol"] == symbol
                                                and _csv_time_epoch(row["time"])
                                                == latest_candle.time
                                            ),
                                            None,
                                        )
                                        expected_ema10 = _expected_ema(
                                            [candle.close for candle in symbol_rows], 10
                                        )
                                        expected_ema20 = _expected_ema(
                                            [candle.close for candle in symbol_rows], 20
                                        )
                                        try:
                                            actual_ema10 = (
                                                float(latest_row["ma10"])
                                                if latest_row is not None
                                                else math.nan
                                            )
                                            actual_ema20 = (
                                                float(latest_row["ma20"])
                                                if latest_row is not None
                                                else math.nan
                                            )
                                        except ValueError:
                                            actual_ema10 = actual_ema20 = math.nan
                                        if (
                                            latest_row is None
                                            or not math.isclose(
                                                actual_ema10,
                                                expected_ema10,
                                                rel_tol=0,
                                                abs_tol=0.0051,
                                            )
                                            or not math.isclose(
                                                actual_ema20,
                                                expected_ema20,
                                                rel_tol=0,
                                                abs_tol=0.0051,
                                            )
                                        ):
                                            failures.append(
                                                f"{case.name}: {runtime} {symbol} latest "
                                                f"EMA10/EMA20 {actual_ema10!r}/{actual_ema20!r}, "
                                                f"expected {expected_ema10!r}/{expected_ema20!r}"
                                            )

                    if case.name in {
                        "web-playground-native-hourly-range-csv",
                        "web-playground-native-weekly-range-csv",
                        "web-playground-native-weekly-range-csv-postgres",
                        "web-playground-lazy-minute-range-csv",
                    }:
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            csv_rows, parse_errors = _parse_web_ticker_csv(response.text)
                            if parse_errors:
                                failures.append(
                                    f"{case.name}: {runtime} Playground CSV parse errors "
                                    f"{parse_errors!r}"
                                )
                            if case.name == "web-playground-native-hourly-range-csv":
                                expected_candles = _expected_csv_candle_rows(
                                    playground_hourly_candles, interval="1h", ema=True
                                )
                            elif case.name in {
                                "web-playground-native-weekly-range-csv",
                                "web-playground-native-weekly-range-csv-postgres",
                            }:
                                # A redis=false SQL request still uses the
                                # available ZSET's coverage to choose the same
                                # retained raw window; without Redis it keeps
                                # the partial first SQL bucket.
                                expected_candles = (
                                    playground_weekly_uncovered_sql_expected
                                    if case.name.endswith("-postgres") and not use_live_redis
                                    else playground_weekly_expected
                                )
                            else:
                                    expected_candles = playground_minute_expected
                            actual_identity_dates = [
                                (row.get("symbol"), row.get("time")) for row in csv_rows
                            ]
                            expected_identity_dates = [
                                (row["symbol"], row["time"]) for row in expected_candles
                            ]
                            if actual_identity_dates != expected_identity_dates:
                                failures.append(
                                    f"{case.name}: {runtime} returned symbol/time rows "
                                    f"{actual_identity_dates!r}, expected "
                                    f"{expected_identity_dates!r}"
                                )
                            if len(csv_rows) == len(expected_candles):
                                for actual, expected in zip(
                                    csv_rows, expected_candles, strict=True
                                ):
                                    for field_name, expected_value in expected.items():
                                        raw_value = actual.get(field_name, "")
                                        if expected_value is None:
                                            if raw_value != "":
                                                failures.append(
                                                    f"{case.name}: {runtime} {field_name} "
                                                    f"was {raw_value!r}, expected an empty CSV cell"
                                                )
                                        elif field_name in {"symbol", "time"}:
                                            if raw_value != expected_value:
                                                failures.append(
                                                    f"{case.name}: {runtime} {field_name} "
                                                    f"was {raw_value!r}, expected {expected_value!r}"
                                                )
                                        else:
                                            try:
                                                actual_value = (
                                                    int(raw_value)
                                                    if field_name == "volume"
                                                    else float(raw_value)
                                                )
                                            except ValueError:
                                                actual_value = math.nan
                                            if not math.isclose(
                                                actual_value,
                                                float(expected_value),
                                                rel_tol=0,
                                                abs_tol=0.0051,
                                            ):
                                                failures.append(
                                                    f"{case.name}: {runtime} {field_name} "
                                                    f"{actual_value!r}, expected {expected_value!r} "
                                                    f"at {expected['time']!r}"
                                                )

                    if case.name in {
                        "web-volume-profile-widget-date-discovery-csv",
                        "web-volume-profile-widget-dated-daily-csv",
                    }:
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            csv_rows, parse_errors = _parse_web_ticker_csv(response.text)
                            expected_candle = fixture_daily_by_key.get(
                                ("crypto", "ETHUSDT", FIXTURE_END.date().isoformat())
                            )
                            expected_ema10 = _expected_fixture_ema("crypto", "ETHUSDT", 10)
                            expected_ema20 = _expected_fixture_ema("crypto", "ETHUSDT", 20)
                            row = csv_rows[0] if len(csv_rows) == 1 else {}
                            try:
                                actual_ohlcv = {
                                    field: float(row[field])
                                    for field in ("open", "high", "low", "close")
                                }
                                actual_volume = int(row["volume"])
                                actual_ema10 = float(row["ma10"])
                                actual_ema20 = float(row["ma20"])
                            except (KeyError, ValueError):
                                actual_ohlcv = {}
                                actual_volume = -1
                                actual_ema10 = actual_ema20 = math.nan
                            if (
                                parse_errors
                                or len(csv_rows) != 1
                                or row.get("symbol") != "ETHUSDT"
                                or row.get("time") != FIXTURE_END.date().isoformat()
                                or expected_candle is None
                                or any(
                                    not math.isclose(
                                        actual_ohlcv.get(field, math.nan),
                                        getattr(expected_candle, field),
                                        rel_tol=0,
                                        abs_tol=0.0051,
                                    )
                                    for field in ("open", "high", "low", "close")
                                )
                                or actual_volume != expected_candle.volume
                                or not math.isclose(actual_ema10, expected_ema10, rel_tol=0, abs_tol=0.0051)
                                or not math.isclose(actual_ema20, expected_ema20, rel_tol=0, abs_tol=0.0051)
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} did not match the actual dated "
                                    f"ETHUSDT widget request: rows={csv_rows!r}, "
                                    f"parse_errors={parse_errors!r}"
                                )

                    if case.name == "web-backtest-current-prices-comma-joined-csv":
                        # The production backtest leaves the default snapshot
                        # option enabled. The seeded Redis fixture only has a
                        # ticker snapshot for VCB; FPT therefore comes from the
                        # independent PostgreSQL fixture.
                        fpt_candle = fixture_daily_by_key[
                            ("vn", "FPT", FIXTURE_END.date().isoformat())
                        ]
                        vcb_candle = fixture_daily_by_key[
                            ("vn", "VCB", FIXTURE_END.date().isoformat())
                        ]
                        vcb_expected = (
                            {
                                "open": 9800.0,
                                "high": 9900.0,
                                "low": 9700.0,
                                "close": 9876.5,
                                "volume": 250_000,
                            }
                            if use_live_redis
                            else {
                                "open": vcb_candle.open,
                                "high": vcb_candle.high,
                                "low": vcb_candle.low,
                                "close": vcb_candle.close,
                                "volume": vcb_candle.volume,
                            }
                        )
                        expected_candles = {
                            "FPT": {
                                "open": fpt_candle.open,
                                "high": fpt_candle.high,
                                "low": fpt_candle.low,
                                "close": fpt_candle.close,
                                "volume": fpt_candle.volume,
                            },
                            "VCB": vcb_expected,
                        }
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            csv_rows, parse_errors = _parse_web_ticker_csv(response.text)
                            actual_by_symbol = {row["symbol"]: row for row in csv_rows}
                            if (
                                parse_errors
                                or set(actual_by_symbol) != set(expected_candles)
                                or len(csv_rows) != len(expected_candles)
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} did not return one row for each "
                                    f"comma-joined ticker: rows={csv_rows!r}, "
                                    f"parse_errors={parse_errors!r}"
                                )
                            for symbol, expected in expected_candles.items():
                                row = actual_by_symbol.get(symbol)
                                if row is None:
                                    continue
                                try:
                                    actual_ohlcv = {
                                        field: float(row[field])
                                        for field in ("open", "high", "low", "close")
                                    }
                                    actual_volume = int(row["volume"])
                                except (KeyError, ValueError):
                                    failures.append(
                                        f"{case.name}: {runtime} malformed {symbol} row {row!r}"
                                    )
                                    continue
                                if (
                                    row.get("time") != FIXTURE_END.date().isoformat()
                                    or any(
                                        not math.isclose(
                                            actual_ohlcv[field],
                                            expected[field],
                                            rel_tol=0,
                                            abs_tol=0.0051,
                                        )
                                        for field in ("open", "high", "low", "close")
                                    )
                                    or actual_volume != expected["volume"]
                                ):
                                    failures.append(
                                        f"{case.name}: {runtime} {symbol} row {row!r} did not "
                                        f"match independent expected values {expected!r}"
                                    )

                    if case.name in {
                        "web-alert-context-mode-all-no-ma-csv",
                        "web-alert-context-max-lookback-mode-all-no-ma-csv",
                    }:
                        alert_limit = (
                            110
                            if case.name == "web-alert-context-max-lookback-mode-all-no-ma-csv"
                            else 4
                        )
                        # The caller can request 110 rows, but both APIs clamp
                        # multi-symbol responses to API_MAX_LIMIT=40.
                        effective_alert_limit = min(alert_limit, 40)
                        selected_tickers = _mode_all_fixture_tickers()
                        selected_sources = {
                            symbol: source for source, symbol in selected_tickers
                        }
                        expected_alert_symbols = Counter(
                            {
                                symbol: min(
                                    effective_alert_limit,
                                    11 if symbol == "CSVEDGE=F" else DAILY_BARS,
                                )
                                for _source, symbol in selected_tickers
                            }
                        )
                        expected_alert_symbol_dates = Counter(
                            (symbol, FIXTURE_DATES[index].date().isoformat())
                            for source, symbol in selected_tickers
                            for index in (
                                range(
                                    11 - min(effective_alert_limit, 11),
                                    11,
                                )
                                if source == "yahoo" and symbol == "CSVEDGE=F"
                                else range(
                                    DAILY_BARS
                                    - min(effective_alert_limit, DAILY_BARS),
                                    DAILY_BARS,
                                )
                            )
                        )
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            lines = response.text.splitlines()
                            rows_for_alert = []
                            observed_alert_symbols = Counter()
                            observed_alert_symbol_dates = Counter()
                            parse_errors = []
                            for line in lines[1:]:
                                values = next(csv.reader([line]))
                                if line.startswith("DUPL,FPT,"):
                                    if len(values) == len(WEB_TICKER_CSV_COLUMNS) + 1:
                                        row = dict(
                                            zip(
                                                WEB_TICKER_CSV_COLUMNS[1:],
                                                values[2:],
                                                strict=True,
                                            )
                                        )
                                        row["symbol"] = "DUPL,FPT"
                                        rows_for_alert.append(row)
                                        observed_alert_symbols["DUPL,FPT"] += 1
                                        observed_alert_symbol_dates[
                                            ("DUPL,FPT", row["time"])
                                        ] += 1
                                    else:
                                        parse_errors.append(values)
                                    continue
                                if len(values) == len(WEB_TICKER_CSV_COLUMNS):
                                    row = dict(zip(WEB_TICKER_CSV_COLUMNS, values, strict=True))
                                    rows_for_alert.append(row)
                                    observed_alert_symbols[row["symbol"]] += 1
                                    observed_alert_symbol_dates[
                                        (row["symbol"], row["time"])
                                    ] += 1
                                else:
                                    parse_errors.append(values)
                            if (
                                observed_alert_symbols != expected_alert_symbols
                                or observed_alert_symbol_dates
                                != expected_alert_symbol_dates
                                or parse_errors
                                or any(
                                row.get(column, "")
                                for row in rows_for_alert
                                for column in WEB_TICKER_CSV_COLUMNS[7:17]
                                )
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} no-MA CSV rows did not exactly "
                                    f"match the expected universe ({observed_alert_symbols!r} vs "
                                    f"{expected_alert_symbols!r}; symbol/date rows "
                                    f"{observed_alert_symbol_dates!r} vs "
                                    f"{expected_alert_symbol_dates!r}); "
                                    f"parse errors={parse_errors!r}; "
                                    f"rows={rows_for_alert!r}"
                                )
                            for row in rows_for_alert:
                                symbol = row["symbol"]
                                source = selected_sources[symbol]
                                candle = fixture_daily_by_key.get(
                                    (source, symbol, row.get("time", ""))
                                )
                                try:
                                    actual_ohlcv = {
                                        field: float(row[field])
                                        for field in ("open", "high", "low", "close")
                                    }
                                    actual_volume = int(row.get("volume", "-1"))
                                except (KeyError, ValueError):
                                    actual_ohlcv = {
                                        field: math.nan
                                        for field in ("open", "high", "low", "close")
                                    }
                                    actual_volume = -1
                                if (
                                    candle is None
                                    or any(
                                        not math.isclose(
                                            actual_ohlcv[field],
                                            getattr(candle, field),
                                            rel_tol=0,
                                            abs_tol=0.0051,
                                        )
                                        for field in ("open", "high", "low", "close")
                                    )
                                    or actual_volume
                                    != (candle.volume if candle is not None else -1)
                                ):
                                    failures.append(
                                        f"{case.name}: {runtime} {symbol}/{row.get('time')} "
                                        f"OHLCV {actual_ohlcv!r}/{actual_volume!r}, "
                                        f"expected fixture candle {candle!r}"
                                    )

                    if case.name == "web-trend-signal-table-mode-all-csv":
                        expected_daily_candles = {}
                        for source, symbol in _mode_all_fixture_tickers():
                            indexes = (
                                range(8, 11)
                                if source == "yahoo" and symbol == "CSVEDGE=F"
                                else range(DAILY_BARS - 3, DAILY_BARS)
                            )
                            for index in indexes:
                                candle = fixture_daily_by_key.get(
                                    (source, symbol, FIXTURE_DATES[index].date().isoformat())
                                )
                                if candle is not None:
                                    expected_daily_candles[
                                        (symbol, FIXTURE_DATES[index].date().isoformat())
                                    ] = candle
                        expected_symbol_dates = Counter(expected_daily_candles.keys())
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            csv_rows, parse_errors = _parse_web_ticker_csv(response.text)
                            observed_symbol_dates = Counter(
                                (row["symbol"], row["time"]) for row in csv_rows
                            )
                            if observed_symbol_dates != expected_symbol_dates:
                                failures.append(
                                    f"{case.name}: {runtime} did not return the latest three "
                                    "rows per all-universe ticker: "
                                    f"{observed_symbol_dates!r}, expected "
                                    f"{expected_symbol_dates!r}"
                                )
                            if parse_errors:
                                failures.append(
                                    f"{case.name}: {runtime} CSV parse errors {parse_errors!r}"
                                )
                            for row in csv_rows:
                                candle = expected_daily_candles.get(
                                    (row["symbol"], row["time"])
                                )
                                if candle is None:
                                    continue
                                try:
                                    actual_ohlcv = {
                                        field: float(row[field])
                                        for field in ("open", "high", "low", "close")
                                    }
                                    actual_volume = int(row["volume"])
                                except (KeyError, ValueError):
                                    failures.append(
                                        f"{case.name}: {runtime} malformed OHLCV row {row!r}"
                                    )
                                    continue
                                if (
                                    any(
                                        not math.isclose(
                                            actual_ohlcv[field],
                                            getattr(candle, field),
                                            rel_tol=0,
                                            abs_tol=0.0051,
                                        )
                                        for field in ("open", "high", "low", "close")
                                    )
                                    or actual_volume != candle.volume
                                ):
                                    failures.append(
                                        f"{case.name}: {runtime} {row['symbol']}/{row['time']} "
                                        f"OHLCV {actual_ohlcv!r}/{actual_volume!r}, expected "
                                        f"{candle.open, candle.high, candle.low, candle.close, candle.volume!r}"
                                    )

                    expected_ticker_symbols = {
                        "ticker-more-than-500-symbol-parameters": {"FPT"},
                        "ticker-comma-joined-multiple-symbols": {"FPT", "VCB"},
                        "ticker-ambiguous-literal-comma-symbol": {"DUPL,FPT"},
                        "ticker-cache-comma-literal-first": {"DUPL,FPT"},
                        "ticker-cache-comma-repeated-second": {"DUPL", "FPT"},
                        "ticker-cache-comma-repeated-first": {"DUPL", "FPT"},
                        "ticker-cache-comma-literal-second": {"DUPL,FPT"},
                    }.get(case.name)
                    if expected_ticker_symbols is not None:
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            if case.name in {
                                "ticker-cache-comma-repeated-second",
                                "ticker-cache-comma-literal-second",
                            } and response.headers.get("x-data-source") == "in-memory":
                                failures.append(
                                    f"{case.name}: {runtime} reused the other symbol selection's cached response"
                                )
                            try:
                                ticker_payload = response.json()
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                ticker_payload = None
                            if not isinstance(ticker_payload, dict) or set(ticker_payload) != (
                                expected_ticker_symbols
                            ):
                                actual_symbols = (
                                    set(ticker_payload)
                                    if isinstance(ticker_payload, dict)
                                    else None
                                )
                                failures.append(
                                    f"{case.name}: {runtime} symbols were {actual_symbols!r}, "
                                    f"expected {expected_ticker_symbols!r}"
                                )
                            elif any(
                                not ticker_payload[symbol] for symbol in expected_ticker_symbols
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} returned an empty bar list for "
                                    f"{sorted(expected_ticker_symbols)!r}"
                                )
                            if (
                                case.name == "ticker-more-than-500-symbol-parameters"
                                and isinstance(ticker_payload, dict)
                            ):
                                fpt_rows = ticker_payload.get("FPT", [])
                                expected_candle = fixture_daily_by_key.get(
                                    ("vn", "FPT", FIXTURE_END.date().isoformat())
                                )
                                if (
                                    len(fpt_rows) != 1
                                    or expected_candle is None
                                    or fpt_rows[0].get("time") != FIXTURE_END.date().isoformat()
                                    or any(
                                        not math.isclose(
                                            float(fpt_rows[0].get(field, math.nan)),
                                            getattr(expected_candle, field),
                                            rel_tol=0,
                                            abs_tol=0.0051,
                                        )
                                        for field in ("open", "high", "low", "close")
                                    )
                                    or fpt_rows[0].get("volume") != expected_candle.volume
                                ):
                                    failures.append(
                                        f"{case.name}: {runtime} FPT rows {fpt_rows!r}, "
                                        f"expected exactly the latest fixture candle {expected_candle!r}"
                                    )

                    if (
                        case.name.startswith(("home-", "production-origin-bootstrap-"))
                        and "-csv-" in case.name
                    ) or case.name.startswith("production-origin-bootstrap-"):
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            failures.extend(_assert_home_csv_expectations(case, response, runtime))
                    if case.name.startswith("production-origin-data-ticker-") and (
                        "-json" not in case.name
                    ):
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            if response.status_code != 200:
                                failures.append(
                                    f"{case.name}: expected {runtime} HTTP 200, "
                                    f"got {response.status_code}"
                                )
                            failures.extend(_assert_home_csv_expectations(case, response, runtime))

                    if refresh_before_states is not None:
                        failures.extend(
                            _assert_refresh_result(
                                case,
                                rust_response,
                                python_response,
                                refresh_before_states,
                                postgres_handle,
                                app.state.repo,
                            )
                        )

                    if (
                        case.headers
                        and case.headers.get("origin") == PRODUCTION_WEB_ORIGIN
                        and case.method != "OPTIONS"
                    ):
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            if response.headers.get("access-control-allow-origin") != (
                                PRODUCTION_WEB_ORIGIN
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} did not allow production origin; "
                                    f"got {response.headers.get('access-control-allow-origin')!r}"
                                )
                            expected_exposed = (
                                "x-data-source,x-redis-base,x-redis-raw-count,x-redis-aligned,"
                                "x-redis-limit,cf-cache-status,cf-ray,x-ratelimit-limit,"
                                "x-ratelimit-remaining,retry-after"
                            )
                            if response.headers.get("access-control-expose-headers") != (
                                expected_exposed
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} exposed headers were "
                                    f"{response.headers.get('access-control-expose-headers')!r}, "
                                    f"expected {expected_exposed!r}"
                                )
                            if case.name.startswith("production-origin-data-"):
                                exposed_headers = {
                                    item.strip().lower()
                                    for item in response.headers.get(
                                        "access-control-expose-headers", ""
                                    ).split(",")
                                }
                                if "x-data-source" not in exposed_headers:
                                    failures.append(
                                        f"{case.name}: {runtime} CORS does not expose x-data-source"
                                    )
                                if case.name.startswith(
                                    "production-origin-data-ticker-"
                                ) and not response.headers.get("x-data-source"):
                                    failures.append(
                                        f"{case.name}: {runtime} response omitted x-data-source"
                                    )

                    if (
                        case.headers
                        and case.headers.get("origin") == PRODUCTION_WEB_ORIGIN
                        and case.method == "OPTIONS"
                    ):
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            allowed_methods = {
                                method.strip()
                                for method in response.headers.get(
                                    "access-control-allow-methods", ""
                                ).split(",")
                            }
                            allowed_headers = {
                                header.strip().lower()
                                for header in response.headers.get(
                                    "access-control-allow-headers", ""
                                ).split(",")
                            }
                            requested_method = case.headers.get(
                                "access-control-request-method", ""
                            ).upper()
                            requested_headers = {
                                header.strip().lower()
                                for header in case.headers.get(
                                    "access-control-request-headers", ""
                                ).split(",")
                                if header.strip()
                            }
                            if response.status_code != 200 or response.content != b"":
                                failures.append(
                                    f"{case.name}: expected {runtime} empty HTTP 200, got "
                                    f"{response.status_code} and {len(response.content)} bytes"
                                )
                            if response.headers.get("access-control-allow-origin") != (
                                PRODUCTION_WEB_ORIGIN
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} allow-origin was "
                                    f"{response.headers.get('access-control-allow-origin')!r}"
                                )
                            if case.name == "production-origin-head-options-preflight":
                                if requested_method != "HEAD" or "HEAD" in allowed_methods:
                                    failures.append(
                                        f"{case.name}: {runtime} CORS method policy was "
                                        f"{sorted(allowed_methods)!r}; expected HEAD to remain "
                                        "outside the advertised preflight method set"
                                    )
                            elif requested_method not in allowed_methods:
                                failures.append(
                                    f"{case.name}: {runtime} did not allow requested method "
                                    f"{requested_method}; "
                                    f"methods={sorted(allowed_methods)!r}, "
                                    f"headers={sorted(allowed_headers)!r}"
                                )
                            if not requested_headers <= allowed_headers:
                                failures.append(
                                    f"{case.name}: {runtime} did not allow requested headers "
                                    f"{sorted(requested_headers - allowed_headers)!r}; "
                                    f"allowed={sorted(allowed_headers)!r}"
                                )

                    if case.name in {
                        "volume-profile-date-overrides-range",
                        "volume-profile-start-only",
                    }:
                        # A date query takes precedence over conflicting range
                        # fields; a start-only query defaults its end to start.
                        # Compare each implementation with its own date-only
                        # result so identical Rust/Python bugs cannot pass.
                        date_only_path = (
                            "/analysis/volume-profile?symbol=FPT&mode=vn&date="
                            f"{FIXTURE_END.date().isoformat()}"
                            "&bins=20&value_area_pct=70"
                        )
                        for runtime, client, response in (
                            ("Rust", rust_client, rust_response),
                            ("Python", python_client, python_response),
                        ):
                            expected_response = client.get(
                                date_only_path,
                                headers={"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                            )
                            if response.status_code != 200:
                                failures.append(
                                    f"{case.name}: expected {runtime} HTTP 200, "
                                    f"got {response.status_code}"
                                )
                            if expected_response.status_code != 200:
                                failures.append(
                                    f"{case.name}: {runtime} date-only oracle returned "
                                    f"{expected_response.status_code}"
                                )
                            else:
                                difference = _differences(
                                    _snapshot(expected_response), _snapshot(response)
                                )
                                if difference:
                                    failures.append(
                                        f"{case.name}: {runtime} did not match its "
                                        "date-only semantics: " + "; ".join(difference)
                                    )

                    if case.name == "head-tickers-data-csv":
                        get_responses = {}
                        for runtime, client, response in (
                            ("Rust", rust_client, rust_response),
                            ("Python", python_client, python_response),
                        ):
                            if response.status_code != 200 or response.content != b"":
                                failures.append(
                                    f"{case.name}: expected {runtime} HTTP 200 with no body, "
                                    f"got {response.status_code} and {len(response.content)} bytes"
                                )
                            if not response.headers.get("content-type", "").startswith("text/csv"):
                                failures.append(
                                    f"{case.name}: {runtime} did not preserve CSV content type"
                                )
                            get_response = client.get(case.path, headers=case.headers)
                            get_responses[runtime] = get_response
                            if get_response.status_code != 200:
                                failures.append(
                                    f"{case.name}: matching {runtime} GET returned "
                                    f"{get_response.status_code}"
                                )
                            else:
                                reader = csv.DictReader(get_response.text.splitlines())
                                csv_rows = list(reader)
                                if tuple(reader.fieldnames or ()) != WEB_TICKER_CSV_COLUMNS:
                                    failures.append(
                                        f"{case.name}: matching {runtime} GET did not return "
                                        f"the full web ticker CSV column contract: "
                                        f"{reader.fieldnames!r}"
                                    )
                                if (
                                    len(csv_rows) != 1
                                    or csv_rows[0].get("symbol") != "FPT"
                                    or not csv_rows[0].get("close")
                                ):
                                    failures.append(
                                        f"{case.name}: matching {runtime} GET did not return "
                                        f"one populated FPT row: {csv_rows!r}"
                                    )
                                difference = _differences(
                                    _snapshot(
                                        get_response,
                                        case.ignored_headers,
                                        request_method="GET",
                                    ),
                                    _snapshot(
                                        response,
                                        case.ignored_headers,
                                        request_method=case.method,
                                    ),
                                    compare_body=False,
                                    health_clock_policy=case.health_clock_policy,
                                )
                                if difference:
                                    failures.append(
                                        f"{case.name}: {runtime} HEAD headers differ from its "
                                        "matching GET: " + "; ".join(difference)
                                    )
                                get_length = get_response.headers.get("content-length")
                                head_length = response.headers.get("content-length")
                                if get_length is None:
                                    failures.append(
                                        f"{case.name}: {runtime} GET omitted Content-Length"
                                    )
                                elif head_length != get_length:
                                    failures.append(
                                        f"{case.name}: {runtime} HEAD Content-Length "
                                        f"{head_length!r} differs from matching GET "
                                        f"Content-Length {get_length!r}"
                                    )
                                if get_length is not None:
                                    try:
                                        parsed_get_length = int(get_length)
                                    except ValueError:
                                        failures.append(
                                            f"{case.name}: {runtime} GET Content-Length "
                                            f"{get_length!r} is not an integer"
                                        )
                                    else:
                                        if parsed_get_length != len(get_response.content):
                                            failures.append(
                                                f"{case.name}: {runtime} GET Content-Length "
                                                f"{get_length!r} does not match its body size "
                                                f"{len(get_response.content)}"
                                            )
                        if set(get_responses) == {"Rust", "Python"}:
                            difference = _differences(
                                _snapshot(get_responses["Rust"], case.ignored_headers),
                                _snapshot(get_responses["Python"], case.ignored_headers),
                            )
                            if difference:
                                failures.append(
                                    f"{case.name}: matching GET representations differ across "
                                    "Rust and Python: " + "; ".join(difference)
                                )

                    if case.name.startswith("head-contract-") or case.name in {
                        "head-health",
                        "head-health-gzip",
                        "head-static-js",
                    }:
                        head_method_only_allow = {
                            "head-contract-refresh-post-only": "POST",
                            "head-contract-upload-post-only": "POST",
                            "head-contract-notes-session-delete-only": "DELETE",
                        }
                        expected_head_status = (
                            405 if case.name in head_method_only_allow else 200
                        )
                        self_compare_ignored_headers = case.ignored_headers
                        if case.name in {"head-static-js", "head-contract-public-mount-root"}:
                            # Tower's compression layer adds this GET-only hint
                            # even when HEAD has no representation body. The
                            # paired server comparison still checks both HEAD
                            # responses against each other.
                            self_compare_ignored_headers = (
                                *self_compare_ignored_headers,
                                "vary",
                            )
                        get_responses = {}
                        for runtime, client, response in (
                            ("Rust", rust_client, rust_response),
                            ("Python", python_client, python_response),
                        ):
                            if response.status_code != expected_head_status:
                                failures.append(
                                    f"{case.name}: expected {runtime} HEAD HTTP "
                                    f"{expected_head_status}, got {response.status_code}"
                                )
                            if response.content:
                                failures.append(
                                    f"{case.name}: {runtime} HEAD returned "
                                    f"{len(response.content)} body bytes"
                                )
                            expected_allow = head_method_only_allow.get(case.name)
                            if expected_allow is not None and response.headers.get("allow") != expected_allow:
                                failures.append(
                                    f"{case.name}: {runtime} HEAD Allow header was "
                                    f"{response.headers.get('allow')!r}, expected {expected_allow!r}"
                                )
                            get_response = client.get(case.path, headers=case.headers)
                            get_responses[runtime] = get_response
                            if get_response.status_code != response.status_code:
                                failures.append(
                                    f"{case.name}: {runtime} HEAD status "
                                    f"{response.status_code} differs from GET status "
                                    f"{get_response.status_code}"
                                )
                            difference = _differences(
                                _snapshot(
                                    get_response,
                                    self_compare_ignored_headers,
                                    request_method="GET",
                                ),
                                _snapshot(
                                    response,
                                    self_compare_ignored_headers,
                                    request_method=case.method,
                                ),
                                compare_body=False,
                                health_clock_policy=case.health_clock_policy,
                            )
                            if difference:
                                failures.append(
                                    f"{case.name}: {runtime} HEAD headers differ from its "
                                    "matching GET: " + "; ".join(difference)
                                )
                            if response.headers.get("content-length") != (
                                get_response.headers.get("content-length")
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} HEAD Content-Length "
                                    f"{response.headers.get('content-length')!r} differs from "
                                    f"matching GET Content-Length "
                                    f"{get_response.headers.get('content-length')!r}"
                                )
                            if expected_allow is not None:
                                if (
                                    get_response.status_code != 405
                                    or get_response.content
                                    or get_response.headers.get("allow") != expected_allow
                                ):
                                    failures.append(
                                        f"{case.name}: expected {runtime} GET oracle to be empty "
                                        f"HTTP 405 Allow={expected_allow!r}, got "
                                        f"{get_response.status_code} Allow="
                                        f"{get_response.headers.get('allow')!r} and "
                                        f"{len(get_response.content)} body bytes"
                                    )
                            elif get_response.status_code != 200 or not get_response.content:
                                failures.append(
                                    f"{case.name}: expected {runtime} GET oracle to be a "
                                    f"populated HTTP 200 for {case.path}, got "
                                    f"{get_response.status_code} and {len(get_response.content)} "
                                    f"body bytes: {get_response.text[:200]!r}"
                                )
                        if set(get_responses) == {"Rust", "Python"}:
                            difference = _differences(
                                _snapshot(
                                    get_responses["Rust"],
                                    self_compare_ignored_headers,
                                    request_method="GET",
                                ),
                                _snapshot(
                                    get_responses["Python"],
                                    self_compare_ignored_headers,
                                    request_method="GET",
                                ),
                                volatile_json_fields=case.volatile_json_fields,
                                health_clock_policy=case.health_clock_policy,
                            )
                            if difference:
                                failures.append(
                                    f"{case.name}: matching GET oracle representations differ "
                                    "across Rust and Python: " + "; ".join(difference)
                                )
                        if (
                            case.name == "head-contract-notes-markdown"
                            and set(get_responses) == {"Rust", "Python"}
                        ):
                            difference = _differences(
                                _snapshot(
                                    get_responses["Rust"], self_compare_ignored_headers
                                ),
                                _snapshot(
                                    get_responses["Python"], self_compare_ignored_headers
                                ),
                            )
                            if difference:
                                failures.append(
                                    f"{case.name}: seeded GET Notes representations differ "
                                    "across Rust and Python: " + "; ".join(difference)
                                )
                        if case.name == "head-contract-notes-markdown":
                            # This isolated public session is needed only while
                            # the HEAD oracle runs. Remove it before the Notes
                            # quota cases so it does not consume a quota slot.
                            for implementation in ("rust-uploads", "python-uploads"):
                                shutil.rmtree(
                                    root / implementation / head_notes_session_id,
                                    ignore_errors=True,
                                )

                    if case.name.startswith("method-not-allowed-"):
                        expected_allow = {
                            "health": "GET,HEAD",
                            "tickers": "GET,HEAD",
                            "groups": "GET,HEAD",
                            "names": "GET,HEAD",
                            "info": "GET,HEAD",
                            "refresh": "POST",
                            "top-performers": "GET,HEAD",
                            "ma-scores": "GET,HEAD",
                            "volume-profile": "GET,HEAD",
                            "rrg": "GET,HEAD",
                            "explorer": "GET,HEAD",
                            "sync": "GET,HEAD,POST",
                            "upload": "POST",
                            "notes-markdown": "GET,HEAD",
                            "notes-session": "DELETE",
                            "public-root-post": "GET,HEAD",
                            "public-root-put": "GET,HEAD",
                            "public-root-delete": "GET,HEAD",
                            "public-root-patch": "GET,HEAD",
                            "public-file-delete": "GET,HEAD",
                            "public-file-patch": "GET,HEAD",
                        }[case.name.removeprefix("method-not-allowed-")]
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            if response.status_code != 405:
                                failures.append(
                                    f"{case.name}: expected {runtime} HTTP 405, "
                                    f"got {response.status_code}"
                                )
                            if response.content:
                                failures.append(
                                    f"{case.name}: expected empty {runtime} HTTP 405 body, "
                                    f"got {len(response.content)} bytes"
                                )
                            if response.headers.get("allow") != expected_allow:
                                failures.append(
                                    f"{case.name}: {runtime} Allow header was "
                                    f"{response.headers.get('allow')!r}, "
                                    f"expected {expected_allow!r}"
                                )

                    if case.name == "notes-delete-options-preflight":
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            allowed_methods = response.headers.get(
                                "access-control-allow-methods", ""
                            )
                            if response.status_code != 200 or response.content != b"":
                                failures.append(
                                    f"{case.name}: expected {runtime} HTTP 200 with no body, "
                                    f"got {response.status_code} and {len(response.content)} bytes"
                                )
                            if "DELETE" not in {
                                method.strip() for method in allowed_methods.split(",")
                            }:
                                failures.append(
                                    f"{case.name}: {runtime} did not allow DELETE; "
                                    f"allow-methods was {allowed_methods!r}"
                                )
                            if response.headers.get("access-control-allow-origin") != WEB_ORIGIN:
                                failures.append(
                                    f"{case.name}: {runtime} returned unexpected allow-origin "
                                    f"{response.headers.get('access-control-allow-origin')!r}"
                                )

                    if case.name in {
                        "static-post-file-method-not-allowed",
                        "static-put-file-method-not-allowed",
                    }:
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            if response.status_code != 405 or response.content != b"":
                                failures.append(
                                    f"{case.name}: expected {runtime} empty HTTP 405, got "
                                    f"{response.status_code} and {len(response.content)} bytes"
                                )
                            if response.headers.get("allow") != "GET,HEAD":
                                failures.append(
                                    f"{case.name}: {runtime} Allow header was "
                                    f"{response.headers.get('allow')!r}, expected 'GET,HEAD'"
                                )

                    if case.name == "csv-price-format-thresholds-zero-negative":
                        expected_closes = [
                            "0.00",
                            "-0.000005",
                            "0.000009",
                            "0.00001",
                            "0.00050",
                            "0.0010",
                            "0.9999",
                            "1.000",
                            "99.999",
                            "100.00",
                            "-100.00",
                        ]
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            actual_closes = [
                                row["close"] for row in csv.DictReader(response.text.splitlines())
                            ]
                            if response.status_code != 200 or actual_closes != expected_closes:
                                failures.append(
                                    f"{case.name}: {runtime} close formatting was "
                                    f"{actual_closes!r}, expected {expected_closes!r}"
                                )

                    if case.name == "analysis-mode-all-tie-top-performers":
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            performers = response.json().get("data", {}).get("performers", [])
                            identities = [
                                (row.get("symbol"), row.get("source")) for row in performers
                            ]
                            duplicate_sources = [
                                source for symbol, source in identities if symbol == "DUPL"
                            ]
                            if (
                                not performers
                                or any(row.get("close_changed") != 7.0 for row in performers)
                                or identities != sorted(identities)
                                or duplicate_sources != ["vn", "yahoo"]
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} did not preserve equal-metric "
                                    "symbol/source ordering"
                                )

                    if case.name == "analysis-mode-all-tie-ma-scores":
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            sectors = response.json().get("data", {}).get("sectors", [])
                            sector_keys = [
                                (
                                    sector.get("sector_name"),
                                    next(iter(sector.get("top_stocks", [])), {}).get("source"),
                                )
                                for sector in sectors
                            ]
                            technology_sources = [
                                source for name, source in sector_keys if name == "Technology"
                            ]
                            if (
                                not sectors
                                or any(sector.get("average_score") != 7.0 for sector in sectors)
                                or sector_keys != sorted(sector_keys)
                                or technology_sources != ["vn", "yahoo"]
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} did not preserve equal-score "
                                    "sector/source ordering"
                                )

                    if case.name == "volume-profile-widget-fallback-empty-profile":
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            body = response.json()
                            if (
                                response.status_code != 200
                                or body.get("data")
                                or body.get("error") != "No volume profile data generated"
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} did not return the successful empty-profile body that triggers the widget fallback"
                                )

                    if case.name == "volume-profile-widget-fallback-daily-csv":
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            reader = csv.DictReader(response.text.splitlines())
                            fallback_rows = list(reader)
                            row = fallback_rows[0] if len(fallback_rows) == 1 else None
                            date_index = DAILY_BARS - 2
                            expected_close = _fixture_daily_close("vn", "FPT", date_index)
                            expected_ema = {
                                period: _expected_fixture_ema(
                                    "vn", "FPT", period, bar_count=DAILY_BARS - 1
                                )
                                for period in (10, 20, 50, 100, 200)
                            }
                            valid_fallback = (
                                response.status_code == 200
                                and tuple(reader.fieldnames or ()) == WEB_TICKER_CSV_COLUMNS
                                and row is not None
                                and row.get("symbol") == "FPT"
                                and row.get("time") == widget_fallback_date
                                and math.isclose(
                                    float(row.get("open", "nan")),
                                    expected_close - 0.5,
                                    abs_tol=0.0051,
                                )
                                and math.isclose(
                                    float(row.get("high", "nan")),
                                    expected_close + 1.25,
                                    abs_tol=0.0051,
                                )
                                and math.isclose(
                                    float(row.get("low", "nan")),
                                    expected_close - 1.5,
                                    abs_tol=0.0051,
                                )
                                and math.isclose(
                                    float(row.get("close", "nan")),
                                    expected_close,
                                    abs_tol=0.0051,
                                )
                                and int(row.get("volume", "-1"))
                                == 200_000 + date_index * 100 + len("FPT") * 1_000
                                and all(
                                    math.isclose(
                                        float(row.get(f"ma{period}", "nan")),
                                        expected_ema[period],
                                        abs_tol=0.0051,
                                    )
                                    for period in expected_ema
                                )
                            )
                            if not valid_fallback:
                                failures.append(
                                    f"{case.name}: {runtime} did not return the expected single "
                                    f"FPT fallback row for {widget_fallback_date}: "
                                    f"{fallback_rows!r}"
                                )

                    if case.name == "ticker-explicit-snapshot-hit":
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            rows_for_vcb = response.json().get("VCB", [])
                            actual_close = rows_for_vcb[0].get("close") if rows_for_vcb else None
                            if actual_close != 9876.5:
                                failures.append(
                                    f"{case.name}: {runtime} did not return the seeded snapshot"
                                )

                    if case.name == "top-performers-explicit-joined-snapshot-hit":
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            performers = response.json().get("data", {}).get("performers", [])
                            vcb = next(
                                (row for row in performers if row.get("symbol") == "VCB"), None
                            )
                            if (
                                vcb is None
                                or vcb.get("close") != 9876.5
                                or vcb.get("ma20_score") is not None
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} did not return the seeded joined snapshot"
                                )

                    if (
                        use_live_redis
                        and case.name == "top-performers-default-snapshot-option"
                    ):
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            data = response.json().get("data", {})
                            performers = [
                                *data.get("performers", []),
                                *data.get("worst_performers", []),
                            ]
                            fpt = next(
                                (row for row in performers if row.get("symbol") == "FPT"),
                                None,
                            )
                            if (
                                fpt is None
                                or fpt.get("close") != 101.0
                                or fpt.get("volume") != 250_000
                                or fpt.get("ma20_score") != 1.0
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} did not use the reset default "
                                    f"joined snapshot for FPT: {fpt!r}"
                                )

                    if (
                        use_live_redis
                        and case.name == "ma-scores-by-sector-default-snapshot-option"
                    ):
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            sectors = response.json().get("data", {}).get("sectors", [])
                            technology = next(
                                (
                                    sector
                                    for sector in sectors
                                    if sector.get("sector_name") == "Technology"
                                ),
                                {},
                            )
                            fpt = next(
                                (
                                    row
                                    for row in technology.get("top_stocks", [])
                                    if row.get("symbol") == "FPT"
                                ),
                                None,
                            )
                            if (
                                fpt is None
                                or fpt.get("close") != 101.0
                                or fpt.get("volume") != 250_000
                                or fpt.get("ma_value") != 99.0
                                or fpt.get("ma_score") != 1.0
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} did not use the reset default "
                                    f"joined snapshot for FPT: {fpt!r}"
                                )

                    if case.name == "top-performers-stale-joined-snapshot":
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            payload = response.json()
                            data = payload.get("data", {})
                            performers = [
                                *data.get("performers", []),
                                *data.get("worst_performers", []),
                            ]
                            vcb = next(
                                (row for row in performers if row.get("symbol") == "VCB"), None
                            )
                            if vcb is None or vcb.get("close") != 9876.5:
                                failures.append(
                                    f"{case.name}: {runtime} did not accept the stale joined snapshot"
                                )

                    if case.name.startswith("analysis-mode-all-joined-snapshot-"):
                        is_fallback = case.name.endswith("incomplete-yahoo-fallback")
                        expected_snapshot_closes = {
                            ("vn", "VCB"): 9876.5
                            + _analysis_snapshot_symbols()["vn"].index("VCB") / 100,
                            ("yahoo", "AAPL"): 8765.5,
                            ("crypto", "BTCUSDT"): 7654.5,
                        }
                        fixture_latest_closes = {}
                        for source, symbol in (("yahoo", "AAPL"), ("yahoo", "FPT")):
                            fixture_latest_closes[(source, symbol)] = max(
                                (
                                    row
                                    for row in rows
                                    if row.source == source
                                    and row.symbol == symbol
                                    and row.interval == "1D"
                                ),
                                key=lambda row: row.time,
                            ).close

                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            payload = response.json()
                            data = payload.get("data", {})
                            if "top-performers" in case.name:
                                entries = [
                                    *data.get("performers", []),
                                    *data.get("worst_performers", []),
                                ]
                            elif "ma-scores" in case.name:
                                entries = [
                                    stock
                                    for sector in data.get("sectors", [])
                                    for stock in sector.get("top_stocks", [])
                                ]
                            else:
                                entries = data.get("tickers", [])

                            indexed = {
                                (entry.get("source"), entry.get("symbol")): entry
                                for entry in entries
                            }
                            required = dict(expected_snapshot_closes)
                            if is_fallback:
                                required[("yahoo", "AAPL")] = fixture_latest_closes[
                                    ("yahoo", "AAPL")
                                ]
                            for identity, expected_close in required.items():
                                entry = indexed.get(identity)
                                if entry is None or entry.get("close") != expected_close:
                                    failures.append(
                                        f"{case.name}: {runtime} expected {identity!r} close "
                                        f"{expected_close!r}, got {entry.get('close') if entry else None!r}"
                                    )

                            if is_fallback:
                                yahoo_fpt = indexed.get(("yahoo", "FPT"))
                                if (
                                    yahoo_fpt is None
                                    or yahoo_fpt.get("close")
                                    != fixture_latest_closes[("yahoo", "FPT")]
                                ):
                                    failures.append(
                                        f"{case.name}: {runtime} did not fall back to raw/SQL rows "
                                        "for the incomplete Yahoo snapshot source"
                                    )

                    if case.name == "ma-scores-explicit-null-indicator-snapshot-hit":
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            sectors = response.json().get("data", {}).get("sectors", [])
                            symbols = {
                                row.get("symbol")
                                for sector in sectors
                                for row in sector.get("top_stocks", [])
                            }
                            if "VCB" in symbols:
                                failures.append(
                                    f"{case.name}: {runtime} included VCB with a null MA score"
                                )

                    if case.name == "volume-profile-partial-tail-overlap":
                        selected = [
                            row
                            for row in rows
                            if row.source == "vn"
                            and row.symbol == "FPT"
                            and row.interval == "1m"
                            and FIXTURE_END.replace(hour=2, minute=10)
                            <= datetime.fromtimestamp(row.time, UTC)
                            < FIXTURE_END.replace(hour=2, minute=18)
                        ]
                        expected_volume = sum(row.volume for row in selected)
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            payload = response.json()
                            profile = payload.get("data", {})
                            if (
                                response.status_code != 200
                                or payload.get("total_analyzed") != len(selected)
                                or profile.get("total_minutes") != len(selected)
                                or profile.get("total_volume") != expected_volume
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} did not use only the overlapping "
                                    f"Redis tail ({len(selected)} bars, volume {expected_volume})"
                                )

                    if case.name == "volume-profile-tail-no-overlap":
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            try:
                                payload = response.json()
                            except (json.JSONDecodeError, AttributeError):
                                payload = None
                            if response.status_code != 200 or payload != {
                                "error": "No volume profile data generated"
                            }:
                                failures.append(
                                    f"{case.name}: {runtime} did not fall back to the prior-day "
                                    "zero-volume SQL row"
                                )

                    if case.name in {
                        "rrg-mascore-valid-stale-joined-snapshot",
                        "rrg-mascore-null-required-ohlc-joined-snapshot",
                    }:
                        expected_fallback = None
                        if case.name == "rrg-mascore-null-required-ohlc-joined-snapshot":
                            from aipriceaction_api.calculations import enhance

                            fallback_bars = sorted(
                                (
                                    row
                                    for row in rows
                                    if row.source == "vn"
                                    and row.symbol == "VCB"
                                    and row.interval == "1D"
                                ),
                                key=lambda row: row.time,
                            )[-100:]
                            fallback_bars[-1] = replace(
                                fallback_bars[-1],
                                open=4320.25,
                                high=4322.25,
                                low=4319.25,
                                close=4321.25,
                            )
                            expected_fallback = enhance(fallback_bars, "1D", True, False)[-1]
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            tickers = response.json().get("data", {}).get("tickers", [])
                            vcb = next((row for row in tickers if row.get("symbol") == "VCB"), None)
                            if vcb is None:
                                failures.append(f"{case.name}: {runtime} omitted VCB")
                            elif case.name == "rrg-mascore-valid-stale-joined-snapshot":
                                if (
                                    vcb.get("close") != 9876.5
                                    or vcb.get("rs_ratio") != 1234.5
                                    or vcb.get("rs_momentum") != 2345.5
                                ):
                                    failures.append(
                                        f"{case.name}: {runtime} did not accept the stale joined snapshot"
                                    )
                            elif (
                                vcb.get("close") != 4321.25
                                or vcb.get("rs_ratio") != expected_fallback["ma20_score"]
                                or vcb.get("rs_momentum") != expected_fallback["ma100_score"]
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} did not reject the null-OHLC snapshot and return the expected Redis bar and scores"
                                )

                    if case.name == "rrg-mascore-duplicate-member-raw-limit-seed-depth":
                        from aipriceaction_api.calculations import enhance

                        vcb_rows = sorted(
                            (
                                row
                                for row in rows
                                if row.source == "vn"
                                and row.symbol == "VCB"
                                and row.interval == "1D"
                            ),
                            key=lambda row: row.time,
                        )[-201:]
                        selected = [
                            replace(
                                row,
                                open=99.5,
                                high=101.25,
                                low=98.5,
                                close=100.0,
                            )
                            for row in vcb_rows[-99:-1]
                        ]
                        selected.append(
                            replace(
                                vcb_rows[-1],
                                open=109.5,
                                high=111.25,
                                low=108.5,
                                close=110.0,
                            )
                        )
                        excluded_outlier = replace(
                            vcb_rows[-100],
                            open=9_999.5,
                            high=10_001.25,
                            low=9_998.5,
                            close=10_000.0,
                        )
                        expected_selected = enhance(selected, "1D", True, False)[-1]
                        expected_with_excluded = enhance(
                            [excluded_outlier, *selected], "1D", True, False
                        )[-1]
                        if (
                            expected_selected["ma20_score"] != expected_with_excluded["ma20_score"]
                            or expected_selected["ma100_score"]
                            == expected_with_excluded["ma100_score"]
                        ):
                            failures.append(
                                f"{case.name}: fixture does not distinguish the raw-limit-before-dedup window"
                            )
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            tickers = response.json().get("data", {}).get("tickers", [])
                            vcb = next((row for row in tickers if row.get("symbol") == "VCB"), None)
                            if (
                                response.status_code != 200
                                or sum(row.get("symbol") == "VCB" for row in tickers) != 1
                                or vcb is None
                                or vcb.get("close") != 110.0
                                or vcb.get("rs_ratio") != expected_selected["ma20_score"]
                                or vcb.get("rs_momentum") != expected_selected["ma100_score"]
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} did not use the 99-bar Redis result selected by raw-member limit then crawl-time deduplication"
                                )

                    if case.name in {"rrg-mascore", "rrg-mascore-no-trails"}:
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            tickers = response.json().get("data", {}).get("tickers", [])
                            if sum(row.get("symbol") == "VCB" for row in tickers) != 1:
                                failures.append(
                                    f"{case.name}: {runtime} did not deduplicate overlapping VCB membership"
                                )

                    if case.name == "ticker-stale-snapshot-hit":
                        expected_date = (FIXTURE_END - timedelta(days=30)).date().isoformat()
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            rows_for_vcb = response.json().get("VCB", [])
                            if (
                                not rows_for_vcb
                                or rows_for_vcb[0].get("close") != 9876.5
                                or rows_for_vcb[0].get("time") != expected_date
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} did not preserve the stale snapshot hit"
                                )
                            if response.headers.get("x-data-source") != "redis-snap":
                                failures.append(
                                    f"{case.name}: {runtime} did not tag the snapshot source"
                                )
                            if any(header.startswith("x-redis-") for header in response.headers):
                                failures.append(
                                    f"{case.name}: {runtime} exposed raw Redis diagnostics for a snapshot hit"
                                )

                    if case.name == "ticker-incomplete-snapshot-miss":
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            result = response.json()
                            if (
                                not result.get("VCB")
                                or result["VCB"][0].get("close") != 9876.5
                                or not result.get("FPT")
                                or result["FPT"][-1].get("close") != 4321.25
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} did not combine the valid snapshot hit with the missing ticker fetch"
                                )
                            if response.headers.get("x-data-source") != "redis-snap":
                                failures.append(
                                    f"{case.name}: {runtime} did not tag the snapshot source"
                                )
                            if any(header.startswith("x-redis-") for header in response.headers):
                                failures.append(
                                    f"{case.name}: {runtime} exposed raw Redis diagnostics for a snapshot read"
                                )

                    if case.name in {
                        "ticker-malformed-snapshot-miss",
                        "ticker-snapshot-hash-error-fallback",
                    }:
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            result = response.json().get("FPT", [])
                            if (
                                not result
                                or result[-1].get("time") != FIXTURE_END.date().isoformat()
                                or result[-1].get("close") != 4321.25
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} did not fall through to raw Redis after rejecting the malformed snapshot"
                                )
                            if response.headers.get("x-data-source") != "redis-snap":
                                failures.append(
                                    f"{case.name}: {runtime} did not tag the snapshot read"
                                )
                            if any(header.startswith("x-redis-") for header in response.headers):
                                failures.append(
                                    f"{case.name}: {runtime} exposed raw Redis diagnostics for a snapshot read"
                                )

                    if case.name == "ticker-redis-ticker-list-subset":
                        expected_symbols = {"VCB", "BTCUSDT"}
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            if set(response.json()) != expected_symbols:
                                failures.append(
                                    f"{case.name}: {runtime} did not use the seeded Redis ticker-list membership"
                                )
                            if response.headers.get("x-data-source") != "cache":
                                failures.append(
                                    f"{case.name}: {runtime} did not tag the Redis cache source"
                                )
                            if any(header.startswith("x-redis-") for header in response.headers):
                                failures.append(
                                    f"{case.name}: {runtime} exposed single-source Redis diagnostics in mode=all"
                                )

                    if case.name == "ticker-redis-ticker-list-source-priority":
                        ticker = next(row for row in TICKERS if row[:2] == ("yahoo", "DUPL"))
                        index = DAILY_BARS - 1
                        expected_close = ticker[3] + index * ticker[4] + (index % 7) * 0.25
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            result = response.json().get("DUPL", [])
                            if not result or result[0].get("close") != expected_close:
                                failures.append(
                                    f"{case.name}: {runtime} ignored the cached Yahoo identity"
                                )
                            if response.headers.get("x-data-source") != "cache":
                                failures.append(
                                    f"{case.name}: {runtime} did not tag the Redis cache source"
                                )
                            if any(header.startswith("x-redis-") for header in response.headers):
                                failures.append(
                                    f"{case.name}: {runtime} exposed single-source Redis diagnostics in mode=all"
                                )

                    if case.name in {
                        "ticker-redis-ticker-list-absent",
                        "ticker-redis-ticker-list-malformed",
                        "ticker-redis-ticker-list-empty",
                    }:
                        expected_symbols = {row[1] for row in TICKERS if row[0] == "vn"}
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            if set(response.json()) != expected_symbols:
                                failures.append(
                                    f"{case.name}: {runtime} did not fall back to the full database ticker identity set"
                                )
                            if response.headers.get("x-data-source") != "cache":
                                failures.append(
                                    f"{case.name}: {runtime} did not fetch fallback identities from Redis bars"
                                )

                    if case.name == "ticker-redis-ticker-list-all-empty":
                        expected_symbols = {row[1] for row in TICKERS}
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            if set(response.json()) != expected_symbols:
                                failures.append(
                                    f"{case.name}: {runtime} did not fall back to the all-mode database identity set"
                                )
                            if response.headers.get("x-data-source") != "cache":
                                failures.append(
                                    f"{case.name}: {runtime} did not tag the Redis cache source"
                                )
                            if any(header.startswith("x-redis-") for header in response.headers):
                                failures.append(
                                    f"{case.name}: {runtime} exposed single-source Redis diagnostics in mode=all"
                                )

                    if case.name == "ticker-redis-ticker-list-incomplete-explicit":
                        expected_symbols = {"DUPL", "FPT"}
                        vn_duplicates = next(row for row in TICKERS if row[:2] == ("vn", "DUPL"))
                        vn_fpt = next(row for row in TICKERS if row[:2] == ("vn", "FPT"))
                        index = DAILY_BARS - 1
                        expected_closes = {
                            "DUPL": vn_duplicates[3]
                            + index * vn_duplicates[4]
                            + (index % 7) * 0.25,
                            "FPT": vn_fpt[3] + index * vn_fpt[4] + (index % 7) * 0.25,
                        }
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            result = response.json()
                            if set(result) != expected_symbols or any(
                                not result[symbol]
                                or result[symbol][0].get("close") != expected_close
                                for symbol, expected_close in expected_closes.items()
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} did not fall back to database identity resolution for all explicit symbols"
                                )
                            if response.headers.get("x-data-source") != "cache":
                                failures.append(
                                    f"{case.name}: {runtime} did not tag the Redis cache source"
                                )
                            if any(header.startswith("x-redis-") for header in response.headers):
                                failures.append(
                                    f"{case.name}: {runtime} exposed single-source Redis diagnostics in mode=all"
                                )

                    if case.name == "ticker-partial-redis-batch":
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            if set(response.json()) != {"VCB"}:
                                failures.append(
                                    f"{case.name}: {runtime} filled a missing ticker from the database after a partial Redis hit"
                                )
                            expected_diagnostics = {
                                "x-redis-base": "1D",
                                "x-redis-raw-count": "2",
                                "x-redis-aligned": "2",
                                "x-redis-limit": "2",
                            }
                            actual_diagnostics = {
                                header: value
                                for header, value in response.headers.items()
                                if header.startswith("x-redis-")
                            }
                            if actual_diagnostics != expected_diagnostics:
                                failures.append(
                                    f"{case.name}: {runtime} Redis diagnostics {actual_diagnostics!r} did not match {expected_diagnostics!r}"
                                )
                            if response.headers.get("x-data-source") != "cache":
                                failures.append(
                                    f"{case.name}: {runtime} did not tag the Redis cache source"
                                )

                    if case.name == "ticker-empty-redis-batch":
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            if set(response.json()) != {"VCB", "FPT"}:
                                failures.append(
                                    f"{case.name}: {runtime} did not fall back to the database after an empty Redis batch"
                                )
                            if response.headers.get("x-data-source") != "database":
                                failures.append(
                                    f"{case.name}: {runtime} did not tag the database fallback"
                                )
                            if any(header.startswith("x-redis-") for header in response.headers):
                                failures.append(
                                    f"{case.name}: {runtime} exposed raw Redis diagnostics after an empty-cache database fallback"
                                )

                    if case.name in expected_notes_status:
                        expected_status = expected_notes_status[case.name]
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            if response.status_code != expected_status:
                                failures.append(
                                    f"{case.name}: expected {runtime} status {expected_status}, "
                                    f"got {response.status_code}"
                                )
                            if case.name == "notes-fetch-maximum-markdown-size":
                                expected_digest = hashlib.sha256(
                                    b"x" * (5 * 1024 * 1024)
                                ).hexdigest()
                                if len(response.content) != 5 * 1024 * 1024 or hashlib.sha256(
                                    response.content
                                ).hexdigest() != expected_digest:
                                    failures.append(
                                        f"{case.name}: {runtime} did not return the exact "
                                        "5 MiB upload bytes"
                                    )

                    if case.name in expected_query_status:
                        expected_status = expected_query_status[case.name]
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            if response.status_code != expected_status:
                                failures.append(
                                    f"{case.name}: expected {runtime} status {expected_status}, "
                                    f"got {response.status_code}"
                                )

                    if case.name in {
                        "production-origin-data-ticker-json",
                        "production-origin-data-ticker-all-json",
                    }:
                        expected_candle = current_daily_candles[("vn", "FPT")]
                        required_fields = {
                            "time",
                            "open",
                            "high",
                            "low",
                            "close",
                            "volume",
                            "symbol",
                        }
                        ma_fields = {
                            *(f"ma{period}" for period in (10, 20, 50, 100, 200)),
                            *(f"ma{period}_score" for period in (10, 20, 50, 100, 200)),
                        }
                        if case.name != "production-origin-data-ticker-all-json":
                            required_fields.update(ma_fields)
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            try:
                                payload = response.json()
                            except ValueError:
                                payload = None
                            records = payload.get("FPT") if isinstance(payload, dict) else None
                            row = records[0] if isinstance(records, list) and records else None
                            if (
                                not isinstance(payload, dict)
                                or not response.headers.get("content-type", "").startswith(
                                    "application/json"
                                )
                                or set(payload) != {"FPT"}
                                or not isinstance(records, list)
                                or len(records) != 1
                                or not isinstance(row, dict)
                                or not required_fields.issubset(row)
                                or any(
                                    value is not None and not _is_finite_json_number(value)
                                    for field in (
                                        *ma_fields,
                                        "close_changed",
                                        "volume_changed",
                                        "total_money_changed",
                                    )
                                    for value in (row.get(field),)
                                )
                                or row.get("time") != FIXTURE_END.date().isoformat()
                                or row.get("symbol") != "FPT"
                                or any(
                                    not _is_finite_json_number(row.get(field))
                                    for field in ("open", "high", "low", "close")
                                )
                                or not isinstance(row.get("volume"), int)
                                or isinstance(row.get("volume"), bool)
                                or row.get("open") != expected_candle.open
                                or row.get("high") != expected_candle.high
                                or row.get("low") != expected_candle.low
                                or row.get("close") != expected_candle.close
                                or row.get("volume") != expected_candle.volume
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} did not return the complete seeded "
                                    f"FPT JSON candle for {FIXTURE_END.date()}: {payload!r}"
                                )

                    if case.name == "blank-numeric-ticker-limit":
                        expected_symbols = {
                            symbol for source, symbol, *_ in TICKERS if source == "vn"
                        }
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            try:
                                payload = response.json()
                            except ValueError:
                                payload = None
                            if (
                                not isinstance(payload, dict)
                                or set(payload) != expected_symbols
                                or any(
                                    not isinstance(records, list)
                                    or len(records) != 1
                                    or records[0].get("time") != FIXTURE_END.date().isoformat()
                                    for records in payload.values()
                                )
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} blank optional limit did not use "
                                    f"the default one-row-per-VN-symbol result: {payload!r}"
                                )

                    if case.name in {"ticker-limit-i64-minimum", "ticker-limit-i64-maximum"}:
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            try:
                                payload = response.json()
                            except ValueError:
                                payload = None
                            if case.name == "ticker-limit-i64-minimum":
                                valid = response.status_code == 200 and payload == {}
                            else:
                                records = payload.get("FPT") if isinstance(payload, dict) else None
                                expected_fpt_dates = sorted(
                                    datetime.fromtimestamp(row.time, UTC).date().isoformat()
                                    for row in rows
                                    if row.source == "vn"
                                    and row.symbol == "FPT"
                                    and row.interval == "1D"
                                )
                                valid = (
                                    response.status_code == 200
                                    and isinstance(payload, dict)
                                    and set(payload) == {"FPT"}
                                    and isinstance(records, list)
                                    and [record.get("time") for record in records]
                                    == expected_fpt_dates
                                )
                            if not valid:
                                expected = (
                                    "an empty object for i64::MIN"
                                    if case.name == "ticker-limit-i64-minimum"
                                    else "the full ascending seeded FPT history for i64::MAX"
                                )
                                failures.append(
                                    f"{case.name}: {runtime} did not return {expected}; "
                                    f"status={response.status_code}, payload={str(payload)[:500]!r}"
                                )

                    if case.name in {
                        "ticker-json-format-uppercase",
                        "ticker-ema-ignored-when-ma-disabled",
                        "ticker-legacy-scaled-with-ma-enabled",
                    }:
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            try:
                                payload = response.json()
                            except ValueError:
                                payload = None
                            records = payload.get("FPT") if isinstance(payload, dict) else None
                            expected_count = 1 if case.name == "ticker-json-format-uppercase" else 3
                            valid = (
                                response.status_code == 200
                                and response.headers.get("content-type", "").startswith(
                                    "application/json"
                                )
                                and isinstance(payload, dict)
                                and set(payload) == {"FPT"}
                                and isinstance(records, list)
                                and len(records) == expected_count
                            )
                            if valid and case.name == "ticker-ema-ignored-when-ma-disabled":
                                ma_fields = {
                                    *(f"ma{period}" for period in (10, 20, 50, 100, 200)),
                                    *(f"ma{period}_score" for period in (10, 20, 50, 100, 200)),
                                }
                                valid = all(
                                    ma_fields.isdisjoint(record)
                                    for record in records
                                    if isinstance(record, dict)
                                )
                            if valid and case.name == "ticker-legacy-scaled-with-ma-enabled":
                                for offset, record in enumerate(records):
                                    fixture_index = DAILY_BARS - len(records) + offset
                                    date_text = FIXTURE_DATES[fixture_index].date().isoformat()
                                    candle = fixture_daily_by_key.get(("vn", "FPT", date_text))
                                    if candle is None:
                                        valid = False
                                        break
                                    metrics = _expected_fixture_ticker_metrics(
                                        "vn",
                                        "FPT",
                                        fixture_index,
                                        ema=True,
                                        output_limit=3,
                                    )
                                    expected = {
                                        "open": candle.open / 1000,
                                        "high": candle.high / 1000,
                                        "low": candle.low / 1000,
                                        "close": candle.close / 1000,
                                        "volume": candle.volume,
                                        "time": date_text,
                                        "symbol": "FPT",
                                        **{
                                            field: value
                                            for field, value in metrics.items()
                                            if field not in {"close", "volume"}
                                        },
                                        "total_money_changed": (
                                            metrics["close"]
                                            - _fixture_daily_close("vn", "FPT", fixture_index - 1)
                                        )
                                        * metrics["volume"],
                                    }
                                    if any(
                                        not (
                                            math.isclose(
                                                record.get(field, math.nan),
                                                value,
                                                rel_tol=0,
                                                abs_tol=1e-8,
                                            )
                                            if isinstance(value, (int, float))
                                            and not isinstance(value, bool)
                                            else record.get(field) == value
                                        )
                                        for field, value in expected.items()
                                    ):
                                        valid = False
                                        break
                            if not valid:
                                failures.append(
                                    f"{case.name}: {runtime} JSON format/MA/legacy oracle failed: "
                                    f"{payload!r}"
                                )

                    if case.name in {
                        "ticker-csv-format-uppercase",
                        "ticker-csv-format-mixed-case",
                    }:
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            csv_rows, parse_errors = _parse_web_ticker_csv(response.text)
                            if (
                                response.status_code != 200
                                or not response.headers.get("content-type", "").startswith(
                                    "text/csv"
                                )
                                or parse_errors
                                or len(csv_rows) != 1
                                or csv_rows[0].get("symbol") != "FPT"
                                or csv_rows[0].get("time") != FIXTURE_END.date().isoformat()
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} case-insensitive CSV oracle failed: "
                                    f"{csv_rows!r}, {parse_errors!r}"
                                )

                    if case.name.startswith(
                        ("production-origin-groups", "production-origin-names")
                    ):
                        is_group = case.name.startswith("production-origin-groups")
                        mode_name = (
                            case.name.rsplit("-", 1)[-1] if case.name.count("-") > 2 else "vn"
                        )
                        expected_groups = {
                            "vn": {
                                "Banking": ["VCB", "VCB"],
                                "Duplicate": ["DUPL"],
                                "Indices": ["VNINDEX"],
                                "Technology": ["FPT"],
                                "Microcap": ["ABC"],
                            },
                            "crypto": {"CRYPTO_TOP_100": ["BTCUSDT", "ETHUSDT"]},
                            "yahoo": {
                                "Technology": ["AAPL", "FPT"],
                                "Duplicate": ["DUPL"],
                                "Fixture": ["DUPL,FPT"],
                                "Global Gold": ["COLL"],
                                "Indices": ["^GSPC"],
                                "Commodities": ["SJC", "COLL"],
                            },
                            "all": {
                                "Banking": ["VCB", "VCB"],
                                "Duplicate": ["DUPL"],
                                "Indices": ["VNINDEX"],
                                "Technology": ["FPT"],
                                "Microcap": ["ABC"],
                                "Fixture": ["DUPL,FPT"],
                                "Global Gold": ["COLL"],
                                "Commodities": ["SJC", "COLL"],
                                "CRYPTO_TOP_100": ["BTCUSDT", "ETHUSDT"],
                            },
                        }
                        expected_names = {
                            "vn": {
                                "DUPL": "Duplicate Corporation",
                                "FPT": "FPT Corporation",
                                "VCB": "Vietcombank",
                                "VNINDEX": "Vietnam Index",
                                "ABC": "Three Letter Stock",
                            },
                            "crypto": {"BTCUSDT": "Bitcoin", "ETHUSDT": "Ether"},
                            "yahoo": {
                                "AAPL": "Apple Inc.",
                                "DUPL": "Yahoo Duplicate",
                                "FPT": "Yahoo FPT",
                                "DUPL,FPT": "Yahoo Literal Comma",
                                "COLL": "Yahoo Collision",
                                "^GSPC": "S&P 500",
                                "SJC": "SJC Gold",
                            },
                            "all": {
                                "DUPL": "Duplicate Corporation",
                                "FPT": "FPT Corporation",
                                "VCB": "Vietcombank",
                                "VNINDEX": "Vietnam Index",
                                "ABC": "Three Letter Stock",
                                "AAPL": "Apple Inc.",
                                "DUPL,FPT": "Yahoo Literal Comma",
                                "COLL": "Yahoo Collision",
                                "^GSPC": "S&P 500",
                                "SJC": "SJC Gold",
                                "BTCUSDT": "Bitcoin",
                                "ETHUSDT": "Ether",
                            },
                        }
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            try:
                                payload = response.json()
                            except ValueError:
                                payload = None
                            if not isinstance(payload, dict) or not payload:
                                failures.append(
                                    f"{case.name}: {runtime} returned an empty or invalid catalog"
                                )
                                continue
                            expected_catalog = (
                                expected_groups[mode_name]
                                if is_group
                                else expected_names[mode_name]
                            )
                            if payload != expected_catalog:
                                failures.append(
                                    f"{case.name}: {runtime} catalog was {payload!r}, expected "
                                    f"the complete {mode_name} fixture catalog {expected_catalog!r}"
                                )

                    if (
                        case.method in {"GET", "POST"}
                        and case.path.startswith("/sync/")
                        and any(
                        response.status_code == 200 for response in (rust_response, python_response)
                        )
                    ):
                        raw_id = case.path.split("?", 1)[0].rsplit("/", 1)[-1]
                        try:
                            expected_id = str(uuid.UUID(raw_id))
                        except ValueError:
                            expected_id = None
                        expected_value = sync_case_values.get(case.name)
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            if response.status_code != 200:
                                continue
                            try:
                                payload = response.json()
                            except ValueError:
                                payload = None
                            valid_sync = (
                                response.headers.get("content-type", "").startswith(
                                    "application/json"
                                )
                                and isinstance(payload, dict)
                                and set(payload) == {"id", "value", "created_at", "updated_at"}
                                and expected_id is not None
                                and payload.get("id") == expected_id
                                and payload.get("value") == expected_value
                            )
                            for field in ("created_at", "updated_at"):
                                value = payload.get(field) if isinstance(payload, dict) else None
                                if not isinstance(value, str):
                                    valid_sync = False
                                    continue
                                try:
                                    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                                except ValueError:
                                    valid_sync = False
                                else:
                                    if timestamp.utcoffset() != timedelta(0):
                                        valid_sync = False
                            if not valid_sync:
                                failures.append(
                                    f"{case.name}: {runtime} successful sync response did not "
                                    f"match the independent envelope, UUID, value, and UTC "
                                    f"timestamp contract: {payload!r}"
                                )

                    if case.name in expected_sync_bodies:
                        expected_body = expected_sync_bodies[case.name]
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            if response.json() != expected_body:
                                failures.append(
                                    f"{case.name}: {runtime} body "
                                    f"{response.json()!r} did not match independent "
                                    f"expectation {expected_body!r}"
                                )

                    if case.name == "sync-read-isolated":
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            value = response.json().get("value", {})
                            if value.get("watchlists") != {"My List": "ACB,VCB,TCB"}:
                                failures.append(
                                    f"{case.name}: {runtime} lost the original sync value "
                                    "after the wrong-secret overwrite attempt"
                                )

                    if case.name in expected_route_status:
                        expected_status = expected_route_status[case.name]
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            if response.status_code != expected_status:
                                failures.append(
                                    f"{case.name}: expected {runtime} status {expected_status}, "
                                    f"got {response.status_code}"
                                )

                    if case.name in expected_plain_bodies:
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            if response.content != expected_plain_bodies[case.name]:
                                failures.append(
                                    f"{case.name}: {runtime} body {response.content!r} did not "
                                    f"match independent expectation {expected_plain_bodies[case.name]!r}"
                                )

                    if case.name in expected_analysis_query_status:
                        expected_status = expected_analysis_query_status[case.name]
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            if response.status_code != expected_status:
                                failures.append(
                                    f"{case.name}: expected {runtime} status {expected_status}, "
                                    f"got {response.status_code}"
                                )
                            if case.name in expected_analysis_query_bodies:
                                try:
                                    actual_body = response.json()
                                except ValueError:
                                    actual_body = None
                                if actual_body != expected_analysis_query_bodies[case.name]:
                                    failures.append(
                                        f"{case.name}: unexpected {runtime} JSON body "
                                        f"{actual_body!r}"
                                    )
                            if case.name in expected_analysis_dates:
                                try:
                                    actual_date = response.json().get("analysis_date")
                                except ValueError:
                                    actual_date = None
                                if actual_date != expected_analysis_dates[case.name]:
                                    failures.append(
                                        f"{case.name}: expected {runtime} analysis_date "
                                        f"{expected_analysis_dates[case.name]!r}, got {actual_date!r}"
                                    )
                            if case.name in expected_rrg_metadata and response.status_code == 200:
                                try:
                                    rrg_data = response.json().get("data", {})
                                except ValueError:
                                    rrg_data = {}
                                expected_algorithm, expected_benchmark, expected_period = (
                                    expected_rrg_metadata[case.name]
                                )
                                if (
                                    not isinstance(rrg_data, dict)
                                    or rrg_data.get("algorithm") != expected_algorithm
                                ):
                                    failures.append(
                                        f"{case.name}: {runtime} did not echo the requested "
                                        f"RRG algorithm {expected_algorithm!r}: {rrg_data!r}"
                                    )
                                elif expected_benchmark is None:
                                    if "benchmark" in rrg_data or "period" in rrg_data:
                                        failures.append(
                                            f"{case.name}: {runtime} emitted JdK-only metadata "
                                            f"for MASCORE: {rrg_data!r}"
                                        )
                                elif (
                                    rrg_data.get("benchmark") != expected_benchmark
                                    or rrg_data.get("period") != expected_period
                                ):
                                    failures.append(
                                        f"{case.name}: {runtime} RRG metadata was "
                                        f"{rrg_data.get('benchmark')!r}/"
                                        f"{rrg_data.get('period')!r}, expected "
                                        f"{expected_benchmark!r}/{expected_period!r}"
                                    )
                            if case.name in nonfinite_threshold_cases:
                                try:
                                    actual_threshold = response.json()["data"]["threshold"]
                                except (KeyError, TypeError, ValueError):
                                    actual_threshold = object()
                                if actual_threshold is not None:
                                    failures.append(
                                        f"{case.name}: expected {runtime} threshold null, "
                                        f"got {actual_threshold!r}"
                                    )

                            expected_analysis_type = expected_success_analysis_types.get(case.name)
                            if expected_analysis_type is not None and response.status_code == 200:
                                try:
                                    payload = response.json()
                                except ValueError:
                                    payload = None
                                valid_envelope = (
                                    isinstance(payload, dict)
                                    and response.headers.get("content-type", "").startswith(
                                        "application/json"
                                    )
                                    and payload.get("analysis_type") == expected_analysis_type
                                    and isinstance(payload.get("analysis_date"), str)
                                    and type(payload.get("total_analyzed")) is int
                                    and (
                                        payload.get("total_analyzed", 0) == 0
                                        if case.name in expected_empty_analysis_cases
                                        else payload.get("total_analyzed", 0) > 0
                                    )
                                    and isinstance(payload.get("data"), dict)
                                )
                                if not valid_envelope:
                                    failures.append(
                                        f"{case.name}: {runtime} success body did not have the "
                                        f"{expected_analysis_type!r} analysis envelope: {payload!r}"
                                    )
                                else:
                                    data = payload["data"]
                                    if expected_analysis_type == "top_performers":
                                        performer_fields = {"symbol", "close", "volume"}
                                        valid_shape = (
                                            all(
                                                isinstance(data.get(field), list)
                                                for field in ("performers", "worst_performers")
                                            )
                                            and "hourly" in data
                                        )
                                        performer_rows = [
                                            *(
                                                data.get("performers", [])
                                                if isinstance(data.get("performers"), list)
                                                else []
                                            ),
                                            *(
                                                data.get("worst_performers", [])
                                                if isinstance(data.get("worst_performers"), list)
                                                else []
                                            ),
                                        ]
                                        valid_shape = (
                                            valid_shape
                                            and (
                                                bool(performer_rows)
                                                or case.name in expected_empty_analysis_cases
                                            )
                                            and all(
                                                isinstance(row, dict)
                                                and performer_fields.issubset(row)
                                                and isinstance(row.get("symbol"), str)
                                                and _is_finite_json_number(row.get("close"))
                                                and isinstance(row.get("volume"), int)
                                                and not isinstance(row.get("volume"), bool)
                                                and all(
                                                    value is None
                                                    or _is_finite_json_number(value)
                                                    for field in (
                                                        "close_changed",
                                                        "volume_changed",
                                                        "ma10",
                                                        "ma20",
                                                        "ma50",
                                                        "ma100",
                                                        "ma200",
                                                        "ma10_score",
                                                        "ma20_score",
                                                        "ma50_score",
                                                        "ma100_score",
                                                        "ma200_score",
                                                        "total_money_changed",
                                                    )
                                                    for value in (row.get(field),)
                                                )
                                                for row in performer_rows
                                            )
                                        )
                                        valid_shape = valid_shape and all(
                                            row.get("sector") is None
                                            or isinstance(row.get("sector"), str)
                                            for row in performer_rows
                                        )
                                        if case.name in expected_empty_analysis_cases:
                                            valid_shape = (
                                                valid_shape
                                                and payload.get("total_analyzed") == 0
                                                and not performer_rows
                                            )
                                        expected_symbol, expected_source = (
                                            expected_latest_analysis_symbols.get(
                                                case.name, ("FPT", "vn")
                                            )
                                        )
                                        target = next(
                                            (
                                                row
                                                for row in performer_rows
                                                if isinstance(row, dict)
                                                and row.get("symbol") == expected_symbol
                                                and row.get("source")
                                                in (None, expected_source)
                                            ),
                                            None,
                                        )
                                        expected_candle = current_daily_candles.get(
                                            (expected_source, expected_symbol)
                                        )
                                        valid_shape = valid_shape and (
                                            target is not None
                                            or case.name in expected_empty_analysis_cases
                                        )
                                        if target is not None and expected_candle is not None:
                                            expected_close = expected_candle.close
                                            expected_volume = expected_candle.volume
                                            if use_live_redis and case.name in {
                                                "top-performers-default-snapshot-option",
                                                "ma-scores-by-sector-default-snapshot-option",
                                                    "production-origin-data-top-performers",
                                            }:
                                                expected_close = 101.0
                                                expected_volume = 250_000
                                            valid_shape = valid_shape and (
                                                target.get("close") == expected_close
                                                and target.get("volume") == expected_volume
                                            )
                                            if case.name.startswith(
                                                "top-performers-latest-"
                                            ):
                                                expected_metrics = _expected_fixture_performer_metrics(
                                                    expected_source, expected_symbol, ema=True
                                                )
                                                valid_shape = valid_shape and all(
                                                    math.isclose(
                                                        target.get(field, math.nan),
                                                        expected_metrics[field],
                                                        rel_tol=0,
                                                        abs_tol=0.0051,
                                                    )
                                                    for field in expected_metrics
                                                )
                                        hourly = data.get("hourly")
                                        if case.name.startswith("top-performers-with-hour-"):
                                            # The public Explorer advertises this
                                            # switch, but neither server implements
                                            # hourly analysis. It is an unknown query
                                            # field on both routers and must remain
                                            # ignored until the feature has a defined
                                            # cross-source calculation contract.
                                            valid_shape = (
                                                valid_shape and "hourly" in data and hourly is None
                                            )
                                        else:
                                            valid_shape = valid_shape and (
                                                hourly is None
                                                or isinstance(hourly, list)
                                                and all(
                                                    isinstance(item, dict)
                                                    and isinstance(item.get("hour"), str)
                                                    and isinstance(item.get("performers"), list)
                                                    and isinstance(item.get("worst_performers"), list)
                                                    for item in hourly
                                                )
                                            )
                                        if case.name == "top-performers-min-volume-u64-maximum":
                                            performers = data.get("performers")
                                            worst_performers = data.get("worst_performers")
                                            valid_shape = valid_shape and (
                                                payload.get("total_analyzed") == 2
                                                and isinstance(performers, list)
                                                and isinstance(worst_performers, list)
                                                and [row.get("symbol") for row in performers]
                                                == ["DUPL"]
                                                and [row.get("symbol") for row in worst_performers]
                                                == ["DUPL"]
                                            )
                                    elif expected_analysis_type == "ma_scores_by_sector":
                                        sectors = data.get("sectors")
                                        stock_fields = {
                                            "symbol",
                                            "close",
                                            "volume",
                                            "ma_value",
                                            "ma_score",
                                        }
                                        valid_shape = isinstance(sectors, list) and (
                                            bool(sectors)
                                            or case.name in expected_empty_analysis_cases
                                        )
                                        stocks = []
                                        if isinstance(sectors, list):
                                            for sector in sectors:
                                                if (
                                                    not isinstance(sector, dict)
                                                    or not {
                                                        "sector_name",
                                                        "total_stocks",
                                                        "stocks_above_threshold",
                                                        "average_score",
                                                        "top_stocks",
                                                    }.issubset(sector)
                                                    or not isinstance(
                                                        sector.get("sector_name"), str
                                                    )
                                                    or not isinstance(
                                                        sector.get("total_stocks"), int
                                                    )
                                                    or not isinstance(
                                                        sector.get("stocks_above_threshold"), int
                                                    )
                                                    or not _is_finite_json_number(
                                                        sector.get("average_score")
                                                    )
                                                    or not isinstance(
                                                        sector.get("top_stocks"), list
                                                    )
                                                ):
                                                    valid_shape = False
                                                else:
                                                    stocks.extend(sector["top_stocks"])
                                        valid_shape = valid_shape and all(
                                            isinstance(stock, dict)
                                            and stock_fields.issubset(stock)
                                            and isinstance(stock.get("symbol"), str)
                                            and _is_finite_json_number(stock.get("close"))
                                            and isinstance(stock.get("volume"), int)
                                            and not isinstance(stock.get("volume"), bool)
                                            and _is_finite_json_number(stock.get("ma_value"))
                                            and _is_finite_json_number(stock.get("ma_score"))
                                            and all(
                                                value is None
                                                or _is_finite_json_number(value)
                                                for value in (
                                                    stock.get("close_changed"),
                                                    stock.get("volume_changed"),
                                                )
                                            )
                                            for stock in stocks
                                        )
                                        if case.name in expected_empty_analysis_cases:
                                            period = int(case.name.rsplit("-", 1)[1])
                                            valid_shape = (
                                                valid_shape
                                                and payload.get("total_analyzed") == 0
                                                and data
                                                == {
                                                    "sectors": [],
                                                    "ma_period": period,
                                                    "threshold": 0.0,
                                                }
                                            )
                                        elif case.name.startswith("ma-scores-latest-yahoo-"):
                                            period = int(case.name.rsplit("-", 1)[1])
                                            members = {
                                                "Duplicate": "DUPL",
                                                "Technology": "FPT",
                                            }
                                            scores = {}
                                            expected_stock_by_sector = {}
                                            for sector_name, symbol in members.items():
                                                metrics = _expected_fixture_performer_metrics(
                                                    "yahoo", symbol, ema=True
                                                )
                                                score = metrics[f"ma{period}_score"]
                                                scores[sector_name] = score
                                                expected_stock_by_sector[sector_name] = {
                                                    "symbol": symbol,
                                                    "close": metrics["close"],
                                                    "volume": metrics["volume"],
                                                    "ma_value": metrics[f"ma{period}"],
                                                    "ma_score": score,
                                                    "close_changed": metrics["close_changed"],
                                                    "volume_changed": metrics["volume_changed"],
                                                }
                                            expected_sector_order = sorted(
                                                members,
                                                key=lambda name: (-scores[name], name),
                                            )
                                            actual_sector_order = [
                                                sector.get("sector_name")
                                                for sector in sectors
                                                if isinstance(sector, dict)
                                            ]
                                            valid_shape = (
                                                valid_shape
                                                and payload.get("total_analyzed") == len(members)
                                                and data.get("ma_period") == period
                                                and data.get("threshold") == 0.0
                                                and actual_sector_order == expected_sector_order
                                            )
                                            for sector in sectors:
                                                if not isinstance(sector, dict):
                                                    valid_shape = False
                                                    continue
                                                sector_name = sector.get("sector_name")
                                                expected_stock = expected_stock_by_sector.get(
                                                    sector_name
                                                )
                                                top_stocks = sector.get("top_stocks")
                                                if expected_stock is None or not isinstance(
                                                    top_stocks, list
                                                ):
                                                    valid_shape = False
                                                    continue
                                                actual_stock = (
                                                    top_stocks[0]
                                                    if len(top_stocks) == 1
                                                    and isinstance(top_stocks[0], dict)
                                                    else None
                                                )
                                                valid_shape = (
                                                    valid_shape
                                                    and sector.get("total_stocks") == 1
                                                    and sector.get("stocks_above_threshold") == 1
                                                    and math.isclose(
                                                        sector.get("average_score", math.nan),
                                                        expected_stock["ma_score"],
                                                        rel_tol=0,
                                                        abs_tol=0.0051,
                                                    )
                                                    and actual_stock is not None
                                                )
                                                if actual_stock is not None:
                                                    for field, expected_value in expected_stock.items():
                                                        actual_value = actual_stock.get(field)
                                                        if field == "symbol" or field == "volume":
                                                            matches = actual_value == expected_value
                                                        else:
                                                            matches = (
                                                                _is_finite_json_number(actual_value)
                                                                and math.isclose(
                                                                    actual_value,
                                                                    expected_value,
                                                                    rel_tol=0,
                                                                    abs_tol=0.0051,
                                                                )
                                                            )
                                                        valid_shape = valid_shape and matches
                                        else:
                                            expected_symbol, expected_source = (
                                                expected_latest_analysis_symbols.get(
                                                    case.name, ("FPT", "vn")
                                                )
                                            )
                                            target = next(
                                                (
                                                    stock
                                                    for stock in stocks
                                                    if isinstance(stock, dict)
                                                    and stock.get("symbol") == expected_symbol
                                                ),
                                                None,
                                            )
                                            expected_candle = current_daily_candles.get(
                                                (expected_source, expected_symbol)
                                            )
                                            valid_shape = valid_shape and target is not None
                                            if target is not None and expected_candle is not None:
                                                expected_close = expected_candle.close
                                                expected_volume = expected_candle.volume
                                                if use_live_redis and case.name in {
                                                    "ma-scores-by-sector-default-snapshot-option",
                                                }:
                                                    expected_close = 101.0
                                                    expected_volume = 250_000
                                                valid_shape = valid_shape and (
                                                    target.get("close") == expected_close
                                                    and target.get("volume") == expected_volume
                                                )
                                    elif expected_analysis_type == "rrg":
                                        ticker_rows = data.get("tickers")
                                        ticker_fields = {
                                            "symbol",
                                            "rs_ratio",
                                            "rs_momentum",
                                            "raw_rs",
                                            "close",
                                            "volume",
                                            "sector",
                                        }
                                        valid_shape = (
                                            isinstance(ticker_rows, list)
                                            and (
                                                bool(ticker_rows)
                                                or case.name in expected_empty_analysis_cases
                                            )
                                            and all(
                                                isinstance(row, dict)
                                                and ticker_fields.issubset(row)
                                                and isinstance(row.get("symbol"), str)
                                                and all(
                                                    _is_finite_json_number(row.get(field))
                                                    for field in (
                                                        "rs_ratio",
                                                        "rs_momentum",
                                                        "raw_rs",
                                                        "close",
                                                    )
                                                )
                                                and isinstance(row.get("volume"), int)
                                                and not isinstance(row.get("volume"), bool)
                                                and (
                                                    row.get("sector") is None
                                                    or isinstance(row.get("sector"), str)
                                                )
                                                and (
                                                    row.get("trails") is None
                                                    or isinstance(row.get("trails"), list)
                                                    and all(
                                                        isinstance(point, dict)
                                                        and isinstance(point.get("date"), str)
                                                        and _is_finite_json_number(
                                                            point.get("rs_ratio")
                                                        )
                                                        and _is_finite_json_number(
                                                            point.get("rs_momentum")
                                                        )
                                                        for point in row["trails"]
                                                    )
                                                )
                                                for row in ticker_rows
                                            )
                                        )
                                        if case.name in expected_empty_analysis_cases:
                                            valid_shape = (
                                                valid_shape
                                                and payload.get("total_analyzed") == 0
                                                and ticker_rows == []
                                            )
                                        expected_symbol_source = expected_rrg_symbols.get(case.name)
                                        if expected_symbol_source is not None and isinstance(
                                            ticker_rows, list
                                        ):
                                            required_symbol, source = expected_symbol_source
                                            target = next(
                                                (
                                                    row
                                                    for row in ticker_rows
                                                    if isinstance(row, dict)
                                                    and row.get("symbol") == required_symbol
                                                    and (
                                                        row.get("source") == source
                                                        if "mode=all" in case.path
                                                        else row.get("source") in (None, source)
                                                    )
                                                ),
                                                None,
                                            )
                                            expected_candle = current_daily_candles.get(
                                                (source, required_symbol)
                                            )
                                            valid_shape = valid_shape and target is not None
                                            if target is not None and expected_candle is not None:
                                                valid_shape = valid_shape and (
                                                    target.get("close") == expected_candle.close
                                                    and target.get("volume")
                                                    == expected_candle.volume
                                                )
                                            query_params = parse_qs(
                                                case.path.partition("?")[2]
                                            )
                                            if target is not None and query_params.get(
                                                "algorithm", ["jdk"]
                                            )[0] == "jdk":
                                                benchmark = query_params.get(
                                                    "benchmark", ["VNINDEX"]
                                                )[0]
                                                expected_jdk = _expected_jdk_rrg(
                                                    rows,
                                                    source=source,
                                                    symbol=required_symbol,
                                                    benchmark=benchmark,
                                                    period=int(query_params.get("period", [10])[0]),
                                                    ema=query_params.get("ema", ["false"])[0]
                                                    .lower()
                                                    == "true",
                                                    end_date=query_params.get("date", [None])[0],
                                                    trails=int(query_params.get("trails", [10])[0]),
                                                )
                                                if expected_jdk is None:
                                                    valid_shape = False
                                                else:
                                                    for field_name in (
                                                        "rs_ratio",
                                                        "rs_momentum",
                                                        "raw_rs",
                                                    ):
                                                        if not math.isclose(
                                                            target.get(field_name, math.nan),
                                                            expected_jdk[
                                                                "rs_ratio"
                                                                if field_name == "rs_ratio"
                                                                else "rs_momentum"
                                                                if field_name == "rs_momentum"
                                                                else "raw_rs"
                                                            ],
                                                            rel_tol=0,
                                                            abs_tol=1e-8,
                                                        ):
                                                            valid_shape = False
                                                    expected_trails = expected_jdk["trails"]
                                                    actual_trails = target.get("trails")
                                                    if not isinstance(actual_trails, list) or len(
                                                        actual_trails
                                                    ) != len(expected_trails):
                                                        valid_shape = False
                                                    else:
                                                        for actual_point, expected_point in zip(
                                                            actual_trails,
                                                            expected_trails,
                                                            strict=True,
                                                        ):
                                                            valid_shape = valid_shape and (
                                                                actual_point.get("date")
                                                                == expected_point["date"]
                                                                and math.isclose(
                                                                    actual_point.get(
                                                                        "rs_ratio", math.nan
                                                                    ),
                                                                    expected_point["rs_ratio"],
                                                                    rel_tol=0,
                                                                    abs_tol=1e-8,
                                                                )
                                                                and math.isclose(
                                                                    actual_point.get(
                                                                        "rs_momentum", math.nan
                                                                    ),
                                                                    expected_point[
                                                                        "rs_momentum"
                                                                    ],
                                                                    rel_tol=0,
                                                                    abs_tol=1e-8,
                                                                )
                                                            )
                                        required_identities = expected_rrg_mixed_identities.get(
                                            case.name
                                        )
                                        if required_identities is not None:
                                            query_params = parse_qs(
                                                case.path.partition("?")[2]
                                            )
                                            requested_mode = query_params.get("mode", ["vn"])[0]
                                            single_source = {
                                                "stock": "vn",
                                                "stocks": "vn",
                                                "vn": "vn",
                                                "crypto": "crypto",
                                                "cryptos": "crypto",
                                                "yahoo": "yahoo",
                                            }.get(requested_mode)
                                            actual_identities = Counter(
                                                (
                                                    row.get("symbol"),
                                                    row.get("source") or single_source,
                                                )
                                                for row in ticker_rows
                                                if isinstance(row, dict)
                                            )
                                            if actual_identities != required_identities:
                                                failures.append(
                                                    f"{case.name}: {runtime} RRG ticker identities "
                                                    f"were {actual_identities!r}, expected "
                                                    f"{required_identities!r}"
                                                )
                                            valid_shape = (
                                                valid_shape
                                                and actual_identities == required_identities
                                            )
                                            for row in ticker_rows:
                                                if not isinstance(row, dict):
                                                    continue
                                                expected_candle = current_daily_candles.get(
                                                    (
                                                        row.get("source") or single_source,
                                                        row.get("symbol"),
                                                    )
                                                )
                                                valid_shape = valid_shape and (
                                                    expected_candle is not None
                                                    and row.get("close") == expected_candle.close
                                                    and row.get("volume")
                                                    == expected_candle.volume
                                                )
                                        query_params = parse_qs(case.path.partition("?")[2])
                                        if (
                                            query_params.get("algorithm", ["jdk"])[0] == "jdk"
                                            and isinstance(ticker_rows, list)
                                        ):
                                            requested_mode = query_params.get("mode", ["vn"])[0]
                                            single_source = {
                                                "stock": "vn",
                                                "stocks": "vn",
                                                "vn": "vn",
                                                "crypto": "crypto",
                                                "cryptos": "crypto",
                                                "yahoo": "yahoo",
                                            }.get(requested_mode)
                                            benchmark = query_params.get(
                                                "benchmark", ["VNINDEX"]
                                            )[0].upper()
                                            benchmark_source = single_source
                                            if requested_mode == "all":
                                                end_date = query_params.get("date", [None])[0]
                                                benchmark_end = (
                                                    int(
                                                        datetime.fromisoformat(end_date)
                                                        .replace(tzinfo=UTC)
                                                        .timestamp()
                                                    )
                                                    + 86399
                                                    if end_date
                                                    else None
                                                )
                                                benchmark_source = next(
                                                    (
                                                        source
                                                        for source in ("vn", "yahoo", "crypto")
                                                        if any(
                                                            row.source == source
                                                            and row.symbol == benchmark
                                                            and row.interval == "1D"
                                                            and (
                                                                benchmark_end is None
                                                                or row.time <= benchmark_end
                                                            )
                                                            for row in rows
                                                        )
                                                    ),
                                                    None,
                                                )
                                            for actual in ticker_rows:
                                                if not isinstance(actual, dict):
                                                    continue
                                                source = actual.get("source") or single_source
                                                if source is None or benchmark_source is None:
                                                    valid_shape = False
                                                    continue
                                                expected_jdk = _expected_jdk_rrg(
                                                    rows,
                                                    source=source,
                                                    symbol=actual.get("symbol", ""),
                                                    benchmark=benchmark,
                                                    benchmark_source=benchmark_source,
                                                    period=int(
                                                        query_params.get("period", [10])[0]
                                                    ),
                                                    ema=query_params.get("ema", ["false"])[
                                                        0
                                                    ].lower()
                                                    == "true",
                                                    end_date=query_params.get("date", [None])[0],
                                                    trails=int(
                                                        query_params.get("trails", [10])[0]
                                                    ),
                                                )
                                                if expected_jdk is None:
                                                    valid_shape = False
                                                    continue
                                                valid_shape = valid_shape and (
                                                    actual.get("close") == expected_jdk["close"]
                                                    and actual.get("volume")
                                                    == expected_jdk["volume"]
                                                )
                                                for field_name in (
                                                    "rs_ratio",
                                                    "rs_momentum",
                                                    "raw_rs",
                                                ):
                                                    valid_shape = valid_shape and math.isclose(
                                                        actual.get(field_name, math.nan),
                                                        expected_jdk[field_name],
                                                        rel_tol=0,
                                                        abs_tol=1e-8,
                                                    )
                                                expected_trails = expected_jdk["trails"]
                                                actual_trails = actual.get("trails")
                                                if expected_trails is None:
                                                    valid_shape = valid_shape and actual_trails is None
                                                elif not isinstance(actual_trails, list) or len(
                                                    actual_trails
                                                ) != len(expected_trails):
                                                    valid_shape = False
                                                else:
                                                    for actual_point, expected_point in zip(
                                                        actual_trails,
                                                        expected_trails,
                                                        strict=True,
                                                    ):
                                                        valid_shape = valid_shape and (
                                                            actual_point.get("date")
                                                            == expected_point["date"]
                                                            and math.isclose(
                                                                actual_point.get(
                                                                    "rs_ratio", math.nan
                                                                ),
                                                                expected_point["rs_ratio"],
                                                                rel_tol=0,
                                                                abs_tol=1e-8,
                                                            )
                                                            and math.isclose(
                                                                actual_point.get(
                                                                    "rs_momentum", math.nan
                                                                ),
                                                                expected_point["rs_momentum"],
                                                                rel_tol=0,
                                                                abs_tol=1e-8,
                                                            )
                                                        )
                                    else:
                                        profile = data.get("profile")
                                        profile_fields = {
                                            "price",
                                            "volume",
                                            "percentage",
                                            "cumulative_percentage",
                                        }
                                        valid_shape = (
                                            isinstance(profile, list)
                                            and bool(profile)
                                            and all(
                                                isinstance(level, dict)
                                                and profile_fields.issubset(level)
                                                and all(
                                                    _is_finite_json_number(level.get(field))
                                                    for field in profile_fields
                                                )
                                                and level.get("volume") >= 0
                                                and 0 <= level.get("percentage") <= 100 + 1e-9
                                                and 0
                                                <= level.get("cumulative_percentage")
                                                <= 100 + 1e-9
                                                for level in profile
                                            )
                                            and isinstance(data.get("total_minutes"), int)
                                            and not isinstance(data.get("total_minutes"), bool)
                                            and data.get("total_minutes", 0) > 0
                                            and _is_finite_json_number(data.get("total_volume"))
                                            and data.get("total_volume", 0) > 0
                                            and isinstance(data.get("poc"), dict)
                                            and isinstance(data.get("value_area"), dict)
                                            and isinstance(data.get("price_range"), dict)
                                            and isinstance(data.get("statistics"), dict)
                                        )
                                        if valid_shape:
                                            valid_shape = (
                                                all(
                                                    _is_finite_json_number(data["poc"].get(field))
                                                    for field in ("price", "volume", "percentage")
                                                )
                                                and all(
                                                    _is_finite_json_number(
                                                        data["value_area"].get(field)
                                                    )
                                                    for field in (
                                                        "low",
                                                        "high",
                                                        "volume",
                                                        "percentage",
                                                    )
                                                )
                                                and all(
                                                    _is_finite_json_number(
                                                        data["price_range"].get(field)
                                                    )
                                                    for field in ("low", "high", "spread")
                                                )
                                                and all(
                                                    _is_finite_json_number(
                                                        data["statistics"].get(field)
                                                    )
                                                    for field in (
                                                        "mean_price",
                                                        "median_price",
                                                        "std_deviation",
                                                        "skewness",
                                                    )
                                                )
                                            )
                                        if valid_shape:
                                            valid_shape = (
                                                payload.get("total_analyzed")
                                                == data.get("total_minutes")
                                                and math.isclose(
                                                    sum(level["volume"] for level in profile),
                                                    data["total_volume"],
                                                    rel_tol=0,
                                                    abs_tol=1e-6,
                                                )
                                                and data["price_range"]["low"]
                                                <= data["price_range"]["high"]
                                                and data["value_area"]["low"]
                                                <= data["value_area"]["high"]
                                            )
                                        if case.name in {
                                            "volume-profile-range",
                                            "production-origin-data-volume-profile",
                                            "production-origin-data-volume-profile-range",
                                        }:
                                            start_day = (
                                                (FIXTURE_END - timedelta(days=30)).date()
                                                if case.name
                                                in {
                                                    "volume-profile-range",
                                                    "production-origin-data-volume-profile-range",
                                                }
                                                else FIXTURE_END.date()
                                            )
                                            minute_rows = [
                                                row
                                                for row in rows
                                                if row.source == "vn"
                                                and row.symbol == "FPT"
                                                and row.interval == "1m"
                                                and start_day
                                                <= datetime.fromtimestamp(row.time, UTC).date()
                                                <= FIXTURE_END.date()
                                            ]
                                            valid_shape = valid_shape and (
                                                payload.get("total_analyzed") == len(minute_rows)
                                                and data.get("total_minutes") == len(minute_rows)
                                                and data.get("total_volume")
                                                == sum(row.volume for row in minute_rows)
                                            )
                                        elif case.name in {
                                            "volume-profile-crypto-success",
                                            "volume-profile-yahoo-success",
                                        }:
                                            source, symbol = (
                                                ("crypto", "ETHUSDT")
                                                if case.name == "volume-profile-crypto-success"
                                                else ("yahoo", "AAPL")
                                            )
                                            minute_rows = [
                                                row
                                                for row in rows
                                                if row.source == source
                                                and row.symbol == symbol
                                                and row.interval == "1m"
                                                and datetime.fromtimestamp(row.time, UTC).date()
                                                == FIXTURE_END.date()
                                            ]
                                            valid_shape = valid_shape and (
                                                bool(minute_rows)
                                                and payload.get("total_analyzed")
                                                == len(minute_rows)
                                                and data.get("total_minutes") == len(minute_rows)
                                                and data.get("total_volume")
                                                == sum(row.volume for row in minute_rows)
                                            )
                                        profile_config = {
                                            "volume-profile-bins-usize-maximum-clamp": (
                                                "crypto",
                                                "PROFILEUSDT",
                                                200,
                                                70,
                                            ),
                                            "volume-profile-exact-low-boundaries": (
                                                "crypto",
                                                "PROFILEUSDT",
                                                2,
                                                70,
                                            ),
                                            "volume-profile-exact-high-boundaries": (
                                                "crypto",
                                                "PROFILEUSDT",
                                                200,
                                                90,
                                            ),
                                            "volume-profile-value-area-minimum-boundary": (
                                                "crypto",
                                                "PROFILEUSDT",
                                                200,
                                                60,
                                            ),
                                            "volume-profile-clamped-low-bounds": (
                                                "crypto",
                                                "PROFILEUSDT",
                                                2,
                                                60,
                                            ),
                                            "volume-profile-clamped-high-bounds": (
                                                "crypto",
                                                "PROFILEUSDT",
                                                200,
                                                90,
                                            ),
                                            "volume-profile": ("vn", "FPT", 20, 70),
                                            "volume-profile-widget-vn-default-bins": (
                                                "vn",
                                                "FPT",
                                                10,
                                                70,
                                            ),
                                            "volume-profile-widget-crypto-default-bins": (
                                                "crypto",
                                                "ETHUSDT",
                                                10,
                                                70,
                                            ),
                                            "volume-profile-low-priced-crypto-poc-tolerance": (
                                                "crypto",
                                                "MICROUSDT",
                                                10,
                                                70,
                                            ),
                                            "volume-profile-widget-yahoo-default-bins": (
                                                "yahoo",
                                                "AAPL",
                                                10,
                                                70,
                                            ),
                                            "volume-profile-default-query-options": (
                                                "vn",
                                                "FPT",
                                                50,
                                                70,
                                            ),
                                            "volume-profile-date-overrides-range": (
                                                "vn",
                                                "FPT",
                                                20,
                                                70,
                                            ),
                                            "volume-profile-start-only": ("vn", "FPT", 20, 70),
                                            "volume-profile-range": ("vn", "FPT", 10, 70),
                                            "production-origin-data-volume-profile": (
                                                "vn",
                                                "FPT",
                                                10,
                                                70,
                                            ),
                                            "production-origin-data-volume-profile-range": (
                                                "vn",
                                                "FPT",
                                                10,
                                                70,
                                            ),
                                            "volume-profile-crypto-success": (
                                                "crypto",
                                                "ETHUSDT",
                                                20,
                                                70,
                                            ),
                                            "volume-profile-yahoo-success": (
                                                "yahoo",
                                                "AAPL",
                                                20,
                                                70,
                                            ),
                                        }.get(case.name)
                                        if profile_config is not None:
                                            source, symbol, bins, value_area_pct = profile_config
                                            start_day = (
                                                (FIXTURE_END - timedelta(days=30)).date()
                                                if case.name
                                                in {
                                                    "volume-profile-range",
                                                    "production-origin-data-volume-profile-range",
                                                }
                                                else FIXTURE_END.date()
                                            )
                                            profile_rows = [
                                                row
                                                for row in rows
                                                if row.source == source
                                                and row.symbol == symbol
                                                and row.interval == "1m"
                                                and start_day
                                                <= datetime.fromtimestamp(row.time, UTC).date()
                                                <= FIXTURE_END.date()
                                            ]
                                            expected_profile = _expected_volume_profile(
                                                profile_rows,
                                                source=source,
                                                symbol=symbol,
                                                bins=bins,
                                                value_area_pct=value_area_pct,
                                            )
                                            expected_profile["price_range"]["spread"] = (
                                                expected_profile["price_range"]["high"]
                                                - expected_profile["price_range"]["low"]
                                            )

                                            def profile_float_matches(actual_value, expected_value):
                                                return (
                                                    _is_finite_json_number(actual_value)
                                                    and math.isclose(
                                                        actual_value,
                                                        expected_value,
                                                        rel_tol=0,
                                                        abs_tol=1e-7,
                                                    )
                                                )

                                            profile_matches = (
                                                data.get("symbol") == symbol
                                                and data.get("total_minutes")
                                                == expected_profile["total_minutes"]
                                                and data.get("total_volume")
                                                == expected_profile["total_volume"]
                                            )
                                            if case.name == (
                                                "volume-profile-low-priced-crypto-poc-tolerance"
                                            ):
                                                poc = data.get("poc", {})
                                                value_area = data.get("value_area", {})
                                                profile_matches = profile_matches and (
                                                    profile_float_matches(poc.get("price"), 0.0002)
                                                    and profile_float_matches(
                                                        value_area.get("low"), 0.0001
                                                    )
                                                    and profile_float_matches(
                                                        value_area.get("high"), 0.0002
                                                    )
                                                    and profile_float_matches(
                                                        value_area.get("volume"), 101.0
                                                    )
                                                )
                                            for group_name in (
                                                "price_range",
                                                "poc",
                                                "value_area",
                                                "statistics",
                                            ):
                                                actual_group = data.get(group_name)
                                                expected_group = expected_profile[group_name]
                                                profile_matches = (
                                                    profile_matches
                                                    and isinstance(actual_group, dict)
                                                    and all(
                                                        profile_float_matches(
                                                            actual_group.get(field_name),
                                                            expected_value,
                                                        )
                                                        for field_name, expected_value
                                                        in expected_group.items()
                                                    )
                                                )
                                            actual_levels = data.get("profile")
                                            expected_levels = expected_profile["profile"]
                                            profile_matches = (
                                                profile_matches
                                                and isinstance(actual_levels, list)
                                                and len(actual_levels) == len(expected_levels)
                                            )
                                            if isinstance(actual_levels, list) and len(
                                                actual_levels
                                            ) == len(expected_levels):
                                                for actual_level, expected_level in zip(
                                                    actual_levels,
                                                    expected_levels,
                                                    strict=True,
                                                ):
                                                    profile_matches = profile_matches and all(
                                                        profile_float_matches(
                                                            actual_level.get(field_name),
                                                            expected_value,
                                                        )
                                                        for field_name, expected_value
                                                        in expected_level.items()
                                                    )
                                            valid_shape = valid_shape and profile_matches
                                    if not valid_shape:
                                        failures.append(
                                            f"{case.name}: {runtime} {expected_analysis_type} "
                                            f"data has an unexpected shape: {repr(data)[:800]}"
                                        )

                    if case.name.startswith("top-performers-sort-") or case.name in {
                        "production-origin-data-top-performers-raw"
                    }:
                        raw_ema = "ema=true" in case.path
                        expected_rows = {
                            symbol: _expected_fixture_performer_metrics(
                                source, symbol, ema=raw_ema
                            )
                            for source, symbol, *_ in TICKERS
                            if source == "vn" and symbol != "VNINDEX"
                        }
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            try:
                                payload = response.json()
                                data = payload["data"]
                            except (KeyError, TypeError, ValueError):
                                failures.append(
                                    f"{case.name}: {runtime} omitted the performer data needed "
                                    "for independent metric checks"
                                )
                                continue
                            performers = data.get("performers", [])
                            worst_performers = data.get("worst_performers", [])
                            expected_symbols = Counter(
                                {symbol: 1 for symbol in expected_rows}
                            )
                            for collection_name, collection in (
                                ("performers", performers),
                                ("worst_performers", worst_performers),
                            ):
                                actual_symbols = Counter(
                                    row.get("symbol")
                                    for row in collection
                                    if isinstance(row, dict)
                                )
                                if actual_symbols != expected_symbols:
                                    failures.append(
                                        f"{case.name}: {runtime} {collection_name} symbols "
                                        f"{actual_symbols!r}, expected {expected_symbols!r}"
                                    )
                                for row in collection:
                                    if not isinstance(row, dict):
                                        continue
                                    symbol = row.get("symbol")
                                    expected = expected_rows.get(symbol)
                                    if expected is None:
                                        continue
                                    for field, expected_value in expected.items():
                                        actual_value = row.get(field)
                                        if (
                                            not _is_finite_json_number(actual_value)
                                            or not math.isclose(
                                                actual_value,
                                                expected_value,
                                                rel_tol=0,
                                                abs_tol=1e-9,
                                            )
                                        ):
                                            failures.append(
                                                f"{case.name}: {runtime} {collection_name} "
                                                f"{symbol} {field} {actual_value!r}, expected "
                                                f"{expected_value!r}"
                                            )

                            if case.name.startswith("top-performers-sort-"):
                                metric, direction = case.name.removeprefix(
                                    "top-performers-sort-"
                                ).rsplit("-", 1)
                                rows_for_sort = [
                                    {"symbol": symbol, **metrics}
                                    for symbol, metrics in expected_rows.items()
                                ]

                                ascending_rows = sorted(
                                    rows_for_sort,
                                    key=lambda row: (row[metric], row["symbol"]),
                                )
                                descending_rows = sorted(
                                    rows_for_sort,
                                    key=lambda row: (-row[metric], row["symbol"]),
                                )
                                expected_top = (
                                    ascending_rows if direction == "asc" else descending_rows
                                )[:7]
                                expected_worst = (
                                    descending_rows if direction == "asc" else ascending_rows
                                )[:7]
                                actual_top = [
                                    row.get("symbol")
                                    for row in performers
                                    if isinstance(row, dict)
                                ]
                                actual_worst = [
                                    row.get("symbol")
                                    for row in worst_performers
                                    if isinstance(row, dict)
                                ]
                                if (
                                    actual_top != [row["symbol"] for row in expected_top]
                                    or actual_worst
                                    != [row["symbol"] for row in expected_worst]
                                    or payload.get("total_analyzed")
                                    != len(expected_top) + len(expected_worst)
                                ):
                                    failures.append(
                                        f"{case.name}: {runtime} ordering/limit was "
                                        f"{actual_top!r}/{actual_worst!r}, expected "
                                        f"{[row['symbol'] for row in expected_top]!r}/"
                                        f"{[row['symbol'] for row in expected_worst]!r}"
                                    )

                    if case.name.startswith("top-performers-latest-") or case.name == (
                        "production-origin-data-top-performers"
                    ):
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            try:
                                payload = response.json()
                                data = payload["data"]
                                performers = data["performers"]
                                worst_performers = data["worst_performers"]
                            except (KeyError, TypeError, ValueError):
                                failures.append(
                                    f"{case.name}: {runtime} omitted the complete performer lists"
                                )
                                continue

                            if case.name.startswith("top-performers-latest-"):
                                source = case.name.split("-")[3]
                                symbols_by_source = {
                                    "crypto": ("BTCUSDT", "ETHUSDT"),
                                    "yahoo": (
                                        "AAPL",
                                        "DUPL",
                                        "FPT",
                                        "DUPL,FPT",
                                        "COLL",
                                        "^GSPC",
                                    ),
                                }
                                expected_rows = [
                                    {
                                        "symbol": symbol,
                                        "sector": {
                                            "crypto": {
                                                "BTCUSDT": None,
                                                "ETHUSDT": None,
                                            },
                                            "yahoo": {
                                                "AAPL": None,
                                                "DUPL": "Duplicate",
                                                "FPT": "Technology",
                                                "DUPL,FPT": None,
                                                "COLL": None,
                                                "^GSPC": None,
                                            },
                                        }[source][symbol],
                                        **_expected_fixture_performer_metrics(
                                            source, symbol, ema=True
                                        ),
                                    }
                                    for symbol in symbols_by_source[source]
                                ]
                                sort_by = "total_money_changed"
                                expected_top = sorted(
                                    expected_rows,
                                    key=lambda row: (-row[sort_by], row["symbol"]),
                                )[:7]
                                expected_worst = sorted(
                                    expected_rows,
                                    key=lambda row: (row[sort_by], row["symbol"]),
                                )[:7]
                            else:
                                if use_live_redis:
                                    expected_rows = []
                                    for source, symbol, _name, base, _slope in TICKERS:
                                        if source != "vn" or symbol == "VNINDEX":
                                            continue
                                        close = 9876.5 if symbol == "VCB" else base + 1
                                        expected_rows.append(
                                            {
                                                "symbol": symbol,
                                                "close": close,
                                                "volume": 250_000,
                                                "close_changed": None,
                                                "volume_changed": None,
                                                "ma10": None,
                                                "ma20": close - 2,
                                                "ma50": None,
                                                "ma100": None,
                                                "ma200": None,
                                                "ma10_score": None,
                                                "ma20_score": None
                                                if symbol == "VCB"
                                                else base / 100,
                                                "ma50_score": None,
                                                "ma100_score": None,
                                                "ma200_score": None,
                                                "total_money_changed": None,
                                            }
                                        )
                                else:
                                    expected_rows = [
                                        {
                                            "symbol": symbol,
                                            **_expected_fixture_performer_metrics(
                                                "vn", symbol, ema=False
                                            ),
                                        }
                                        for source, symbol, *_ in TICKERS
                                        if source == "vn" and symbol != "VNINDEX"
                                    ]
                                sort_by = "close_changed"
                                expected_top = sorted(
                                    expected_rows,
                                    key=lambda row: (
                                        row[sort_by] is None,
                                        -(row[sort_by] or 0),
                                        row["symbol"],
                                    ),
                                )[:10]
                                expected_worst = sorted(
                                    expected_rows,
                                    key=lambda row: (
                                        row[sort_by] is None,
                                        row[sort_by] or 0,
                                        row["symbol"],
                                    ),
                                )[:10]

                            for label, actual_rows, expected in (
                                ("performers", performers, expected_top),
                                ("worst_performers", worst_performers, expected_worst),
                            ):
                                actual_symbols = [
                                    row.get("symbol")
                                    for row in actual_rows
                                    if isinstance(row, dict)
                                ]
                                expected_symbols = [row["symbol"] for row in expected]
                                if actual_symbols != expected_symbols:
                                    failures.append(
                                        f"{case.name}: {runtime} {label} order/membership "
                                        f"{actual_symbols!r}, expected {expected_symbols!r}"
                                    )
                                expected_by_symbol = {
                                    row["symbol"]: row for row in expected_rows
                                }
                                for actual in actual_rows:
                                    if not isinstance(actual, dict):
                                        continue
                                    if case.name.startswith("top-performers-latest-") and (
                                        "source" in actual
                                    ):
                                        failures.append(
                                            f"{case.name}: {runtime} single-source performer "
                                            f"{actual.get('symbol')} unexpectedly serialized source"
                                        )
                                    expected_row = expected_by_symbol.get(actual.get("symbol"))
                                    if expected_row is None:
                                        continue
                                    for field_name, expected_value in expected_row.items():
                                        actual_value = actual.get(field_name)
                                        if expected_value is None:
                                            matches = actual_value is None
                                        elif field_name in {"symbol", "volume", "sector"}:
                                            matches = actual_value == expected_value
                                        else:
                                            matches = (
                                                _is_finite_json_number(actual_value)
                                                and math.isclose(
                                                    actual_value,
                                                    expected_value,
                                                    rel_tol=0,
                                                    abs_tol=1e-9,
                                                )
                                            )
                                        if not matches:
                                            failures.append(
                                                f"{case.name}: {runtime} {actual.get('symbol')} "
                                                f"{field_name} {actual_value!r}, expected "
                                                f"{expected_value!r}"
                                            )
                            expected_total = len(expected_top) + len(expected_worst)
                            if payload.get("total_analyzed") != expected_total:
                                failures.append(
                                    f"{case.name}: {runtime} total_analyzed "
                                    f"{payload.get('total_analyzed')!r}, expected {expected_total}"
                                )

                    if (
                        case.name.startswith("ma-scores-period-")
                        and "-ema-" in case.name
                    ) or case.name == "production-origin-data-ma-scores-raw":
                        if case.name.startswith("ma-scores-period-"):
                            name_parts = case.name.split("-")
                            ma_period = int(name_parts[3])
                            ma_ema = name_parts[5] == "true"
                        else:
                            ma_period = 20
                            ma_ema = False
                        expected_fpt = _expected_fixture_performer_metrics(
                            "vn", "FPT", ema=ma_ema
                        )
                        expected_ma_value = expected_fpt[f"ma{ma_period}"]
                        expected_ma_score = expected_fpt[f"ma{ma_period}_score"]
                        expected_close = _fixture_daily_close(
                            "vn", "FPT", DAILY_BARS - 1
                        )
                        expected_volume = _fixture_daily_volume(
                            "vn", "FPT", DAILY_BARS - 1
                        )
                        expected_sector_members = {
                            "Banking": ["VCB", "VCB"],
                            "Duplicate": ["DUPL"],
                            "Microcap": ["ABC"],
                            "Technology": ["FPT"],
                        }
                        expected_sector_rows = {
                            name: [
                                _expected_fixture_performer_metrics("vn", symbol, ema=ma_ema)
                                for symbol in symbols
                            ]
                            for name, symbols in expected_sector_members.items()
                        }
                        expected_sector_averages = {
                            name: sum(
                                row[f"ma{ma_period}_score"] for row in sector_rows
                            )
                            / len(sector_rows)
                            for name, sector_rows in expected_sector_rows.items()
                        }
                        expected_sector_order = sorted(
                            expected_sector_members,
                            key=lambda name: (-expected_sector_averages[name], name),
                        )
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            try:
                                payload = response.json()
                                data = payload["data"]
                                sectors = data["sectors"]
                                technology = next(
                                    sector
                                    for sector in sectors
                                    if sector.get("sector_name") == "Technology"
                                )
                                fpt = next(
                                    stock
                                    for stock in technology.get("top_stocks", [])
                                    if stock.get("symbol") == "FPT"
                                )
                            except (KeyError, StopIteration, TypeError, ValueError):
                                failures.append(
                                    f"{case.name}: {runtime} omitted the expected FPT/"
                                    "Technology MA result"
                                )
                                continue
                            actual_sector_names = [
                                sector.get("sector_name")
                                for sector in sectors
                                if isinstance(sector, dict)
                            ]
                            if actual_sector_names != expected_sector_order:
                                failures.append(
                                    f"{case.name}: {runtime} sector order/membership "
                                    f"{actual_sector_names!r}, expected "
                                    f"{expected_sector_order!r}"
                                )
                            exact_values = {
                                "data.ma_period": (data.get("ma_period"), ma_period),
                                "data.threshold": (data.get("threshold"), 0),
                                "fpt.close": (fpt.get("close"), expected_close),
                                "fpt.volume": (fpt.get("volume"), expected_volume),
                                "fpt.ma_value": (fpt.get("ma_value"), expected_ma_value),
                                "fpt.ma_score": (fpt.get("ma_score"), expected_ma_score),
                                "technology.total_stocks": (technology.get("total_stocks"), 1),
                                "technology.stocks_above_threshold": (
                                    technology.get("stocks_above_threshold"),
                                    1,
                                ),
                            }
                            for field, (actual_value, expected_value) in exact_values.items():
                                if isinstance(expected_value, float):
                                    matches = _is_finite_json_number(actual_value) and math.isclose(
                                        actual_value,
                                        expected_value,
                                        rel_tol=0,
                                        abs_tol=1e-9,
                                    )
                                else:
                                    matches = actual_value == expected_value
                                if not matches:
                                    failures.append(
                                        f"{case.name}: {runtime} {field} "
                                        f"{actual_value!r}, expected {expected_value!r}"
                                    )
                            expected_total_stocks = sum(
                                len(symbols)
                                for symbols in expected_sector_members.values()
                            )
                            if payload.get("total_analyzed") != expected_total_stocks:
                                failures.append(
                                    f"{case.name}: {runtime} total_analyzed "
                                    f"{payload.get('total_analyzed')!r}, expected "
                                    f"{expected_total_stocks}"
                                )
                            for sector in sectors:
                                if not isinstance(sector, dict):
                                    continue
                                name = sector.get("sector_name")
                                if name not in expected_sector_members:
                                    continue
                                expected_members = expected_sector_members[name]
                                expected_rows_for_sector = expected_sector_rows[name]
                                actual_stocks = sector.get("top_stocks")
                                if not isinstance(actual_stocks, list):
                                    failures.append(
                                        f"{case.name}: {runtime} {name} top_stocks is not a list"
                                    )
                                    continue
                                actual_members = Counter(
                                    stock.get("symbol")
                                    for stock in actual_stocks
                                    if isinstance(stock, dict)
                                )
                                if actual_members != Counter(expected_members):
                                    failures.append(
                                        f"{case.name}: {runtime} {name} members "
                                        f"{actual_members!r}, expected "
                                        f"{Counter(expected_members)!r}"
                                    )
                                expected_by_symbol = {
                                    symbol: metrics
                                    for symbol, metrics in zip(
                                        expected_members,
                                        expected_rows_for_sector,
                                        strict=True,
                                    )
                                }
                                for stock in actual_stocks:
                                    if not isinstance(stock, dict):
                                        continue
                                    symbol = stock.get("symbol")
                                    expected_metrics = expected_by_symbol.get(symbol)
                                    if expected_metrics is None:
                                        continue
                                    expected_stock_values = {
                                        "close": expected_metrics["close"],
                                        "volume": expected_metrics["volume"],
                                        "ma_value": expected_metrics[f"ma{ma_period}"],
                                        "ma_score": expected_metrics[f"ma{ma_period}_score"],
                                        "close_changed": expected_metrics["close_changed"],
                                        "volume_changed": expected_metrics["volume_changed"],
                                    }
                                    for field, expected_value in expected_stock_values.items():
                                        actual_value = stock.get(field)
                                        if field == "volume":
                                            matches = actual_value == expected_value
                                        else:
                                            matches = (
                                                _is_finite_json_number(actual_value)
                                                and math.isclose(
                                                    actual_value,
                                                    expected_value,
                                                    rel_tol=0,
                                                    abs_tol=1e-9,
                                                )
                                            )
                                        if not matches:
                                            failures.append(
                                                f"{case.name}: {runtime} {name}/{symbol} "
                                                f"{field} {actual_value!r}, expected "
                                                f"{expected_value!r}"
                                            )
                                expected_above = sum(
                                    metrics[f"ma{ma_period}_score"] >= 0
                                    for metrics in expected_rows_for_sector
                                )
                                if sector.get("total_stocks") != len(expected_members):
                                    failures.append(
                                        f"{case.name}: {runtime} {name} total_stocks "
                                        f"{sector.get('total_stocks')!r}, expected "
                                        f"{len(expected_members)}"
                                    )
                                if sector.get("stocks_above_threshold") != expected_above:
                                    failures.append(
                                        f"{case.name}: {runtime} {name} above-threshold count "
                                        f"{sector.get('stocks_above_threshold')!r}, expected "
                                        f"{expected_above}"
                                    )
                                actual_average = sector.get("average_score")
                                expected_average = expected_sector_averages[name]
                                if (
                                    not _is_finite_json_number(actual_average)
                                    or not math.isclose(
                                        actual_average,
                                        expected_average,
                                        rel_tol=0,
                                        abs_tol=1e-9,
                                    )
                                ):
                                    failures.append(
                                        f"{case.name}: {runtime} {name} average score "
                                        f"{actual_average!r}, expected {expected_average!r}"
                                    )

                    if case.name == "ticker-default-query-options":
                        for runtime, response in (
                            ("Rust", rust_response),
                            ("Python", python_response),
                        ):
                            try:
                                payload = response.json()
                            except ValueError:
                                payload = None
                            if (
                                not response.headers.get("content-type", "").startswith(
                                    "application/json"
                                )
                                or not isinstance(payload, dict)
                                or set(payload) != {"FPT"}
                                or not isinstance(payload["FPT"], list)
                                or len(payload["FPT"]) != 1
                            ):
                                failures.append(
                                    f"{case.name}: {runtime} default query did not return one "
                                    f"JSON FPT candle: {payload!r}"
                                )

                    if case.name in rejected_upload_sessions:
                        rejected_id = rejected_upload_sessions[case.name]
                        for implementation in ("rust-uploads", "python-uploads"):
                            path = root / implementation / rejected_id
                            if path.exists():
                                failures.append(
                                    f"{case.name}: {implementation} created session state "
                                    "before accepting the upload"
                                )

                    if case.name in successful_upload_sessions:
                        uploaded_id = successful_upload_sessions[case.name]
                        expected_keys = ["session_id", "secret_sha256", "is_public", "created_at"]
                        for implementation in ("rust-uploads", "python-uploads"):
                            metadata_path = root / implementation / uploaded_id / "metadata.json"
                            try:
                                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                            except (OSError, json.JSONDecodeError) as exc:
                                failures.append(
                                    f"{case.name}: {implementation} metadata unavailable: {exc}"
                                )
                                continue
                            if list(metadata) != expected_keys:
                                failures.append(
                                    f"{case.name}: {implementation} metadata keys/order "
                                    f"{list(metadata)!r}, expected {expected_keys!r}"
                                )
                            if (
                                metadata.get("secret_sha256")
                                != hashlib.sha256(SYNC_SECRET.encode("utf-8")).hexdigest()
                            ):
                                failures.append(
                                    f"{case.name}: {implementation} did not store the expected secret hash"
                                )
                            if "secret" in metadata:
                                failures.append(
                                    f"{case.name}: {implementation} stored the secret in plaintext"
                                )
                            if case.name == "notes-upload-maximum-markdown-size":
                                markdown_path = (
                                    root
                                    / implementation
                                    / uploaded_id
                                    / "markdown"
                                    / "content.md"
                                )
                                try:
                                    actual_size = markdown_path.stat().st_size
                                except OSError as exc:
                                    failures.append(
                                        f"{case.name}: {implementation} maximum-size file "
                                        f"was unavailable: {exc}"
                                    )
                                else:
                                    if actual_size != 5 * 1024 * 1024:
                                        failures.append(
                                            f"{case.name}: {implementation} stored {actual_size} "
                                            "bytes, expected exactly 5 MiB"
                                        )
                                    actual_digest = hashlib.sha256(
                                        markdown_path.read_bytes()
                                    ).hexdigest()
                                    expected_digest = hashlib.sha256(
                                        b"x" * (5 * 1024 * 1024)
                                    ).hexdigest()
                                    if actual_digest != expected_digest:
                                        failures.append(
                                            f"{case.name}: {implementation} stored incorrect "
                                            "5 MiB content"
                                        )

                    if case.name == "notes-fetch-symlinked-markdown-directory":
                        for implementation in ("rust-uploads", "python-uploads"):
                            external = root / f"outside-markdown-{implementation}"
                            (external / "outside.marker").write_text("keep", encoding="utf-8")

                    if case.name == "notes-delete-legacy-session":
                        # Add attack fixtures only after the intentional
                        # three-session quota cases have completed.
                        _seed_symlinked_notes(root, symlinked_notes)

                    if case.name == "notes-upload-symlinked-populated-markdown-directory":
                        for implementation in ("rust-uploads", "python-uploads"):
                            external = (
                                root / f"outside-populated-markdown-{implementation}" / "content.md"
                            )
                            if external.read_text(encoding="utf-8") != (
                                "must remain outside the session"
                            ):
                                failures.append(
                                    f"{implementation} modified a populated symlink target"
                                )

                baseline_bodies = top_performers_ignored_query_bodies
                baseline_name = "top-performers"
                for runtime, bodies in baseline_bodies.items():
                    baseline = bodies.get(baseline_name)
                    for case_name in (
                        "top-performers-with-hour-requested",
                        "top-performers-with-hour-false",
                        "top-performers-with-hour-invalid",
                        "top-performers-with-hour-duplicate",
                    ):
                        if case_name in bodies and bodies[case_name] != baseline:
                            failures.append(
                                f"{case_name}: {runtime} changed the top-performers response body "
                                "despite `with_hour` being an ignored query key"
                            )

                if use_unreachable_redis or use_permission_error_redis:
                    assert not failures, (
                        "Redis-unavailable/error fallback parity differences on the shared fixture:\n"
                        + _format_parity_failures(failures)
                    )
                    return

                public_directory = root / "public"
                saved_public_directory = root / "public-present"
                python_public_directory = app.state.settings.public_dir
                saved_python_public_directory = root / "python-public-present"
                public_directory.rename(saved_public_directory)
                python_public_directory.rename(saved_python_public_directory)
                try:
                    for method, path, expected_status in (
                        ("GET", "/public", 404),
                        ("HEAD", "/public", 404),
                        ("GET", "/public/missing.js", 404),
                        ("HEAD", "/public/missing.js", 404),
                        ("POST", "/public/missing.js", 405),
                        ("PUT", "/public/missing.js", 405),
                    ):
                        rust_missing = rust_client.request(
                            method,
                            path,
                            headers={"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                        )
                        python_missing = python_client.request(
                            method,
                            path,
                            headers={"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                        )
                        label = f"static-missing-directory-{method.lower()}-{path.strip('/').replace('/', '-')}"
                        differences = _differences(
                            _snapshot(rust_missing), _snapshot(python_missing)
                        )
                        if differences:
                            failures.append(f"{label}: " + "; ".join(differences))
                        rust_length = rust_missing.headers.get("content-length")
                        python_length = python_missing.headers.get("content-length")
                        if rust_length != python_length:
                            failures.append(
                                f"{label}: Rust Content-Length {rust_length!r} differs from "
                                f"Python Content-Length {python_length!r}"
                            )
                        for runtime, response in (
                            ("Rust", rust_missing),
                            ("Python", python_missing),
                        ):
                            if response.status_code != expected_status or response.content != b"":
                                failures.append(
                                    f"{label}: expected {runtime} empty HTTP {expected_status}, "
                                    f"got {response.status_code} and {len(response.content)} bytes"
                                )
                            if (
                                method == "GET"
                                and response.headers.get("content-length") is not None
                            ):
                                try:
                                    declared_length = int(response.headers["content-length"])
                                except ValueError:
                                    failures.append(
                                        f"{label}: {runtime} GET Content-Length is not an integer"
                                    )
                                else:
                                    if declared_length != len(response.content):
                                        failures.append(
                                            f"{label}: {runtime} GET Content-Length "
                                            f"{declared_length} differs from its empty body"
                                        )
                            if (
                                expected_status == 405
                                and response.headers.get("allow") != "GET,HEAD"
                            ):
                                failures.append(
                                    f"{label}: {runtime} Allow header was "
                                    f"{response.headers.get('allow')!r}, expected 'GET,HEAD'"
                                )
                        if method == "HEAD":
                            for runtime, client, response in (
                                ("Rust", rust_client, rust_missing),
                                ("Python", python_client, python_missing),
                            ):
                                get_response = client.get(
                                    path,
                                    headers={
                                        "origin": WEB_ORIGIN,
                                        "accept-encoding": "identity",
                                    },
                                )
                                difference = _differences(
                                    _snapshot(get_response, ("vary", "content-length")),
                                    _snapshot(response, ("vary", "content-length")),
                                    compare_body=False,
                                )
                                if difference:
                                    failures.append(
                                        f"{label}: {runtime} HEAD headers differ from matching "
                                        f"GET: " + "; ".join(difference)
                                    )
                                if get_response.status_code != response.status_code:
                                    failures.append(
                                        f"{label}: {runtime} HEAD status {response.status_code} "
                                        f"differs from matching GET {get_response.status_code}"
                                    )
                                if get_response.content:
                                    failures.append(
                                        f"{label}: {runtime} missing-resource GET unexpectedly "
                                        f"returned {len(get_response.content)} body bytes"
                                    )
                finally:
                    saved_public_directory.rename(public_directory)
                    saved_python_public_directory.rename(python_public_directory)

                for implementation in ("rust-uploads", "python-uploads"):
                    external_session = root / f"outside-session-{implementation}"
                    session_link = root / implementation / symlink_session_id
                    if not session_link.is_symlink():
                        failures.append(
                            f"{implementation} removed or replaced a rejected session symlink"
                        )
                    if (external_session / "outside.marker").read_text(encoding="utf-8") != "keep":
                        failures.append(f"{implementation} modified data outside the session root")

                    external_markdown = root / f"outside-markdown-{implementation}"
                    if (external_markdown / "outside.marker").read_text(encoding="utf-8") != "keep":
                        failures.append(
                            f"{implementation} traversed a symlinked markdown directory"
                        )
                    if (root / implementation / symlink_markdown_id).exists():
                        failures.append(f"{implementation} did not remove the symlinked session")

                    external_content = root / f"outside-content-{implementation}.md"
                    if (
                        external_content.read_text(encoding="utf-8")
                        != "must remain outside the session"
                    ):
                        failures.append(f"{implementation} modified a symlinked content target")
                    if (root / implementation / symlink_content_id).exists():
                        failures.append(
                            f"{implementation} did not remove the content symlink session"
                        )

                    metadata_link = root / implementation / symlink_metadata_id / "metadata.json"
                    if not metadata_link.is_symlink():
                        failures.append(f"{implementation} replaced a rejected metadata symlink")

                # Rust's volume-profile route reads available SQL/Redis rows
                # without consulting Python's separate history-gap ledger.
                # Verify that Python preserves this partial-data behavior too.
                minute_start = FIXTURE_END.replace(hour=2)
                app.state.repo.record_history_gap(
                    "vn",
                    "FPT",
                    "1m",
                    int((minute_start + timedelta(minutes=5)).timestamp()),
                    int((minute_start + timedelta(minutes=10)).timestamp()),
                    "differential fixture gap",
                    {"kind": "differential_policy_probe"},
                )
                gap_path = (
                    f"/analysis/volume-profile?symbol=FPT&mode=vn&date={FIXTURE_END.date()}"
                    "&bins=20&value_area_pct=70"
                )
                gap_headers = {"origin": WEB_ORIGIN}
                python_gap = python_client.get(gap_path, headers=gap_headers)
                rust_gap = rust_client.get(gap_path, headers=gap_headers)
                if python_gap.status_code != 200 or rust_gap.status_code != 200:
                    failures.append(
                        "volume-profile-history-gap: expected both APIs to return 200 from "
                        f"available partial minute rows, got Python {python_gap.status_code} "
                        f"{python_gap.text[:300]!r}; Rust {rust_gap.status_code} "
                        f"{rust_gap.text[:300]!r}"
                    )
                else:
                    difference = _differences(_snapshot(rust_gap), _snapshot(python_gap))
                    if difference:
                        failures.append("volume-profile-history-gap: " + "; ".join(difference))
                    try:
                        rust_body = rust_gap.json()
                        python_body = python_gap.json()
                    except (json.JSONDecodeError, AttributeError):
                        rust_body = python_body = {}
                    rust_profile = rust_body.get("data", {}) if isinstance(rust_body, dict) else {}
                    if (
                        not isinstance(rust_body, dict)
                        or not isinstance(python_body, dict)
                        or rust_body.get("total_analyzed") != 30
                        or not isinstance(rust_profile, dict)
                        or rust_profile.get("total_minutes") != 30
                        or rust_profile.get("total_volume", 0) <= 0
                        or rust_profile.get("total_volume") != 34_350
                        or python_body.get("total_analyzed") != 30
                    ):
                        failures.append(
                            "volume-profile-history-gap: expected 30 available rows and "
                            "34,350 total volume in both partial profiles"
                        )

                # The regular fixture intentionally stays small. Add a
                # request-scoped 110-symbol sector after all ordinary cases so
                # the maximum top-performer and per-sector clamps are visible
                # without widening unrelated catalog/health expectations.
                if not (use_live_redis or use_unreachable_redis or use_permission_error_redis):
                    cap_symbols = tuple(f"CAP{index:03d}" for index in range(110))
                    cap_tickers = tuple(
                        ("vn", symbol, f"Cap probe {symbol}") for symbol in cap_symbols
                    )
                    cap_group_path = catalog / "ticker_group.json"
                    cap_groups = json.loads(cap_group_path.read_text(encoding="utf-8"))
                    cap_groups["CapProbe"] = list(cap_symbols)
                    cap_group_path.write_text(
                        json.dumps(cap_groups, ensure_ascii=False), encoding="utf-8"
                    )
                    directory_path = catalog / "vn_tickers.json"
                    directory_payload = json.loads(directory_path.read_text(encoding="utf-8"))
                    cap_directory_rows = [
                        {
                            "symbol": symbol,
                            "name": f"Cap probe {symbol}",
                            "en_name": f"Cap probe {symbol}",
                            "exchange": "HOSE",
                            "listing_board": "HSX",
                            "listing_status": "listed",
                            "type": "stock",
                            "icb_level2_code": "CAP-PROBE",
                            "icb_level2_name": "CapProbe",
                            "icb_level2_name_en": "CapProbe",
                            "market_cap_vnd": 1_000_000_000,
                            "market_cap_as_of": "2026-10-20T00:00:00Z",
                            "market_cap_price_vnd": 10_000,
                            "market_cap_price_basis": "last_trade",
                            "shares_listed": 100_000,
                        }
                        for symbol in cap_symbols
                    ]
                    directory_payload["stocks"].extend(cap_directory_rows)
                    directory_payload["stock_count"] = len(directory_payload["stocks"])
                    directory_payload["market_cap_known_count"] = len(
                        directory_payload["stocks"]
                    )
                    directory_path.write_text(
                        json.dumps(directory_payload, ensure_ascii=False), encoding="utf-8"
                    )
                    app.state.catalog.vn_stock_rows = (
                        directory_payload,
                        tuple(
                            sorted(
                                directory_payload["stocks"],
                                key=lambda row: row["symbol"],
                            )
                        ),
                    )

                    cap_rows = []
                    cap_dates = FIXTURE_DATES[-21:]
                    cap_updated_at = int((FIXTURE_END + timedelta(days=1)).timestamp())
                    for ticker_index, symbol in enumerate(cap_symbols):
                        base = 20.0 + ticker_index
                        for bar_index, stamp in enumerate(cap_dates):
                            close = base + bar_index * 0.125 + (bar_index % 3) * 0.01
                            cap_rows.append(
                                Candle(
                                    "vn",
                                    symbol,
                                    "1D",
                                    int(stamp.timestamp()),
                                    close - 0.1,
                                    close + 0.2,
                                    close - 0.3,
                                    close,
                                    50_000 + ticker_index + bar_index,
                                    "differential-cap-fixture",
                                    "fixture-v1",
                                    cap_updated_at,
                                )
                            )
                    cap_seed_file = root / "cap-probe-seed.sql"
                    _write_postgres_seed(cap_seed_file, cap_rows, cap_tickers)
                    _seed_postgres(postgres_handle, cap_seed_file)
                    app.state.repo.put(cap_rows)

                    cap_query = (
                        f"mode=vn&date={FIXTURE_END.date().isoformat()}&sector=CapProbe"
                        "&limit=18446744073709551615&min_volume=0&snap=false"
                    )
                    rust_cap = rust_client.get(
                        f"/analysis/top-performers?{cap_query}", headers=gap_headers
                    )
                    python_cap = python_client.get(
                        f"/analysis/top-performers?{cap_query}", headers=gap_headers
                    )
                    cap_difference = _differences(
                        _snapshot(rust_cap), _snapshot(python_cap)
                    )
                    if cap_difference:
                        failures.append(
                            "top-performers-limit-usize-maximum-cap: "
                            + "; ".join(cap_difference)
                        )
                    try:
                        rust_cap_body = rust_cap.json()
                        python_cap_body = python_cap.json()
                        rust_cap_data = rust_cap_body.get("data", {})
                        python_cap_data = python_cap_body.get("data", {})
                    except (json.JSONDecodeError, AttributeError):
                        rust_cap_body = python_cap_body = {}
                        rust_cap_data = python_cap_data = {}
                    for runtime, body, data in (
                        ("Rust", rust_cap_body, rust_cap_data),
                        ("Python", python_cap_body, python_cap_data),
                    ):
                        performers = data.get("performers") if isinstance(data, dict) else None
                        worst = data.get("worst_performers") if isinstance(data, dict) else None
                        if (
                            not isinstance(body, dict)
                            or body.get("total_analyzed") != 200
                            or not isinstance(performers, list)
                            or len(performers) != 100
                            or not isinstance(worst, list)
                            or len(worst) != 100
                        ):
                            failures.append(
                                f"top-performers-limit-usize-maximum-cap: {runtime} did not "
                                "return exactly 100 top and 100 worst rows from 110 eligible symbols"
                            )

                    ma_cap_query = (
                        f"mode=vn&date={FIXTURE_END.date().isoformat()}&ma_period=20"
                        "&min_score=0&top_per_sector=18446744073709551615&snap=false"
                    )
                    rust_ma_cap = rust_client.get(
                        f"/analysis/ma-scores-by-sector?{ma_cap_query}", headers=gap_headers
                    )
                    python_ma_cap = python_client.get(
                        f"/analysis/ma-scores-by-sector?{ma_cap_query}", headers=gap_headers
                    )
                    ma_cap_difference = _differences(
                        _snapshot(rust_ma_cap), _snapshot(python_ma_cap)
                    )
                    if ma_cap_difference:
                        failures.append(
                            "ma-scores-top-per-sector-usize-maximum-cap: "
                            + "; ".join(ma_cap_difference)
                        )
                    try:
                        rust_ma_body = rust_ma_cap.json()
                        python_ma_body = python_ma_cap.json()
                    except (json.JSONDecodeError, AttributeError):
                        rust_ma_body = python_ma_body = {}
                    for runtime, body in (
                        ("Rust", rust_ma_body),
                        ("Python", python_ma_body),
                    ):
                        data = body.get("data", {}) if isinstance(body, dict) else {}
                        sectors = data.get("sectors", []) if isinstance(data, dict) else []
                        cap_sector = next(
                            (
                                sector
                                for sector in sectors
                                if isinstance(sector, dict)
                                and sector.get("sector_name") == "CapProbe"
                            ),
                            None,
                        )
                        stocks = cap_sector.get("top_stocks") if cap_sector else None
                        if (
                            cap_sector is None
                            or cap_sector.get("total_stocks") != len(cap_symbols)
                            or not isinstance(stocks, list)
                            or len(stocks) != 50
                            or len({stock.get("symbol") for stock in stocks if isinstance(stock, dict)})
                            != 50
                        ):
                            failures.append(
                                f"ma-scores-top-per-sector-usize-maximum-cap: {runtime} did not "
                                "return exactly 50 distinct rows from the 110-member CapProbe sector"
                            )

                # Exercise the Rust route's legacy database-failure behavior
                # only after every other API check, and only against this
                # run's disposable PostgreSQL and SQLite fixtures.
                database_failure_name = "ticker-database-query-error-empty-success"
                _run_postgres_sql(postgres_handle, "DROP TABLE ohlcv CASCADE")
                with app.state.repo.connect() as connection:
                    connection.execute("DROP TABLE candles")
                live_redis_snapshot_row = None
                if use_live_redis:
                    import redis

                    live_redis_snapshot_row = {
                        "time": FIXTURE_END.date().isoformat(),
                        "open": 776.0,
                        "high": 778.0,
                        "low": 775.0,
                        "close": 777.0,
                        "volume": 777_000,
                        "symbol": "FPT",
                    }
                    for redis_url in (rust_redis_url, python_redis_url):
                        redis_client = redis.Redis.from_url(redis_url, decode_responses=True)
                        try:
                            # Keep one valid response snapshot, while forcing
                            # the second ticker through the disposable SQL
                            # failure. Remove only these run-owned fixture keys.
                            redis_client.hset(
                                "snap:vn:FPT:1D",
                                "1:sma",
                                json.dumps([live_redis_snapshot_row], allow_nan=False),
                            )
                            redis_client.delete(
                                "snap:vn:VCB:1D",
                                "ohlcv:vn:FPT:1D",
                                "ohlcv:vn:VCB:1D",
                            )
                        finally:
                            redis_client.close()
                database_failure_headers = {
                    "origin": WEB_ORIGIN,
                    "accept-encoding": "identity",
                }
                for mode, expected_tag in (("vn", "database"), ("all", "empty")):
                    database_failure_path = (
                        f"/tickers?symbol=FPT&limit=1&mode={mode}&ma=false&format=json"
                        "&redis=false&snap=false&cache=false"
                    )
                    rust_database_failure = rust_client.get(
                        database_failure_path, headers=database_failure_headers
                    )
                    python_database_failure = python_client.get(
                        database_failure_path, headers=database_failure_headers
                    )
                    case_name = f"{database_failure_name}-{mode}"
                    differences = _differences(
                        _snapshot(rust_database_failure), _snapshot(python_database_failure)
                    )
                    if differences:
                        failures.append(f"{case_name}: " + "; ".join(differences))
                    for runtime, response in (
                        ("Rust", rust_database_failure),
                        ("Python", python_database_failure),
                    ):
                        try:
                            payload = response.json()
                        except ValueError:
                            payload = None
                        if (
                            response.status_code != 200
                            or payload != {}
                            or response.headers.get("x-data-source") != expected_tag
                        ):
                            failures.append(
                                f"{case_name}: {runtime} returned status "
                                f"{response.status_code}, body {payload!r}, and "
                                f"x-data-source={response.headers.get('x-data-source')!r}; "
                                f"expected an empty HTTP 200 {expected_tag} response"
                            )
                if use_live_redis:
                    snapshot_failure_path = (
                        "/tickers?symbol=FPT&symbol=VCB&interval=1D&limit=1&mode=vn"
                        "&ma=true&ema=false&format=json&redis=true&snap=true&cache=false"
                    )
                    rust_snapshot_failure = rust_client.get(
                        snapshot_failure_path, headers=database_failure_headers
                    )
                    python_snapshot_failure = python_client.get(
                        snapshot_failure_path, headers=database_failure_headers
                    )
                    snapshot_failure_case = "ticker-database-error-preserves-valid-snapshot-hit"
                    differences = _differences(
                        _snapshot(rust_snapshot_failure), _snapshot(python_snapshot_failure)
                    )
                    if differences:
                        failures.append(
                            f"{snapshot_failure_case}: " + "; ".join(differences)
                        )
                    for runtime, response in (
                        ("Rust", rust_snapshot_failure),
                        ("Python", python_snapshot_failure),
                    ):
                        try:
                            payload = response.json()
                        except ValueError:
                            payload = None
                        expected_rows = {"FPT": [live_redis_snapshot_row]}
                        if (
                            response.status_code != 200
                            or payload != expected_rows
                            or response.headers.get("x-data-source") != "redis-snap"
                        ):
                            failures.append(
                                f"{snapshot_failure_case}: {runtime} returned status "
                                f"{response.status_code}, body {payload!r}, and "
                                f"x-data-source={response.headers.get('x-data-source')!r}; "
                                "expected the valid FPT snapshot hit with a redis-snap tag"
                            )

            if any("header x-data-source" in failure for failure in failures):
                failures.append(
                    "x-data-source mismatch: verify the public data-source tag mapping "
                    "and cache-hit behavior for this endpoint."
                )

            assert not failures, (
                "Rust/Python endpoint parity differences on the same temporary fixture:\n"
                + _format_parity_failures(failures)
            )
        finally:
            try:
                if rust_client is not None:
                    rust_client.close()
            finally:
                try:
                    if rust_process is not None and rust_process.poll() is None:
                        _stop_child_process(rust_process)
                finally:
                    try:
                        if redis_process is not None and redis_process.poll() is None:
                            _stop_child_process(redis_process)
                    finally:
                        _stop_postgres(root, postgres_handle)


@pytest.mark.skipif(
    os.environ.get("AIPA_RUN_RUST_DIFFERENTIAL") != "1"
    and os.environ.get("AIPA_REQUIRE_RUST_DIFFERENTIAL") != "1",
    reason="set AIPA_RUN_RUST_DIFFERENTIAL=1 to run isolated Rust/Postgres HTTP differential checks",
)
def test_rust_python_refresh_secret_empty_and_unset_match():
    if (
        os.environ.get("AIPA_REQUIRE_RUST_DIFFERENTIAL") == "1"
        and os.environ.get("AIPA_RUN_RUST_DIFFERENTIAL") != "1"
    ):
        pytest.fail(
            "AIPA_REQUIRE_RUST_DIFFERENTIAL=1 requires AIPA_RUN_RUST_DIFFERENTIAL=1",
            pytrace=False,
        )
    _build_current_rust_binary()
    provider = _postgres_provider()
    if provider is None:
        _skip_or_fail_required_differential(_postgres_skip_reason())

    with tempfile.TemporaryDirectory(prefix="aipa-refresh-secret-diff-") as temp_name:
        root = Path(temp_name)
        catalog = _write_shared_catalog(root)
        rows = _candle_rows()
        seed_file = root / "seed.sql"
        _write_postgres_seed(seed_file, rows)
        postgres_handle = None
        rust_clients = []
        rust_processes = []
        try:
            postgres_handle = _start_postgres(root, provider)
            _apply_rust_migrations(root, postgres_handle.database_url)
            _seed_postgres(postgres_handle, seed_file)

            for label, secret, body, expected_status in (
                ("empty-configured", "", {"interval": "1D", "mode": "vn", "key": ""}, 200),
                ("empty-configured-missing-key", "", {"interval": "1D", "mode": "vn"}, 401),
                ("unset", None, {"interval": "1D", "mode": "vn", "key": ""}, 403),
            ):
                rust_root = root / f"rust-{label}"
                rust_root.mkdir()
                (rust_root / "catalog").symlink_to(catalog, target_is_directory=True)
                rust_client, rust_process = _start_rust(
                    rust_root,
                    postgres_handle.database_url,
                    refresh_secret=secret,
                )
                rust_clients.append(rust_client)
                rust_processes.append(rust_process)

                python_settings = replace(
                    Settings(),
                    database=root / f"python-{label}.sqlite3",
                    cache_dir=root / f"python-{label}-cache",
                    object_dir=root / f"python-{label}-objects",
                    uploads_dir=root / f"python-{label}-uploads",
                    archive_backend="filesystem",
                    catalog_dir=catalog,
                    company_info=catalog / "company_info.json",
                    refresh_secret=secret,
                    cors_origins=(WEB_ORIGIN, PRODUCTION_WEB_ORIGIN),
                )
                python_app = create_app(python_settings)
                with TestClient(python_app) as python_client:
                    _seed_catalog_symbols(python_app.state.repo)
                    python_app.state.repo.put(rows)
                    rust_response = rust_client.post(
                        "/tickers/refresh",
                        json=body,
                        headers={"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                    )
                    python_response = python_client.post(
                        "/tickers/refresh",
                        json=body,
                        headers={"origin": WEB_ORIGIN, "accept-encoding": "identity"},
                    )
                    differences = _differences(
                        _snapshot(rust_response),
                        _snapshot(python_response),
                    )
                    assert not differences, (
                        f"{label}: Rust/Python refresh responses differ: "
                        + "; ".join(differences)
                    )
                    assert rust_response.status_code == expected_status, (
                        f"{label}: expected HTTP {expected_status}, "
                        f"got {rust_response.status_code}: {rust_response.text}"
                    )

            sync_id = str(uuid.uuid4())
            for label, token, authorization, expected_status in (
                ("sync-token-unset", None, "Bearer anything", 403),
                ("sync-token-empty", "", "Bearer anything", 403),
                ("sync-token-control-character", "\x1csecret", "Bearer secret", 401),
                ("sync-token-nonascii-header", "Ã©", b"Bearer \xc3\xa9", 401),
            ):
                rust_root = root / f"rust-{label}"
                rust_root.mkdir()
                (rust_root / "catalog").symlink_to(catalog, target_is_directory=True)
                rust_client, rust_process = _start_rust(
                    rust_root,
                    postgres_handle.database_url,
                    refresh_secret=REFRESH_SECRET,
                    sync_token=token,
                )
                rust_clients.append(rust_client)
                rust_processes.append(rust_process)

                python_settings = replace(
                    Settings(),
                    database=root / f"python-{label}.sqlite3",
                    cache_dir=root / f"python-{label}-cache",
                    object_dir=root / f"python-{label}-objects",
                    uploads_dir=root / f"python-{label}-uploads",
                    archive_backend="filesystem",
                    catalog_dir=catalog,
                    company_info=catalog / "company_info.json",
                    sync_tokens=(
                        ()
                        if token in (None, "")
                        else ("\x1csecret",)
                        if token == "\x1csecret"
                        else (token,)
                    ),
                    refresh_secret=REFRESH_SECRET,
                    cors_origins=(WEB_ORIGIN, PRODUCTION_WEB_ORIGIN),
                )
                python_app = create_app(python_settings)
                with TestClient(python_app) as python_client:
                    _seed_catalog_symbols(python_app.state.repo)
                    python_app.state.repo.put(rows)
                    url = f"/sync/{sync_id}?secret={SYNC_SECRET}"
                    if isinstance(authorization, bytes):
                        headers = httpx.Headers(
                            [
                                (b"origin", WEB_ORIGIN.encode()),
                                (b"authorization", authorization),
                            ]
                        )
                    else:
                        headers = {"origin": WEB_ORIGIN, "authorization": authorization}
                    rust_response = rust_client.get(url, headers=headers)
                    python_response = python_client.get(url, headers=headers)
                    differences = _differences(
                        _snapshot(rust_response),
                        _snapshot(python_response),
                    )
                    assert not differences, (
                        f"{label}: Rust/Python sync responses differ: "
                        + "; ".join(differences)
                    )
                    assert rust_response.status_code == expected_status, (
                        f"{label}: expected HTTP {expected_status}, "
                        f"got {rust_response.status_code}: {rust_response.text}"
                    )

            # An authenticated sync lookup without its required query parameter
            # exercises the same Axum/FastAPI extraction error shape.
            missing_secret_root = root / "rust-sync-missing-secret"
            missing_secret_root.mkdir()
            (missing_secret_root / "catalog").symlink_to(catalog, target_is_directory=True)
            rust_client, rust_process = _start_rust(
                missing_secret_root,
                postgres_handle.database_url,
            )
            rust_clients.append(rust_client)
            rust_processes.append(rust_process)
            missing_secret_settings = replace(
                Settings(),
                database=root / "python-sync-missing-secret.sqlite3",
                cache_dir=root / "python-sync-missing-secret-cache",
                object_dir=root / "python-sync-missing-secret-objects",
                uploads_dir=root / "python-sync-missing-secret-uploads",
                archive_backend="filesystem",
                catalog_dir=catalog,
                company_info=catalog / "company_info.json",
                sync_tokens=(SYNC_TOKEN,),
                cors_origins=(WEB_ORIGIN, PRODUCTION_WEB_ORIGIN),
            )
            missing_secret_app = create_app(missing_secret_settings)
            with TestClient(missing_secret_app) as python_client:
                _seed_catalog_symbols(missing_secret_app.state.repo)
                missing_secret_response = python_client.get(
                    f"/sync/{sync_id}",
                    headers={"origin": WEB_ORIGIN, "authorization": f"Bearer {SYNC_TOKEN}"},
                )
                rust_response = rust_client.get(
                    f"/sync/{sync_id}",
                    headers={"origin": WEB_ORIGIN, "authorization": f"Bearer {SYNC_TOKEN}"},
                )
                differences = _differences(
                    _snapshot(rust_response),
                    _snapshot(missing_secret_response),
                )
                assert not differences, (
                    "sync-read-missing-required-secret: Rust/Python responses differ: "
                    + "; ".join(differences)
                )
        finally:
            for client in rust_clients:
                client.close()
            for process in rust_processes:
                if process.poll() is None:
                    _stop_child_process(process)
            _stop_postgres(root, postgres_handle)


@pytest.mark.skipif(
    os.environ.get("AIPA_RUN_RUST_DIFFERENTIAL") != "1"
    and os.environ.get("AIPA_REQUIRE_RUST_DIFFERENTIAL") != "1",
    reason="set AIPA_RUN_RUST_DIFFERENTIAL=1 to run isolated Rust/Postgres differential checks",
)
def test_rust_python_empty_uploads_path_matches():
    if (
        os.environ.get("AIPA_REQUIRE_RUST_DIFFERENTIAL") == "1"
        and os.environ.get("AIPA_RUN_RUST_DIFFERENTIAL") != "1"
    ):
        pytest.fail(
            "AIPA_REQUIRE_RUST_DIFFERENTIAL=1 requires AIPA_RUN_RUST_DIFFERENTIAL=1",
            pytrace=False,
        )
    _build_current_rust_binary()
    provider = _postgres_provider()
    if provider is None:
        _skip_or_fail_required_differential(_postgres_skip_reason())

    with tempfile.TemporaryDirectory(prefix="aipa-empty-upload-path-diff-") as temp_name:
        root = Path(temp_name)
        catalog = _write_shared_catalog(root)
        seed_file = root / "seed.sql"
        _write_postgres_seed(seed_file, _candle_rows())
        postgres_handle = None
        rust_client = None
        rust_process = None
        try:
            postgres_handle = _start_postgres(root, provider)
            _apply_rust_migrations(root, postgres_handle.database_url)
            _seed_postgres(postgres_handle, seed_file)

            rust_root = root / "rust-empty-upload-path"
            rust_root.mkdir()
            (rust_root / "catalog").symlink_to(catalog, target_is_directory=True)
            rust_client, rust_process = _start_rust(
                rust_root,
                postgres_handle.database_url,
                uploads_path="",
            )

            python_app = create_app(
                replace(
                    Settings(),
                    database=root / "python-empty-upload-path.sqlite3",
                    cache_dir=root / "python-empty-upload-path-cache",
                    object_dir=root / "python-empty-upload-path-objects",
                    uploads_dir="",
                    archive_backend="filesystem",
                    catalog_dir=catalog,
                    company_info=catalog / "company_info.json",
                    cors_origins=(WEB_ORIGIN, PRODUCTION_WEB_ORIGIN),
                )
            )
            session_id = str(uuid.uuid4())
            url = f"/upload/markdown?session_id={session_id}&secret={SYNC_SECRET}"
            with TestClient(python_app) as python_client:
                python_response = python_client.post(
                    url,
                    files={"file": ("content.md", b"fixture", "text/markdown")},
                    headers={"origin": WEB_ORIGIN},
                )
            rust_response = rust_client.post(
                url,
                files={"file": ("content.md", b"fixture", "text/markdown")},
                headers={"origin": WEB_ORIGIN},
            )
            differences = _differences(_snapshot(rust_response), _snapshot(python_response))
            assert not differences, (
                "empty-UPLOADS_PATH: Rust/Python upload responses differ: "
                + "; ".join(differences)
            )
            assert rust_response.status_code == 500
        finally:
            if rust_client is not None:
                rust_client.close()
            if rust_process is not None and rust_process.poll() is None:
                _stop_child_process(rust_process)
            _stop_postgres(root, postgres_handle)


def test_loopback_bind_conflict_detection_covers_service_errors():
    assert _is_loopback_port_bind_conflict("Address already in use (os error 48)")
    assert _is_loopback_port_bind_conflict("Bind for 127.0.0.1:5432 failed: port is already allocated")
    assert not _is_loopback_port_bind_conflict("database authentication failed")


def test_postgres_provider_selection_prefers_host_and_requires_docker_opt_in():
    host_tools = {
        "postgres": "/pg/bin/postgres",
        "initdb": "/pg/bin/initdb",
        "pg_ctl": "/pg/bin/pg_ctl",
        "psql": "/pg/bin/psql",
    }
    host = _select_postgres_provider(
        {"AIPA_USE_DOCKER_POSTGRES": "1"}, host_tools, "/usr/bin/docker"
    )
    assert host is not None and host.kind == "host"

    incomplete_tools = {**host_tools, "postgres": None}
    assert _select_postgres_provider({}, incomplete_tools, "/usr/bin/docker") is None

    docker = _select_postgres_provider(
        {"AIPA_USE_DOCKER_POSTGRES": "1"},
        incomplete_tools,
        "/usr/bin/docker",
        "colima",
    )
    assert docker == PostgresProvider(
        kind="docker", docker_path="/usr/bin/docker", docker_context="colima"
    )


def test_docker_fallback_requires_verified_local_context():
    incomplete_tools = {
        "postgres": None,
        "initdb": "/pg/bin/initdb",
        "pg_ctl": "/pg/bin/pg_ctl",
        "psql": "/pg/bin/psql",
    }
    assert (
        _select_postgres_provider(
            {"AIPA_USE_DOCKER_POSTGRES": "1"}, incomplete_tools, "/usr/bin/docker"
        )
        is None
    )
    assert _is_local_docker_endpoint("unix:///var/run/docker.sock")
    assert not _is_local_docker_endpoint("ssh://user@remote.example")
    assert not _is_local_docker_endpoint("tcp://remote.example:2376")


def test_docker_postgres_commands_use_loopback_tmpfs_and_forced_cleanup():
    env_file = Path("/temporary/aipa-postgres.env")
    run_command = _docker_postgres_run_command(
        "/usr/bin/docker", "aipa-differential-pg-unique", 54321, env_file
    )

    assert run_command[:2] == ["/usr/bin/docker", "run"]
    assert "--pull=never" in run_command
    assert run_command[run_command.index("--publish") + 1] == "127.0.0.1:54321:5432"
    assert run_command[run_command.index("--tmpfs") + 1] == (
        f"{DOCKER_POSTGRES_DATA}:rw,noexec,nosuid,size=1g"
    )
    assert run_command[-1] == DOCKER_POSTGRES_IMAGE
    assert not any(
        item in ("-v", "--volume", "--mount") or item.startswith("--volume=")
        for item in run_command
    )
    assert _docker_postgres_remove_command("/usr/bin/docker", "aipa-differential-pg-unique") == [
        "/usr/bin/docker",
        "rm",
        "-f",
        "-v",
        "aipa-differential-pg-unique",
    ]


def test_docker_postgres_mount_policy_rejects_any_non_tmpfs_mount():
    _assert_docker_postgres_tmpfs_only(
        json.dumps({DOCKER_POSTGRES_DATA: "rw,noexec,nosuid,size=1g"}),
        "[]",
    )
    _assert_docker_postgres_tmpfs_only(
        json.dumps({DOCKER_POSTGRES_DATA: "rw,noexec,nosuid,size=1g"}),
        json.dumps([{"Type": "tmpfs", "Destination": DOCKER_POSTGRES_DATA}]),
    )
    with pytest.raises(RuntimeError, match="no persistent mounts"):
        _assert_docker_postgres_tmpfs_only(
            json.dumps({DOCKER_POSTGRES_DATA: "rw,noexec,nosuid,size=1g"}),
            json.dumps(
                [
                    {"Type": "volume", "Destination": "/var/lib/postgresql/data"},
                ]
            ),
        )
    with pytest.raises(RuntimeError, match="PGDATA tmpfs"):
        _assert_docker_postgres_tmpfs_only("{}", "[]")


def test_health_readiness_waits_for_populated_fixture_snapshot_without_hiding_counts():
    empty_snapshot = {
        "total_tickers_count": FIXTURE_TICKER_COUNT,
        "active_tickers_count": 0,
        "daily_records_count": 0,
        "hourly_records_count": 0,
        "minute_records_count": 0,
        "daily_last_sync": None,
    }
    assert not _health_fixture_ready(empty_snapshot)

    populated_snapshot = {
        "total_tickers_count": FIXTURE_TICKER_COUNT,
        "active_tickers_count": 7,
        # Readiness does not normalize or replace approximate Rust statistics;
        # the differential comparator still reports these exact values.
        "daily_records_count": len(TICKERS) * DAILY_BARS - 1,
        "hourly_records_count": 24 + (len(TICKERS) - 1) * 21,
        "minute_records_count": 30,
        "daily_last_sync": (FIXTURE_END + timedelta(days=1)).isoformat(),
    }
    assert _health_fixture_ready(populated_snapshot)

    populated_snapshot["daily_last_sync"] = FIXTURE_END.isoformat()
    assert not _health_fixture_ready(populated_snapshot)

    populated_snapshot["daily_last_sync"] = (FIXTURE_END + timedelta(days=1)).isoformat()
    del populated_snapshot["daily_last_sync"]
    assert not _health_fixture_ready(populated_snapshot)


def test_differential_comparator_reports_header_body_and_null_shape_differences():
    rust = Snapshot(
        200, {"content-type": "application/json", "x-data-source": "postgres"}, b"{}", {}
    )
    python = Snapshot(
        200,
        {"content-type": "application/json", "x-data-source": "sqlite"},
        b'{"x":null}',
        {"x": None},
    )

    differences = _differences(rust, python)

    assert any("header x-data-source" in item for item in differences)
    assert any("missing from Rust" in item for item in differences)


def test_differential_comparator_rejects_large_volatile_timestamp_drift():
    rust = Snapshot(
        200,
        {"content-type": "application/json"},
        b"{}",
        {
            "created_at": "2026-10-21T03:00:00Z",
            "updated_at": "2026-10-21T03:00:00Z",
        },
    )
    python = Snapshot(
        200,
        {"content-type": "application/json"},
        b"{}",
        {
            "created_at": "2026-10-21T03:00:10Z",
            "updated_at": "2026-10-21T03:00:10Z",
        },
    )

    differences = _differences(rust, python, volatile_json_fields=("created_at", "updated_at"))

    assert any("maximum 5.000s" in item for item in differences)


def test_differential_comparator_rejects_missing_volatile_timestamps():
    rust = {
        "created_at": "2026-10-21T03:00:00Z",
        "updated_at": "2026-10-21T03:00:00Z",
    }
    python = {}

    differences = _volatile_timestamp_differences(rust, python, ("created_at", "updated_at"))

    assert "Python created_at is missing" in differences
    assert "Python updated_at is missing" in differences


def test_health_clock_policy_rejects_large_current_time_drift():
    rust = {
        "current_system_time": "2026-10-21T03:00:00Z",
        "uptime_secs": 10,
        "total_tickers_count": 8,
    }
    python = {
        "current_system_time": "2026-10-21T03:00:10Z",
        "uptime_secs": 10,
        "total_tickers_count": 8,
    }

    differences = _health_clock_differences(rust, python)

    assert any("maximum 5.000s" in item for item in differences)


def test_differential_json_comparator_distinguishes_scalar_kinds():
    assert _json_differences({"value": 1}, {"value": 1.0}) == [
        "$.value: Rust=1 (int) Python=1.0 (float)"
    ]
    assert _json_differences({"value": True}, {"value": 1}) == [
        "$.value: Rust=True (bool) Python=1 (int)"
    ]


def test_differential_json_comparator_detects_nested_object_key_order():
    assert _json_differences(
        {"outer": {"first": 1, "second": 2}},
        {"outer": {"second": 2, "first": 1}},
    ) == ["$.outer: object key order Rust=['first', 'second'] Python=['second', 'first']"]


def test_differential_snapshot_preserves_raw_json_object_key_order():
    rust = _snapshot(
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"interval":"1D","sources":["vn"],"updated":1}',
        )
    )
    python = _snapshot(
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"updated":1,"interval":"1D","sources":["vn"]}',
        )
    )

    differences = _differences(rust, python)
    assert any("object key order Rust=" in item for item in differences)
    assert any("JSON body wire bytes differ" in item for item in differences)


def test_differential_json_comparator_detects_wire_whitespace():
    rust = _snapshot(
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"value":1}',
        )
    )
    python = _snapshot(
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{ "value" : 1 }',
        )
    )

    differences = _differences(rust, python)

    assert any("JSON body wire bytes differ" in item for item in differences)


def test_differential_json_comparator_normalizes_only_volatile_wire_values():
    rust = _snapshot(
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=(
                b'{"created_at":"2026-10-21T03:00:00Z",'
                b'"updated_at":"2026-10-21T03:00:00Z","value":1}'
            ),
        )
    )
    python = _snapshot(
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=(
                b'{"created_at":"2026-10-21T03:00:01Z",'
                b'"updated_at":"2026-10-21T03:00:01Z","value":1}'
            ),
        )
    )

    assert not _differences(
        rust,
        python,
        volatile_json_fields=("created_at", "updated_at"),
    )

    python = _snapshot(
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=(
                b'{ "created_at":"2026-10-21T03:00:01Z",'
                b'"updated_at":"2026-10-21T03:00:01Z","value":1}'
            ),
        )
    )
    differences = _differences(
        rust,
        python,
        volatile_json_fields=("created_at", "updated_at"),
    )
    assert any("JSON body wire bytes differ" in item for item in differences)


def test_differential_json_comparator_does_not_normalize_duplicate_volatile_keys():
    rust = _snapshot(
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=(
                b'{"created_at":"old-rust","created_at":"2026-10-21T03:00:00Z",'
                b'"updated_at":"2026-10-21T03:00:00Z"}'
            ),
        )
    )
    python = _snapshot(
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=(
                b'{"created_at":"old-python","created_at":"2026-10-21T03:00:00Z",'
                b'"updated_at":"2026-10-21T03:00:00Z"}'
            ),
        )
    )

    differences = _differences(
        rust,
        python,
        volatile_json_fields=("created_at", "updated_at"),
    )

    assert any("JSON body wire bytes differ" in item for item in differences)


def test_health_comparator_does_not_hide_content_length_for_malformed_json():
    rust = _snapshot(
        httpx.Response(
            200,
            headers={"content-type": "application/json", "content-length": "8"},
            content=b"not-json",
        )
    )
    python = _snapshot(
        httpx.Response(
            200,
            headers={"content-type": "application/json", "content-length": "999"},
            content=b"not-json",
        )
    )

    differences = _differences(rust, python, health_clock_policy=True)

    assert any("header content-length" in item for item in differences)
    assert any("Content-Length='999' does not match" in item for item in differences)


def test_differential_comparator_checks_identity_content_length_against_body():
    response = _snapshot(
        httpx.Response(
            200,
            headers={"content-type": "application/json", "content-length": "999"},
            content=b"{}",
        )
    )

    differences = _differences(response, response)

    assert any("Content-Length='999' does not match identity body length=2" in item for item in differences)


def test_differential_comparator_normalizes_content_length_only_for_valid_volatile_json():
    rust_body = (
        b'{"created_at":"2026-10-21T03:00:00Z",'
        b'"updated_at":"2026-10-21T03:00:00Z"}'
    )
    python_body = (
        b'{"created_at":"2026-10-21T03:00:01.000000Z",'
        b'"updated_at":"2026-10-21T03:00:01.000000Z"}'
    )
    rust = _snapshot(
        httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "content-length": str(len(rust_body)),
            },
            content=rust_body,
        )
    )
    python = _snapshot(
        httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "content-length": str(len(python_body)),
            },
            content=python_body,
        )
    )

    assert not _differences(
        rust,
        python,
        volatile_json_fields=("created_at", "updated_at"),
    )


def test_differential_comparator_preserves_volatile_content_length_presence_parity():
    body = b'{"created_at":"2026-10-21T03:00:00Z"}'
    rust_response = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        content=body,
    )
    rust_response.headers.pop("content-length", None)
    rust = _snapshot(rust_response)
    python = _snapshot(
        httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "content-length": str(len(body)),
            },
            content=body,
        )
    )

    differences = _differences(
        rust,
        python,
        volatile_json_fields=("created_at",),
    )

    assert any("header content-length" in item for item in differences)


def test_differential_comparator_checks_encoded_content_length_presence_only():
    rust_response = httpx.Response(200, headers={"content-type": "application/json"}, content=b"{}")
    rust_response.headers["content-encoding"] = "gzip"
    rust_response.headers.pop("content-length", None)
    rust = _snapshot(rust_response)
    python_response = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        content=b"{}",
    )
    python_response.headers["content-encoding"] = "gzip"
    python_response.headers["content-length"] = "37"
    python = _snapshot(python_response)

    differences = _differences(rust, python)
    assert any("header content-length" in item for item in differences)

    rust_response = httpx.Response(200, content=b"{}")
    rust_response.headers["content-encoding"] = "gzip"
    rust_response.headers["content-length"] = "37"
    rust = _snapshot(rust_response)
    python_response = httpx.Response(200, content=b"{}")
    python_response.headers["content-encoding"] = "gzip"
    python_response.headers["content-length"] = "41"
    python = _snapshot(python_response)
    assert not _differences(rust, python)


@pytest.mark.parametrize("content_length", ["+37", "3_7"])
def test_differential_comparator_rejects_non_decimal_encoded_content_length(content_length):
    rust_response = httpx.Response(200, content=b"{}")
    rust_response.headers["content-encoding"] = "gzip"
    rust_response.headers["content-length"] = content_length
    rust = _snapshot(rust_response)
    python_response = httpx.Response(200, content=b"{}")
    python_response.headers["content-encoding"] = "gzip"
    python_response.headers["content-length"] = "37"
    python = _snapshot(python_response)

    differences = _differences(rust, python)

    assert any(
        f"Content-Length={content_length!r} must contain only decimal digits" in item
        for item in differences
    )


@pytest.mark.parametrize("content_length", ["+2", "2_0"])
def test_differential_comparator_rejects_non_decimal_content_length(content_length):
    response = _snapshot(
        httpx.Response(
            200,
            headers={"content-type": "application/json", "content-length": content_length},
            content=b"{}",
        )
    )

    differences = _differences(response, response)
    assert any(
        f"Content-Length={content_length!r} must contain only decimal digits" in item
        for item in differences
    )


def test_differential_comparator_preserves_head_health_content_length_presence():
    rust = _snapshot(
        httpx.Response(200, headers={"content-type": "application/json"}, content=b""),
        request_method="HEAD",
    )
    python = _snapshot(
        httpx.Response(
            200,
            headers={"content-type": "application/json", "content-length": "100"},
            content=b"",
        ),
        request_method="HEAD",
    )

    differences = _differences(
        rust,
        python,
        health_clock_policy=True,
    )

    assert any("header content-length" in item for item in differences)


def test_health_clock_policy_keeps_stable_schema_and_fields_in_comparison():
    rust = {
        "current_system_time": "2026-10-21T03:00:00+00:00",
        "uptime_secs": 10,
        "total_tickers_count": 8,
        "memory_usage_mb": 0.0,
    }
    python = {
        "current_system_time": "2026-10-21T03:00:01Z",
        "uptime_secs": 11,
        "total_tickers_count": 8,
        "memory_usage_mb": 0.0,
    }
    assert not _health_clock_differences(rust, python)

    python["memory_usage_mb"] = 1.0
    differences = _health_clock_differences(rust, python)
    assert any("memory_usage_mb" in item for item in differences)

    python["memory_usage_mb"] = 0.0
    python["uptime_secs"] = 14
    differences = _health_clock_differences(rust, python)
    assert any("uptime_secs exceeds documented tolerance" in item for item in differences)

    del python["total_tickers_count"]
    differences = _health_clock_differences(rust, python)
    assert any("total_tickers_count: missing from Python" in item for item in differences)


def test_differential_fixture_exercises_jdk_min_volume_threshold():
    rows = _candle_rows()
    latest_vn = {
        row.symbol: row
        for row in rows
        if row.source == "vn" and row.interval == "1D" and row.time == int(FIXTURE_END.timestamp())
    }

    assert latest_vn["DUPL"].volume < 1_000
    assert latest_vn["FPT"].volume >= 1_000
    assert latest_vn["VNINDEX"].volume >= 1_000


def test_profile_boundary_fixture_makes_clamps_observable():
    rows = [
        row
        for row in _candle_rows()
        if row.source == "crypto" and row.symbol == "PROFILEUSDT" and row.interval == "1m"
    ]
    price_ticks = {
        tick
        for row in rows
        for tick in range(
            math.floor(row.low / 0.0001 + 0.5),
            math.floor(row.high / 0.0001 + 0.5) + 1,
        )
    }
    low_clamp = _expected_volume_profile(
        rows, source="crypto", symbol="PROFILEUSDT", bins=0, value_area_pct=0
    )
    exact_low = _expected_volume_profile(
        rows, source="crypto", symbol="PROFILEUSDT", bins=2, value_area_pct=60
    )
    high_clamp = _expected_volume_profile(
        rows, source="crypto", symbol="PROFILEUSDT", bins=201, value_area_pct=100
    )
    exact_high = _expected_volume_profile(
        rows, source="crypto", symbol="PROFILEUSDT", bins=200, value_area_pct=90
    )

    assert len(price_ticks) > 200
    assert len(low_clamp["profile"]) == 2
    assert low_clamp == exact_low
    assert len(high_clamp["profile"]) == 200
    assert high_clamp == exact_high
    assert low_clamp["value_area"] != high_clamp["value_area"]


def test_python_jdk_rrg_applies_web_volume_threshold_on_requested_date():
    with tempfile.TemporaryDirectory(prefix="aipa-python-rrg-threshold-") as temp_name:
        root = Path(temp_name)
        catalog = _write_shared_catalog(root)
        settings = replace(
            Settings(),
            database=root / "python.sqlite3",
            cache_dir=root / "python-cache",
            object_dir=root / "python-objects",
            archive_backend="filesystem",
            catalog_dir=catalog,
            company_info=catalog / "company_info.json",
            max_limit=40,
            single_limit=10000,
        )
        app = create_app(settings)
        with TestClient(app) as client:
            _seed_catalog_symbols(app.state.repo)
            app.state.repo.put(_candle_rows())
            response = client.get(
                "/analysis/rrg?algorithm=jdk&mode=vn&trails=5&min_volume=1000"
                f"&benchmark=VNINDEX&period=10&date={FIXTURE_END.date()}&ema=true"
            )

    assert response.status_code == 200
    tickers = response.json()["data"]["tickers"]
    symbols = {ticker["symbol"] for ticker in tickers}
    assert "DUPL" not in symbols
    assert {"FPT", "VCB"} <= symbols


def test_notes_web_feature_contract_in_temporary_storage():
    """Exercise Notes edge cases independently of the paired HTTP matrix."""
    with tempfile.TemporaryDirectory(prefix="aipa-python-notes-contract-") as temp_name:
        root = Path(temp_name)
        settings = replace(
            Settings(),
            database=root / "db.sqlite3",
            cache_dir=root / "cache",
            object_dir=root / "objects",
            uploads_dir=root / "uploads",
            archive_backend="filesystem",
        )
        app = create_app(settings)
        session_id = str(uuid.uuid4())
        secret = "differential-notes-secret"
        content = b"# Notes contract\n\nTemporary web feature fixture.\n"

        with TestClient(app) as client:
            uploaded = client.post(
                f"/upload/markdown?session_id={session_id}&secret={secret}",
                files={"file": ("content.md", content, "text/markdown")},
            )
            assert uploaded.status_code == 200
            payload = uploaded.json()
            assert payload["success"] is True
            assert payload["files"][0]["url"] == (f"/uploads/{session_id}/markdown/content.md")

            fetched = client.get(payload["files"][0]["url"])
            assert fetched.status_code == 200
            assert fetched.content == content
            assert fetched.headers["cache-control"] == "public, max-age=3600"

            deleted = client.delete(f"/uploads/{session_id}?secret={secret}")
            assert deleted.status_code == 200
            assert deleted.json()["files_deleted"] == {"markdown": 1, "images": 0}
