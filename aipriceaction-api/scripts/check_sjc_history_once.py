#!/usr/bin/env python3
"""Exercise one real SJC historical quote through the cold-history pipeline.

The upstream quote is real. SQLite, Parquet, manifests, and the S3-compatible
CSV PUT/HEAD verification use temporary local files and an in-memory S3 client;
this script never writes to the configured production database or bucket.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import tempfile
from dataclasses import replace
from datetime import UTC, date, datetime, time
from pathlib import Path

from botocore.exceptions import ClientError

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from aipriceaction_api.archive import Archive, S3Store  # noqa: E402
from aipriceaction_api.config import Settings  # noqa: E402
from aipriceaction_api.domain import Candle, DataError  # noqa: E402
from aipriceaction_api.history_worker import HistoryWorker  # noqa: E402
from aipriceaction_api.providers import Providers  # noqa: E402
from aipriceaction_api.storage import Repository  # noqa: E402


class MemoryS3Store(S3Store):
    """S3Store-shaped isolated object store for exercising production code."""

    def __init__(self, settings):
        super().__init__(settings)
        self.objects = {}

    @property
    def client(self):
        return self

    def put(self, key, path):
        self.objects[key] = {
            "body": Path(path).read_bytes(),
            "content_type": "application/octet-stream",
            "metadata": {},
        }

    def download(self, key, path):
        Path(path).write_bytes(self.objects[key]["body"])

    def read(self, key):
        return self.objects[key]["body"]

    def head_object(self, *, Bucket, Key):
        del Bucket
        stored = self.objects.get(Key)
        if stored is None:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {
            "ContentLength": len(stored["body"]),
            "ContentType": stored["content_type"],
            "Metadata": stored["metadata"],
        }

    def put_object(self, *, Bucket, Key, Body, ContentType, Metadata):
        del Bucket
        self.objects[Key] = {
            "body": bytes(Body),
            "content_type": ContentType,
            "metadata": {key.lower(): value for key, value in Metadata.items()},
        }
        return {}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        default="2013-10-24",
        help="UTC quote date to verify (default: 2013-10-24, near the 10-year target)",
    )
    return parser.parse_args()


async def run(target_day: date):
    settings = Settings.from_env()
    target_start = int(datetime.combine(target_day, time.min, UTC).timestamp())
    target_end = target_start + 86400
    providers = Providers(settings)

    try:
        revision = f"sjc-history-smoke-{target_day.isoformat()}"
        with tempfile.TemporaryDirectory(prefix="aipa-sjc-history-") as temporary:
            root = Path(temporary)
            smoke_settings = replace(
                settings,
                database=root / "sqlite" / "smoke.sqlite3",
                cache_dir=root / "cache",
                object_dir=root / "objects",
                archive_backend="s3",
                public_export_enabled=True,
                s3_bucket="temporary-sjc-history-smoke",
                s3_prefix="parquet",
            )
            repo = Repository(smoke_settings.database)
            repo.initialize()

            # Seed only the temporary series basis. The actual old quote remains
            # outside this SQLite row and must pass through Parquet publication.
            today = datetime.now(UTC).date()
            live_stamp = int(datetime.combine(today, time.min, UTC).timestamp())
            if live_stamp <= target_start:
                live_stamp = target_end + 86400
            seed = Candle(
                "sjc",
                "SJC-GOLD",
                "1D",
                live_stamp,
                100.0,
                101.0,
                99.0,
                100.0,
                1,
                "sjc-quote",
                revision,
            )
            repo.put([seed])

            store = MemoryS3Store(smoke_settings)
            archive = Archive(repo, smoke_settings, store=store)
            worker = HistoryWorker(repo, smoke_settings, providers=providers, archive=archive)
            # The production worker uses 29 quotes per page. This targeted
            # one-day smoke intentionally requests only one quote.
            worker._page_size = lambda _source, _interval: 1

            job = repo.configure_history_job(
                "sjc",
                "SJC-GOLD",
                "1D",
                target_end,
                target_start,
                "sjc-quote",
                revision,
            )
            # Exercise the same fetch, Parquet preparation, manifest indexing,
            # and public daily CSV publication methods used by HistoryWorker.
            await worker._fetch_page(
                job,
                batch_start=target_start,
                batch_end=target_end,
                batch_cursor=target_end,
            )
            job = repo.history_job("sjc", "SJC-GOLD", "1D")
            await worker._publish_parquet_batch(job)
            accepted_rows = await asyncio.to_thread(
                worker._read,
                "sjc",
                "SJC-GOLD",
                "1D",
                target_start,
                target_end - 1,
                revision,
            )
            if len(accepted_rows) != 1:
                raise DataError(
                    f"Expected one archived candle for {target_day.isoformat()}, "
                    f"found {len(accepted_rows)}"
                )
            job = repo.history_job("sjc", "SJC-GOLD", "1D")
            while job["batch_status"] == "csv":
                await worker._publish_next_csv(job)
                job = repo.history_job("sjc", "SJC-GOLD", "1D")

            parquet = repo.archives("sjc", "SJC-GOLD", "1D")
            csv_keys = sorted(key for key in store.objects if key.startswith("ohlcv/sjc/SJC-GOLD/"))
            if len(parquet) != 1 or parquet[0]["row_count"] != 1:
                raise DataError(f"Expected one indexed Parquet row, got {parquet}")
            if len(csv_keys) != 1:
                raise DataError(f"Expected one verified public CSV, got {csv_keys}")
            if job["status"] != "complete":
                raise DataError(f"Temporary history job did not complete: {job}")

            print(
                json.dumps(
                    {
                        "result": "PASS",
                        "mode": "real upstream; all writes isolated in a temporary directory",
                        "upstream_provider": job["provider"],
                        "quote_date": target_day.isoformat(),
                        "sqlite": "temporary live-series basis only",
                        "parquet": {
                            "status": parquet[0]["status"],
                            "rows": parquet[0]["row_count"],
                            "key": parquet[0]["object_key"],
                            "manifest_latest": "written and verified in memory store",
                        },
                        "public_csv": {
                            "status": "PUT + HEAD verified in memory S3 client",
                            "keys": csv_keys,
                            "bytes": len(store.objects[csv_keys[0]]["body"]),
                        },
                        "history_job_status": job["status"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
    finally:
        await providers.close()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )
    args = parse_args()
    try:
        target_day = date.fromisoformat(args.date)
        asyncio.run(run(target_day))
    except Exception as exc:
        print(
            json.dumps(
                {"result": "FAIL", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
