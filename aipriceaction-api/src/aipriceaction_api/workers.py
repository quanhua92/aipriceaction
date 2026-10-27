import asyncio
import hashlib
import json
import logging
import math
import time
import uuid
from dataclasses import replace

from .domain import INDEXES, DataError, completed_vn_sessions, cutoff
from .providers import Providers, adjustment_changes, suspected_adjustment, vn_provider_order
from .quality import audit as audit  # Re-export for the existing operational CLI.
from .redis_client import create_redis_client
from .redis_publish import publish_ticker_list, publish_worker_candles, replace_worker_cache

log = logging.getLogger(__name__)

YAHOO_NATIVE_INTERVALS = ["1D", "1h", "1m"]
YAHOO_NO_DATA_FINDING = "Yahoo has no candles in the supported current window; retry probe in 24 hours"
YAHOO_NO_DATA_RETRY_SECONDS = 24 * 60 * 60


class Worker:
    def __init__(self, repo, settings, providers=None, archive=None, redis_client=None):
        self.repo, self.settings = repo, settings
        self.providers = providers or Providers(settings)
        self.archive = archive
        self.redis_client = (
            create_redis_client(settings.redis_url) if redis_client is None else redis_client
        )
        self.owner = uuid.uuid4().hex
        self.configuration = []
        self.worker_scope = None
        self.deadline = 90  # Keep network operations shorter than durable leases.
        self.recovery_turn = 0
        self.live_turn = 0
        self.archive_repair_turn = 0
        self.archive_repair_scope = (None, None, None)
        self._next_manifest_retry = 0.0

    def _public_csv_store(self):
        if not self.settings.public_export_enabled or self.archive is None:
            return None
        from .archive import S3Store

        if not isinstance(self.archive.store, S3Store):
            return None
        from .public_csv import publish_object

        return publish_object, self.archive.store.client, self.settings.s3_bucket

    def _publish_public_candles(self, source, symbol, iv, rows):
        store = self._public_csv_store()
        if store is None or not rows:
            return {"objects": 0, "uploaded": 0}
        from datetime import UTC, datetime

        from .history import History
        from .public_csv import daily_key, period_bounds, plan_candles, yearly_key

        publish, client, bucket = store
        history = History(self.repo, self.archive, self.settings)
        periods = sorted({datetime.fromtimestamp(row.time, UTC).date() for row in rows})
        years = sorted({day.year for day in periods}) if iv in ("1D", "1h") else []
        published, uploaded = 0, 0
        for day in periods:
            key = daily_key(source, symbol, iv, day)
            with self.archive.public_csv_lock(key):
                start, end = period_bounds(day=day)
                candles = history.read(source, symbol, iv, start=start, end=end, forward=True)
                item = plan_candles(candles, source, symbol, iv, day=day)
                result = publish(client, bucket, item)
            published += 1
            uploaded += int(result["uploaded"])
        for year in years:
            key = yearly_key(source, symbol, iv, year)
            with self.archive.public_csv_lock(key):
                start, end = period_bounds(year=year)
                candles = history.read(source, symbol, iv, start=start, end=end, forward=True)
                if not candles:
                    continue
                item = plan_candles(candles, source, symbol, iv, year=year)
                result = publish(client, bucket, item)
            published += 1
            uploaded += int(result["uploaded"])
        return {"objects": published, "uploaded": uploaded}

    def _publish_public_catalog(self):
        store = self._public_csv_store()
        if store is None:
            return None
        from .catalog import Catalog
        from .public_csv import plan_catalog

        publish, client, bucket = store
        with self.archive.public_csv_lock("meta/tickers.json"):
            item = plan_catalog(Catalog(self.settings), self.repo)
            result = publish(client, bucket, item)
        log.info(
            "public ticker catalog rows=%s uploaded=%s",
            result["rows"],
            result["uploaded"],
        )
        return result

    async def _try_publish_public_catalog(self):
        try:
            return await asyncio.to_thread(self._publish_public_catalog)
        except Exception as exc:
            log.warning("public ticker catalog export failed: %s", type(exc).__name__)
            return None

    def load_watchlist(self):
        raw = json.loads(self.settings.watchlist.read_text())
        if self.settings.ingest_universe == "catalog":
            from .catalog import Catalog

            overrides = {}
            preferred = {}
            for source, items in raw.items():
                preferred[source] = []
                for item in items:
                    entry = {"symbol": item} if isinstance(item, str) else dict(item)
                    symbol = entry["symbol"].upper()
                    overrides[(source, symbol)] = entry
                    preferred[source].append(symbol)
            expanded = {}
            catalog = Catalog(self.settings)
            liquid_vn = self.repo.liquid_vn_symbols(catalog.vn_listed_symbols)
            for source, groups in catalog.groups_by_source.items():
                catalog_symbols = {symbol for values in groups.values() for symbol in values}
                if source == "vn":
                    catalog_symbols = set(catalog.vn_listed_symbols) | {
                        row["symbol"] for row in catalog.vn_index_rows
                    }
                priority = [
                    symbol for symbol in preferred.get(source, ()) if symbol in catalog_symbols
                ]
                symbols = priority + sorted(catalog_symbols - set(priority))
                expanded[source] = []
                for symbol in symbols:
                    entry = {"symbol": symbol} | overrides.get((source, symbol), {})
                    if source == "vn" and symbol not in liquid_vn and "intervals" not in entry:
                        # Probe and retain three years of daily data for every
                        # listed share. Minute/hourly history is promoted only
                        # after broad daily discovery ranks it in the liquid 80%.
                        entry["intervals"] = ["1D"]
                    expanded[source].append(entry)
            raw = expanded
        entries = []
        for source, items in raw.items():
            if source not in ("vn", "crypto", "yahoo", "sjc"):
                raise DataError("Unknown source in watchlist", 400)
            for item in items:
                entry = {"symbol": item} if isinstance(item, str) else dict(item)
                symbol = entry["symbol"].upper()
                if source == "yahoo":
                    # Probe every Yahoo catalog/watchlist symbol at its native
                    # intervals; missing symbols are cooled down, not retried.
                    intervals = list(YAHOO_NATIVE_INTERVALS)
                elif source == "sjc":
                    intervals = ["1D"]
                else:
                    intervals = entry.get("intervals", ["1D", "1h", "1m"])
                if not intervals or set(intervals) - {"1D", "1h", "1m"}:
                    raise DataError("Watchlist intervals must be native 1D, 1h, 1m", 400)
                entry.update(source=source, symbol=symbol, intervals=intervals)
                entries.append(entry)
        # Watchlist is authoritative: removing a ticker disables future ingestion,
        # but never deletes its metadata or historical data.
        self.repo.activate_watchlist(entries)
        self.configuration = entries
        return entries

    def floor(self, entry, iv):
        from .domain import parse_time

        years = {
            "1D": self.settings.daily_years,
            "1h": self.settings.hourly_years,
            "1m": self.settings.minute_years,
        }[iv]
        result = cutoff(years)
        if entry["source"] == "yahoo":
            # Yahoo only serves bounded intraday history. Stay one day inside
            # its documented edge, matching the old Rust bootstrap behavior.
            yahoo_days = {"1h": 729, "1m": 29}.get(iv)
            if yahoo_days is not None:
                result = max(result, int(time.time()) - yahoo_days * 86400)
        # Explicit listing/history start prevents invented pre-listing gaps.
        if entry.get("history_start"):
            result = max(result, parse_time(entry["history_start"]))
        return result

    def bootstrap(self):
        jobs = []
        configuration = self.configuration
        if not configuration and self.worker_scope is None:
            configuration = self.load_watchlist()
        for entry in configuration:
            for iv in entry["intervals"]:
                state = self.repo.state(entry["source"], entry["symbol"], iv)
                if state:
                    self.repo.cancel_superseded_bootstrap(
                        entry["source"], entry["symbol"], iv, self.floor(entry, iv)
                    )
                    if entry["source"] == "vn" and iv == "1D" and state["provider"] == "vndirect":
                        preferred = vn_provider_order(
                            self.settings, "1D", int(time.time()) + 1, entry["symbol"]
                        )[0]
                        if preferred != state["provider"]:
                            jobs.append(
                                self.repo.queue(
                                    "vn",
                                    entry["symbol"],
                                    "1D",
                                    "repair",
                                    self.floor(entry, iv),
                                    preferred,
                                )
                            )
                else:
                    if entry["source"] == "sjc" and iv == "1D":
                        cancelled = self.repo.cancel_empty_bootstrap(
                            "sjc",
                            entry["symbol"],
                            iv,
                            "SJC now seeds the live quote directly; cold history belongs to the history worker",
                        )
                        if cancelled:
                            log.info(
                                "bootstrap cancelled source=sjc symbol=%s interval=1D "
                                "reason=cold_history_worker",
                                entry["symbol"],
                            )
                        continue
                    retry_at = self.repo.no_data_retry_at(
                        entry["source"], entry["symbol"], iv
                    )
                    if retry_at is not None:
                        self.repo.schedule(entry["source"], entry["symbol"], iv, retry_at)
                        continue
                    jobs.append(
                        self.repo.queue(
                            entry["source"], entry["symbol"], iv, "bootstrap", self.floor(entry, iv)
                        )
                    )
        return jobs

    async def repair_page(self, job):
        try:
            return await asyncio.wait_for(self._repair_page(job), timeout=self.deadline)
        except TimeoutError:
            reason = "Provider page exceeded worker time budget"
            log.warning(
                "recovery timeout source=%s symbol=%s interval=%s provider=%s",
                job["source"],
                job["symbol"],
                job["interval"],
                job["provider"],
            )
            self.repo.finding(
                job["source"], job["symbol"], job["interval"], "incomplete_repair", reason
            )
            self.repo.fail_job(job, reason)
            return 0

    async def _repair_page(self, job):
        before = job["cursor"] or int(time.time()) + 1
        try:
            if (
                job["source"] == "sjc"
                and job["kind"] == "bootstrap"
                and self.repo.state("sjc", job["symbol"], job["interval"])
            ):
                self.repo.cancel_leased_job(
                    job,
                    "SJC live quote is seeded separately; cold history belongs to the history worker",
                )
                log.info(
                    "bootstrap cancelled source=sjc symbol=%s interval=%s reason=live_seed_ready",
                    job["symbol"],
                    job["interval"],
                )
                return 0
            entry = next(
                (
                    entry
                    for entry in self.configuration
                    if (entry["source"], entry["symbol"]) == (job["source"], job["symbol"])
                ),
                None,
            )
            if entry is not None:
                job = self.repo.advance_job_floor(job, self.floor(entry, job["interval"]))
            if job["cursor"] is not None and job["cursor"] <= job["floor"]:
                return await self.finish_recent_job(job)
            page = await self.providers.page(
                job["source"],
                job["symbol"],
                job["interval"],
                before,
                count=10
                if job["source"] == "sjc"
                else (
                    50_000
                    if job["source"] == "crypto" and job["interval"] == "1m"
                    else 1000
                    if job["source"] == "crypto"
                    else 500
                ),
                provider=job["provider"],
                **({"start": job["floor"]} if job["source"] == "vn" else {}),
            )
            if (
                job["source"] == "yahoo"
                and job["kind"] == "bootstrap"
                and job["cursor"] is None
                and page.no_data
                and not page.rows
            ):
                retry_at = int(time.time()) + YAHOO_NO_DATA_RETRY_SECONDS
                reason = "Yahoo has no bars for this interval in its current history window"
                self.repo.skip_no_data_job(job, page.provider, reason, retry_at)
                self.repo.finding(
                    job["source"],
                    job["symbol"],
                    job["interval"],
                    "provider_no_data",
                    YAHOO_NO_DATA_FINDING,
                )
                self.repo.schedule(job["source"], job["symbol"], job["interval"], retry_at)
                log.info(
                    "bootstrap skipped source=yahoo symbol=%s interval=%s reason=no_data retry_at=%s",
                    job["symbol"],
                    job["interval"],
                    retry_at,
                )
                return 0
            oldest = (
                page.cursor
                if page.cursor is not None
                else (page.rows[0].time if page.rows else None)
            )
            if self.at_verified_listing_prefix(job, entry, page):
                return await self.finish_recent_job(job)
            if oldest is None or (
                not page.rows and (job["cursor"] is None or oldest > job["floor"])
            ):
                # No-data is not evidence that the target window was covered.
                raise DataError(
                    "Provider history ended before configured floor; supply a verified history_start for newly listed assets"
                )
            if job["cursor"] is None and (not page.rows or page.rows[-1].time < job["floor"]):
                raise DataError("Provider latest candle precedes configured retained window")
            if oldest >= before:
                raise DataError("Provider cursor did not move backwards")
            prepared = [replace(r, revision=job["revision"]) for r in page.rows]
            self.repo.record_volume_proofs(getattr(page, "volume_proofs", ()))
            complete = oldest <= job["floor"]
            self.repo.stage(job, prepared, oldest, page.provider)
            current_state = self.repo.state(job["source"], job["symbol"], job["interval"])
            if (
                job["kind"] == "bootstrap"
                and current_state is None
                and page.provider != "vci"
            ):
                # Publish the first validated recent page promptly, without
                # claiming that the requested history floor has been reached.
                published = [r for r in prepared if r.time >= job["floor"]]
                self.repo.put(published)
                await asyncio.to_thread(
                    publish_worker_candles, self.redis_client, self.settings, published
                )
                self.repo.finding(
                    job["source"],
                    job["symbol"],
                    job["interval"],
                    "coverage_pending",
                    f"Requested floor {job['floor']}; recent page published, backfill incomplete",
                )
            if complete:
                # Stage releases the lease to preserve restartability/fairness.
                # Reclaim this particular job, not whichever happens to be first.
                with self.repo.connect() as con:
                    con.execute("BEGIN IMMEDIATE")
                    row = con.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
                    if row["lease_until"] > int(time.time()):
                        return
                    con.execute(
                        "UPDATE jobs SET lease_owner=?,lease_until=? WHERE id=?",
                        (self.owner, int(time.time()) + 120, job["id"]),
                    )
                    claimed = dict(row) | {"lease_owner": self.owner}
                await self.finish_recent_job(claimed)
            return len(prepared)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, DataError) else type(exc).__name__
            log.log(
                logging.WARNING if isinstance(exc, DataError) else logging.ERROR,
                "recovery failed source=%s symbol=%s interval=%s provider=%s kind=%s "
                "before=%s cursor=%s floor=%s reason=%s",
                job["source"],
                job["symbol"],
                job["interval"],
                job["provider"],
                type(exc).__name__,
                before,
                job["cursor"],
                job["floor"],
                reason,
                exc_info=(type(exc), exc, exc.__traceback__)
                if not isinstance(exc, DataError)
                else None,
            )
            self.repo.finding(
                job["source"], job["symbol"], job["interval"], "incomplete_repair", reason
            )
            self.repo.fail_job(job, reason)
            if (
                isinstance(exc, DataError)
                and job["source"] == "vn"
                and job["provider"]
                and job["attempts"] >= 2
            ):
                for alternate in vn_provider_order(
                    self.settings, job["interval"], before, job["symbol"]
                ):
                    if alternate == job["provider"]:
                        continue
                    try:
                        sample = await self.providers.page(
                            job["source"],
                            job["symbol"],
                            job["interval"],
                            before,
                            count=3,
                            provider=alternate,
                        )
                        if not sample.rows or sample.rows[0].time >= before:
                            continue
                        # A current-only alternate can answer this cursor yet be
                        # unable to rebuild the configured retained window. Do
                        # not discard staged progress and bounce between such
                        # providers. Require independently parsed candles near
                        # the durable floor before restarting from the head.
                        floor_probe_before = min(before, job["floor"] + 31 * 86400)
                        if floor_probe_before < before:
                            floor_sample = await self.providers.page(
                                job["source"],
                                job["symbol"],
                                job["interval"],
                                floor_probe_before,
                                count=3,
                                provider=alternate,
                                start=job["floor"],
                            )
                            if not floor_sample.rows or not any(
                                job["floor"] <= row.time < floor_probe_before
                                for row in floor_sample.rows
                            ):
                                continue
                        elif not any(job["floor"] <= row.time < before for row in sample.rows):
                            continue
                        kind = (
                            "repair"
                            if self.repo.state(job["source"], job["symbol"], job["interval"])
                            else job["kind"]
                        )
                        self.repo.queue(
                            job["source"],
                            job["symbol"],
                            job["interval"],
                            kind,
                            job["floor"],
                            alternate,
                            expected=(job["id"], job["revision"]),
                        )
                        self.repo.finding(
                            job["source"],
                            job["symbol"],
                            job["interval"],
                            "provider_switch",
                            "Replacement restarted on an available fallback; staging never combines providers",
                        )
                        break
                    except Exception:
                        continue
            return 0

    def at_verified_listing_prefix(self, job, entry, page):
        """Accept an empty prefix only within an independently sourced listing day."""
        from .domain import parse_time

        if (
            job["source"] != "vn"
            or job["interval"] != "1h"
            or job["kind"] != "bootstrap"
            or not entry
            or not entry.get("history_start")
            or not isinstance(entry.get("history_start_source"), str)
            or not entry["history_start_source"].startswith("https://")
            or job["floor"] != parse_time(entry["history_start"])
            or job["floor"] % 86400
            or job["cursor"] is None
            or not job["floor"] < job["cursor"] < job["floor"] + 86400
            or not page.no_data
            or page.rows
            or page.cursor is not None
            or not job["provider"]
            or page.provider != job["provider"]
        ):
            return False
        with self.repo.connect() as con:
            con.execute("BEGIN")
            state = con.execute(
                "SELECT * FROM series WHERE source=? AND symbol=? AND interval=?",
                (job["source"], job["symbol"], job["interval"]),
            ).fetchone()
            first = con.execute(
                """SELECT MIN(time) FROM (
                SELECT time FROM staging WHERE job_id=?
                UNION ALL SELECT time FROM candles
                WHERE source=? AND symbol=? AND interval=?)""",
                (job["id"], job["source"], job["symbol"], job["interval"]),
            ).fetchone()[0]
        return bool(
            state
            and state["status"] == "ready"
            and (state["provider"], state["revision"]) == (job["provider"], job["revision"])
            and first == job["cursor"]
        )

    async def finish_recent_job(self, job):
        if job["provider"] == "vci":
            state = self.repo.state(job["source"], job["symbol"], job["interval"])
            if not state or state["provider"] != "vci" or not self.repo.snapshot_adoption(state):
                reason = "VCI candidate remains staged until a verified per-series handoff licenses publication"
                self.repo.finding(
                    job["source"],
                    job["symbol"],
                    job["interval"],
                    "vci_verification_pending",
                    reason,
                )
                self.repo.fail_job(job, reason)
                return 0
        # Cursor progress proves only the requested bound. A VN hourly bootstrap
        # also needs every completed daily date observed for this same ticker.
        # Missing dates remain reviewable; never invent candles or switch bases.
        if job["source"] == "vn" and job["interval"] == "1h" and job["kind"] == "bootstrap":
            with self.repo.connect() as con:
                missing = con.execute(
                    """SELECT d.time FROM candles d WHERE d.source='vn' AND d.symbol=?
                    AND d.interval='1D' AND d.time>=? AND d.time<?
                    AND strftime('%w',d.time,'unixepoch') NOT IN ('0','6')
                    AND NOT EXISTS (SELECT 1 FROM staging s WHERE s.job_id=?
                        AND s.time>=d.time AND s.time<d.time+86400)
                    AND NOT EXISTS (SELECT 1 FROM candles c WHERE c.source='vn'
                        AND c.symbol=d.symbol AND c.interval='1h'
                        AND c.time>=d.time AND c.time<d.time+86400)
                    ORDER BY d.time LIMIT 1""",
                    (job["symbol"], job["floor"], completed_vn_sessions(), job["id"]),
                ).fetchone()
            if missing:
                reason = f"Hourly bootstrap lacks observed daily session at {missing[0]}; verify provider coverage or no-trade convention"
                self.repo.finding("vn", job["symbol"], "1h", "coverage_pending", reason)
                self.repo.fail_job(job, reason)
                return 0
        if job["kind"] == "repair" and self.archive is not None:
            await asyncio.to_thread(self.archive.finish_live_repair, job)
        else:
            self.repo.finish_job(job)
        self.advance_adjustment_rebase(job)
        if self.redis_client is not None:
            retained = {
                "1D": self.settings.redis_daily_max_size,
                "1h": self.settings.redis_hourly_max_size,
                "1m": self.settings.redis_minute_max_size,
            }[job["interval"]]
            tail = await asyncio.to_thread(
                self.repo.read,
                job["source"],
                job["symbol"],
                job["interval"],
                limit=retained,
            )
            await asyncio.to_thread(
                replace_worker_cache, self.redis_client, self.settings, tail
            )
        self.repo.schedule(job["source"], job["symbol"], job["interval"], int(time.time()) + 60)
        return 0

    def advance_adjustment_rebase(self, job):
        """Rebuild daily, hourly, then minute data after Rust's adjustment signal."""
        if job["kind"] != "repair":
            return
        source, symbol, interval = job["source"], job["symbol"], job["interval"]
        rebase = self.repo.adjustment_rebase(source, symbol)
        expected = {"daily_pending": "1D", "hourly_pending": "1h", "minute_pending": "1m"}
        if not rebase or rebase["status"] not in expected or expected[rebase["status"]] != interval:
            return
        next_intervals = {"1D": ("1h", "1m"), "1h": ("1m",), "1m": ()}[interval]
        entry = next(
            (row for row in self.configuration if row["source"] == source and row["symbol"] == symbol),
            {"source": source, "symbol": symbol, "intervals": []},
        )
        for next_interval in next_intervals:
            state = self.repo.state(source, symbol, next_interval)
            if not state or state["status"] != "ready":
                continue
            self.repo.queue(
                source,
                symbol,
                next_interval,
                "repair",
                self.floor(entry, next_interval),
                state["provider"],
            )
            stage = "hourly_pending" if next_interval == "1h" else "minute_pending"
            self.repo.set_adjustment_rebase(source, symbol, stage)
            log.info(
                "adjustment rebase queued source=%s symbol=%s interval=%s provider=%s",
                source,
                symbol,
                next_interval,
                state["provider"],
            )
            return
        self.repo.set_adjustment_rebase(source, symbol, "cold_pending")

    async def sync(self, entry, iv):
        try:
            return await asyncio.wait_for(self._sync(entry, iv), timeout=self.deadline)
        except TimeoutError:
            log.warning(
                "update timeout source=%s symbol=%s interval=%s",
                entry["source"],
                entry["symbol"],
                iv,
            )
            self.repo.finding(
                entry["source"],
                entry["symbol"],
                iv,
                "provider_failure",
                "Provider update exceeded worker time budget",
            )
            return 0

    async def _seed_sjc(self, entry):
        """Create the first live SJC candle without importing a legacy CSV."""
        page = await self.providers.page(
            "sjc",
            entry["symbol"],
            "1D",
            before=int(time.time()) + 1,
            count=1,
            provider="sjc-quote",
        )
        if not page.rows:
            reason = "SJC has no available quote in the provider scan window"
            self.repo.finding("sjc", entry["symbol"], "1D", "provider_failure", reason)
            log.warning("SJC live seed unavailable symbol=%s", entry["symbol"])
            return 0
        row = replace(page.rows[-1], revision=uuid.uuid4().hex)
        self.repo.put([row])
        await asyncio.to_thread(
            publish_worker_candles, self.redis_client, self.settings, [row]
        )
        if self.settings.public_export_enabled:
            try:
                result = await asyncio.to_thread(
                    self._publish_public_candles, "sjc", entry["symbol"], "1D", [row]
                )
                if result and result["objects"]:
                    log.info(
                        "public CSV export source=sjc symbol=%s interval=1D objects=%s uploaded=%s",
                        entry["symbol"],
                        result["objects"],
                        result["uploaded"],
                    )
            except Exception as exc:
                log.warning(
                    "public CSV export failed source=sjc symbol=%s interval=1D error=%s",
                    entry["symbol"],
                    type(exc).__name__,
                )
        log.info(
            "SJC live seed ready symbol=%s date=%s provider=%s",
            entry["symbol"],
            time.strftime("%Y-%m-%d", time.gmtime(row.time)),
            page.provider,
        )
        return 1

    async def _sync(self, entry, iv):
        started = asyncio.get_running_loop().time()
        source, symbol = entry["source"], entry["symbol"]
        if not self.repo.live_claim(source, symbol, iv, self.owner):
            return 0
        attempt = None
        cooldown_until = None
        failed = False
        try:
            state = self.repo.state(source, symbol, iv)
            if not state:
                if source == "sjc" and iv == "1D":
                    seeded = await self._seed_sjc(entry)
                    failed = not seeded
                    return seeded
                retry_at = self.repo.no_data_retry_at(source, symbol, iv)
                if retry_at is not None:
                    cooldown_until = retry_at
                    return 0
                self.repo.queue(source, symbol, iv, "bootstrap", self.floor(entry, iv))
                return 0
            if state["status"] != "ready" and not (
                state["status"] == "repairing"
                and self.repo.active_repair(source, symbol, iv)
            ):
                return 0
            attempt = self.repo.start_source_check(source, symbol, iv)
            imported_snapshot = (
                (source in ("vn", "yahoo") and iv == "1m" or source == "vn" and iv == "1D")
                and state["provider"] == "legacy-api"
            ) or (
                source in ("vn", "yahoo")
                and iv == "1h"
                and state["provider"] in {"legacy-api", "legacy-s3", "legacy"}
            )
            if imported_snapshot:
                label = {"1D": "daily", "1h": "hourly", "1m": "minute"}[iv]
                reason = f"Imported {label} snapshot requires verified provider handoff"
                self.repo.fail_source_check(source, symbol, iv, attempt, reason, "handoff_required")
                self.repo.finding(source, symbol, iv, "provider_handoff_pending", reason)
                return 0
            if state["provider"] == "vci" and not self.repo.snapshot_adoption(state):
                reason = "VCI minute source requires verified per-series handoff"
                self.repo.fail_source_check(source, symbol, iv, attempt, reason, "handoff_required")
                self.repo.finding(source, symbol, iv, "vci_verification_pending", reason)
                return 0
            latest = self.repo.read(source, symbol, iv, limit=50)
            hourly_range = self.repo.snapshot_hourly_range(state)
            request_policy = {"yahoo_hourly_range": hourly_range} if hourly_range else {}
            overlap = latest
            count = 1 if source == "sjc" else 40
            step = {"1D": 86400, "1h": 3600, "1m": 60}[iv]
            tail_times = {latest[-1].time} if latest else set()
            if source == "yahoo" and iv in ("1h", "1m") and latest:
                # Yahoo can omit an earlier closing quote from later replies.
                # Require its adjacent stored bar; retain the original quote.
                tail_times.update(r.time for r in latest if latest[-1].time - r.time <= step)
            if source == "crypto" and latest:
                count = min(1000, max(count, (int(time.time()) - latest[-1].time) // step + 40))
            # Pin incremental reads to the current upstream. A failure cannot
            # silently stitch a different provider's adjustment basis into it.
            try:
                page = await self.providers.page(
                    source, symbol, iv, count=count, provider=state["provider"], **request_policy
                )
            except DataError:
                if source != "vn":
                    raise
                page = None
                for alternate in vn_provider_order(
                    self.settings, iv, int(time.time()) + 1, symbol
                ):
                    if alternate == state["provider"]:
                        continue
                    try:
                        candidate = await self.providers.page(
                            source, symbol, iv, count=40, provider=alternate
                        )
                        if candidate.rows:
                            page = candidate
                            break
                    except DataError:
                        continue
                if page is None:
                    raise DataError("All configured VN providers unavailable") from None
            if not page.rows:
                if source == "yahoo" and page.no_data:
                    reason = "Yahoo returned no candles in the current request window"
                    self.repo.fail_source_check(source, symbol, iv, attempt, reason, "no_data")
                    self.repo.finding(
                        source,
                        symbol,
                        iv,
                        "provider_no_data",
                        YAHOO_NO_DATA_FINDING,
                    )
                    cooldown_until = int(time.time()) + YAHOO_NO_DATA_RETRY_SECONDS
                    return 0
                raise DataError("No recent provider data")
            if (
                source in ("vn", "yahoo")
                and iv in ("1D", "1h", "1m")
                and latest
                and page.provider == state["provider"]
                and page.rows[-1].time > latest[-1].time
                and not any(r.time in tail_times for r in page.rows)
            ):
                # Keep ordinary/closed-market checks cheap. Expand only when
                # newer data has outrun the small overlap page; pin the retry
                # to the existing provider's adjustment basis.
                count = min(1000, max(40, (int(time.time()) - latest[-1].time) // step + 40))
                if count > 40:
                    page = await self.providers.page(
                        source,
                        symbol,
                        iv,
                        count=count,
                        provider=state["provider"],
                        start=None if hourly_range else self.floor(entry, iv),
                        **request_policy,
                    )
                    if not page.rows:
                        market = "VN" if source == "vn" else "Yahoo"
                        raise DataError(f"No provider data for expanded {market} overlap")
                    # An expanded page can reach beyond the normal 50-bar
                    # comparison. Check all stored overlap before publishing
                    # older candles on a potentially changed adjustment basis.
                    overlap = self.repo.read(
                        source, symbol, iv, start=page.rows[0].time, end=latest[-1].time
                    )
            gap = None
            if source == "crypto" and latest and page.rows[0].time > latest[-1].time + step:
                gap = f"Continuous-market gap from {latest[-1].time} to {page.rows[0].time}; recovery queued"
            elif (
                source in ("vn", "yahoo")
                and iv in ("1D", "1h", "1m")
                and latest
                and page.provider == state["provider"]
                and page.rows[-1].time > latest[-1].time
                and not any(r.time in tail_times for r in page.rows)
            ):
                # Trading breaks, holidays, and sparse stocks do not imply
                # missing candles. Require an observed overlap instead of
                # guessing which intervening candles should have traded.
                market = "VN" if source == "vn" else "Yahoo"
                gap = f"Bounded {market} provider page does not overlap the published tail; recovery queued"
            if gap:
                # A long outage can exceed one bounded live page. Preserve the
                # published series and resume its durable recovery rather than
                # appending a newer tail without coverage evidence.
                self.repo.queue(source, symbol, iv, "repair", self.floor(entry, iv), page.provider)
                self.repo.finding(
                    source,
                    symbol,
                    iv,
                    "coverage_pending",
                    gap,
                )
                self.repo.fail_source_check(
                    source,
                    symbol,
                    iv,
                    attempt,
                    gap,
                    "repair_queued",
                )
                return 0
            completed = (
                completed_vn_sessions()
                if source == "vn"
                else int(time.time()) // 86400 * 86400
                if iv == "1D"
                else int(time.time()) // (3600 if iv == "1h" else 60) * (3600 if iv == "1h" else 60)
            )
            adjustment = (
                suspected_adjustment(overlap, page.rows)
                if source in ("vn", "yahoo") and iv == "1D" and symbol not in INDEXES
                else None
            )
            if adjustment:
                self.repo.begin_adjustment_rebase(source, symbol, adjustment)
                self.repo.finding(
                    source,
                    symbol,
                    iv,
                    "suspected_adjustment",
                    json.dumps(adjustment, sort_keys=True, separators=(",", ":")),
                )
                log.warning(
                    "suspected price adjustment source=%s symbol=%s interval=%s "
                    "divergent_bars=%s worst_date=%s worst_ratio=%.6f",
                    source,
                    symbol,
                    iv,
                    adjustment["divergent_bars"],
                    adjustment["worst_date"],
                    adjustment["worst_ratio"],
                )
            if page.provider != state["provider"] or adjustment or adjustment_changes(
                overlap, page.rows, completed, snapshot_provider=self.repo.snapshot_provider(state)
            ):
                self.repo.queue(source, symbol, iv, "repair", self.floor(entry, iv), page.provider)
                self.repo.finding(
                    source,
                    symbol,
                    iv,
                    "historical_revision",
                    "Corroborated historical changes; retained window staged for recovery",
                )
                self.repo.fail_source_check(
                    source,
                    symbol,
                    iv,
                    attempt,
                    "Provider/revision change; recovery queued",
                    "repair_queued",
                )
                return 0
            # Protect a daily historical revision from late/partial intraday
            # replies ending before the published latest candle.
            if latest and page.rows[-1].time < latest[-1].time:
                raise DataError("Provider recent tail ends before published latest candle")
            if source == "sjc":
                quote = page.rows[-1]
                previous = next((row for row in latest if row.time == quote.time), None)
                if previous:
                    quote = replace(
                        quote,
                        open=previous.open,
                        high=max(previous.high, quote.high),
                        low=min(previous.low, quote.low),
                        volume=previous.volume,
                    )
                rows = (
                    [replace(quote, revision=state["revision"])]
                    if quote.time >= self.floor(entry, iv)
                    else []
                )
            else:
                rows = [
                    replace(r, revision=state["revision"])
                    for r in page.rows
                    if r.time >= self.floor(entry, iv)
                ]
            if not rows:
                raise DataError("Provider has no candles inside the retained window")
            self.repo.record_volume_proofs(getattr(page, "volume_proofs", ()))
            self.repo.put(rows, verification={"attempt_ns": attempt, "completed_before": completed})
            if source == "yahoo":
                self.repo.finding(
                    source,
                    symbol,
                    iv,
                    "provider_no_data",
                    YAHOO_NO_DATA_FINDING,
                    resolved=True,
                )
            await asyncio.to_thread(
                publish_worker_candles, self.redis_client, self.settings, rows
            )
            # The recent observation and candles are committed together. A later
            # optional historical probe must not rewrite that successful result,
            # including when the worker is cancelled during the probe.
            attempt = None
            if self.settings.public_export_enabled:
                try:
                    public_result = await asyncio.to_thread(
                        self._publish_public_candles, source, symbol, iv, rows
                    )
                    if public_result and public_result["objects"]:
                        log.info(
                            "public CSV export source=%s symbol=%s interval=%s objects=%s uploaded=%s",
                            source,
                            symbol,
                            iv,
                            public_result["objects"],
                            public_result["uploaded"],
                        )
                except Exception as exc:
                    # SQLite remains the ingestion source of truth. The next
                    # successful check re-reads canonical history and retries
                    # these direct-key exports.
                    log.warning(
                        "public CSV export failed source=%s symbol=%s interval=%s error=%s",
                        source,
                        symbol,
                        iv,
                        str(exc) if isinstance(exc, DataError) else type(exc).__name__,
                    )
            if iv == "1D" and source not in ("sjc", "crypto"):
                try:
                    # Leave time for recording a failed probe and releasing the
                    # live lease before the outer live-update deadline expires.
                    remaining = self.deadline - (asyncio.get_running_loop().time() - started)
                    if remaining <= 0:
                        raise TimeoutError
                    await asyncio.wait_for(
                        self.sentinel(entry, state), timeout=min(30, remaining / 2)
                    )
                except Exception as exc:
                    reason = str(exc) if isinstance(exc, DataError) else type(exc).__name__
                    self.repo.finding(source, symbol, iv, "historical_probe_failure", reason)
            return len(rows)
        except Exception as exc:
            failed = True
            reason = str(exc) if isinstance(exc, DataError) else type(exc).__name__
            log.log(
                logging.WARNING if isinstance(exc, DataError) else logging.ERROR,
                "update failed source=%s symbol=%s interval=%s kind=%s reason=%s",
                source,
                symbol,
                iv,
                type(exc).__name__,
                reason,
                exc_info=(type(exc), exc, exc.__traceback__)
                if not isinstance(exc, DataError)
                else None,
            )
            if attempt is not None:
                self.repo.fail_source_check(source, symbol, iv, attempt, reason)
            self.repo.finding(source, symbol, iv, "provider_failure", reason)
            return 0
        except asyncio.CancelledError:
            failed = True
            if attempt is not None:
                self.repo.fail_source_check(
                    source, symbol, iv, attempt, "Provider update interrupted or timed out"
                )
            raise
        finally:
            # Bounded cooldown also prevents one unavailable provider monopolizing
            # the loop. Other tickers remain independently scheduled.
            now = int(time.time())
            if source == "sjc" and iv == "1D":
                if failed:
                    next_sync = max(cooldown_until or 0, now + 60)
                else:
                    utc = time.gmtime(now)
                    delay = 300 if utc.tm_wday < 5 and 2 <= utc.tm_hour < 8 else 1800
                    next_sync = now + delay
            else:
                next_sync = max(
                    cooldown_until or 0,
                    now + {"1D": 3600, "1h": 900, "1m": 60}[iv],
                )
            self.repo.schedule(source, symbol, iv, next_sync)
            self.repo.live_release(source, symbol, iv, self.owner)

    async def sentinel(self, entry, state):
        # One inexpensive historical sample per UTC day/ticker, offset over time.
        # A durable lease prevents duplicate checks across worker restarts/processes.
        source, symbol = entry["source"], entry["symbol"]
        owner = self.owner + "-sentinel"
        if not self.repo.live_claim(source, symbol, "sentinel", owner, lease=86400):
            return
        age = 90 + (int(time.time()) // 86400 % 10) * 90
        existing = self.repo.read(
            source, symbol, "1D", end=int(time.time()) - age * 86400, limit=10
        )
        if not existing:
            return
        page = await self.providers.page(
            source, symbol, "1D", existing[-1].time + 86400, count=10, provider=state["provider"]
        )
        if adjustment_changes(existing, page.rows, int(time.time()) // 86400 * 86400):
            self.repo.queue(source, symbol, "1D", "repair", self.floor(entry, "1D"), page.provider)
            self.repo.finding(
                source,
                symbol,
                "1D",
                "historical_revision",
                "Historical sample changed outside live overlap",
            )

    async def verify_archive_head(self, obj, state):
        """Recheck completed retained prices immediately before publication."""
        completed = (
            completed_vn_sessions()
            if obj["source"] == "vn"
            else int(time.time())
            // {"1D": 86400, "1h": 3600, "1m": 60}[obj["interval"]]
            * {"1D": 86400, "1h": 3600, "1m": 60}[obj["interval"]]
        )
        retained = self.repo.read(
            obj["source"], obj["symbol"], obj["interval"], end=completed - 1, limit=40
        )
        hourly_range = self.repo.snapshot_hourly_range(state)
        head = await self.providers.page(
            obj["source"],
            obj["symbol"],
            obj["interval"],
            None if hourly_range else completed,
            count=40,
            provider=state["provider"],
            **({"yahoo_hourly_range": hourly_range} if hourly_range else {}),
        )
        latest = {r.time: r for r in retained}
        matched = [r for r in head.rows if r.time in latest]
        if not retained or len(matched) < min(3, len(retained)):
            raise DataError("Archived replacement cannot verify completed retained overlap")
        if head.provider != state["provider"] or any(
            not math.isclose(
                getattr(r, key), getattr(latest[r.time], key), rel_tol=1e-8, abs_tol=1e-8
            )
            for r in matched
            for key in ("open", "high", "low", "close", "volume")
        ):
            entry = next(
                (
                    e
                    for e in self.configuration
                    if e["source"] == obj["source"] and e["symbol"] == obj["symbol"]
                ),
                {"source": obj["source"], "symbol": obj["symbol"]},
            )
            self.repo.queue(
                obj["source"],
                obj["symbol"],
                obj["interval"],
                "repair",
                self.floor(entry, obj["interval"]),
                state["provider"],
            )
            raise DataError(
                "Completed retained prices changed; retained recovery must precede archive publication"
            )
        return {
            "kind": "completed_retained_ohlc",
            "matched_rows": len(matched),
            "first": min(r.time for r in matched),
            "last": max(r.time for r in matched),
            "verified_at_ns": time.time_ns(),
        }

    def _claim_archive_repair_job(self, job_id, restart_failed=False):
        now = int(time.time())
        with self.repo.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row["status"] in ("complete", "cancelled"):
                return None
            if row["lease_until"] > now and row["lease_owner"] != self.owner:
                return None
            if row["lease_owner"] == self.owner and row["lease_until"] > now:
                con.execute(
                    "UPDATE jobs SET lease_until=? WHERE id=?",
                    (now + 120, job_id),
                )
                return dict(row) | {"lease_owner": self.owner, "status": "running"}
            if row["retry_at"] > now and not restart_failed:
                return None
            if restart_failed and row["error"]:
                con.execute("DELETE FROM staging WHERE job_id=?", (job_id,))
                con.execute(
                    "UPDATE jobs SET cursor=NULL,retry_at=0,error=NULL WHERE id=?", (job_id,)
                )
                row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            con.execute(
                "UPDATE jobs SET lease_owner=?,lease_until=?,status='running' WHERE id=?",
                (self.owner, now + 120, job_id),
            )
            return dict(row) | {"lease_owner": self.owner, "status": "running"}

    def _archive_repair_staging(self, job_id):
        from .domain import Candle

        with self.repo.connect() as con:
            return [
                Candle(**{key: value for key, value in dict(row).items() if key != "job_id"})
                for row in con.execute(
                    "SELECT * FROM staging WHERE job_id=? ORDER BY time", (job_id,)
                )
            ]

    async def _publish_archive_repair_csv(self, batch_id, owner, payload):
        if self.archive is None:
            self.repo.complete_archive_repair_batch(batch_id, owner)
            return
        public_store = self._public_csv_store()
        if public_store is None:
            self.repo.complete_archive_repair_batch(batch_id, owner)
            return
        groups = {}
        for target in payload["targets"]:
            obj = target["replacement"]
            key = (obj["source"], obj["symbol"], obj["interval"])
            groups.setdefault(key, []).extend(await asyncio.to_thread(self.archive.read, obj))
        try:
            for (source, symbol, iv), rows in groups.items():
                await asyncio.to_thread(
                    self._publish_public_candles,
                    source,
                    symbol,
                    iv,
                    sorted(rows, key=lambda row: row.time),
                )
            with self.repo.connect() as con:
                con.execute("BEGIN IMMEDIATE")
                for source, symbol, iv in groups:
                    con.execute(
                        "UPDATE quality SET resolved=1 WHERE source=? AND symbol=? AND interval=? AND kind='archive_csv_repair_pending'",
                        (source, symbol, iv),
                    )
            self.repo.complete_archive_repair_batch(batch_id, owner)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, DataError) else type(exc).__name__
            for source, symbol, iv in groups:
                self.repo.finding(
                    source, symbol, iv, "archive_csv_repair_pending", reason
                )
            raise

    async def archive_repair(
        self, source=None, symbol=None, interval=None, restart_failed=False, max_targets=None
    ):
        """Repair all captured pending objects as one resumable manifest batch."""
        if self.archive is None:
            return 0
        if restart_failed and not all((source, symbol, interval)):
            raise DataError("Restart requires a source, symbol, and native interval", 400)
        batch = self.repo.ensure_archive_repair_batch(source, symbol, interval)
        if not batch:
            return 0
        batch = self.repo.claim_archive_repair_batch(self.owner, force=restart_failed)
        if not batch:
            return 0
        payload = batch["payload"]
        total_rows = sum(
            target["old"]["row_count"] for target in payload.get("targets", [])
        )
        try:
            if batch["status"] in ("indexed", "manifest_published"):
                payload = await asyncio.to_thread(
                    self.archive.publish_repair_batch, batch["id"], self.owner
                )
                if batch["status"] == "manifest_published":
                    await self._publish_archive_repair_csv(batch["id"], self.owner, payload)
                    return total_rows
                batch = self.repo.pending_archive_repair_batch()
                if batch and batch["status"] == "manifest_published":
                    await self._publish_archive_repair_csv(batch["id"], self.owner, payload)
                    return total_rows
                return total_rows

            targets = payload.get("targets", [])
            if not targets or len(targets) > 100:
                raise DataError("Archive repair batch target list is empty or too large")
            prepared_this_call = 0
            for target_index, target in enumerate(targets):
                old = target["old"]
                already_prepared = bool(target["replacement"])
                identity = (old["source"], old["symbol"], old["interval"])
                state = self.repo.state(*identity)
                if not state or state["status"] != "ready" or (
                    state["provider"], state["revision"]
                ) != (target["provider"], target["revision"]):
                    self.repo.fail_archive_repair_batch(
                        batch["id"], self.owner, "Series revision changed during archive repair"
                    )
                    return 0
                job = self._claim_archive_repair_job(
                    target["job_id"],
                    restart_failed=restart_failed and not target["replacement"],
                )
                if not job:
                    with self.repo.connect() as con:
                        row = con.execute(
                            "SELECT retry_at,error FROM jobs WHERE id=?", (target["job_id"],)
                        ).fetchone()
                    retry_at = row["retry_at"] if row else int(time.time()) + 30
                    message = (row["error"] if row else None) or "Archive repair job is leased or delayed"
                    self.repo.release_archive_repair_batch(
                        batch["id"], self.owner, message, max(int(time.time()) + 5, retry_at)
                    )
                    return 0
                obj = old
                try:
                    if target["replacement"]:
                        replacement = await asyncio.to_thread(
                            self.archive.read, target["replacement"], refresh=True
                        )
                        staged = self._archive_repair_staging(target["job_id"])
                        staged = [replace(row, revision=target["revision"]) for row in staged]
                        if replacement != staged:
                            raise DataError("Prepared Parquet differs from its durable staged candles")
                    else:
                        old_rows = await asyncio.to_thread(self.archive.read, obj)
                        covered = False
                        rows = []
                        pages = 0
                        while not covered:
                            with self.repo.connect() as con:
                                job_row = con.execute(
                                    "SELECT * FROM jobs WHERE id=?", (target["job_id"],)
                                ).fetchone()
                                if not job_row:
                                    raise DataError("Archive repair checkpoint job disappeared")
                                current_job = dict(job_row)
                                downloaded = con.execute(
                                    "SELECT COUNT(*) FROM staging WHERE job_id=?",
                                    (target["job_id"],),
                                ).fetchone()[0]
                            before = current_job["cursor"] or obj["end"] + (
                                86400 if obj["interval"] == "1D" else 1
                            )
                            covered = current_job["cursor"] is not None and current_job["cursor"] <= obj["start"]
                            if covered:
                                break
                            job = self._claim_archive_repair_job(target["job_id"])
                            if not job:
                                raise DataError("Archive repair page lease could not be renewed")
                            count = min(500, max(1, obj["row_count"] - downloaded))
                            page = await self.providers.page(
                                obj["source"],
                                obj["symbol"],
                                obj["interval"],
                                before,
                                count=count,
                                provider=target["provider"],
                            )
                            if (
                                not page.rows
                                or page.provider != target["provider"]
                                or page.rows[0].time >= before
                            ):
                                raise DataError(
                                    "Pinned provider returned no usable adjusted history or did not advance"
                                )
                            rows = [
                                replace(row, revision=job["revision"])
                                for row in page.rows
                                if obj["start"] <= row.time <= obj["end"]
                            ]
                            if (
                                not rows
                                and obj["start"] <= page.rows[0].time <= obj["end"]
                            ):
                                raise DataError("Pinned provider skipped dates inside the archive")
                            self.repo.record_volume_proofs(
                                getattr(page, "volume_proofs", ())
                            )
                            self.repo.stage(job, rows, page.rows[0].time, target["provider"])
                            covered = page.rows[0].time <= obj["start"]
                            pages += 1
                            if pages > 20:
                                raise DataError("Archive repair exceeded its bounded page budget")
                        replacement = [
                            replace(row, revision=target["revision"])
                            for row in self._archive_repair_staging(target["job_id"])
                        ]
                        if {row.time for row in replacement} != {row.time for row in old_rows}:
                            raise DataError(
                                "Provider timestamp coverage differs from the archive; target remains pending"
                            )
                        head_evidence = await self.verify_archive_head(obj, state)
                        capture = json.dumps(
                            [row.record() for row in replacement],
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ).encode()
                        evidence = {
                            "provider": target["provider"],
                            "revision": target["revision"],
                            "rows": len(replacement),
                            "first": replacement[0].time,
                            "last": replacement[-1].time,
                            "normalized_candles_sha256": hashlib.sha256(capture).hexdigest(),
                            "retained_overlap": head_evidence,
                        }
                        new_obj = await asyncio.to_thread(self.archive.prepare, replacement)
                        target["replacement"] = new_obj
                        target["evidence"] = evidence
                        self.repo.save_archive_repair_batch(
                            batch["id"], self.owner, "collecting", payload
                        )
                    if target["replacement"] and not target["evidence"]:
                        target["evidence"] = {
                            "provider": target["provider"],
                            "revision": target["revision"],
                            "rows": len(replacement),
                            "first": replacement[0].time,
                            "last": replacement[-1].time,
                            "normalized_candles_sha256": target["replacement"]["checksum"],
                            "resumed_prepared_object": True,
                        }
                    target["replacement"] = target["replacement"] or await asyncio.to_thread(
                        self.archive.prepare, replacement
                    )
                    self.repo.save_archive_repair_batch(
                        batch["id"], self.owner, "collecting", payload
                    )
                    with self.repo.connect() as con:
                        con.execute("BEGIN IMMEDIATE")
                        con.execute(
                            "UPDATE jobs SET status='pending',lease_owner=NULL,lease_until=0 WHERE id=? AND lease_owner=?",
                            (target["job_id"], self.owner),
                        )
                    if not already_prepared:
                        prepared_this_call += 1
                    if max_targets and prepared_this_call >= max_targets and any(
                        not following["replacement"] for following in targets[target_index + 1 :]
                    ):
                        self.repo.release_archive_repair_batch(
                            batch["id"], self.owner, "Batch preparation checkpoint saved", int(time.time())
                        )
                        return 0
                except Exception as exc:
                    reason = str(exc) if isinstance(exc, DataError) else type(exc).__name__
                    self.repo.finding(
                        old["source"], old["symbol"], old["interval"],
                        "archive_repair_pending", reason
                    )
                    self.repo.fail_job(job, reason)
                    with self.repo.connect() as con:
                        failed = con.execute(
                            "SELECT retry_at FROM jobs WHERE id=?", (target["job_id"],)
                        ).fetchone()
                    self.repo.release_archive_repair_batch(
                        batch["id"], self.owner, reason,
                        failed["retry_at"] if failed else int(time.time()) + 30,
                    )
                    return 0
            self.repo.save_archive_repair_batch(batch["id"], self.owner, "prepared", payload)
            payload = await asyncio.to_thread(
                self.archive.publish_repair_batch, batch["id"], self.owner
            )
            await self._publish_archive_repair_csv(batch["id"], self.owner, payload)
            return total_rows
        except Exception as exc:
            reason = str(exc) if isinstance(exc, DataError) else type(exc).__name__
            current = self.repo.pending_archive_repair_batch()
            if current and current["id"] == batch["id"]:
                stale_receipt = any(
                    marker in reason.lower()
                    for marker in (
                        "target changed after batch capture",
                        "series basis changed during batch",
                        "does not preserve its exact target range",
                    )
                )
                if current["status"] in ("collecting", "prepared") and stale_receipt:
                    self.repo.fail_archive_repair_batch(batch["id"], self.owner, reason)
                else:
                    self.repo.release_archive_repair_batch(
                        batch["id"], self.owner, reason, int(time.time()) + 30
                    )
            return 0

    async def cycle(self):
        if not self.configuration and self.worker_scope is None:
            self.load_watchlist()
        if (
            self.archive is not None
            and time.monotonic() >= self._next_manifest_retry
            and self.repo.archive_manifest_is_dirty()
        ):
            self._next_manifest_retry = time.monotonic() + 60
            try:
                await asyncio.to_thread(self.archive.publish_metadata)
            except Exception as exc:
                log.warning("archive manifest dirty; publish retry failed error=%s", type(exc).__name__)
        available = {(t["source"], t["symbol"]): t for t in self.repo.tickers(enabled=True)}
        due = []
        now = int(time.time())
        for entry in self.configuration:
            ticker = available.get((entry["source"], entry["symbol"]))
            if ticker is None:
                continue  # Another worker may have loaded an edited watchlist.
            for iv in entry["intervals"]:
                due_at = ticker[{"1D": "next_1d", "1h": "next_1h", "1m": "next_1m"}[iv]]
                if due_at <= now:
                    due.append((due_at, entry, iv))
        configured = {
            (entry["source"], entry["symbol"]): priority
            for priority, entry in enumerate(self.configuration)
        }
        interval_priority = {"1m": 0, "1h": 1, "1D": 2}

        def live_priority(item):
            entry, interval = item[1], item[2]
            bitcoin_minute = (
                entry["source"] == "crypto"
                and entry["symbol"] == "BTCUSDT"
                and interval == "1m"
            )
            return (
                0 if bitcoin_minute else 1,
                item[0],
                interval_priority[interval],
                configured[(entry["source"], entry["symbol"])],
            )

        scheduled = sorted((item for item in due if item[0] > 0), key=live_priority)
        never = sorted(
            (item for item in due if item[0] == 0),
            key=live_priority,
        )
        capacity = self.settings.worker_concurrency
        if scheduled and never and capacity == 1:
            selected = (scheduled if self.live_turn % 2 == 0 else never)[:1]
            self.live_turn += 1
        elif scheduled and never:
            scheduled_count = min(len(scheduled), capacity - 1)
            selected = scheduled[:scheduled_count]
            selected.extend(never[: capacity - scheduled_count])
            if len(selected) < capacity:
                selected.extend(scheduled[scheduled_count:capacity])
        else:
            selected = (scheduled or never)[:capacity]
        # Ordinary updates get a turn before one bounded recovery page.
        values = await asyncio.gather(
            *(self.sync(entry, iv) for _, entry, iv in selected)
        )
        allowed = [
            (entry["source"], entry["symbol"], iv)
            # Establish the primary three-year OHLCV API first for every new
            # ticker, then its recent minute tail, before the secondary hourly
            # history. Live refresh has separate overdue/zero-slot budgeting.
            for iv in ("1D", "1m", "1h")
            for entry in self.configuration
            if iv in entry["intervals"]
            and (entry["source"], entry["symbol"]) in available
        ]
        # During a restart/outage, hundreds of stale one-minute tails can need
        # two requests each to regain overlap. Reserve most of the provider
        # budget for those checks, while still giving durable history one turn
        # in four so backfills cannot starve indefinitely.
        live_backlog = (
            self.repo.live_check_backlog(
                "vn", "1m", time.time_ns() - 15 * 60 * 1_000_000_000
            )
            if any(
                entry["source"] == "vn" and "1m" in entry["intervals"]
                for entry in self.configuration
            )
            else 0
        )
        run_recovery = live_backlog <= self.settings.worker_concurrency or not (
            self.recovery_turn % 4
        )
        self.recovery_turn += 1
        job = self.repo.claim_job(self.owner, allowed=allowed) if run_recovery else None
        if job:
            values.append(await self.repair_page(job))
            log.info(
                "recovery source=%s symbol=%s interval=%s processed=%s",
                job["source"],
                job["symbol"],
                job["interval"],
                values[-1],
            )
        if self.archive is not None and self.archive_repair_turn % 4 == 0:
            repair_source, repair_symbols, repair_interval = self.archive_repair_scope
            repaired = await self.archive_repair(
                repair_source,
                repair_symbols,
                repair_interval,
                max_targets=1,
            )
            if repaired:
                values.append(repaired)
                log.info("archive repair batch processed rows=%s", repaired)
        self.archive_repair_turn += 1
        return sum(values)

    async def refresh(self, source, symbols, interval):
        """Explicit bounded live rechecks; repairs remain independently queued."""
        try:
            self.load_watchlist()
            selected = [
                e
                for e in self.configuration
                if e["source"] == source
                and interval in e["intervals"]
                and (not symbols or e["symbol"] in symbols)
            ]
            if not selected:
                raise DataError("No configured watchlist entries match refresh filters", 400)
            if symbols and set(symbols) - {e["symbol"] for e in selected}:
                raise DataError("Some requested tickers are outside the refresh watchlist", 400)
            results = []
            for entry in selected:
                ident = (source, entry["symbol"], interval)
                state = self.repo.state(*ident)
                result = dict(source=source, symbol=entry["symbol"], interval=interval, rows=0)
                if not state or state["status"] != "ready" and not (
                    state["status"] == "repairing" and self.repo.active_repair(*ident)
                ):
                    results.append(result | {"outcome": "not_ready"})
                    continue
                with self.repo.connect() as con:
                    previous = con.execute(
                        "SELECT attempted_at_ns FROM source_checks WHERE source=? AND symbol=? AND interval=?",
                        ident,
                    ).fetchone()
                result["rows"] = await self.sync(entry, interval)
                with self.repo.connect() as con:
                    check = con.execute(
                        "SELECT * FROM source_checks WHERE source=? AND symbol=? AND interval=?",
                        ident,
                    ).fetchone()
                if (
                    not check
                    or previous
                    and check["attempted_at_ns"] == previous[0]
                    or not result["rows"]
                    and check["outcome"] == "succeeded"
                ):
                    results.append(result | {"outcome": "skipped"})
                    continue
                results.append(
                    result
                    | {
                        "outcome": check["outcome"],
                        "error": check["error"],
                        "attempted_at_ns": check["attempted_at_ns"],
                        "successful_at_ns": check["successful_at_ns"],
                        "checked_provider": check["provider"],
                        "checked_revision": check["revision"],
                        "completed_rows": check["completed_rows"],
                        "provisional_rows": check["provisional_rows"],
                    }
                )
            return results
        finally:
            await self.providers.close()

    async def run(
        self,
        once=False,
        cycles=None,
        source=None,
        symbols=None,
        interval=None,
        archive_daily=False,
        skip_catalog_publish=False,
    ):
        maintenance_task = None
        try:
            self.worker_scope = (source, symbols, interval)

            def configure():
                self.load_watchlist()
                self.configuration = [
                    entry
                    | {
                        "intervals": [
                            iv for iv in entry["intervals"] if not interval or iv == interval
                        ]
                    }
                    for entry in self.configuration
                    if (not source or entry["source"] == source)
                    and (not symbols or entry["symbol"] in symbols)
                ]
                self.configuration = [entry for entry in self.configuration if entry["intervals"]]

            configure()
            self.archive_repair_scope = (source, symbols, interval)
            if not self.configuration:
                if interval and not source and not symbols:
                    if once or cycles:
                        log.info(
                            "worker has no configured series interval=%s; exiting idle", interval
                        )
                        return
                    log.info(
                        "worker has no configured series interval=%s; checking again in 15 minutes",
                        interval,
                    )
                else:
                    raise DataError("No configured watchlist entries match worker filters", 400)
            if not skip_catalog_publish:
                ticker_identities = await asyncio.to_thread(
                    self.repo.discovered_tickers, ("vn", "crypto", "yahoo", "sjc")
                )
                await asyncio.to_thread(publish_ticker_list, self.redis_client, ticker_identities)
                await self._try_publish_public_catalog()
            log.info(
                "worker started owner=%s series=%s source=%s symbols=%s interval=%s archive_daily=%s",
                self.owner,
                sum(len(entry["intervals"]) for entry in self.configuration),
                source or "all",
                len(symbols) if symbols else "all",
                interval or "all",
                archive_daily,
            )
            self.bootstrap()
            next_configuration_refresh = time.monotonic() + 15 * 60
            maintenance = None
            if archive_daily:
                from .archive import Archive
                from .maintenance import DailyArchive

                maintenance = DailyArchive(
                    self.archive or Archive(self.repo, self.settings), source, symbols, interval
                )
            completed_cycles = 0
            while True:
                if time.monotonic() >= next_configuration_refresh:
                    configure()
                    if not skip_catalog_publish:
                        ticker_identities = await asyncio.to_thread(
                            self.repo.discovered_tickers, ("vn", "crypto", "yahoo", "sjc")
                        )
                        await asyncio.to_thread(
                            publish_ticker_list, self.redis_client, ticker_identities
                        )
                        await self._try_publish_public_catalog()
                    self.bootstrap()
                    next_configuration_refresh = time.monotonic() + 15 * 60
                if not self.configuration:
                    if maintenance is not None and (
                        maintenance_task is None or maintenance_task.done()
                    ):
                        if maintenance_task is not None:
                            await maintenance_task
                        maintenance_task = asyncio.create_task(
                            asyncio.to_thread(maintenance.tick)
                        )
                    refresh_delay = max(1, next_configuration_refresh - time.monotonic())
                    await asyncio.sleep(
                        min(refresh_delay, 60 if maintenance is not None else refresh_delay)
                    )
                    continue
                await self.cycle()
                if maintenance is not None:
                    # One transfer batch at a time, independent of live ingestion.
                    # Exact-version publication/pruning already handles corrections
                    # written while an exported snapshot is being uploaded.
                    if maintenance_task is None or maintenance_task.done():
                        if maintenance_task is not None:
                            await maintenance_task
                        maintenance_task = asyncio.create_task(asyncio.to_thread(maintenance.tick))
                completed_cycles += 1
                if once or cycles and completed_cycles >= cycles:
                    break
                await asyncio.sleep(1)
        finally:
            try:
                await self.providers.close()
            finally:
                try:
                    # Cancellation stops ingestion, not an upload's verification
                    # and pruning. Drain before returning or releasing job claims.
                    if maintenance_task is not None:
                        await asyncio.shield(maintenance_task)
                finally:
                    self.repo.release_worker_leases(self.owner)
                    log.info("worker stopped owner=%s", self.owner)
