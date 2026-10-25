"""Single-flight cold-history backfill into indexed Parquet and public CSV."""

import asyncio
import json
import logging
import time
import uuid
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime

from .archive import Archive, S3Store
from .domain import DataError, parse_time
from .history import History
from .maintenance import DailyArchive
from .providers import Providers, vn_provider_order
from .public_csv import daily_key, period_bounds, plan_candles, publish_object, yearly_key
from .workers import Worker

log = logging.getLogger(__name__)

HISTORY_LEASE = ("vn", "__HISTORY_WORKER__", "1D")
BINANCE_MINUTE_PAGE_SIZE = 50_000
HANDOFF_FAILURES = 3
RETRY_SECONDS = 300
COVERAGE_RECHECK_SECONDS = 30 * 24 * 60 * 60
HISTORY_LEASE_SECONDS = 180


class HistoricalProviderError(Exception):
    """The selected provider could not advance this pinned history pass."""


class HistoricalBasisMismatch(DataError):
    def __init__(self, evidence):
        self.evidence = evidence
        super().__init__("Historical overlap differs from the active provider/revision")


def _shift_years(stamp, years):
    value = datetime.fromtimestamp(stamp, UTC)
    try:
        return int(value.replace(year=value.year + years).timestamp())
    except ValueError:
        return int(value.replace(year=value.year + years, day=28).timestamp())


def _partition(epoch, interval):
    value = datetime.fromtimestamp(max(0, epoch - 1), UTC)
    if interval == "1m":
        start = datetime(value.year, value.month, 1, tzinfo=UTC)
        end = (
            datetime(value.year + 1, 1, 1, tzinfo=UTC)
            if value.month == 12
            else datetime(value.year, value.month + 1, 1, tzinfo=UTC)
        )
        return int(start.timestamp()), int(end.timestamp()), start.strftime("%Y-%m")
    start = datetime(value.year, 1, 1, tzinfo=UTC)
    end = datetime(value.year + 1, 1, 1, tzinfo=UTC)
    return int(start.timestamp()), int(end.timestamp()), str(value.year)


def _market_values(row):
    return row.open, row.high, row.low, row.close, row.volume


class HistoryWorker:
    """Fetch one provider page at a time; SQLite stores job/index metadata only."""

    def __init__(self, repo, settings, providers=None, archive=None):
        self.repo = repo
        self.settings = settings
        self.archive = archive or Archive(repo, settings)
        provider_settings = replace(
            settings, requests_per_minute=settings.history_requests_per_minute
        )
        self.providers = providers or Providers(provider_settings)
        self.policy = Worker(
            repo,
            settings,
            providers=self.providers,
            archive=self.archive,
            redis_client=False,
        )
        self.history = History(repo, self.archive, settings)
        self.owner = uuid.uuid4().hex
        self.configuration = []
        self.scope = (None, None, None)
        self._next_catalog_refresh = 0.0
        self._next_archive_tick = 0.0
        self._maintenance = None
        self._jobs_initialized = False
        self._lease_lost = None
        self._next_manifest_retry = 0.0

    def _configure(self):
        source, symbols, interval = self.scope
        entries = self.policy.load_watchlist()
        self.configuration = [
            entry
            | {"intervals": [iv for iv in entry["intervals"] if not interval or iv == interval]}
            for entry in entries
            if (not source or entry["source"] == source)
            and (not symbols or entry["symbol"] in symbols)
        ]
        self.configuration = [entry for entry in self.configuration if entry["intervals"]]
        if not self.configuration:
            raise DataError("No configured watchlist entries match history-worker filters", 400)
        self._next_catalog_refresh = time.monotonic() + 15 * 60

    def _expected_provider(self, source, symbol, interval, active):
        if source == "vn":
            # Include the configured VCI minute-history fallback when checking
            # imported active bases; the cold cursor is older than its 7-day
            # eligibility boundary even though this check runs at startup.
            before = int(time.time()) - 8 * 86400
            supported = set(vn_provider_order(self.settings, interval, before, symbol))
            return active in supported
        return active == {"crypto": "binance", "yahoo": "yahoo", "sjc": "sjc-quote"}.get(
            source
        )

    def _queue_provider_activation(self, entry, interval, state):
        source, symbol = entry["source"], entry["symbol"]
        active = state["provider"]
        if self._expected_provider(source, symbol, interval, active):
            return False
        candidates = (
            vn_provider_order(self.settings, interval, self.policy.floor(entry, interval), symbol)
            if source == "vn"
            else ["binance" if source == "crypto" else "yahoo" if source == "yahoo" else "sjc-quote"]
        )
        selected = next((provider for provider in candidates if provider != active), None)
        if selected is None:
            return False
        floor = self.policy.floor(entry, interval)
        target_start = self._target_start(floor, entry)
        self.repo.configure_history_job(
            source,
            symbol,
            interval,
            floor,
            target_start,
            active,
            state["revision"],
        )
        self.repo.set_history_job_status(
            source,
            symbol,
            interval,
            "waiting_series",
            f"Active provider {active} is not a historical adapter; live repair queued on {selected}",
        )
        self.repo.queue(source, symbol, interval, "repair", floor, selected)
        self.repo.finding(
            source,
            symbol,
            interval,
            "history_provider_activation_pending",
            f"Historical provider {active} is unsupported; live series repair queued on {selected}",
        )
        log.warning(
            "history provider activation queued source=%s symbol=%s interval=%s from=%s to=%s",
            source,
            symbol,
            interval,
            active,
            selected,
        )
        return True

    def _target_start(self, hot_cutoff, entry):
        target = _shift_years(hot_cutoff, -self.settings.history_years)
        listing = entry.get("history_start")
        listing_source = entry.get("history_start_source")
        if listing:
            if not isinstance(listing_source, str) or not listing_source.startswith("https://"):
                raise DataError("Configured history_start requires an HTTPS evidence source")
            target = max(target, parse_time(listing))
        return target

    def _archive_repair_pending(self, source, symbol, interval):
        return any(
            obj["status"] == "pending_repair"
            for obj in self.repo.archives(source, symbol, interval)
        )

    def _job_basis_is_current(self, job):
        state = self.repo.state(job["source"], job["symbol"], job["interval"])
        return bool(
            state
            and state["status"] == "ready"
            and (state["provider"], state["revision"])
            == (job["provider"], job["revision"])
        )

    def _job_can_continue(self, job):
        if not self._job_basis_is_current(job):
            self.repo.set_history_job_status(
                job["source"], job["symbol"], job["interval"], "waiting_series",
                "Live series provider/revision changed; waiting for a fresh history cursor",
            )
            return False
        source, symbol, interval = job["source"], job["symbol"], job["interval"]
        rebase = self.repo.adjustment_rebase(source, symbol)
        if rebase and rebase["status"] not in ("complete", "failed"):
            if rebase["status"] != "cold_pending" or self.repo.adjustment_rebase_outputs_pending(
                source, symbol
            ):
                self.repo.set_history_job_status(
                    source, symbol, interval, "waiting_rebase",
                    f"Adjustment recovery is at stage {rebase['status']}",
                )
                return False
            self.repo.set_adjustment_rebase(source, symbol, "complete")
        if self._archive_repair_pending(source, symbol, interval):
            self.repo.set_history_job_status(
                source, symbol, interval, "waiting_archive_repair",
                "Existing Parquet objects are awaiting provider-basis repair",
            )
            return False
        if job["status"] in (
            "waiting_series",
            "waiting_rebase",
            "waiting_archive_repair",
        ):
            self.repo.set_history_job_status(source, symbol, interval, "pending")
        return True

    def _ensure_jobs(self):
        for entry in self.configuration:
            source, symbol = entry["source"], entry["symbol"]
            for iv in entry["intervals"]:
                state = self.repo.state(source, symbol, iv)
                existing = self.repo.history_job(source, symbol, iv)
                if not state or state["status"] != "ready":
                    if existing:
                        self.repo.set_history_job_status(
                            source, symbol, iv, "waiting_series", "Live series is not ready"
                        )
                    continue
                if self._queue_provider_activation(entry, iv, state):
                    continue
                rebase = self.repo.adjustment_rebase(source, symbol)
                if rebase and rebase["status"] not in ("complete", "failed"):
                    if rebase["status"] != "cold_pending":
                        if existing:
                            self.repo.set_history_job_status(
                                source,
                                symbol,
                                iv,
                                "waiting_rebase",
                                f"Adjustment recovery is at stage {rebase['status']}",
                            )
                        continue
                    if self.repo.adjustment_rebase_outputs_pending(source, symbol):
                        if existing:
                            self.repo.set_history_job_status(
                                source,
                                symbol,
                                iv,
                                "waiting_rebase",
                                "Hot intervals were rebuilt; corrected Parquet or public CSV objects are still pending",
                            )
                        continue
                    self.repo.set_adjustment_rebase(source, symbol, "complete")
                    log.info("adjustment rebase complete source=%s symbol=%s", source, symbol)
                if self._archive_repair_pending(source, symbol, iv):
                    if existing:
                        self.repo.set_history_job_status(
                            source,
                            symbol,
                            iv,
                            "waiting_archive_repair",
                            "Existing Parquet objects are awaiting provider-basis repair",
                        )
                    continue
                hot_floor = self.policy.floor(entry, iv)
                target_start = self._target_start(hot_floor, entry)
                hot_cutoff = hot_floor
                if source == "sjc" and iv == "1D":
                    # SJC's live worker retains only its latest quote in SQLite.
                    # Archive everything before that quote, including the years
                    # that the normal three-year hot window would otherwise skip.
                    latest = self.repo.read(source, symbol, iv, limit=1)
                    if latest:
                        hot_cutoff = latest[-1].time
                job = self.repo.configure_history_job(
                    source,
                    symbol,
                    iv,
                    hot_cutoff,
                    target_start,
                    state["provider"],
                    state["revision"],
                )
                if source == "sjc" and iv == "1D" and hot_cutoff > job["cursor"]:
                    if self.repo.advance_unstarted_history_cursor(
                        source, symbol, iv, hot_cutoff
                    ):
                        job = self.repo.history_job(source, symbol, iv)
                        log.info(
                            "SJC history cursor advanced to latest SQLite quote "
                            "symbol=%s hot_cutoff=%s target_start=%s",
                            symbol,
                            hot_cutoff,
                            target_start,
                        )
                if job["status"] in (
                    "waiting_series",
                    "waiting_rebase",
                    "waiting_archive_repair",
                ):
                    self.repo.set_history_job_status(source, symbol, iv, "pending")
                    job = self.repo.history_job(source, symbol, iv)
                if source == "yahoo" and iv in ("1h", "1m"):
                    # The live policy already moves these hot cutoffs to Yahoo's
                    # supported boundary. Do not retry a known-unavailable decade.
                    self.repo.set_history_job_status(
                        source,
                        symbol,
                        iv,
                        "provider_limit",
                        "Yahoo intraday history does not extend before its configured hot cutoff",
                    )
                elif job["cursor"] <= job["target_start"]:
                    self.repo.set_history_job_status(source, symbol, iv, "complete")

    def _route_delta(self, before):
        after = self.providers.provider_route_attempt_counts
        return {
            f"{provider}:{route}": count - before.get((provider, route), 0)
            for (provider, route), count in after.items()
            if count - before.get((provider, route), 0) > 0
        }

    def _provider_delta(self, before):
        after = self.providers.provider_attempt_counts
        return {
            provider: count - before.get(provider, 0)
            for provider, count in after.items()
            if count - before.get(provider, 0) > 0
        }

    def _page_size(self, source, iv):
        if source == "sjc":
            return 29
        if source == "crypto" and iv == "1m":
            # Uses Binance's verified monthly archive when available; falls back
            # to the exchange's bounded REST page when that month is absent.
            return BINANCE_MINUTE_PAGE_SIZE
        return self.settings.history_page_size

    def _page_start(self, source):
        return source in ("vn", "yahoo")

    def _read(self, source, symbol, iv, start, end, revision):
        return self.history.read(
            source,
            symbol,
            iv,
            start=start,
            end=end,
            revision=revision,
            forward=True,
            ignore_history_gaps=True,
        )

    async def _prepare_rows(self, job, rows):
        if not rows:
            return []
        existing = await asyncio.to_thread(
            self._read,
            job["source"],
            job["symbol"],
            job["interval"],
            rows[0].time,
            rows[-1].time,
            job["revision"],
        )
        existing_by_time = {row.time: row for row in existing}
        state = self.repo.state(job["source"], job["symbol"], job["interval"])
        snapshot_provider = self.repo.snapshot_provider(state) if state else None
        accepted_providers = {job["provider"]}
        if snapshot_provider:
            accepted_providers.add(snapshot_provider)
        new_rows = []
        divergences = []
        for row in rows:
            previous = existing_by_time.get(row.time)
            if previous is None:
                new_rows.append(row)
                continue
            if (
                previous.provider not in accepted_providers
                or previous.revision != row.revision
                or _market_values(previous) != _market_values(row)
            ):
                ratio = previous.close / row.close if row.close > 0 else None
                divergences.append(
                    {
                        "time": row.time,
                        "stored_close": previous.close,
                        "provider_close": row.close,
                        "close_ratio": ratio,
                    }
                )
        if divergences:
            raise HistoricalBasisMismatch(
                {
                    "detector": "cold_history_overlap_v1",
                    "interval": job["interval"],
                    "provider": job["provider"],
                    "revision": job["revision"],
                    "divergent_bars": len(divergences),
                    "divergences": divergences[:20],
                    "classification": "historical revision; adjustment cause unverified",
                }
            )
        objects = []
        limit = self.settings.history_batch_rows
        for offset in range(0, len(new_rows), limit):
            obj = await asyncio.to_thread(
                self.archive.prepare, new_rows[offset : offset + limit]
            )
            objects.append(obj)
            log.info(
                "history parquet prepared job_id=%s source=%s symbol=%s interval=%s "
                "key=%s rows=%s bytes_checksum=%s",
                job["job_id"],
                job["source"],
                job["symbol"],
                job["interval"],
                obj["object_key"],
                obj["row_count"],
                obj["checksum"],
            )
        return objects, len(new_rows)

    async def _handoff_vn_provider(self, job):
        if job["source"] != "vn" or job["attempts"] + 1 < HANDOFF_FAILURES:
            return False
        before = job.get("batch_cursor") or job["cursor"]
        live_floor = self.policy.floor(
            next(
                entry
                for entry in self.configuration
                if entry["source"] == job["source"] and entry["symbol"] == job["symbol"]
            ),
            job["interval"],
        )
        target_floor_probe = min(before, job["target_start"] + 31 * 86400)
        for alternate in vn_provider_order(
            self.settings, job["interval"], before, job["symbol"]
        ):
            if alternate == job["provider"]:
                continue
            try:
                sample = await self.providers.page(
                    "vn",
                    job["symbol"],
                    job["interval"],
                    before,
                    count=3,
                    provider=alternate,
                    start=job["target_start"],
                )
                floor_sample = await self.providers.page(
                    "vn",
                    job["symbol"],
                    job["interval"],
                    target_floor_probe,
                    count=3,
                    provider=alternate,
                    start=job["target_start"],
                )
                if not sample.rows or not floor_sample.rows:
                    continue
                self.repo.queue(
                    "vn",
                    job["symbol"],
                    job["interval"],
                    "repair",
                    live_floor,
                    alternate,
                )
                self.repo.finding(
                    "vn",
                    job["symbol"],
                    job["interval"],
                    "provider_handoff_pending",
                    f"Historical provider {job['provider']} failed; live series repair queued on {alternate}",
                )
                log.warning(
                    "history provider handoff queued job_id=%s source=vn symbol=%s interval=%s "
                    "from=%s to=%s; waiting for the live repair to activate a coherent basis",
                    job["job_id"],
                    job["symbol"],
                    job["interval"],
                    job["provider"],
                    alternate,
                )
                return True
            except Exception:
                continue
        return False

    async def _fetch_page(self, job, batch_start, batch_end, batch_cursor):
        route_counts = Counter(self.providers.provider_route_attempt_counts)
        provider_counts = Counter(self.providers.provider_attempt_counts)
        started = time.perf_counter()
        kwargs = {"start": batch_start} if self._page_start(job["source"]) else {}
        try:
            page = await self.providers.page(
                job["source"],
                job["symbol"],
                job["interval"],
                before=batch_cursor,
                count=self._page_size(job["source"], job["interval"]),
                provider=job["provider"],
                **kwargs,
            )
        except Exception as exc:
            raise HistoricalProviderError(str(exc)) from exc
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        if page.provider != job["provider"]:
            raise DataError("Provider changed during a pinned historical series")
        oldest = page.cursor if page.cursor is not None else (page.rows[0].time if page.rows else None)
        if oldest is None or not page.rows:
            raise HistoricalProviderError(
                f"Provider returned no candles before {batch_cursor}; coverage remains unverified"
            )
        if oldest >= batch_cursor:
            raise HistoricalProviderError("Historical provider cursor did not move backwards")
        rows = [
            replace(row, revision=job["revision"])
            for row in page.rows
            if batch_start <= row.time < batch_end and row.time < batch_cursor
        ]
        if job["source"] == "crypto":
            step = {"1D": 86400, "1h": 3600, "1m": 60}[job["interval"]]
            expected = batch_cursor - step
            if expected >= batch_start and (not rows or max(row.time for row in rows) != expected):
                raise HistoricalProviderError(
                    f"Crypto historical coverage remains unverified before {batch_cursor}"
                )
        if rows:
            prepared, prepared_rows = await self._prepare_rows(job, rows)
        else:
            prepared, prepared_rows = [], 0
        next_cursor = batch_start if oldest <= batch_start else oldest
        self.repo.checkpoint_history_page(
            job,
            batch_start=batch_start,
            batch_end=batch_end,
            batch_cursor=next_cursor,
            objects=prepared,
            row_count=prepared_rows,
        )
        log.info(
            "history page job_id=%s source=%s symbol=%s interval=%s provider=%s "
            "provider_attempts=%s route_attempts=%s before=%s oldest=%s next_cursor=%s target_start=%s "
            "rows_received=%s rows_accepted=%s new_rows=%s prepared_objects=%s elapsed_ms=%s",
            job["job_id"],
            job["source"],
            job["symbol"],
            job["interval"],
            page.provider,
            json.dumps(self._provider_delta(provider_counts), sort_keys=True),
            json.dumps(self._route_delta(route_counts), sort_keys=True),
            batch_cursor,
            oldest,
            next_cursor,
            job["target_start"],
            len(page.rows),
            len(rows),
            prepared_rows,
            len(prepared),
            elapsed_ms,
        )

    async def _publish_parquet_batch(self, job):
        objects = self.repo.history_pending_objects(
            job["source"], job["symbol"], job["interval"], job["batch_start"]
        )
        if objects:
            await asyncio.to_thread(
                self.archive.publish_prepared, objects, require_current=True
            )
        rows = await asyncio.to_thread(
            self._read,
            job["source"],
            job["symbol"],
            job["interval"],
            job["batch_start"],
            job["batch_end"] - 1,
            job["revision"],
        )
        if not rows:
            reason = (
                f"No provider/archive candles found in cold interval "
                f"[{job['batch_start']},{job['batch_end']}); skipped without creating an empty object"
            )
            self.repo.finding(
                job["source"], job["symbol"], job["interval"], "history_coverage_gap", reason
            )
            self.repo.finish_history_batch(
                job["source"], job["symbol"], job["interval"], job["batch_start"]
            )
            log.warning(
                "history partition empty; cursor advanced job_id=%s source=%s symbol=%s "
                "interval=%s start=%s end=%s",
                job["job_id"],
                job["source"],
                job["symbol"],
                job["interval"],
                job["batch_start"],
                job["batch_end"],
            )
            return
        keys = []
        for day in sorted({datetime.fromtimestamp(row.time, UTC).date().isoformat() for row in rows}):
            keys.append({"kind": "day", "value": day})
        period_start, _period_end, _label = _partition(job["batch_end"], job["interval"])
        if job["interval"] in ("1D", "1h") and job["batch_start"] == period_start:
            year = datetime.fromtimestamp(period_start, UTC).year
            keys.append({"kind": "year", "value": year})
        if not self.settings.public_export_enabled or not isinstance(self.archive.store, S3Store):
            keys = []
        self.repo.mark_history_batch_csv(
            job["source"], job["symbol"], job["interval"], keys
        )
        log.info(
            "history parquet batch published job_id=%s source=%s symbol=%s interval=%s "
            "provider=%s start=%s end=%s rows=%s objects=%s csv_objects=%s",
            job["job_id"],
            job["source"],
            job["symbol"],
            job["interval"],
            job["provider"],
            job["batch_start"],
            job["batch_end"],
            len(rows),
            len(objects),
            len(keys),
        )

    def _public_candles(self, job, key_spec):
        if key_spec["kind"] == "day":
            from datetime import date

            day = date.fromisoformat(key_spec["value"])
            start, end = period_bounds(day=day)
            rows = self._read(
                job["source"],
                job["symbol"],
                job["interval"],
                start,
                end,
                job["revision"],
            )
            return plan_candles(rows, job["source"], job["symbol"], job["interval"], day=day)
        year = int(key_spec["value"])
        start, end = period_bounds(year=year)
        rows = self._read(
            job["source"],
            job["symbol"],
            job["interval"],
            start,
            end,
            job["revision"],
        )
        return plan_candles(rows, job["source"], job["symbol"], job["interval"], year=year)

    async def _publish_next_csv(self, job):
        keys = json.loads(job["csv_keys"])
        if job["csv_cursor"] >= len(keys):
            self.repo.finish_history_batch(
                job["source"], job["symbol"], job["interval"], job["batch_start"]
            )
            log.info(
                "history batch complete job_id=%s source=%s symbol=%s interval=%s "
                "cursor=%s target_start=%s",
                job["job_id"],
                job["source"],
                job["symbol"],
                job["interval"],
                job["batch_start"],
                job["target_start"],
            )
            return
        key_spec = keys[job["csv_cursor"]]
        if key_spec["kind"] == "day":
            from datetime import date

            public_key = daily_key(
                job["source"],
                job["symbol"],
                job["interval"],
                date.fromisoformat(key_spec["value"]),
            )
        else:
            public_key = yearly_key(
                job["source"], job["symbol"], job["interval"], int(key_spec["value"])
            )
        def publish_locked():
            with self.archive.public_csv_lock(public_key):
                if not self._job_basis_is_current(job):
                    raise DataError("History CSV basis changed before publication")
                item = self._public_candles(job, key_spec)
                if item.key != public_key:
                    raise DataError("Historical CSV planner produced an unexpected object key")
                result = publish_object(
                    self.archive.store.client,
                    self.settings.s3_bucket,
                    item,
                )
                return item, result

        item, result = await asyncio.to_thread(publish_locked)
        if not result.get("verified"):
            raise DataError(f"Historical CSV object failed verification: {item.key}")
        self.repo.advance_history_csv(
            job["source"], job["symbol"], job["interval"], job["csv_cursor"] + 1
        )
        log.info(
            "history csv published job_id=%s source=%s symbol=%s interval=%s key=%s "
            "rows=%s bytes=%s uploaded=%s verified=%s",
            job["job_id"],
            job["source"],
            job["symbol"],
            job["interval"],
            item.key,
            item.rows,
            len(item.body),
            result["uploaded"],
            result["verified"],
        )

    async def _record_failure(self, job, exc, stage, *, provider_failure=False):
        coverage_gap = (
            "coverage remains unverified" in str(exc).lower()
            or "no candles" in str(exc).lower()
        )
        if provider_failure and await self._handoff_vn_provider(job):
            self.repo.set_history_job_status(
                job["source"],
                job["symbol"],
                job["interval"],
                "waiting_series",
                "Waiting for the live provider-basis repair before restarting cold history",
            )
            return
        terminal_gap = coverage_gap and (
            job["source"] != "vn" or job["attempts"] + 1 >= HANDOFF_FAILURES
        )
        retry_at = int(time.time()) + (
            COVERAGE_RECHECK_SECONDS if terminal_gap else RETRY_SECONDS
        )
        self.repo.fail_history_job(
            job["source"],
            job["symbol"],
            job["interval"],
            exc,
            retry_at,
            blocked=terminal_gap,
        )
        log.warning(
            "history step failed stage=%s job_id=%s source=%s symbol=%s interval=%s "
            "provider=%s before=%s target_start=%s retry_at=%s reason=%s",
            stage,
            job["job_id"],
            job["source"],
            job["symbol"],
            job["interval"],
            job["provider"],
            job.get("batch_cursor") or job["cursor"],
            job["target_start"],
            retry_at,
            str(exc),
            exc_info=not isinstance(exc, (DataError, HistoricalProviderError)),
        )

    def _queue_cold_adjustment_rebase(self, job, evidence):
        source, symbol = job["source"], job["symbol"]
        self.repo.begin_adjustment_rebase(source, symbol, evidence)
        entry = next(
            (
                item
                for item in self.configuration
                if item["source"] == source and item["symbol"] == symbol
            ),
            {"source": source, "symbol": symbol, "intervals": []},
        )
        statuses = {"1D": "daily_pending", "1h": "hourly_pending", "1m": "minute_pending"}
        for interval in ("1D", "1h", "1m"):
            state = self.repo.state(source, symbol, interval)
            if not state or state["status"] != "ready":
                continue
            self.repo.queue(
                source,
                symbol,
                interval,
                "repair",
                self.policy.floor(entry, interval),
                state["provider"],
            )
            self.repo.set_adjustment_rebase(source, symbol, statuses[interval])
            self.repo.finding(
                source,
                symbol,
                interval,
                "cold_history_revision",
                json.dumps(evidence, sort_keys=True, separators=(",", ":")),
            )
            log.warning(
                "cold history revision queued hot rebase source=%s symbol=%s interval=%s provider=%s",
                source,
                symbol,
                interval,
                state["provider"],
            )
            return True
        self.repo.set_adjustment_rebase(source, symbol, "failed", "No ready interval is available for rebase")
        return False

    async def step(self):
        if not self._jobs_initialized:
            self._ensure_jobs()
            self._jobs_initialized = True
        now = int(time.time())
        jobs = [
            job
            for job in self.repo.history_jobs()
            if job["status"] in ("pending", "retry", "coverage_gap") and job["retry_at"] <= now
        ]
        if not jobs:
            return False
        # There is one SJC series, but it covers a long gap between the latest
        # live quote and the historical cutoff. Let it drain before the broad
        # multi-market archive queue.
        job = next((item for item in jobs if item["source"] == "sjc"), jobs[0])
        if not self._job_can_continue(job):
            return True
        if job["batch_status"] == "csv":
            try:
                await self._publish_next_csv(job)
            except Exception as exc:
                await self._record_failure(job, exc, "csv_publish")
            return True
        if job["batch_start"] is None:
            if job["cursor"] <= job["target_start"]:
                self.repo.set_history_job_status(
                    job["source"], job["symbol"], job["interval"], "complete"
                )
                return True
            partition_start, _partition_end, _label = _partition(job["cursor"], job["interval"])
            batch_start = max(job["target_start"], partition_start)
            batch_end = job["cursor"]
            batch_cursor = job["cursor"]
        else:
            batch_start = job["batch_start"]
            batch_end = job["batch_end"]
            batch_cursor = job["batch_cursor"]
        if batch_cursor <= batch_start:
            try:
                await self._publish_parquet_batch(job)
            except Exception as exc:
                await self._record_failure(job, exc, "parquet_publish")
            return True
        try:
            await self._fetch_page(job, batch_start, batch_end, batch_cursor)
        except Exception as exc:
            if isinstance(exc, HistoricalBasisMismatch):
                if self._queue_cold_adjustment_rebase(job, exc.evidence):
                    self.repo.set_history_job_status(
                        job["source"], job["symbol"], job["interval"], "waiting_rebase",
                        "Cold overlap changed; waiting for an ordered hot and archive rebase",
                    )
                    return True
            await self._record_failure(
                job,
                exc,
                "provider_fetch" if isinstance(exc, HistoricalProviderError) else "page_prepare",
                provider_failure=isinstance(exc, HistoricalProviderError),
            )
            return True
        return True

    async def _tick_retention_archive(self):
        if time.monotonic() < self._next_archive_tick:
            return
        if self._maintenance is None:
            self._maintenance = DailyArchive(
                self.archive,
                self.scope[0],
                sorted(self.scope[1]) if self.scope[1] else None,
                self.scope[2],
            )
        # DailyArchive takes one eligibility snapshot and publishes its groups
        # sequentially. Rebuilding that entire snapshot once per object would
        # repeatedly scan every retained ticker and interval.
        result = await asyncio.to_thread(self._maintenance.tick, max_objects=1)
        if result and not result["complete"]:
            log.info(
                "history retention archive progress day=%s objects=%s rows=%s complete=false",
                result["day"],
                result["objects"],
                result["rows"],
            )
        self._next_archive_tick = time.monotonic() + 60

    async def _heartbeat_lease(self):
        while True:
            await asyncio.sleep(60)
            renewed = await asyncio.to_thread(
                self.repo.renew_live_lease,
                *HISTORY_LEASE,
                self.owner,
                lease=HISTORY_LEASE_SECONDS,
            )
            if not renewed:
                self._lease_lost.set()
                log.error("history worker single-flight lease was lost owner=%s", self.owner)
                return

    async def run(self, once=False, cycles=None, source=None, symbols=None, interval=None):
        if cycles is not None and cycles < 1:
            raise DataError("History worker cycles must be positive", 400)
        self.scope = (source, {value.upper() for value in symbols} if symbols else None, interval)
        self._configure()
        if not self.repo.live_claim(
            *HISTORY_LEASE, self.owner, lease=HISTORY_LEASE_SECONDS
        ):
            raise DataError("Another historical worker owns the single-flight lease")
        self._lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat_lease())
        count = 0
        try:
            log.info(
                "history worker started owner=%s series=%s source=%s symbols=%s interval=%s "
                "years=%s page_size=%s provider_rpm=%s",
                self.owner,
                sum(len(entry["intervals"]) for entry in self.configuration),
                source or "all",
                len(symbols) if symbols else "all",
                interval or "all",
                self.settings.history_years,
                self.settings.history_page_size,
                self.settings.history_requests_per_minute,
            )
            while True:
                if self._lease_lost.is_set() or not self.repo.renew_live_lease(
                    *HISTORY_LEASE, self.owner, lease=HISTORY_LEASE_SECONDS
                ):
                    raise DataError("Historical worker lost its single-flight lease")
                if (
                    time.monotonic() >= self._next_manifest_retry
                    and self.repo.archive_manifest_is_dirty()
                ):
                    self._next_manifest_retry = time.monotonic() + 60
                    try:
                        await asyncio.to_thread(self.archive.publish_metadata)
                    except Exception as exc:
                        log.warning(
                            "archive manifest dirty; history-worker retry failed error=%s",
                            type(exc).__name__,
                        )
                if time.monotonic() >= self._next_catalog_refresh:
                    self._configure()
                    self._ensure_jobs()
                    self._jobs_initialized = True
                progressed = await self.step()
                await self._tick_retention_archive()
                if progressed:
                    count += 1
                    if once or cycles and count >= cycles:
                        break
                    await asyncio.sleep(self.settings.history_pause_seconds)
                elif once or cycles:
                    break
                else:
                    await asyncio.sleep(30)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            try:
                await self.providers.close()
            finally:
                self.repo.live_release(*HISTORY_LEASE, self.owner)
                log.info("history worker stopped owner=%s", self.owner)

