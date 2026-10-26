import json
import os
import subprocess
import sys

import pytest

from aipriceaction_api.config import ROOT

CONFIG_RENDERER = ROOT / "ops" / "backup" / "render_backup_config.py"
RUN_RECORD_WRITER = ROOT / "ops" / "backup" / "write_run_record.py"


def compose_config(*, endpoint="https://s3.example.test", access="key", secret="secret"):
    return {
        "services": {
            "api": {
                "environment": {
                    "ARCHIVE_BACKEND": "s3",
                    "S3_ENDPOINT": endpoint,
                    "S3_BUCKET": "aipriceaction-prod",
                }
            },
            "backup-restic": {
                "environment": {
                    "AWS_ACCESS_KEY_ID": access,
                    "AWS_SECRET_ACCESS_KEY": secret,
                }
            },
        }
    }


def render_backup_config(tmp_path, config):
    output = tmp_path / "config" / "backup.env"
    result = subprocess.run(
        [sys.executable, str(CONFIG_RENDERER), str(output)],
        input=json.dumps(config),
        capture_output=True,
        text=True,
        check=False,
    )
    return output, result


def test_rendered_config_points_at_same_bucket_prefix_and_contains_no_credentials(tmp_path):
    output, result = render_backup_config(
        tmp_path,
        compose_config(endpoint="https://s3.example.test/", access="access-secret", secret="do-not-store"),
    )

    assert result.returncode == 0, result.stderr
    config = output.read_text()
    assert "RESTIC_REPOSITORY=s3:https://s3.example.test/aipriceaction-prod/backups/aipriceaction-prod/sqlite-restic" in config
    assert "RESTIC_KEEP_DAILY=7" in config
    assert "access-secret" not in config
    assert "do-not-store" not in config
    assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "config",
    [
        compose_config(endpoint="s3.example.test"),
        compose_config(endpoint="http://s3.example.test"),
        compose_config(secret=""),
        {
            "services": {
                "api": {
                    "environment": {
                        "ARCHIVE_BACKEND": "filesystem",
                        "S3_ENDPOINT": "https://s3.example.test",
                        "S3_BUCKET": "aipriceaction-prod",
                    }
                },
                "backup-restic": {
                    "environment": {
                        "AWS_ACCESS_KEY_ID": "key",
                        "AWS_SECRET_ACCESS_KEY": "secret",
                    }
                },
            }
        },
    ],
)
def test_rendered_config_rejects_insecure_or_incomplete_s3_settings(tmp_path, config):
    output, result = render_backup_config(tmp_path, config)

    assert result.returncode != 0
    assert not output.exists() or output.read_text() == ""


@pytest.mark.parametrize(
    ("result", "stage", "exit_code", "expected_failed_stage"),
    [
        ("success", "complete", "0", None),
        ("failed", "snapshot", "1", "snapshot"),
    ],
)
def test_run_record_only_sets_failed_stage_for_failures(
    tmp_path, result, stage, exit_code, expected_failed_stage
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    env = {
        "AIPA_RUN_ID": "backup-test",
        "AIPA_RUN_KIND": "backup",
        "AIPA_RUN_RESULT": result,
        "AIPA_RUN_STAGE": stage,
        "AIPA_RUN_STARTED_AT": "2026-10-26T00:00:00Z",
        "AIPA_RUN_ENDED_AT": "2026-10-26T00:01:00Z",
        "AIPA_RUN_EXIT_CODE": exit_code,
        "AIPA_RUN_WORK_CLEANUP": "removed",
    }
    completed = subprocess.run(
        [sys.executable, str(RUN_RECORD_WRITER), str(run_dir)],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    record = json.loads((run_dir / "run.json").read_text())
    assert record["result"] == result
    assert record["stage"] == stage
    assert record["failed_stage"] == expected_failed_stage
