#!/usr/bin/env python3
"""Derive non-secret Restic settings from Docker Compose's resolved config."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlsplit


def main() -> int:
    config = json.load(sys.stdin)
    services = config.get("services", {})
    api = services.get("api", {}).get("environment", {})
    restic = services.get("backup-restic", {}).get("environment", {})
    if api.get("ARCHIVE_BACKEND") != "s3":
        raise SystemExit("Production backup requires ARCHIVE_BACKEND=s3")
    endpoint = str(api.get("S3_ENDPOINT") or "").rstrip("/")
    bucket = str(api.get("S3_BUCKET") or "")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise SystemExit("S3_ENDPOINT must be an HTTPS endpoint without embedded credentials")
    if not bucket or any(char.isspace() for char in bucket):
        raise SystemExit("S3_BUCKET is missing or invalid")
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        if not str(restic.get(key) or "").strip():
            raise SystemExit(f"{key} must be configured for the Restic backup service")

    lines = [
        f"RESTIC_REPOSITORY=s3:{endpoint}/{bucket}/backups/aipriceaction-prod/sqlite-restic",
        "RESTIC_HOST=aipriceaction-prod",
        "RESTIC_TAG=aipriceaction-sqlite",
        "RESTIC_KEEP_DAILY=7",
        "RESTIC_KEEP_WEEKLY=4",
        "RESTIC_KEEP_MONTHLY=12",
    ]
    destination = Path(sys.argv[1])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    destination.chmod(0o600)
    print("Restic settings generated (S3 credentials were not written to this file).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
