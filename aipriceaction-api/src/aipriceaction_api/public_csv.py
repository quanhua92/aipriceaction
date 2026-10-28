"""Publish the Rust-compatible CSV view alongside the Parquet archive.

The bucket root remains the terminal/SDK contract. Internal Parquet objects
and manifests use the configured ``S3_PREFIX`` (``parquet`` by default).
"""

import csv
import hashlib
import io
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from .domain import DataError

PUBLIC_CSV_CACHE_CONTROL = "no-store, no-cache, max-age=0, must-revalidate"


@dataclass(frozen=True)
class PublicObject:
    key: str
    body: bytes
    content_type: str
    sha256: str
    content_hash: str
    rows: int

    def report(self):
        return {
            "key": self.key,
            "content_type": self.content_type,
            "sha256": self.sha256,
            "content_hash": self.content_hash,
            "bytes": len(self.body),
            "rows": self.rows,
        }


def daily_key(source, symbol, interval, day):
    return f"ohlcv/{source}/{symbol}/{interval}/{symbol}-{interval}-{day.isoformat()}.csv"


def yearly_key(source, symbol, interval, year):
    if interval not in ("1D", "1h"):
        raise DataError("The public archive has yearly files only for 1D and 1h", 400)
    return f"ohlcv/{source}/{symbol}/yearly/{symbol}-{interval}-{year}.csv"


def _format_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise DataError("Public CSV prices must be finite")
    # Rust f64::to_string() emits the shortest round-trippable decimal in
    # fixed notation. Python repr() may use exponent notation; Decimal expands
    # that same shortest representation without rounding it.
    rendered = format(Decimal(repr(number)), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def rust_content_hash(rows):
    """Reproduce Rust's OHLCV fingerprint used by S3 content-hash metadata."""
    if not rows:
        raise DataError("Refusing to fingerprint an empty public CSV object", 404)
    latest = datetime.fromtimestamp(rows[-1].time, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    scaled_close_sum = sum(
        math.floor(abs(float(row.close) * 10_000) + 0.5)
        * (1 if row.close >= 0 else -1)
        for row in rows
    )
    fingerprint = f"{len(rows)}:{latest}:{scaled_close_sum}:{sum(row.volume for row in rows)}"
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def csv_body(rows, source, symbol, interval):
    """Serialize the exact six-column, headerless format read by the SDK."""
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    previous = None
    count = 0
    for row in rows:
        if (
            row.source != source
            or row.symbol != symbol
            or row.interval != interval
            or previous is not None
            and row.time <= previous
            or row.volume < 0
        ):
            raise DataError("Public CSV rows must be ordered, unique, and match their key")
        stamp = datetime.fromtimestamp(row.time, UTC).strftime("%Y-%m-%d %H:%M:%S")
        writer.writerow(
            (
                stamp,
                _format_float(row.open),
                _format_float(row.high),
                _format_float(row.low),
                _format_float(row.close),
                str(row.volume),
            )
        )
        previous, count = row.time, count + 1
    if count == 0:
        raise DataError("Refusing to publish an empty public CSV object", 404)
    return output.getvalue().encode("utf-8"), count


def plan_candles(rows, source, symbol, interval, *, day=None, year=None):
    if (day is None) == (year is None):
        raise DataError("Choose exactly one public CSV day or year", 400)
    rows = tuple(rows)
    body, count = csv_body(rows, source, symbol, interval)
    first = datetime.fromtimestamp(rows[0].time, UTC)
    last = datetime.fromtimestamp(rows[-1].time, UTC)
    if day is not None and (first.date() != day or last.date() != day):
        raise DataError("Public daily CSV rows must match the key's UTC date", 400)
    if year is not None and (first.year != year or last.year != year):
        raise DataError("Public yearly CSV rows must match the key's UTC year", 400)
    key = (
        daily_key(source, symbol, interval, day)
        if day is not None
        else yearly_key(source, symbol, interval, year)
    )
    return PublicObject(
        key,
        body,
        "text/csv",
        hashlib.sha256(body).hexdigest(),
        rust_content_hash(rows),
        count,
    )


def catalog_records(catalog, repo):
    """Build the legacy metadata array with the Rust enrichment field names."""
    identities = {
        (row["source"], row["symbol"]): row
        for row in repo.tickers()
    }
    for source, groups in catalog.groups_by_source.items():
        for symbols in groups.values():
            for symbol in symbols:
                identities.setdefault((source, symbol), {"source": source, "symbol": symbol})

    group_by_identity = {}
    for source, groups in catalog.groups_by_source.items():
        for group, symbols in sorted(groups.items()):
            if group == "ALL_STOCKS":
                continue
            for symbol in symbols:
                group_by_identity[(source, symbol)] = group

    categories = {}
    for source, filename in (("yahoo", "global_tickers.json"), ("sjc", "sjc_tickers.json")):
        for row in catalog.load(filename).get("data", []):
            if row.get("category"):
                categories[(source, row["symbol"])] = row["category"]

    records = []
    for (source, symbol), base in sorted(identities.items()):
        vn_info = catalog.vn_info.get(symbol, {}) if source == "vn" else {}
        category = categories.get((source, symbol))
        name = base.get("name") or catalog.names_by_source.get(source, {}).get(symbol)
        if source == "vn" and not name:
            name = vn_info.get("organ_name")
        group = group_by_identity.get((source, symbol))
        if source in ("yahoo", "sjc"):
            group = group or category
        records.append(
            {
                "source": source,
                "ticker": symbol,
                "name": name,
                "exchange": vn_info.get("exchange"),
                "type": vn_info.get("type"),
                "category": category,
                "group": group,
            }
        )
    return records


def plan_catalog(catalog, repo):
    records = catalog_records(catalog, repo)
    body = json.dumps(
        records, ensure_ascii=False, indent=2, allow_nan=False
    ).encode("utf-8")
    return PublicObject(
        "meta/tickers.json",
        body,
        "application/json",
        hashlib.sha256(body).hexdigest(),
        hashlib.sha256(body).hexdigest(),
        len(records),
    )


def _not_found(exc):
    response = getattr(exc, "response", {})
    error = response.get("Error", {}) if isinstance(response, dict) else {}
    return error.get("Code") in {"404", "NoSuchKey", "NotFound"}


def _csv_timestamps(body, key):
    """Read timestamps from a Rust-format CSV before replacing an object."""
    try:
        rows = csv.reader(io.StringIO(body.decode("utf-8"), newline=""))
        timestamps = set()
        for row in rows:
            if len(row) != 6:
                raise ValueError("expected six columns")
            stamp = datetime.fromisoformat(row[0])
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            stamp = stamp.astimezone(UTC)
            if stamp in timestamps:
                raise ValueError("duplicate timestamp")
            timestamps.add(stamp)
        return timestamps
    except (UnicodeError, ValueError, csv.Error) as exc:
        raise DataError(f"Cannot safely replace existing public CSV {key}: invalid rows") from exc


def _csv_values(body, key):
    _csv_timestamps(body, key)
    values = {}
    try:
        for row in csv.reader(io.StringIO(body.decode("utf-8"), newline="")):
            stamp = datetime.fromisoformat(row[0])
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            stamp = stamp.astimezone(UTC)
            prices = tuple(float(value) for value in row[1:5])
            volume = int(row[5])
            if not all(math.isfinite(value) for value in prices) or volume < 0:
                raise ValueError("non-finite prices or negative volume")
            values[stamp] = (*prices, volume)
    except (UnicodeError, ValueError, OverflowError, csv.Error) as exc:
        raise DataError(f"Cannot safely compare existing public CSV {key}: invalid rows") from exc
    return values


def publish_object(client, bucket, item, *, expected_etag=None):
    """Idempotently PUT one object, then verify its SDK-visible hash metadata."""
    from botocore.exceptions import ClientError

    try:
        head = client.head_object(Bucket=bucket, Key=item.key)
    except ClientError as exc:
        if not _not_found(exc):
            raise
        head = None

    if expected_etag is not None and (head is None or head.get("ETag") != expected_etag):
        raise DataError(f"Public object changed since it was verified: {item.key}")

    if item.content_type == "text/csv":
        return _publish_csv(client, bucket, item, head)

    if head is not None:
        metadata = {key.lower(): value for key, value in head.get("Metadata", {}).items()}
        if (
            metadata.get("content-hash") == item.content_hash
            and head.get("ContentLength") == len(item.body)
            and head.get("ContentType") == item.content_type
        ):
            return item.report() | {"uploaded": False, "verified": True}

    client.put_object(
        Bucket=bucket,
        Key=item.key,
        Body=item.body,
        ContentType=item.content_type,
        Metadata={"content-hash": item.content_hash},
    )
    verified = client.head_object(Bucket=bucket, Key=item.key)
    metadata = {key.lower(): value for key, value in verified.get("Metadata", {}).items()}
    if (
        metadata.get("content-hash") != item.content_hash
        or verified.get("ContentLength") != len(item.body)
        or verified.get("ContentType") != item.content_type
    ):
        raise DataError(f"Public CSV object failed post-upload verification: {item.key}")
    return item.report() | {"uploaded": True, "verified": True}


def _publish_csv(client, bucket, item, head):
    """Publish a complete CSV with an exact fingerprint and no CDN caching.

    The Rust fingerprint is kept for older SDKs, but it only covers the close
    and volume sums. It cannot establish that the other fields are unchanged.
    Existing objects are upgraded in place when their bytes already match.
    Conditional requests protect other writers between the read and update.
    """
    metadata = {k.lower(): v for k, v in head.get("Metadata", {}).items()} if head else {}
    etag = head.get("ETag") if head else None
    if head and not etag:
        raise DataError(f"Cannot safely update public CSV without an ETag: {item.key}")

    exact_match = bool(
        head
        and metadata.get("sha256") == item.sha256
        and head.get("ContentLength") == len(item.body)
        and head.get("ContentType") == item.content_type
    )
    fully_current = (
        exact_match
        and metadata.get("content-hash") == item.content_hash
        and head.get("CacheControl") == PUBLIC_CSV_CACHE_CONTROL
    )
    if fully_current:
        return item.report() | {"uploaded": False, "verified": True}

    if head and not exact_match:
        response = client.get_object(Bucket=bucket, Key=item.key, IfMatch=etag)
        stream = response["Body"]
        try:
            existing_body = stream.read()
        finally:
            stream.close()
        existing = _csv_timestamps(existing_body, item.key)
        desired = _csv_timestamps(item.body, item.key)
        missing = existing - desired
        if missing:
            if desired <= existing:
                old_values = _csv_values(existing_body, item.key)
                new_values = _csv_values(item.body, item.key)
                if all(
                    old_values[stamp][-1] == new_values[stamp][-1]
                    and all(math.isclose(a, b, rel_tol=1e-6, abs_tol=0.0)
                            for a, b in zip(old_values[stamp][:-1], new_values[stamp][:-1], strict=True))
                    for stamp in desired
                ):
                    # The complete public file already contains every known
                    # canonical candle. Keep its extra dates and upgrade only
                    # its metadata; never replace it with the smaller subset.
                    fingerprint_rows = [
                        SimpleNamespace(time=int(stamp.timestamp()), close=values[3], volume=values[4])
                        for stamp, values in sorted(old_values.items())
                    ]
                    complete = PublicObject(
                        item.key, existing_body, item.content_type,
                        hashlib.sha256(existing_body).hexdigest(),
                        rust_content_hash(fingerprint_rows), len(existing),
                    )
                    preserved = _publish_csv(client, bucket, complete, head)
                    return item.report() | {
                        "uploaded": preserved["uploaded"], "verified": True,
                        "preserved_extra_rows": len(missing)
                    }
            earliest = min(missing).strftime("%Y-%m-%d %H:%M:%S UTC")
            raise DataError(
                f"Refusing to replace public CSV {item.key}: "
                f"{len(missing)} existing timestamp(s) are missing, starting at {earliest}"
            )
        exact_match = existing_body == item.body and head.get("ContentType") == item.content_type

    updated_metadata = metadata | {"content-hash": item.content_hash, "sha256": item.sha256}
    if exact_match:
        # Metadata-only replacement preserves Rust's original bytes and any
        # unrelated object metadata. Both conditions guard against a race.
        request = {
            "Bucket": bucket,
            "Key": item.key,
            "CopySource": {"Bucket": bucket, "Key": item.key},
            "CopySourceIfMatch": etag,
            "IfMatch": etag,
            "MetadataDirective": "REPLACE",
            "Metadata": updated_metadata,
            "ContentType": item.content_type,
            "CacheControl": PUBLIC_CSV_CACHE_CONTROL,
        }
        for name in ("ContentEncoding", "ContentLanguage", "ContentDisposition", "Expires", "WebsiteRedirectLocation"):
            if name in head:
                request[name] = head[name]
        client.copy_object(**request)
        uploaded = False
    else:
        request = {
            "Bucket": bucket,
            "Key": item.key,
            "Body": item.body,
            "ContentType": item.content_type,
            "Metadata": updated_metadata,
            "CacheControl": PUBLIC_CSV_CACHE_CONTROL,
        }
        request["IfMatch" if head else "IfNoneMatch"] = etag if head else "*"
        client.put_object(**request)
        uploaded = True

    verified = client.head_object(Bucket=bucket, Key=item.key)
    verified_metadata = {k.lower(): v for k, v in verified.get("Metadata", {}).items()}
    if (
        verified_metadata.get("content-hash") != item.content_hash
        or verified_metadata.get("sha256") != item.sha256
        or verified.get("ContentLength") != len(item.body)
        or verified.get("ContentType") != item.content_type
        or verified.get("CacheControl") != PUBLIC_CSV_CACHE_CONTROL
    ):
        raise DataError(f"Public CSV object failed post-upload verification: {item.key}")
    return item.report() | {"uploaded": uploaded, "verified": True}


def period_bounds(*, day=None, year=None):
    """Return UTC inclusive epoch bounds for one legacy S3 partition."""
    if (day is None) == (year is None):
        raise DataError("Choose exactly one day or year", 400)
    if day is not None:
        start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
        end = start + timedelta(days=1)
    else:
        if not 1970 <= year <= datetime.now(UTC).year:
            raise DataError("Year must be between 1970 and the current UTC year", 400)
        start = datetime(year, 1, 1, tzinfo=UTC)
        end = datetime(year + 1, 1, 1, tzinfo=UTC)
    return int(start.timestamp()), int(end.timestamp()) - 1
