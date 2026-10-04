"""Replay rejected VCI captures with candidate proofs; no requests or activation."""

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx

from aipriceaction_api.config import Settings
from aipriceaction_api.domain import DataError, date_bounds
from aipriceaction_api.providers import Providers
from scripts.artifact_budget import ArtifactBudget


async def replay_record(settings, symbol, record, *, retain_last_page=False, retain_rows=False):
    first = date_bounds(record["start_date"])
    before = date_bounds(record["end_date"], end=True) + 1
    pages = record["window"]["pages"]
    captures = record["captures"]
    if not captures or len(captures) != len(pages) + int("error" in record):
        raise DataError(
            "Expected recorded successful pages and optionally one rejected VCI capture"
        )
    responses, cursors = {}, []
    for index, capture in enumerate(captures):
        cursor = (
            pages[index]["before"]
            if index < len(pages)
            else pages[-1]["cursor"]
            if pages
            else before
        )
        raw = Path(capture["path"]).read_bytes()
        if (
            capture["status"] != 200
            or len(raw) != capture["bytes"]
            or hashlib.sha256(raw).hexdigest() != capture["sha256"]
        ):
            raise DataError("VCI replay capture identity changed")
        if cursor in responses:
            raise DataError("Duplicate VCI replay request cursor")
        if index and cursor != pages[index - 1]["cursor"]:
            raise DataError("VCI replay request skips the preceding page cursor")
        responses[cursor] = raw
        cursors.append(cursor)

    def respond(request):
        body = json.loads(request.content)
        assert request.method == "POST" and body["symbols"] == [symbol]
        assert body["countBack"] == 10000 and body["timeFrame"] == "ONE_MINUTE"
        return httpx.Response(200, content=responses[body["to"] + 1])

    providers = Providers(
        replace(settings, proxies=(), allow_direct=True, requests_per_minute=1_000_000),
        transport=httpx.MockTransport(respond),
    )
    result = {
        "symbol": symbol,
        "original_error": record.get("error"),
        "pages": [],
        "captured_pages_passed": False,
        "requested_year_proven": False,
    }
    rows = {}

    def finish_rows():
        result["accepted_rows"] = len(rows)
        result["rows_by_date"] = dict(
            sorted(
                Counter(
                    datetime.fromtimestamp(stamp, UTC).strftime("%Y-%m-%d") for stamp in rows
                ).items()
            )
        )
        if retain_rows:
            result["rows"] = list(rows.values())

    try:
        for index, cursor in enumerate(cursors):
            try:
                page = await providers.page(
                    "vn", symbol, "1m", cursor, count=10000, start=first, provider="vci"
                )
                if page.cursor is not None and page.cursor >= cursor:
                    raise DataError("VCI replay cursor did not move backwards")
                if index < len(pages) and (
                    page.cursor != pages[index]["cursor"] or len(page.rows) != pages[index]["rows"]
                ):
                    raise DataError("VCI replay changed a previously observed cursor or row count")
                result["pages"].append(
                    {
                        "before": cursor,
                        "rows": len(page.rows),
                        "cursor": page.cursor,
                        "applied_proofs": len(page.volume_proofs),
                    }
                )
                for row in page.rows:
                    if not first <= row.time < cursor:
                        raise DataError("VCI replay row outside request window")
                    if row.time in rows:
                        raise DataError("VCI replay repeated a timestamp across pages")
                    rows[row.time] = row
            except DataError as exc:
                result["error"] = str(exc)
                result["next_cursor"] = cursor
                finish_rows()
                return result
        result["captured_pages_passed"] = True
        result["next_cursor"] = result["pages"][-1]["cursor"]
        finish_rows()
        if retain_last_page:
            result["last_page"] = page
        return result
    finally:
        await providers.close()


async def run(args):
    summary = json.loads((args.audit / "summary.json").read_text())
    if not summary["completed"]:
        raise DataError("Finish source collection before proof replay")
    settings = replace(
        Settings.from_env(), vci_history_fallback=True, vci_volume_proofs=args.proofs
    )
    args.output.mkdir(parents=True, exist_ok=False)
    budget = ArtifactBudget(args.output, 8 * 1024 * 1024)
    result = {
        "canonical_publication": False,
        "remote_requests": False,
        "active_catalog_changed": False,
        "proofs_sha256": hashlib.sha256(args.proofs.read_bytes()).hexdigest(),
        "series": [],
    }
    for batch in summary["batches"]:
        for symbol in batch["symbols"]:
            record = json.loads((Path(batch["path"]) / "vci" / f"{symbol}-1m.json").read_text())
            if "error" not in record:
                continue
            result["series"].append(await replay_record(settings, symbol, record))
            budget.write(
                args.output / "report.json", (json.dumps(result, indent=2) + "\n").encode()
            )
    result["passed_series"] = sum(row["captured_pages_passed"] for row in result["series"])
    result["blocked_series"] = len(result["series"]) - result["passed_series"]
    budget.write(args.output / "report.json", (json.dumps(result, indent=2) + "\n").encode())
    print(
        json.dumps(
            {key: result[key] for key in ("passed_series", "blocked_series", "remote_requests")}
        ),
        flush=True,
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--proofs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
