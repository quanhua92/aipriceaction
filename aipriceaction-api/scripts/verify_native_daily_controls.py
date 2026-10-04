"""Reparse every successful saved native daily control, offline and without copies."""

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, date_bounds
from aipriceaction_api.providers import Providers
from scripts.artifact_budget import ArtifactBudget
from scripts.compare_vn_feeds import FEEDS, FIELDS


def request_identity(url):
    value = urlsplit(str(url))
    if value.fragment or value.username or value.password:
        raise DataError("Unexpected native capture URL credentials/fragment")
    return value.scheme, value.netloc, value.path, parse_qs(value.query, keep_blank_values=True)


async def verify_record(settings, root, symbol, feed, record, start_date, end_date):
    if feed not in Providers.VN or "error" in record or "rows" not in record:
        raise DataError("Expected a successful native daily control")
    if (record["start_date"], record["end_date"]) != (start_date, end_date):
        raise DataError("Native daily control window differs from audit")
    captures = record["captures"]
    if len(captures) != 1 or captures[0]["status"] != 200:
        raise DataError("Expected one successful daily source response")
    capture = captures[0]
    path = Path(capture["path"]).resolve()
    if not path.is_relative_to((root / feed).resolve()):
        raise DataError("Daily capture outside its source directory")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if (
        type(capture["bytes"]) is not int
        or len(raw) != capture["bytes"]
        or digest != capture["sha256"]
    ):
        raise DataError("Native daily capture bytes changed")
    identity = request_identity(capture["url"])
    requests = []

    def respond(request):
        # Compare against the actual runtime-generated request, including the
        # index endpoint, symbol, native resolution, window and countback.
        if request.method != "GET" or request_identity(request.url) != identity:
            raise DataError("Captured daily request differs from runtime source/symbol/window")
        requests.append(str(request.url))
        return httpx.Response(200, content=raw)

    settings = replace(
        settings,
        proxies=(),
        allow_direct=True,
        requests_per_minute=1_000_000,
        vci_history_fallback=False,
        vci_volume_proofs=None,
    )
    providers = Providers(settings, transport=httpx.MockTransport(respond))
    first = date_bounds(start_date)
    before = date_bounds(end_date, end=True) + 1
    count = min(10000, max(100, (before - first) // 86400 + 1))
    try:
        page = await providers.page(
            "vn", symbol, "1D", before, count=count, start=first, provider=feed
        )
    finally:
        await providers.close()
    if len(requests) != 1 or len(page.rows) >= count:
        raise DataError("Ambiguous or potentially truncated native daily replay")
    rows = [{key: asdict(row)[key] for key in ("time", *FIELDS)} for row in page.rows]
    serialized = json.dumps(rows, sort_keys=True, allow_nan=False)
    if serialized != json.dumps(record["rows"], sort_keys=True, allow_nan=False):
        raise DataError("Saved daily OHLCV differs from native runtime replay")
    return {
        "symbol": symbol,
        "feed": feed,
        "capture_sha256": digest,
        "replayed_rows": len(rows),
        "rows_sha256": hashlib.sha256(serialized.encode()).hexdigest(),
        "native_parser_replayed": True,
        "runtime_request_verified": True,
    }


async def run(args):
    root = args.audit.resolve()
    original_raw = (root / "report.json").read_bytes()
    original = json.loads(original_raw)
    symbols = original["symbols"]
    if (
        original["feeds"] != list(FEEDS)
        or original["intervals"] != ["1D"]
        or original["canonical_publication"] is not False
        or len(symbols) != len(set(symbols))
        or not symbols
        or len(symbols) > 100
        or original["requests"] != len(symbols) * len(FEEDS)
    ):
        raise DataError("Expected a bounded daily-only source comparison")
    args.output.mkdir(parents=True, exist_ok=False)
    budget = ArtifactBudget(args.output, 512 * 1024)
    settings = Settings.from_env()
    results, errors, rejected = [], [], []
    for symbol in symbols:
        for feed in Providers.VN:
            path = root / feed / f"{symbol}-1D.json"
            try:
                raw = path.read_bytes()
                record = json.loads(raw)
                if "error" in record and "rows" not in record:
                    rejected.append(
                        {
                            "symbol": symbol,
                            "feed": feed,
                            "original_error": record["error"],
                            "record_sha256": hashlib.sha256(raw).hexdigest(),
                        }
                    )
                    continue
                result = await verify_record(
                    settings,
                    root,
                    symbol,
                    feed,
                    record,
                    original["daily_start"],
                    original["end_date"],
                )
                result.update(record_sha256=hashlib.sha256(raw).hexdigest(), path=str(path))
                results.append(result)
            except (DataError, KeyError, TypeError, ValueError, OSError) as exc:
                errors.append({"symbol": symbol, "feed": feed, "error": str(exc)})
    counts = Counter()
    for result in results:
        counts[result["feed"]] += result["replayed_rows"]
    report = {
        "completed": True,
        "all_successful_controls_verified": not errors,
        "remote_requests": False,
        "canonical_publication": False,
        "diagnostic_only": True,
        "audit_directory": str(root),
        "audit_sha256": hashlib.sha256(original_raw).hexdigest(),
        "verified_controls": len(results),
        "replayed_rows_by_feed": dict(counts),
        "controls": results,
        "original_rejected_controls": rejected,
        "errors": errors,
        "limitations": [
            "Parser replay proves provenance and saved normalization, not market accuracy.",
            "Rejected full responses remain rejected; this does not replay valid subsets.",
            "Single bounded response pages do not establish complete trading coverage.",
            "Legacy is excluded from native witness verification.",
        ],
    }
    budget.write(args.output / "report.json", (json.dumps(report, indent=2) + "\n").encode())
    print(
        json.dumps(
            {key: report[key] for key in ("verified_controls", "replayed_rows_by_feed", "errors")}
        )
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    result = asyncio.run(run(parser.parse_args()))
    if result["errors"]:
        raise SystemExit(1)
