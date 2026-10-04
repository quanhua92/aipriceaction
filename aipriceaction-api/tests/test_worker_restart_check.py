import importlib.util
import time
from dataclasses import replace
from pathlib import Path

import pytest

from aipriceaction_api.archive import sha256
from aipriceaction_api.domain import Candle
from aipriceaction_api.storage import Repository

spec = importlib.util.spec_from_file_location(
    "worker_restart_check", Path(__file__).parents[1] / "scripts/check_worker_restart.py"
)
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)
verify = script.verify


@pytest.fixture
def images(tmp_path):
    before, after = tmp_path / "before.sqlite3", tmp_path / "after.sqlite3"
    repo = Repository(before)
    repo.initialize()
    complete = Candle("crypto", "BTCUSDT", "1m", 60, 100, 101, 99, 100, 10, "binance", "native")
    provisional = replace(complete, time=120)
    frozen = Candle("yahoo", "MSFT", "1m", 60, 100, 101, 99, 100, 10, "legacy-api", "capture")
    index = Candle("vn", "VNINDEX", "1h", 0, 100, 101, 99, 100, 10, "vps", "native-index")
    attempt = repo.start_source_check("crypto", "BTCUSDT", "1m")
    repo.put([complete, provisional], verification={"attempt_ns": attempt, "completed_before": 120})
    repo.put([frozen, index])
    checkpoint = time.time_ns()
    repo.backup(after)
    current = Repository(after)
    attempt = current.start_source_check("crypto", "BTCUSDT", "1m")
    current.put(
        [complete, replace(provisional, close=100.5)],
        verification={"attempt_ns": attempt, "completed_before": 120},
    )
    return before, after, checkpoint, current, complete, frozen, index


def test_restart_accepts_provisional_updates_and_new_check_in_progress_without_mutating_images(
    images,
):
    before, after, checkpoint, repo, *_ = images
    repo.start_source_check("crypto", "BTCUSDT", "1m")
    hashes = sha256(before), sha256(after)
    report = verify(before, after, ["crypto"], checkpoint)
    assert report["passed"]
    check = report["sources"]["crypto"]["fresh_minute_checks"][0]
    assert check["outcome"] == "running" and check["successful_at_ns"] > checkpoint
    assert (sha256(before), sha256(after)) == hashes


def test_restart_rejects_changed_previously_completed_value(images):
    before, after, checkpoint, repo, complete, *_ = images
    repo.put([replace(complete, close=100.5)])
    report = verify(before, after, ["crypto"], checkpoint)
    assert not report["passed"]
    assert report["sources"]["crypto"]["formerly_completed_records_changed"] == 1


@pytest.mark.parametrize("protected", ["frozen_candle_versions", "index_candle_versions"])
def test_restart_rejects_frozen_or_index_record_version_changes(images, protected):
    before, after, checkpoint, repo, _, frozen, index = images
    # Identical OHLCV with a rewritten version still violates these boundaries.
    repo.put([frozen if protected == "frozen_candle_versions" else index])
    report = verify(before, after, ["crypto"], checkpoint)
    assert not report["passed"] and not report["protected"][protected]


@pytest.mark.parametrize("field,value", [("successful_at_ns", 1), ("provider", "other")])
def test_restart_rejects_missing_or_wrong_frame_fresh_success(images, field, value):
    before, after, checkpoint, repo, *_ = images
    with repo.connect() as con:
        con.execute(f"UPDATE source_checks SET {field}=? WHERE source='crypto'", (value,))
    report = verify(before, after, ["crypto"], checkpoint)
    assert not report["passed"]
    assert report["sources"]["crypto"]["missing_fresh_minute_checks"] == ["BTCUSDT"]
