import json
import subprocess
import sys

import pytest

from aipriceaction_api.config import ROOT

CONFIG_RENDERER = ROOT / "ops" / "backup" / "render_backup_config.py"


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
