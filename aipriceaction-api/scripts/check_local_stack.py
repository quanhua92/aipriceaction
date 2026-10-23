#!/usr/bin/env python3
"""Hourly, read-only acceptance check for the local Compose stack."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import tempfile
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

API_BASE = os.getenv("AIPA_MONITOR_API_URL", "http://127.0.0.1:8899").rstrip("/")
API_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("AIPA_MONITOR_DATA_DIR", API_DIR / "data"))
REPORT_PATH = DATA_DIR / "local-stack-hourly-health.json"
MIN_VN_DATE = os.getenv("AIPA_EXPECTED_VN_DATE", "2026-10-23")
MAX_BTC_AGE = timedelta(minutes=int(os.getenv("AIPA_MAX_BTC_AGE_MINUTES", "15")))
S3_PROBE = r'''
import boto3, csv, hashlib, json, os
from botocore.config import Config
from datetime import UTC, datetime, timedelta
s3 = boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT"], region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"), aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"], aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"], config=Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2}, s3={"addressing_style": "path"}))
bucket = os.environ["S3_BUCKET"]
checks = {}
def csv_rows(key):
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8-sig")
    rows = list(csv.reader(body.splitlines()))
    if not rows or any(len(row) != 6 for row in rows):
        raise RuntimeError("empty or invalid six-column OHLCV CSV: " + key)
    return rows
try:
    pointer = json.loads(s3.get_object(Bucket=bucket, Key="parquet/LATEST.json")["Body"].read())
    raw = s3.get_object(Bucket=bucket, Key=pointer["key"])["Body"].read()
    manifest = json.loads(raw)
    objects = manifest.get("objects", [])
    ok = hashlib.sha256(raw).hexdigest() == pointer["checksum"] and bool(objects) and all(obj["object_key"].startswith("parquet/") for obj in objects)
    sample = next((obj for obj in objects if obj.get("symbol") == "ACB"), objects[0] if objects else None)
    if sample:
        ok = ok and s3.head_object(Bucket=bucket, Key=sample["object_key"]).get("ContentLength", 0) > 0
    checks["parquet_s3"] = {"ok": ok, "detail": f"manifest checksum valid; {len(objects)} objects; prefix parquet/; sample present"}
except Exception as exc:
    checks["parquet_s3"] = {"ok": False, "detail": str(exc)}
for name, key, expected in (
    ("csv_vn_daily", "ohlcv/vn/VCB/1D/VCB-1D-2026-10-23.csv", "2026-10-23"),
    ("csv_vn_yearly", "ohlcv/vn/VCB/yearly/VCB-1D-2026.csv", "2026-"),
    ("csv_btc_minute", "ohlcv/crypto/BTCUSDT/1m/BTCUSDT-1m-2026-10-23.csv", "2026-10-23"),
):
    try:
        rows = csv_rows(key)
        last = rows[-1][0]
        ok = rows[0][0].startswith(expected) and last.startswith(expected)
        if name == "csv_btc_minute":
            stamp = datetime.strptime(last, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
            age = datetime.now(UTC) - stamp
            ok = ok and timedelta(seconds=-120) <= age <= timedelta(minutes=15)
        checks[name] = {"ok": ok, "detail": f"{key}: {len(rows)} rows; first {rows[0][0]}; last {last}"}
    except Exception as exc:
        checks[name] = {"ok": False, "detail": str(exc)}
print(json.dumps(checks, separators=(",", ":")))
'''


def request(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return response.read()


def api_csv(path: str, params: dict[str, str]) -> list[dict[str, str]]:
    url = f"{API_BASE}{path}?{urllib.parse.urlencode(params)}"
    text = request(url).decode("utf-8-sig")
    return list(csv.DictReader(text.splitlines()))


def main() -> int:
    now = datetime.now(UTC)
    checks: list[dict[str, object]] = []

    def record(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    try:
        health = json.loads(request(f"{API_BASE}/health"))
        record(
            "api",
            bool(health.get("current_system_time")),
            f"HTTP 200; system time {health.get('current_system_time')}; daily rows {health.get('daily_records_count')}",
        )
    except Exception as exc:
        health = {}
        record("api", False, str(exc))

    try:
        rows = api_csv(
            "/tickers",
            {"symbol": "VCB", "interval": "1D", "limit": "1", "mode": "vn", "ma": "false", "format": "csv"},
        )
        latest = rows[-1]["time"] if rows else "missing"
        record("vn_oct23_api", bool(rows) and latest >= MIN_VN_DATE, f"VCB latest {latest}; required at least {MIN_VN_DATE}")
    except Exception as exc:
        record("vn_oct23_api", False, str(exc))

    try:
        rows = api_csv(
            "/tickers",
            {"symbol": "BTCUSDT", "interval": "1m", "limit": "1", "mode": "crypto", "ma": "false", "format": "csv"},
        )
        stamp = datetime.fromisoformat(rows[-1]["time"]).replace(tzinfo=UTC) if rows else None
        age = now - stamp if stamp else None
        ok = stamp is not None and timedelta(seconds=-120) <= age <= MAX_BTC_AGE
        record("btc_minute_api", ok, f"BTC latest {stamp.isoformat() if stamp else 'missing'}; age {age}; max {MAX_BTC_AGE}")
    except Exception as exc:
        record("btc_minute_api", False, str(exc))

    try:
        rows = api_csv(
            "/tickers",
            {"symbol": "ACB", "interval": "1D", "start_date": "2023-10-20", "end_date": "2023-10-20", "mode": "vn", "ma": "false", "format": "csv"},
        )
        latest = rows[-1]["time"] if rows else "missing"
        record("parquet_api_read", bool(rows) and latest == "2023-10-20", f"ACB historical Parquet read returned {latest}")
    except Exception as exc:
        record("parquet_api_read", False, str(exc))

    try:
        result = subprocess.run(
            [
                "/opt/homebrew/bin/docker",
                "--host",
                "unix:///Users/quan/.colima/default/docker.sock",
                "exec",
                "aipriceaction-api",
                "python",
                "-c",
                S3_PROBE,
            ],
            capture_output=True,
            check=True,
            text=True,
            timeout=60,
        )
        for name, check in json.loads(result.stdout).items():
            record(name, check["ok"], check["detail"])
    except Exception as exc:
        record("s3_objects", False, str(exc))

    result = {
        "checked_at": now.isoformat(),
        "status": "pass" if all(check["ok"] for check in checks) else "fail",
        "checks": checks,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".local-stack-health-", suffix=".json", dir=REPORT_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
            handle.write("\n")
        os.replace(tmp_name, REPORT_PATH)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
