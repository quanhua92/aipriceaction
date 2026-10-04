import asyncio
import json
import logging
import math
import time
import uuid
from dataclasses import replace

from .domain import DataError, completed_vn_sessions, cutoff
from .providers import Providers, adjustment_changes, vn_provider_order
from .quality import audit as audit  # Re-export for the existing operational CLI.

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, repo, settings, providers=None, archive=None):
        self.repo, self.settings = repo, settings
        self.providers = providers or Providers(settings)
        self.archive = archive
        self.owner = uuid.uuid4().hex
        self.configuration = []
        self.deadline = 90  # Keep network operations shorter than durable leases.

    def load_watchlist(self):
        raw = json.loads(self.settings.watchlist.read_text())
        entries = []
        for source, items in raw.items():
            if source not in ("vn", "crypto", "yahoo", "sjc"):
                raise DataError("Unknown source in watchlist", 400)
            for item in items:
                entry = {"symbol": item} if isinstance(item, str) else dict(item)
                symbol = entry["symbol"].upper()
                intervals = entry.get(
                    "intervals", ["1D"] if source in ("sjc", "yahoo") else ["1D", "1h", "1m"]
                )
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
        # Explicit listing/history start prevents invented pre-listing gaps.
        if entry.get("history_start"):
            result = max(result, parse_time(entry["history_start"]))
        return result

    def bootstrap(self):
        jobs = []
        for entry in self.configuration or self.load_watchlist():
            for iv in entry["intervals"]:
                state = self.repo.state(entry["source"], entry["symbol"], iv)
                if state:
                    self.repo.cancel_superseded_bootstrap(
                        entry["source"], entry["symbol"], iv, self.floor(entry, iv)
                    )
                else:
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
            self.repo.finding(
                job["source"], job["symbol"], job["interval"], "incomplete_repair", reason
            )
            self.repo.fail_job(job, reason)
            return 0

    async def _repair_page(self, job):
        before = job["cursor"] or int(time.time()) + 1
        try:
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
                return self.finish_recent_job(job)
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
            oldest = (
                page.cursor
                if page.cursor is not None
                else (page.rows[0].time if page.rows else None)
            )
            if self.at_verified_listing_prefix(job, entry, page):
                return self.finish_recent_job(job)
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
            if (
                job["kind"] == "bootstrap"
                and self.repo.state(job["source"], job["symbol"], job["interval"]) is None
                and page.provider != "vci"
            ):
                # Publish the first validated recent page promptly, without
                # claiming that the requested history floor has been reached.
                self.repo.put([r for r in prepared if r.time >= job["floor"]])
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
                self.finish_recent_job(claimed)
            return len(prepared)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, DataError) else type(exc).__name__
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
                for alternate in vn_provider_order(self.settings, job["interval"], before):
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

    def finish_recent_job(self, job):
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
        self.repo.finish_job(job)
        self.repo.schedule(job["source"], job["symbol"], job["interval"], int(time.time()) + 60)
        return 0

    async def sync(self, entry, iv):
        try:
            return await asyncio.wait_for(self._sync(entry, iv), timeout=self.deadline)
        except TimeoutError:
            self.repo.finding(
                entry["source"],
                entry["symbol"],
                iv,
                "provider_failure",
                "Provider update exceeded worker time budget",
            )
            return 0

    async def _sync(self, entry, iv):
        started = asyncio.get_running_loop().time()
        source, symbol = entry["source"], entry["symbol"]
        if not self.repo.live_claim(source, symbol, iv, self.owner):
            return 0
        attempt = None
        try:
            state = self.repo.state(source, symbol, iv)
            if not state:
                self.repo.queue(source, symbol, iv, "bootstrap", self.floor(entry, iv))
                return 0
            if state["status"] != "ready":
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
            count = 40
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
                for alternate in self.settings.vn_providers:
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
            if page.provider != state["provider"] or adjustment_changes(
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
            rows = [
                replace(r, revision=state["revision"])
                for r in page.rows
                if r.time >= self.floor(entry, iv)
            ]
            if not rows:
                raise DataError("Provider has no candles inside the retained window")
            self.repo.record_volume_proofs(getattr(page, "volume_proofs", ()))
            self.repo.put(rows, verification={"attempt_ns": attempt, "completed_before": completed})
            # The recent observation and candles are committed together. A later
            # optional historical probe must not rewrite that successful result,
            # including when the worker is cancelled during the probe.
            attempt = None
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
            reason = str(exc) if isinstance(exc, DataError) else type(exc).__name__
            if attempt is not None:
                self.repo.fail_source_check(source, symbol, iv, attempt, reason)
            self.repo.finding(source, symbol, iv, "provider_failure", reason)
            return 0
        except asyncio.CancelledError:
            if attempt is not None:
                self.repo.fail_source_check(
                    source, symbol, iv, attempt, "Provider update interrupted or timed out"
                )
            raise
        finally:
            # Bounded cooldown also prevents one unavailable provider monopolizing
            # the loop. Other tickers remain independently scheduled.
            self.repo.schedule(
                source, symbol, iv, int(time.time()) + {"1D": 3600, "1h": 900, "1m": 60}[iv]
            )
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
            for key in ("open", "high", "low", "close")
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

    async def archive_repair(self, source=None, symbol=None, interval=None, restart_failed=False):
        """Repair one affected archive at a time, pinned to current provider.

        Never apply an inferred dividend factor. Acquire/validate exact source
        candles; inaccessible old upstream data stays explicitly pending.
        """
        if self.archive is None:
            return 0
        if restart_failed and not all((source, symbol, interval)):
            raise DataError("Restart requires a source, symbol, and native interval", 400)
        self.repo.retire_archive_jobs(source, symbol, interval)
        pending = [
            obj
            for obj in self.repo.archives(source, symbol, interval)
            if obj["status"] == "pending_repair"
        ]
        for obj in sorted(pending, key=lambda r: (r["end"], r["symbol"]), reverse=True):
            state = self.repo.state(obj["source"], obj["symbol"], obj["interval"])
            if not state or state["status"] != "ready":
                continue
            job_id = self.repo.queue(
                obj["source"],
                obj["symbol"],
                obj["interval"],
                f"archive_repair:{obj['id']}:{state['revision']}",
                obj["start"],
                state["provider"],
            )
            # Each immutable object/revision has an independent resumable job.
            with self.repo.connect() as con:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                if row["lease_until"] > int(time.time()):
                    continue
                if restart_failed and row["error"]:
                    # Explicitly restart a failed candidate, never published data.
                    # A live owner still wins; default retries retain completed
                    # downloads when only their head/publication check failed.
                    con.execute("DELETE FROM staging WHERE job_id=?", (job_id,))
                    con.execute(
                        "UPDATE jobs SET cursor=NULL,retry_at=0,error=NULL WHERE id=?", (job_id,)
                    )
                    row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                elif row["retry_at"] > int(time.time()):
                    continue
                con.execute(
                    "UPDATE jobs SET lease_owner=?,lease_until=?,status='running' WHERE id=?",
                    (self.owner, int(time.time()) + 120, job_id),
                )
                job = dict(row) | {"lease_owner": self.owner}
            # Daily providers may timestamp the final market date at session
            # start (DNSE 02:00 UTC), rather than its normalized UTC midnight.
            # Include the entire final date in the first upstream request.
            before = job["cursor"] or obj["end"] + (86400 if obj["interval"] == "1D" else 1)
            try:
                rows = []
                covered = job["cursor"] is not None and job["cursor"] <= obj["start"]
                if not covered:
                    with self.repo.connect() as con:
                        downloaded = con.execute(
                            "SELECT COUNT(*) FROM staging WHERE job_id=?", (job_id,)
                        ).fetchone()[0]
                    # Request the remaining indexed coverage, rather than a
                    # large unrelated preceding range for a small partition.
                    # Extra provider dates still fail the exact-set check.
                    count = min(500, max(1, obj["row_count"] - downloaded))
                    page = await self.providers.page(
                        obj["source"],
                        obj["symbol"],
                        obj["interval"],
                        before,
                        count=count,
                        provider=state["provider"],
                    )
                    if not page.rows or page.rows[0].time >= before:
                        raise DataError("Archived adjusted history unavailable or non-advancing")
                    rows = [
                        replace(r, revision=state["revision"])
                        for r in page.rows
                        if obj["start"] <= r.time <= obj["end"]
                    ]
                    # Stable job revision isolates all staging pages. A failed
                    # publication/head check can reuse this completed download.
                    self.repo.record_volume_proofs(getattr(page, "volume_proofs", ()))
                    self.repo.stage(
                        job,
                        [replace(r, revision=job["revision"]) for r in rows],
                        page.rows[0].time,
                        state["provider"],
                    )
                    covered = page.rows[0].time <= obj["start"]
                if covered:
                    with self.repo.connect() as con:
                        staged = [
                            dict(r)
                            for r in con.execute(
                                "SELECT * FROM staging WHERE job_id=? ORDER BY time", (job_id,)
                            )
                        ]
                    from .domain import Candle

                    replacement = [
                        Candle(**{k: v for k, v in r.items() if k != "job_id"}) for r in staged
                    ]
                    replacement = [replace(r, revision=state["revision"]) for r in replacement]
                    old = await asyncio.to_thread(self.archive.read, obj)
                    if {r.time for r in replacement} != {r.time for r in old}:
                        raise DataError(
                            "Archived replacement timestamp coverage differs; manual quality review required"
                        )
                    await self.verify_archive_head(obj, state)
                    await asyncio.to_thread(self.archive.publish, replacement, False, obj["id"])
                    with self.repo.connect() as con:
                        con.execute("DELETE FROM staging WHERE job_id=?", (job_id,))
                        con.execute(
                            "UPDATE jobs SET status='complete',lease_owner=NULL,lease_until=0 WHERE id=?",
                            (job_id,),
                        )
                        if not con.execute(
                            "SELECT 1 FROM archives WHERE source=? AND symbol=? AND interval=? AND status IN ('published','pending_repair') AND (status='pending_repair' OR provider<>? OR revision<>?)",
                            (
                                obj["source"],
                                obj["symbol"],
                                obj["interval"],
                                state["provider"],
                                state["revision"],
                            ),
                        ).fetchone():
                            con.execute(
                                "UPDATE quality SET resolved=1 WHERE source=? AND symbol=? AND interval=? AND kind IN ('archive_repair_pending','imported_revision_boundary')",
                                (obj["source"], obj["symbol"], obj["interval"]),
                            )
                return len(rows)
            except Exception as exc:
                reason = str(exc) if isinstance(exc, DataError) else type(exc).__name__
                self.repo.finding(
                    obj["source"], obj["symbol"], obj["interval"], "archive_repair_pending", reason
                )
                self.repo.fail_job(job, reason)
                return 0
        return 0

    async def cycle(self):
        if not self.configuration:
            self.load_watchlist()
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
        due.sort(key=lambda item: (item[0], item[2] != "1D", item[1]["symbol"]))
        # Ordinary updates get a turn before one bounded recovery page.
        values = await asyncio.gather(
            *(self.sync(entry, iv) for _, entry, iv in due[: self.settings.worker_concurrency])
        )
        allowed = [
            (entry["source"], entry["symbol"], iv)
            for entry in self.configuration
            for iv in entry["intervals"]
            if (entry["source"], entry["symbol"]) in available
        ]
        job = self.repo.claim_job(self.owner, allowed=allowed)
        if job:
            values.append(await self.repair_page(job))
            log.info(
                "recovery source=%s symbol=%s interval=%s processed=%s",
                job["source"],
                job["symbol"],
                job["interval"],
                values[-1],
            )
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
                if not state or state["status"] != "ready":
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
        self, once=False, cycles=None, source=None, symbols=None, interval=None, archive_daily=False
    ):
        maintenance_task = None
        try:
            self.load_watchlist()
            self.configuration = [
                entry
                | {"intervals": [iv for iv in entry["intervals"] if not interval or iv == interval]}
                for entry in self.configuration
                if (not source or entry["source"] == source)
                and (not symbols or entry["symbol"] in symbols)
            ]
            self.configuration = [entry for entry in self.configuration if entry["intervals"]]
            if not self.configuration:
                raise DataError("No configured watchlist entries match worker filters", 400)
            self.bootstrap()
            maintenance = None
            if archive_daily:
                from .archive import Archive
                from .maintenance import DailyArchive

                maintenance = DailyArchive(
                    self.archive or Archive(self.repo, self.settings), source, symbols, interval
                )
            completed_cycles = 0
            while True:
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
