"""Read-only analysis comparison using a check_legacy_contract fixture report."""

import argparse
import json
import math
from dataclasses import replace
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from aipriceaction_api.app import create_app
from aipriceaction_api.config import Settings


def same(a, b, deviations=None, path=""):
    if isinstance(a, dict):
        assert set(a) == set(b), (set(a), set(b))
        for key in a:
            same(a[key], b[key], deviations, path + "/" + key)
    elif isinstance(a, list):
        assert len(a) == len(b)
        for first, second in zip(a, b, strict=True):
            same(first, second, deviations, path)
    elif isinstance(a, (int, float)) and not isinstance(a, bool):
        if not math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-8):
            if deviations is None:
                raise AssertionError((path, a, b))
            deviations.append({"field": path, "legacy": a, "new": b, "absolute_delta": abs(a - b)})
    else:
        assert a == b, (a, b)


def check(path):
    snapshot = json.loads(path.read_text())
    settings = replace(
        Settings(),
        database=Path(snapshot["database"]),
        object_dir=Path(snapshot["objects"]),
        cache_dir=path.parent / "cache",
        archive_backend="filesystem",
    )
    results = []
    with (
        httpx.Client(base_url="https://api.aipriceaction.com", timeout=45) as legacy,
        TestClient(create_app(settings)) as local,
    ):
        # Derive a completed trading date from the actual minute fixture.
        history = local.app.state.repo.read("vn", "FPT", "1m", limit=1)
        from datetime import UTC, datetime

        day = datetime.fromtimestamp(history[-1].time, UTC).strftime("%Y-%m-%d")
        params = {"symbol": "FPT", "date": day, "bins": 50}
        expected = legacy.get("/analysis/volume-profile", params=params)
        expected.raise_for_status()
        actual = local.get("/analysis/volume-profile", params=params)
        same(expected.json(), actual.json())
        results.append({"analysis": "volume_profile", "date": day, "matched": True})
        for algorithm in ("mascore", "jdk"):
            params = {"algorithm": algorithm, "trails": 2, "date": day, "snap": "false"}
            expected = legacy.get("/analysis/rrg", params=params)
            expected.raise_for_status()
            actual = local.get("/analysis/rrg", params=params).json()
            old_rows = {r["symbol"]: r for r in expected.json()["data"]["tickers"]}
            deviations = []
            for row in actual["data"]["tickers"]:
                same(old_rows[row["symbol"]], row, deviations, row["symbol"])
            same(expected.json()["analysis_date"], actual["analysis_date"])
            results.append(
                {
                    "analysis": "rrg",
                    "algorithm": algorithm,
                    "tickers_compared": len(actual["data"]["tickers"]),
                    "matched": not deviations,
                    "numeric_deviations": deviations,
                }
            )
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    check(args.report)
