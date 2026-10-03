"""Bounded, resumable migration of the existing public CSV archive.

Yearly files contain daily/hourly bars; minute files are per UTC day. Cached
downloads are frozen for each explicitly named snapshot revision. No legacy
object, metadata, or production database is changed.
"""

import asyncio
import hashlib
import json
import os
import time
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import httpx

from .domain import SOURCES, DataError, parse_time
from .importing import csv_rows, json_rows


def digest(value):
    return hashlib.sha256(value).hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_bytes(data)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def legacy_files(source, symbol, iv, years=None, start=None, end=None):
    """An explicit bounded list of (period, key, first timestamp, last timestamp)."""
    if source not in SOURCES or iv not in ("1D", "1h", "1m"):
        raise DataError("Legacy migration requires a valid source and native interval", 400)
    if not symbol or any(ord(c) < 32 for c in symbol):
        raise DataError("Invalid symbol", 400)
    safe = quote(symbol, safe="")
    prefix = f"ohlcv/{source}/{safe}"
    files = []
    if iv == "1m":
        if years or not start or not end:
            raise DataError(
                "Minute migration requires --start-date and --end-date, not --years", 400
            )
        try:
            first, last = date.fromisoformat(start), date.fromisoformat(end)
        except ValueError as exc:
            raise DataError("Migration dates require YYYY-MM-DD", 400) from exc
        if not 0 <= (last - first).days < 366:
            raise DataError("Minute migration must cover 1 to 366 days; split longer ranges", 400)
        day = first
        while day <= last:
            stamp = parse_time(day.isoformat())
            files.append(
                (day.strftime("%Y-%m"), f"{prefix}/1m/{safe}-1m-{day}.csv", stamp, stamp + 86399)
            )
            day += timedelta(days=1)
    else:
        if not years or start or end:
            raise DataError("Daily/hourly migration requires explicit --years", 400)
        years = sorted(set(years))
        if len(years) > 30 or any(y < 1970 or y > datetime.now(UTC).year for y in years):
            raise DataError("Supply at most 30 years between 1970 and the current UTC year", 400)
        for year in years:
            files.append(
                (
                    str(year),
                    f"{prefix}/yearly/{safe}-{iv}-{year}.csv",
                    parse_time(f"{year}-01-01"),
                    parse_time(f"{year + 1}-01-01") - 1,
                )
            )
    return files


def api_batches(files, days):
    """Coalesce adjacent minute dates within each receipt month; never cross bounds."""
    batches = []
    for period, key, first, last in files:
        if (
            batches
            and batches[-1][0] == period
            and batches[-1][3] + 1 == first
            and (last - batches[-1][2] + 1) <= days * 86400
        ):
            prev = batches[-1]
            batches[-1] = (period, prev[1], prev[2], last)
        else:
            batches.append((period, key, first, last))
    return batches


class LegacyImporter:
    def __init__(self, repo, archive, cache_dir=None, client=None):
        self.repo, self.archive = repo, archive
        self.cache_dir = Path(cache_dir or repo.path.parent / "legacy-downloads")
        self.client = client
        self.max_file_bytes = 64 * 1024 * 1024

    async def fetch(
        self,
        client,
        url,
        source,
        symbol,
        iv,
        provider,
        revision,
        start,
        end,
        from_api=False,
        api_format="csv",
    ):
        # Content-addressed payload plus an atomically published receipt: interruption
        # can leave an unreferenced file, never a partially verified cache entry.
        key = digest(encode([url, provider, revision]))
        receipt_path = self.cache_dir / f"{key}.json"
        if receipt_path.exists():
            try:
                receipt = json.loads(receipt_path.read_bytes())
                checksum = receipt["checksum"]
                if len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
                    raise ValueError("Invalid checksum")
                raw = (
                    self.cache_dir / f"{checksum}.{api_format if from_api else 'csv'}"
                ).read_bytes()
                if digest(raw) != checksum or receipt["url"] != url:
                    raise ValueError("Cache checksum mismatch")
            except (KeyError, ValueError, OSError) as exc:
                raise DataError("Legacy download cache is corrupt; preserve it for review") from exc
            cached = True
        else:
            chunks, size = [], 0
            async with client.stream("GET", url) as response:
                if response.status_code in (403, 404):
                    return None, {"url": url, "status": response.status_code}, False
                if response.status_code != 200:
                    raise DataError(f"Legacy download returned HTTP {response.status_code}")
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self.max_file_bytes:
                        raise DataError("Legacy file exceeds migration download budget")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                receipt = {
                    "url": url,
                    "checksum": digest(raw),
                    "captured_at_ns": time.time_ns(),
                    "bytes": size,
                    "etag": response.headers.get("etag"),
                    "content_hash": response.headers.get("x-amz-meta-content-hash"),
                }
            cached = False
        try:
            if from_api and api_format == "json":
                rows = json_rows(raw.decode("utf-8-sig"), source, symbol, iv, provider, revision)
            else:
                rows = csv_rows(
                    raw.decode("utf-8-sig"),
                    source,
                    symbol,
                    iv,
                    provider,
                    revision,
                    allow_empty=from_api,
                )
        except UnicodeError as exc:
            raise DataError("Legacy download is not valid UTF-8") from exc
        if rows and (rows[0].time < start or rows[-1].time > end):
            raise DataError("Legacy timestamps fall outside its dated object key")
        if from_api and len(rows) >= 10_000:
            raise DataError("Legacy API export reached its limit; split into smaller date ranges")
        if from_api and not rows:
            # Empty API output is not proof of a holiday or final absence. Retry
            # it on resume so an incomplete legacy ingestion can later fill it.
            if cached:
                receipt_path.unlink()
                return await self.fetch(
                    client,
                    url,
                    source,
                    symbol,
                    iv,
                    provider,
                    revision,
                    start,
                    end,
                    from_api,
                    api_format,
                )
            return [], receipt, False
        if not cached:
            atomic_write(
                self.cache_dir / f"{receipt['checksum']}.{api_format if from_api else 'csv'}", raw
            )
            atomic_write(receipt_path, encode(receipt))
        rows = [replace(r, updated_at=receipt["captured_at_ns"]) for r in rows]
        return rows, receipt, cached

    async def run(
        self,
        base_url,
        source,
        symbol,
        iv,
        years=None,
        start=None,
        end=None,
        provider="legacy",
        revision="legacy-snapshot",
        recent_floor=None,
        older_only=False,
        dry_run=False,
        from_api=False,
        api_batch_days=1,
        api_format="csv",
        api_read_backend="default",
    ):
        parts = urlsplit(base_url)
        if (
            parts.scheme not in ("https", "http")
            or not parts.netloc
            or parts.query
            or parts.fragment
        ):
            raise DataError("Legacy base URL must be an HTTP(S) URL without query/fragment", 400)
        if parts.username or parts.password:
            raise DataError("Legacy imports use a public URL without credentials", 400)
        if older_only and recent_floor is None:
            raise DataError("--older-only requires --split-retention", 400)
        if api_format not in ("csv", "json") or api_format != "csv" and not from_api:
            raise DataError("JSON format applies only to legacy API exports", 400)
        if api_read_backend not in ("default", "database") or (
            api_read_backend != "default" and not from_api
        ):
            raise DataError("API read backend applies only to legacy API exports", 400)
        if from_api:
            state = self.repo.state(source, symbol, iv)
            same_snapshot = state and state["revision"] == revision
            same_snapshot = same_snapshot or any(
                obj["revision"] == revision for obj in self.repo.archives(source, symbol, iv)
            )
            if same_snapshot:
                with self.repo.connect() as con:
                    previous_results = [
                        json.loads(row[0])
                        for row in con.execute(
                            "SELECT result FROM legacy_imports WHERE source=? AND symbol=? AND interval=?",
                            (source, symbol, iv),
                        )
                    ]
                if any(
                    old.get("api_format")
                    and old.get("revision") in (None, revision)
                    and (old.get("api_read_backend") or "default") != api_read_backend
                    for old in previous_results
                ):
                    raise DataError(
                        "Legacy API read backend changed; use a new revision and separate migration database"
                    )
        files = legacy_files(source, symbol, iv, years, start, end)
        if not isinstance(api_batch_days, int) or not 1 <= api_batch_days <= 31:
            raise DataError("API batch days must be between 1 and 31", 400)
        if api_batch_days != 1:
            if not from_api or iv != "1m":
                raise DataError("API batch days apply only to minute API exports", 400)
            files = api_batches(files, api_batch_days)
        if from_api:
            files = [
                (
                    period,
                    "tickers?"
                    + urlencode(
                        {
                            "symbol": symbol,
                            "mode": "yahoo" if source == "sjc" else source,
                            "interval": iv,
                            "start_date": datetime.fromtimestamp(first, UTC).date().isoformat(),
                            "end_date": datetime.fromtimestamp(last, UTC).date().isoformat(),
                            "ma": "false",
                            "format": api_format,
                            "limit": 10_000,
                            "cache": "false",
                            **(
                                {"redis": "false", "snap": "false"}
                                if api_read_backend == "database"
                                else {}
                            ),
                        }
                    ),
                    first,
                    last,
                )
                for period, _, first, last in files
            ]
        report = {
            "dry_run": dry_run,
            "files": len(files),
            "downloaded": 0,
            "cached": 0,
            "unavailable": [],
            "empty_files": [],
            "periods": [],
            "imported": 0,
            "archived": 0,
            "skipped_recent": 0,
        }

        gaps_changed = False

        def unavailable_history(url, first, last, reason):
            nonlocal gaps_changed
            # The dated key declares the attempted historical range. An invalid
            # file cannot prove absence within it. Already verified current-basis
            # objects remain usable; only uncovered older ranges become markers.
            if from_api or iv != "1D" or not older_only or recent_floor is None:
                return
            last = min(last, recent_floor - 1)
            if first > last:
                return
            ranges = [(first, last)]
            state = self.repo.state(source, symbol, iv)
            for obj in self.repo.archives(source, symbol, iv, first, last):
                if (
                    not state
                    or state["status"] != "ready"
                    or obj["status"] != "published"
                    or obj["provider"] != state["provider"]
                    or obj["revision"] != state["revision"]
                ):
                    continue
                uncovered = []
                for begin, finish in ranges:
                    if obj["end"] < begin or obj["start"] > finish:
                        uncovered.append((begin, finish))
                    else:
                        if begin < obj["start"]:
                            uncovered.append((begin, obj["start"] - 1))
                        if finish > obj["end"]:
                            uncovered.append((obj["end"] + 1, finish))
                ranges = uncovered
            for begin, finish in ranges:
                self.repo.record_history_gap(
                    source,
                    symbol,
                    iv,
                    begin,
                    finish,
                    reason,
                    {
                        "kind": "legacy_daily_import",
                        "url": url,
                        "attempted_start": first,
                        "attempted_end": last,
                    },
                )
                gaps_changed = True

        owner = uuid.uuid4().hex
        lease = iv + ":legacy-import"
        if not dry_run and not self.repo.live_claim(source, symbol, lease, owner, lease=3600):
            raise DataError("Another migration for this series is active")
        groups = {}
        for period, key, first, last in files:
            groups.setdefault(period, []).append((key, first, last))
        client = self.client or httpx.AsyncClient(timeout=60, follow_redirects=True)
        try:
            for period, group in groups.items():
                rows, inputs = [], []
                for key, first, last in group:
                    url = base_url.rstrip("/") + "/" + key
                    if dry_run:
                        response = await client.head(url)
                        inputs.append(
                            {
                                "key": key,
                                "status": response.status_code,
                                "bytes": response.headers.get("content-length"),
                            }
                        )
                        continue
                    try:
                        data, receipt, cached = await self.fetch(
                            client,
                            url,
                            source,
                            symbol,
                            iv,
                            provider,
                            revision,
                            first,
                            last,
                            from_api,
                            api_format,
                        )
                    except (DataError, httpx.HTTPError) as exc:
                        unavailable_history(url, first, last, str(exc)[:500])
                        raise
                    if data is None:
                        unavailable = {"key": key, "status": receipt["status"]}
                        report["unavailable"].append(unavailable)
                        unavailable_history(
                            url, first, last, f"Legacy download returned HTTP {receipt['status']}"
                        )
                        inputs.append(unavailable)
                        self.repo.finding(
                            source, symbol, iv, "legacy_unavailable_file", json.dumps(unavailable)
                        )
                    else:
                        if not data:
                            report["empty_files"].append(key)
                        rows.extend(data)
                        inputs.append({"key": key, "checksum": receipt["checksum"]})
                        report["cached" if cached else "downloaded"] += 1
                if dry_run:
                    report["periods"].append({"period": period, "objects": inputs})
                    continue
                checksum = digest(encode(inputs))
                identifier = digest(
                    encode(
                        [
                            base_url,
                            source,
                            symbol,
                            iv,
                            provider,
                            revision,
                            period,
                            recent_floor,
                            older_only,
                        ]
                    )
                )
                with self.repo.connect() as con:
                    previous = con.execute(
                        "SELECT * FROM legacy_imports WHERE id=?", (identifier,)
                    ).fetchone()
                if previous and from_api:
                    prior_result = json.loads(previous["result"])
                    prior_format = prior_result.get("api_format") or "csv"
                    if prior_format != api_format:
                        raise DataError(
                            "Legacy API format changed; use a new revision and separate migration database"
                        )
                    if (prior_result.get("api_read_backend") or "default") != api_read_backend:
                        raise DataError(
                            "Legacy API read backend changed; use a new revision and separate migration database"
                        )
                if previous and previous["input_checksum"] == checksum:
                    report["periods"].append(
                        {
                            "period": period,
                            "resumed": True,
                            "previous": json.loads(previous["result"]),
                        }
                    )
                    continue
                if not rows:
                    report["periods"].append({"period": period, "rows": 0, "complete": False})
                    continue
                older = [r for r in rows if recent_floor is not None and r.time < recent_floor]
                recent = [r for r in rows if recent_floor is None or r.time >= recent_floor]
                # Refuse a provider/revision collision before any archive upload.
                state = self.repo.state(source, symbol, iv)
                if (
                    recent
                    and not older_only
                    and state
                    and (
                        state["provider"] != provider
                        or state["revision"] != revision
                        or state["status"] != "ready"
                    )
                ):
                    raise DataError(
                        "Legacy recent data conflicts with published provider/revision; use a separate migration database or --older-only"
                    )
                partitions = {}
                for row in older:
                    dt = datetime.fromtimestamp(row.time, UTC)
                    partitions.setdefault(dt.strftime("%Y" if iv == "1D" else "%Y-%m"), []).append(
                        row
                    )
                objects = [
                    await asyncio.to_thread(self.archive.publish, values)
                    for values in partitions.values()
                ]
                # A migration fills gaps. A resumed month gaining a previously
                # unavailable file must not rewind already published corrections.
                count = self.repo.put(recent, overwrite=False) if recent and not older_only else 0
                result = {
                    "period": period,
                    "rows": len(rows),
                    "recent": count,
                    "archived": len(older),
                    "skipped_recent": len(recent) if older_only else 0,
                    "archives": [obj["id"] for obj in objects],
                    "api_format": api_format if from_api else None,
                    "api_read_backend": api_read_backend if from_api else None,
                    "revision": revision,
                }
                quote_events = [row.time for row in rows if row.time % 60]
                if quote_events:
                    # Validation permits only the explicit legacy hourly
                    # futures quote shape. Keep a visible finding and receipt;
                    # acceptance does not certify these as hourly trades.
                    result["legacy_quote_events"] = {
                        "rows": len(quote_events),
                        "start": min(quote_events),
                        "end": max(quote_events),
                    }
                    self.repo.finding(
                        source,
                        symbol,
                        iv,
                        "legacy_quote_events",
                        json.dumps(
                            result["legacy_quote_events"]
                            | {"input_checksum": checksum, "revision": revision},
                            sort_keys=True,
                        ),
                    )
                if (
                    older
                    and state
                    and (
                        state["revision"] != revision
                        or provider not in (state["provider"], self.repo.snapshot_provider(state))
                    )
                ):
                    self.repo.finding(
                        source,
                        symbol,
                        iv,
                        "imported_revision_boundary",
                        "Legacy history has an independent adjustment basis; verify or repair before boundary-crossing reads",
                    )
                with self.repo.connect() as con:
                    con.execute(
                        "INSERT OR REPLACE INTO legacy_imports VALUES (?,?,?,?,?,?,?,?)",
                        (
                            identifier,
                            source,
                            symbol,
                            iv,
                            period,
                            checksum,
                            json.dumps(result),
                            int(time.time()),
                        ),
                    )
                report["periods"].append(result)
                report["imported"] += count
                report["archived"] += len(older)
                report["skipped_recent"] += result["skipped_recent"]
            return report
        except httpx.HTTPError as exc:
            raise DataError(f"Legacy archive request failed ({type(exc).__name__})") from None
        finally:
            if self.client is None:
                await client.aclose()
            if not dry_run:
                self.repo.live_release(source, symbol, lease, owner)
                if gaps_changed:
                    await asyncio.to_thread(self.archive.publish_metadata)
