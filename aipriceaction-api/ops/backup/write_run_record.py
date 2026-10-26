#!/usr/bin/env python3
"""Write a small, secret-free JSON summary for one backup operation."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def restic_summary(path: Path) -> dict | None:
    if not path.is_file():
        return None
    result = None
    for line in path.read_text(errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if value.get("message_type") == "summary":
            result = value
    return result


def main() -> int:
    run_dir = Path(sys.argv[1])
    result = os.environ.get("AIPA_RUN_RESULT")
    stage = os.environ.get("AIPA_RUN_STAGE")
    record = {
        "run_id": os.environ.get("AIPA_RUN_ID"),
        "kind": os.environ.get("AIPA_RUN_KIND"),
        "result": result,
        "stage": stage,
        "failed_stage": stage if result == "failed" else None,
        "started_at_utc": os.environ.get("AIPA_RUN_STARTED_AT"),
        "ended_at_utc": os.environ.get("AIPA_RUN_ENDED_AT"),
        "exit_code": int(os.environ.get("AIPA_RUN_EXIT_CODE", "1")),
        "temporary_database_cleanup": os.environ.get("AIPA_RUN_WORK_CLEANUP"),
        "source_database_bytes": int(os.environ["AIPA_RUN_SOURCE_DB_BYTES"])
        if os.environ.get("AIPA_RUN_SOURCE_DB_BYTES", "").isdigit()
        else None,
        "snapshot_bytes": int(os.environ["AIPA_RUN_SNAPSHOT_BYTES"])
        if os.environ.get("AIPA_RUN_SNAPSHOT_BYTES", "").isdigit()
        else None,
        "repository_prefix": "backups/aipriceaction-prod/sqlite-restic",
        "restic_backup": restic_summary(run_dir / "restic-backup.jsonl"),
        "restic_retention": restic_summary(run_dir / "restic-retention.jsonl"),
    }
    target = run_dir / "run.json"
    temporary = run_dir / "run.json.tmp"
    temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    temporary.chmod(0o600)
    temporary.replace(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
