"""Verify and repair one public Yahoo yearly CSV without changing SQLite candles.

The complete Yahoo response must contain every existing CSV date and agree
with all available canonical candles. The original object is saved under
the configured Parquet evidence prefix before any conditional replacement.
"""

import argparse
import asyncio
import csv
import hashlib
import io
import json
import math
import time
import uuid
from datetime import UTC, datetime
from urllib.parse import quote

from botocore.exceptions import ClientError

from .archive import Archive, S3Store
from .config import Settings
from .domain import DataError
from .history import History
from .providers import Providers
from .public_csv import _csv_timestamps, plan_candles, publish_object, yearly_key
from .revision_relabel import _matches
from .storage import Repository


def _read(client, bucket, key):
    response = client.get_object(Bucket=bucket, Key=key)
    stream = response["Body"]
    try:
        return stream.read(), response["ETag"]
    finally:
        stream.close()


def _put_immutable(client, bucket, key, body, content_type):
    try:
        client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type, IfNoneMatch="*")
    except ClientError as exc:
        if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
            raise
    actual, _ = _read(client, bucket, key)
    if actual != body:
        raise DataError(f"Repair evidence object does not match its content: {key}")


async def repair(repo, archive, providers, symbol, year, *, apply=False):
    if not isinstance(archive.store, S3Store):
        raise DataError("This repair requires the configured S3 archive")
    if not 1970 <= year <= datetime.now(UTC).year:
        raise DataError("Invalid Yahoo repair year")
    source, iv = "yahoo", "1D"
    state = repo.state(source, symbol, iv)
    if not state or state["status"] != "ready" or state["provider"] != "yahoo":
        raise DataError("The Yahoo daily series must be ready on the Yahoo provider")
    start = int(datetime(year, 1, 1, tzinfo=UTC).timestamp())
    end = int(datetime(year + 1, 1, 1, tzinfo=UTC).timestamp())
    page = await providers.page(source, symbol, iv, before=end, count=500, provider="yahoo", start=start)
    if page.provider != "yahoo" or not page.rows:
        raise DataError("Yahoo did not provide the requested year")
    observed = {row.time: row for row in page.rows}
    canonical = History(repo, archive, archive.settings).read(
        source, symbol, iv, start=start, end=end - 1, forward=True
    )
    if len(canonical) < 3 or any(
        row.provider != "yahoo" or row.revision != state["revision"]
        or row.time not in observed or not _matches(row, observed[row.time])
        for row in canonical
    ):
        raise DataError("Yahoo does not match every available canonical candle")
    item = plan_candles(page.rows, source, symbol, iv, year=year)
    key = yearly_key(source, symbol, iv, year)
    client, bucket = archive.store.client, archive.settings.s3_bucket
    original, etag = _read(client, bucket, key)
    old_dates = _csv_timestamps(original, key)
    new_dates = _csv_timestamps(item.body, key)
    if not old_dates or old_dates - new_dates:
        raise DataError("Yahoo response does not contain every existing public CSV date")
    changed_existing = 0
    try:
        for record in csv.reader(io.StringIO(original.decode("utf-8"))):
            stamp = datetime.fromisoformat(record[0])
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            row = observed[int(stamp.timestamp())]
            if (
                any(not math.isclose(float(record[index]), getattr(row, field), rel_tol=1e-6, abs_tol=1e-9)
                    for index, field in enumerate(("open", "high", "low", "close"), 1))
                or int(record[5]) != row.volume
            ):
                changed_existing += 1
    except (UnicodeError, ValueError, OverflowError, KeyError, IndexError) as exc:
        raise DataError("Existing public CSV cannot be compared safely") from exc
    old_hash = hashlib.sha256(original).hexdigest()
    canonical_body = json.dumps(
        [row.record() for row in canonical], sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    prefix = f"{archive.settings.s3_prefix}/repair-evidence/yahoo/{quote(symbol, safe='')}/1D/{year}"
    backup_key = f"{prefix}-{old_hash}.csv"
    evidence = {
        "kind": "yahoo_year_repair_v1", "symbol": symbol, "year": year, "key": key,
        "expected_etag": etag, "original_sha256": old_hash, "replacement_sha256": item.sha256,
        "original_backup_key": backup_key, "original_rows": len(old_dates), "provider_rows": len(new_dates),
        "changed_existing_rows": changed_existing,
        "canonical_rows_checked": len(canonical), "canonical_sha256": hashlib.sha256(canonical_body).hexdigest(),
        "revision": state["revision"], "price_relative_tolerance": 1e-6, "volume_exact": True,
        "verified_at_ns": time.time_ns(),
    }
    if not apply:
        return {"status": "verified_dry_run", "evidence": evidence}

    owner = uuid.uuid4().hex
    if not repo.live_claim(source, symbol, iv, owner, lease=300):
        raise DataError("The series is being updated; retry later")
    try:
        with archive.public_csv_lock(key):
            if repo.state(source, symbol, iv) != state:
                raise DataError("The series changed during verification; retry later")
            current = History(repo, archive, archive.settings).read(
                source, symbol, iv, start=start, end=end - 1, forward=True
            )
            current_body = json.dumps(
                [row.record() for row in current], sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
            if hashlib.sha256(current_body).hexdigest() != evidence["canonical_sha256"]:
                raise DataError("Canonical candles changed during verification; retry later")
            _put_immutable(client, bucket, backup_key, original, "text/csv")
            seed = json.dumps(evidence, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            evidence_key = f"{prefix}-{old_hash}-{item.sha256}-{hashlib.sha256(seed).hexdigest()}.json"
            evidence["evidence_key"] = evidence_key
            proof = json.dumps(evidence, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            _put_immutable(client, bucket, evidence_key, proof, "application/json")
            result = publish_object(client, bucket, item, expected_etag=etag)
        repo.finding(
            source, symbol, iv, "public_csv_year_verified_repair",
            json.dumps(evidence, sort_keys=True, separators=(",", ":")), resolved=True,
        )
        return {"status": "applied", "evidence": evidence, "uploaded": result["uploaded"]}
    finally:
        repo.live_release(source, symbol, iv, owner)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol")
    parser.add_argument("year", type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    repo = Repository(settings.database)
    repo.initialize()
    archive = Archive(repo, settings)
    providers = Providers(settings)

    async def run():
        try:
            return await repair(repo, archive, providers, args.symbol, args.year, apply=args.apply)
        finally:
            await providers.close()

    print(json.dumps(asyncio.run(run()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
