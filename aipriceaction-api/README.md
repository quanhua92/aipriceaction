# AIPriceAction API

FastAPI replacement for the Rust HTTP API, ingestion workers, and operational
CLI. Recent candles live in SQLite; older candles live in compressed Parquet in
S3-compatible storage. DuckDB reads verified, cached files. The existing Python
`aipa` analysis CLI and SDK remain available with their existing commands.

The implementation runs locally. [TODO.md](TODO.md) tracks the proposed commits
and remaining acceptance work. [VALIDATION.md](VALIDATION.md) records actual
checks, provider coverage limits, and compatibility differences. Production
cutover requires completing those remaining checks.

## Start locally

Use Python 3.13 or newer. From this directory:

```sh
uv sync
docker compose up -d rustfs
uv run aipa-api init --s3
uv run aipa-api serve
```

API: `http://127.0.0.1:3001`; explorer: `/explorer`; OpenAPI: `/docs`.
RustFS S3: `http://127.0.0.1:9100`; console:
`http://127.0.0.1:9101/rustfs/console/`. Local credentials are `aipa-local` and
`aipa-local-development-only`. The image digest is pinned to the tested build.
Compose contains RustFS only, with project-scoped named volumes and loopback
ports. SQLite, DuckDB, the API, and workers need no additional service.

Copy `.env.example` to `.env` to customize settings. Relative application paths
resolve inside this project. The API process starts independently of ingestion.

## Maintain selected tickers

Inspect actual retained windows and pending backfill work without changing data:

```sh
uv run python scripts/inventory_retained_windows.py --report data/selected-window-inventory.json
```

The report includes every selected published series, configured intervals,
missing configured states, local/archive bounds, staged rows and pending jobs.
`ready` means the series is readable; it does not establish complete retention
coverage. A later first date or pending job is an observation requiring review,
not proof of a missing trading session. Use a new report path to preserve prior
inventories.

`watchlist.json` selects 59 Vietnamese tickers/indexes, four cryptocurrencies,
seven global daily series, and SJC daily quotes. Ticker `VCI` is a stock symbol;
the VCI **data provider** is excluded. Exactly three VN adapters are implemented:
VPS, VNDirect, and DNSE. Daily/minute bootstrap starts with VPS; hourly starts
with DNSE because the live probe found deeper hourly coverage there. Additional
configured providers are tried on initial failures. A later provider switch
queues a complete retained-window replacement before adopting the new basis.

Normal VN minute/hourly updates start with a 40-candle overlap. When newer
provider data no longer overlaps the stored tail, workers retry once on the
same provider with a bounded page of up to 1,000 candles. A verified overlap
allows catch-up; absent overlap queues durable recovery and preserves the old
series. Trading breaks, holidays, and sparse stocks are not treated as missing
minutes from elapsed time alone. An expanded request failure also preserves
published data and records the dated provider error. Expanded updates compare
all stored candles covered by the larger page, so corroborated older price
changes trigger staged revision recovery before any catch-up is published.

OCB, PNJ, and DGC explicitly ingest daily and minute data. Their recent windows,
minute-provider handoffs, archived indicator lookback, and selected older daily
partitions are verified locally. NAB also ingests daily/minute data after its
recent windows and VPS minute handoff passed verification. Its daily window and
older archives now use one verified VNDirect revision; its recovered 2022 year
also supplies the checked early-2023 indicator lookback. Native hourly ingestion
is not enabled for these additions.
Detailed coverage limits and evidence are recorded in `VALIDATION.md`.

`aipa-api audit` also compares each completed observed VN minute session with
its daily OHLC. Differences above 1% are recorded as `audit_interval_basis`
review findings. This exposes upstream adjustment/session inconsistencies;
it does not infer a dividend factor, overwrite candles, or queue a replacement.
The latest audit flags ten stocks and two indices, so successful recent provider
handoffs must not be read as certification of the entire minute history.

Known unavailable historical ranges are recorded as `history_unavailable` quality
findings. `aipa-api status` exposes `history_gaps`; `/health` exposes
`storage.history_gaps`.
A read requiring one of those ranges returns HTTP 503 with an explicit reason;
recent reads with sufficient indicator lookback remain available. These records
survive SQLite backups and S3 index reconstruction. Only a complete verified
dated recovery clears its corresponding range. This prevents failed yearly
imports from silently appearing as empty or skipped history.

The retained VN daily audit identified **13 missing sessions** across seven
tickers. Complete VNDirect daily replacements for HAG, MSN, STB, VDS, and VPL
are now published locally after reconciling their readable older archives,
recovering **nine sessions**. Original candles and archives remain preserved
with immutable S3 before-images and receipts. EIB and HHS retain their existing
VPS bases: their VNDirect candidates contain invalid older OHLC and would lose
previously readable history. Their **four missing sessions** on May 22 / June 11,
2025 remain explicit HTTP 503 errors. HAG's 2021 archive is recovered; its invalid
2019 archive remains pending. Minute data and provider handoffs are unchanged.

CTR's recent daily window and six older partitions now share one verified
VNDirect revision. Its missing 2019 year and pending 2022 year are recovered
without dropping previously published dates. A July 15, 2026 volume disagreement
remains explicit: VNDirect reports 158,907 while original VPS/DNSE report 156,000.
Original data and provider replies remain preserved; no factor or override is
inferred. CTR minute data remains unchanged. See `VALIDATION.md` for the exact
coverage, SDK query-window checks, restores, and remaining bulk EMA limitations.

HCM's missing 2020 year is also recovered with all 252 original dates. Its recent
daily window and five older partitions share one verified VNDirect revision,
preserving all previously published dates. The one larger historical volume
change is independently corroborated by DNSE. Original candles remain preserved;
minute data and the existing unrelated gap/disagreement records remain unchanged.

VIB 2019 and VTP 2019/2022 are recovered on coherent VNDirect daily revisions;
VIB's pending 2020 year also becomes readable with every original date retained.
Three VTP volume disagreements remain explicit in SQLite and immutable S3
evidence, with original values and provider replies preserved. Their minute data
and handoffs remain unchanged.

NAB's missing 2022 year is now recovered with all 249 original dates on a coherent
VNDirect daily revision. Its minute rows remain exact, and archived indicator
lookback now serves the checked early-2023 requests. The remaining typed gaps
cover VND 2020 and four recent EIB/HHS sessions. VND's VNDirect/DNSE candidates
recover 2020 in isolation, but invalid/conflicting older candles prevent replacing
the existing readable archives. Original VND data remains intact; other pending
archive repairs and provider discrepancies remain open.

ACB and LPB's pending 2020 archives are also readable now, preserving all original
247/242 dates on coherent VNDirect revisions. Their original data and measured
historical price differences remain preserved; minute data and handoffs stay exact.

The DNSE adapter accepts VNINDEX's verified 09:15 ICT daily timestamp convention,
as well as its observed UTC-midnight/09:00 timestamps, without shifting market
dates. This support is scoped to DNSE VNINDEX. A native replacement still fails
when an existing completed date is missing; the isolated candidate omits August
12, 2024 and therefore does not replace the current readable series. Invalid
OHLC and conflicting daily duplicates remain rejected.

VN retained-window repairs validate only candles inside the configured window,
while retaining a separate backward cursor to establish the requested boundary.
Invalid candles and conflicting duplicates inside that window still reject the
repair; no-data alone never proves coverage. Both isolated EIB/HHS DNSE windows
now recover the four recent sessions, but their invalid/conflicting 2019/2022
archives still prevent publication without losing readable older history.

To reproduce the read-only whole-universe daily comparison:

```sh
uv run python scripts/check_retained_vn_daily.py
```

The runner opens SQLite read-only, preserves checksummed legacy responses, reports
date/price/volume discrepancies, and verifies unchanged local daily candles and
operational metadata. Existing response receipts are verified and reused, so a
captured comparison can be repeated offline. Use `--symbol` to bound the check
or `--as-of` with a UTC ISO timestamp to reproduce retention/session bounds.
Use `--captures` to compare a later local checkpoint against an existing frozen
capture directory while writing a separate `--report`, preserving the initial
audit. The latest comparison has matching observed date sets for 57 of 59 tickers.
Date parity is an observed-data comparison, not a verified exchange calendar.

`/tickers/name` preserves the existing symbol-to-name map and source modes. It
also discovers registered, imported, and archive-only identities outside the
packaged catalog. Existing catalog names take precedence; a known registered
name is retained, and an unknown name falls back to the symbol. Discovering a
ticker does not enable ingestion. Archive-index reconstruction restores ticker
identities; back up SQLite to preserve registered company names and sync records.

`/health.storage.series` includes archive-only series with `archived` or
`archive_pending` status, zero local rows, and no live-verification claim. Each
series separately exposes `archive_objects`, `archive_rows`,
`pending_archive_repairs`, `first_archived_candle_at`, and
`latest_archived_candle_at`. Bounds and row counts include published objects
only; superseded objects are excluded and pending repairs are counted separately.
`archive_rows` sums indexed object rows and can include overlapping fragments;
it is not a deduplicated candle count or proof of uninterrupted date coverage.
Existing local record counts, ingestion dates, and provider-check dates retain
their meanings. Archived bounds cannot be used as evidence of a recent update.

Use proxies in `HTTP_PROXIES`, or explicitly permit direct access. Run a worker
in another terminal:

```sh
uv run aipa-api --allow-direct worker
uv run aipa-api --allow-direct worker --archive-daily
```

`--archive-daily` opts into retention maintenance after ingestion cycles. It
publishes and verifies expired rows, then prunes matching exported versions once
per UTC day. `--source`, repeated `--symbol`, and `--interval` also scope archival
selection; within those filters, archival includes stored series while ingestion
remains watchlist-driven. Failed publication or concurrent corrections leave
local rows available and retry attempts are spaced at least 60 seconds apart.
Successful-day tracking is in memory; restarting harmlessly rechecks SQLite.
This needs no additional service. Keep the worker under your existing process
supervisor for continuous operation.

`SIGTERM` stops `worker` and `bootstrap` cooperatively. Provider connections
close, the exiting worker's live/job claims are released, and staged candles,
cursor progress and retry history stay intact. Other workers' leases and daily
sentinel leases remain owned. Wait for the process to exit before restarting:
an active archive transfer finishes its verified publication/pruning first.
Forceful termination continues to rely on durable lease expiry.

Rehearse configured native VN refreshes on an isolated populated copy before
expanding a live worker:

```sh
uv run python -m scripts.check_native_refresh --source vn --allow-direct --output data/vn-refresh-review
uv run python -m scripts.check_native_refresh --source yahoo --output data/global-refresh-review
```

This captures VPS/VNDirect/DNSE responses, checks native daily/hourly/minute
states and verifies protected records and archives. It does not publish the
candidate or process queued repairs. Frozen snapshots and both disputed index
hourly series remain excluded; nonconfigured intervals are reported separately.
Inspect recorded outcomes and queued quality findings before treating a recent
refresh as proof of complete historical coverage.
Run while the same-source worker is stopped for the strict canonical version
comparison; other source workers can continue. The previous `check_vn_refresh`
entry point remains available and defaults to VN.

For bounded runs and focused recovery:

```sh
uv run aipa-api --allow-direct bootstrap --source vn --symbol FPT --symbol VCB --interval 1D --cycles 6
uv run aipa-api status
uv run aipa-api quality
uv run aipa-api audit
uv run aipa-api --allow-direct probe FPT --provider dnse --interval 1h --count 3
uv run aipa-api reconcile --source vn --symbol FPT --interval 1D --provider vndirect
```

To recheck every ready series for a selected source/interval once, without
processing bootstrap or historical repair pages:

```sh
uv run aipa-api --allow-direct refresh --source vn --interval 1D
uv run aipa-api --allow-direct refresh --source vn --interval 1m --symbol FPT --symbol VCB
```

`refresh` uses the existing live-update validation, provider pinning, rate limits,
and leases. It ignores the ordinary schedule cooldown for this explicit request;
an active lease still prevents duplicate work. Missing or repairing series report
`not_ready`; leased series report `skipped`. Per-series outcomes distinguish a
successful recheck, a provider failure, a required minute handoff, and a queued
revision repair. Historical jobs remain queued for normal workers. The command
closes its provider connections after the bounded pass. Both source and native
interval are required; optional repeated symbols must belong to that watchlist.

`status` and `/health` include per-series date bounds and update-check records.
`storage.series` separates `last_ingest_at` from `last_provider_success_at`,
retains the last update attempt/result, and reports the returned completed
overlap bounds/counts and provisional rows. `verification_current` means the
record still matches the stored provider, revision, and write versions; its
timestamp tells you how old the proof is. It does not certify the whole retained
history. `latest_verification` is `completed_recheck`, `provisional_at_check`, or
`unverified`. Passing the completion cutoff without another provider read does
not finalize a provisional record. Imported snapshots remain unverified until
an actual published update succeeds. Frozen VN daily/minute snapshots report
`handoff_required` and do not initiate an unverified provider switch.
The additive SQLite schema upgrade is automatic on startup; backup/restore
preserves these records. `enabled` identifies an active ticker; the watchlist's
interval configuration determines which intervals workers maintain.

Daily snapshots can explicitly hand off to a selected VN provider:

```sh
uv run aipa-api --database ./data/api-daily-migration.sqlite3 --allow-direct adopt-snapshot --interval 1D --symbol GEX --provider vps
uv run aipa-api --database ./data/api-daily-migration.sqlite3 --allow-direct adopt-snapshot --interval 1D --symbol GEX --provider vps --execute
```

Inspection is the default. Execution requires 40 exact completed daily candles
through the imported tail, including volume. The replayable certificate retains
original daily rows and archive identities; a concurrent snapshot change or
active worker/recovery lease rejects publication. Original prices, provenance,
and readable archives stay intact. Later provider corrections still queue
retained-window recovery. This verifies a bounded append transition, not the
provider's lifetime dividend policy. Minute adoption remains the default interval;
its complete-session/correction options apply only to minute snapshots.

Daily live checks commit their recent observation with the candles. A later
historical dividend/correction probe has its own bounded time budget; failures
appear as `historical_probe_failure` findings while the successful recent check
remains successful. Actual corroborated historical revisions still queue staged
repair. Cancellation after the commit preserves the completed recent observation.

VN daily/hourly/minute updates that have outrun their 40-candle page make one
larger request, pinned to the current provider and bounded by retention and
1,000 candles. The reply must overlap the published tail before appending. If it
cannot, the worker preserves existing data and queues staged recovery rather
than guessing which dates should have traded. Expanded reads also check stored
price overlap beyond the usual comparison window for historical revisions.

Revision detection compares opens, highs, lows, and closes on completed candles.
It requires at least three corroborating candles on the pinned provider or its
verified snapshot and ignores representation noise. Detected changes queue
staged recovery; diagnostic price ratios are never applied to other history.

For a bounded local read-only HTTP rehearsal, stop ingestion so payloads stay
stable and run:

```sh
uv run python scripts/benchmark_http.py --seconds 30 --workers 4 --max-requests 1000
```

The runner requires a loopback API, checks the selected universe against
sequential baselines, disables response caching, and writes timings/errors to
`data/http-concurrency-benchmark.json`. Its baselines warm the archive file cache;
it does not measure cloud transfer or change candle data.

For native historical candles and actual cold/warm object downloads, use a new
report path on each run:

```sh
uv run python -m scripts.benchmark_history --start-date 2022-01-01 --end-date 2022-12-31 --report data/daily-history-benchmark.json
uv run python -m scripts.benchmark_history --interval 1m --start-date 2025-10-02 --end-date 2025-10-02 --report data/minute-history-benchmark.json
```

This requires loopback RustFS, uses a fresh temporary cache, compares complete
candle values/provenance, and checks main metadata remains unchanged. It records
downloaded object bytes and successful-query timings separately from failures.
Only ranges wholly before a source-backed configured history start are marked
not applicable; an unexplained empty range fails. A failed range returns a
nonzero exit status even when its cold/warm errors match. Measurements include
validation and payload hashing, and do not estimate cloud billing. The volume
profile benchmark always reads minute candles, including for old date ranges;
use this native-history runner for daily archives.

The rolling targets are three calendar years of daily/hourly data and one
calendar year of minute data, using UTC cutoffs with February 29 handled
explicitly. Yahoo/SJC defaults are daily only. Upstream retention and newly
listed assets can prevent complete coverage. A first validated bootstrap page
becomes readable promptly; durable jobs and quality findings retain the unmet
coverage target. `history_start` in a watchlist object may supply a verified
listing/history date. A no-data response does not establish that date. VPL’s configured history start
is supported by the [regulator’s listing announcement](https://ssc.gov.vn/webcenter/portal/ubck/pages_r/l/chitit?dDocName=APPSSCGOVVN1620154820).
After correcting a history-start setting for a pending series, `reconcile` uses
that verified floor when rebuilding it.

Configured worker jobs advance their retained-data floor as the rolling window
moves forward, under the current job lease. If a staged cursor already reaches
that bound, the worker can finish without requesting history outside the current
window. Published older candles remain intact; archival maintenance is separate.
Completion still requires a nonempty current window and preserved observed
coverage. VN hourly bootstrap additionally checks every completed weekday daily
date observed for that ticker against staged or published hourly observations.
Absent dates remain review findings without an automatic provider switch. This
checks observed dates, not a complete exchange calendar or every hourly slot.

A linked, verified `history_start` also permits completion at the first listing
day's empty hourly prefix. The pinned provider must explicitly return no data,
the cursor must equal the earliest staged/published bar on that same day, and
the ready provider/revision must match. Ordinary history exhaustion, unlinked
dates, later-day gaps and repairing series remain pending. Observed daily-date
coverage and the existing atomic record-preservation checks still apply. DNSE's
captured empty response is recognized only as six explicit empty arrays and
integer `nextTime=0`, without extra error fields; it never establishes a listing
date by itself.

At bootstrap, the worker also retires an untouched placeholder left behind by
an adopted snapshot. Cancellation requires a ready current revision, its valid
provider handoff certificate, a snapshot beginning at or before the requested floor,
and a job created in an earlier second than that verification. Jobs with a
provider, attempts, cursor, lease or staging remain untouched. The cancelled job
keeps its original identity and a reason; candles and coverage findings stay
intact. This prevents obsolete retries and does not certify complete coverage.

For a stalled VN hourly bootstrap, verified older staged rows can be made
readable without marking the job complete:

```bash
uv run aipa-api --allow-direct publish-bootstrap-progress --symbol FPT
uv run aipa-api --allow-direct publish-bootstrap-progress --symbol FPT --execute
```

The default is a dry run. Publication requires an idle job, a pinned native
provider and revision, exact staged overlap, and fresh provider confirmation
of at least 100 existing bars across five observed dates through the completed
tail. It readback-verifies immutable before/replacement images, then checks
the database snapshot again before inserting only older rows inside retention.
Existing rows, bootstrap staging and unresolved coverage findings are preserved.
This command currently supports VN hourly series only.

For a bounded pass over configured VN tickers with older staging, the local
batch runner applies the same checks independently and records refusals:

```bash
uv run python scripts/publish_vn_hourly_progress.py --allow-direct --output data/hourly-preview
uv run python scripts/publish_vn_hourly_progress.py --allow-direct --execute --output data/hourly-publication
```

Choose a new output directory for each run; repeated `--symbol` arguments limit
the selection. Execution requires local RustFS, makes complete before/after
SQLite backups, verifies every original candle and operational record stayed
unchanged, and checks the published images and backup records. Raw provider
captures and the final receipt are uploaded with exact readback verification.
Each series publishes atomically; a refusal leaves that series unchanged,
while earlier successful series remain readable. This runner does not mark
bootstrap jobs complete or certify missing sessions.

Inspect dated index-hourly availability before choosing a repair source:

```bash
uv run python scripts/check_vn_index_hourly_windows.py --allow-direct --before 2026-04-01 --output data/index-hourly-probes
uv run python scripts/check_vn_index_hourly_windows.py --allow-direct --before 2026-10-03 --walk-dnse-start 2023-10-03 --output data/index-hourly-walk
```

This diagnostic captures VPS/VNDirect/DNSE responses, records observed date
coverage and local discrepancies, and verifies the main index records and
database epoch stay unchanged. The optional DNSE walk has a 12-page limit and
keeps invalid-page evidence; it never clamps candles or publishes a candidate.
Reaching an older cursor proves traversal only, not complete trading sessions.

Crypto minute bootstrap uses Binance's checksummed monthly spot CSV files when
available; normal updates and unpublished months use the live API. Downloads
have an 8 MiB compressed/32 MiB uncompressed object budget and a 128 MiB local
cache. Complete timestamp sequences and OHLCV are validated before staging.
Microsecond archive timestamps and the legacy integer-volume representation are
normalized explicitly. [Binance's public-data documentation](https://github.com/binance/binance-public-data)
describes publication timing and checksums. This adds no service or dependency.

`audit` uses bounded SQL reads to review local continuous-market gaps, stale
crypto tails, and absent dates observed in VN daily feeds. It excludes unfinished
VN sessions and flags weekend provider observations separately. These are review
findings: holiday, listing, suspension, and no-trade policies still need evidence.
Fixed audit findings resolve on the next run; independent repair findings remain.
Starting filtered workers publishes the whole watchlist atomically, so concurrent
starts cannot temporarily hide another worker's tickers.

Completed-price revisions require corroboration across matching historical
dates. Recovery excludes unfinished candles, pins the provider, checkpoints
each bounded page, retains published data on failure, and publishes a validated
replacement atomically. Periodic historical samples check beyond the ordinary
overlap. Archived revisions are repaired separately; incompatible revision
boundaries produce an explicit error. Thresholds are an initial tested policy,
with provider adjustment semantics still requiring further verification.

An imported VN minute snapshot can hand off to a selected provider explicitly:

```sh
uv run aipa-api --allow-direct adopt-snapshot --symbol FPT --provider vps
uv run aipa-api --allow-direct adopt-snapshot --symbol FPT --provider vps --execute
```

The first command verifies without mutation. The default path requires exact OHLCV
matches across at least five completed sessions and 1,000 candles through the
published tail. It preserves imported prices and provenance, records the bounded
overlap evidence, and schedules normal updates. Concurrent corrections or active
repairs reject adoption. The evidence survives SQLite backups and S3 manifests;
later imports cannot reuse the adopted snapshot revision. This permits an
observed handoff, without claiming all historical provider adjustment policies
are equivalent. Failed verification leaves the published snapshot intact.

VN hourly snapshots use `--interval 1h` and require at least 100 exact bars
across five observed completed session dates through the published tail:

```sh
uv run aipa-api --allow-direct adopt-snapshot --source vn --symbol VNINDEX --interval 1h --provider vps
```

This applies to a ready `legacy-api` hourly snapshot. Add `--execute` only after
reviewing its dry-run proof. The certificate preserves minute-aligned VN labels,
including 02:15 UTC index bars, and binds finality to the completed VN session
cutoff. Hourly correction/complete-session options remain unavailable; this
proof licenses future updates, not historical scaling or calendar completeness.
An imported hourly snapshot stays frozen until a handoff passes.

Capture all three allowed VN sources without executing a handoff:

```sh
uv run python scripts/check_vn_hourly_snapshot_handoff.py --database data/candidate.sqlite3 --symbol VNINDEX --symbol VN30 --allow-direct --output data/hourly-handoff-preflight
```

The input must contain coherent imported snapshots. Raw/normalized responses
and all differences are retained, and input rows/state/certificates/epoch are
checked for preservation. A passing provider is recorded independently of
providers that fail; failed proofs do not trigger a fallback or merge.

Global minute snapshots use the same default exact-overlap path with Yahoo:

```sh
uv run aipa-api adopt-snapshot --source yahoo --symbol '^GSPC' --provider yahoo
uv run aipa-api adopt-snapshot --source yahoo --symbol '^GSPC' --provider yahoo --execute
uv run aipa-api refresh --source yahoo --symbol '^GSPC' --symbol '^DJI' --interval 1m
```

Yahoo minute verification uses completed UTC minute bounds and requires 1,000 exact
candles across five observed date partitions through the published tail. Futures
symbols ending in `=F` retain up to 10,000 rows from the same bounded six-day
Yahoo response; the normal 2,000-row selection can cover fewer than five long
sessions. Stock/index minute and hourly request sizes remain unchanged. The
VN complete-session/correction options remain restricted to VN data. Unverified
global minute snapshots stay frozen; add `1m` to a global watchlist entry only
after verifying its handoff. The included AAPL, SPY, S&P and Dow entries enable
daily and minute updates after verified local snapshot handoffs. MSFT, NVDA and
gold futures still ingest daily data only; their minute discrepancies remain open.

Yahoo hourly snapshots can use the same explicit handoff workflow:

```sh
uv run aipa-api adopt-snapshot --source yahoo --symbol SPY --interval 1h --provider yahoo
uv run aipa-api adopt-snapshot --source yahoo --symbol SPY --interval 1h --provider yahoo --execute
uv run aipa-api refresh --source yahoo --symbol SPY --interval 1h
```

Hourly verification compares up to 200 native bars and requires at least 100
exact OHLCV matches across five completed UTC date partitions through the
published tail. The bounds are completed whole hours. It preserves the entire
snapshot and earlier historical labels; no price or volume changes are allowed
by this proof. The smaller hourly threshold measures hourly observations,
while the minute threshold remains 1,000. VN hourly handoffs and minute
correction options are unsupported on this path. SPY's verified 200-bar/29-date
handoff enables `1h` in its watchlist entry and packaged default. Other global
hourly snapshots still require independent verification. Certificates permit
observed provider continuity, not lifetime calendar or adjustment guarantees.

Yahoo responses can change with the request window. To verify the legacy
worker's fixed five-day hourly policy explicitly:

```sh
uv run python scripts/check_yahoo_minute_query_shapes.py --symbol 'GC=F' --interval 1h --date 2026-09-30 --output data/gold-hourly-request-check
uv run aipa-api adopt-snapshot --source yahoo --symbol 'GC=F' --interval 1h --provider yahoo --yahoo-hourly-range 5d
```

Add `--execute` after reviewing a passing proof. The minimum 100 matching bars,
five completed date partitions, tail, race and preservation checks still apply.
The certificate pins `range=5d&interval=1h` for ordinary updates and archive-head
verification, and survives manifest restoration. Existing certificates keep
their dated policy. Relative ranges cannot be used with explicit historical
bounds; dated backfills remain separately checked. A five-day outage that loses
the stored overlap queues recovery. It does not silently switch request policy.
The diagnostic preserves raw/null fields and flags appended quotes outside
requested dates; replaying Rust null defaults does not license inferred candles.

Gold's local hourly handoff passes 109 exact bars across five completed date
partitions. An ordinary update succeeds with all 10,288 stored timestamps and
OHLCV unchanged. Its watchlist enables daily and hourly updates; minute updates
remain frozen. Full hourly/four-hourly HTTP parity, backup and 890-object RustFS
restoration evidence are recorded in `VALIDATION.md`.

After an outage, Yahoo updates expand the recent request once, up to 1,000
candles within the retained window, using the current provider. Daily replies
must overlap the exact stored tail. Intraday replies may instead match its
adjacent stored bar within one interval, because later Yahoo replies can omit
an earlier close-time row; that original row remains stored. A disjoint reply
queues staged recovery and preserves published data. Unavailable close-time
rows are not inferred during catch-up.

For a sparse ticker with fewer available minute candles, use the explicit
complete-session verification path:

```sh
uv run aipa-api --allow-direct adopt-snapshot --symbol GEG --provider vps --complete-sessions
uv run aipa-api --allow-direct adopt-snapshot --symbol GEG --provider vps --complete-sessions --execute
```

This requires every original timestamp across at least five completed weekday
sessions through the published tail. Each positive-volume provider minute must
match the original OHLCV. Aggregating those minutes must reproduce both fresh
provider daily OHLCV and the ready retained daily series exactly, including
total volume. Missing/truncated minutes, missing daily sessions, differences,
or concurrent minute/daily changes reject adoption. The receipt stores the
compared minute and daily records for replay during index restoration. SQLite
backs up the receipt; subsequent archive manifest publication carries it to S3.
The verification reads one bounded minute page and one daily page, without a
new service or an inferred price adjustment.

If native candles differ, a bounded correction requires explicit corroboration:

```sh
uv run aipa-api --allow-direct adopt-snapshot --symbol VGC --provider vps --complete-sessions --corroborate-provider dnse
uv run aipa-api --allow-direct adopt-snapshot --symbol VGC --provider vps --complete-sessions --corroborate-provider dnse --execute
```

Every changed candle must match the distinct second provider's timestamp and
OHLCV. Original and proposed 15-minute OHLCV must remain identical, and all
complete-session daily checks still apply. Execution publishes only those
corroborated changes together with the handoff in one SQLite transaction.
Changed candles retain the selected provider's provenance; other imported
candles stay intact. The receipt preserves the original records and each
second-provider witness for restoration. Matching daily totals alone cannot
authorize native-minute changes. Unconfirmed differences remain unpublished.

## History, migration, and retention

Requests use the same native and aggregated intervals and response formats.
For a ticker without an existing hourly series or hourly archive, `1h` and `4h`
are aggregated from its minute candles. Existing hourly series remain preferred,
including their historical repair checks. Minute-derived hours have the same
coverage as the available minute data; they do not create missing older history.
One-hour buckets use UTC hours; Vietnamese four-hour buckets retain the existing
02:00 UTC alignment. This lets daily/minute-only ticker selections serve hourly
chart requests without maintaining another ingestion series.
The history reader combines actual SQLite/S3 coverage, applies limits in the
correct direction, and loads earlier indicator data. Historical queries with
`start_date` return the first requested candles; latest queries return the last.
Archive files are partitioned yearly for daily data and monthly for intraday
data. Exact object keys, bounds, checksums, providers, and revisions are indexed
in SQLite. Immutable manifests allow archive-index recovery.

Use bounded JSON exports from `https://api.aipriceaction.com/tickers` as the
primary legacy candle migration source. PostgreSQL access is not required for
candles exposed by that endpoint, including history absent from the old CSV
archive. Migration also supports explicit local files and public yearly/daily
CSV objects. Keep the old CSV URLs, metadata, hashes, and fundamental files
available for existing SDK consumers; new Parquet uses a separate prefix.
Private sync records are a separate migration concern because `/tickers` does
not expose them. Imported snapshots and new-provider data need compatible
adjustment bases before they can be joined.

Frozen public history can also be published as a separate `historical_snapshot`
archive revision with `Archive.publish(rows, historical_snapshot=True)`. This
requires checksummed public rows wholly outside the configured retention window,
positive capture versions and a revision distinct from the active series.
Explicit historical requests ending outside retention can select that snapshot
when no local candles fall in the requested range. Indicator context and partial
aggregation buckets stay on the selected revision. Live, open-ended and queries
crossing local coverage continue using the primary history. Insufficient context
that would require another primary basis still raises the existing guard.
Snapshots preserve primary records/objects and are verified during restoration.
An overlapping pending primary archive does not prevent reading a complete
frozen snapshot when its verified relevant timestamps are all represented.
Missing primary timestamps, unverifiable archive data and incompatible older
context still block the read. Primary repair status is preserved.

Use `import-history` for an already captured, single-symbol `/tickers` JSON
response (or `--format csv` for a CSV export). Supply its actual UTC capture time
and a separate revision; every input candle must be outside retention. The
default reports the validated plan without uploading. Add `--execute` to publish
one immutable object and the manifest, preserving primary records and archives:

```bash
uv run aipa-api import-history /path/FPT-old.json --source vn --symbol FPT --interval 1m --revision public-fpt-capture-20261004 --captured-at 2026-10-04T00:00:00Z
```

Use a coherent capture for the requested range and its indicator lookback;
publication does not prove provider compatibility or fill missing timestamps.

For a complete expired daily year whose original CSV failed validation, use
`recover-public-year`. It requires every public timestamp and OHLCV value to
match that original CSV, including the invalid records. It archives valid rows
under a separate frozen revision and preserves both original files and explicit
invalid-date evidence. The default only validates the plan:

```bash
uv run aipa-api recover-public-year /path/VND-2020.json --original /path/VND-1D-2020.csv --symbol VND --year 2020 --revision public-vnd-2020-capture --captured-at 2026-10-04T02:55:19Z
```

Add `--execute` to publish after validation. Bounded expired requests can use the
valid dates when their lookback also avoids invalid dates. Invalid dates and
affected indicators remain unavailable. Native and mixed-window requests retain
the full-year guard; this operation cannot certify a complete continuous year,
change retained candles, or hand the frozen snapshot to a native provider.

Check an isolated filesystem-backed public snapshot against retained raw
records and historical indicator reads before proposing its publication:

```sh
uv run python -m scripts.check_staged_public_history --candidate data/candidate.sqlite3 --objects data/candidate-objects --output data/candidate-check --symbol FPT --end-date 2025-10-03
```

The output directory must be new. The checker inventories all retained versions
without using mixed revisions for calculations, requires every retained timestamp
in the candidate, and runs ordinary SMA/EMA history queries against both stores.
It verifies that the canonical epoch and record digest stay unchanged. `passed`
means timestamp preservation and readable candidate indicators; changed OHLCV
is reported separately and does not license replacement or provider handoff.

If a native history page fails OHLC validation, the error includes its provider,
market, symbol, interval and Unix timestamp. Use a bounded public JSON export
and a dated native request to preserve and compare the actual records. An
alternate provider's successful response alone does not establish a compatible
adjustment basis; keep invalid originals and candidate data separate until the
complete series is verified.

For the selected cryptocurrencies, `scripts/check_crypto_daily_history.py`
captures checksummed public JSON and original Binance responses, verifies the
entire retained daily overlap, and reports older date/value differences:

```sh
uv run python scripts/check_crypto_daily_history.py --end-date 2026-10-02 --output data/crypto-history-check
```

Choose a completed UTC end date and a new output directory. This command is
read-only and exits nonzero on any legacy/native discrepancy. Its separate
`native_basis_verified` result records exact retained overlap, continuous native
dates and identical public/native prices; older volume differences remain in
the report. That result does not certify legacy volume parity or publish data.

For AAPL/MSFT/NVDA/SPY, build a complete native daily candidate when wide Yahoo
responses change retained adjusted values and cannot be appended exactly:

```sh
uv run python scripts/stage_yahoo_daily_history.py --end-date 2026-10-02 --output data/yahoo-daily-candidate
```

This uses a separate SQLite file and RustFS prefix. It preserves original
responses and the served before-image, requires every retained/public date,
reports value changes and existing adjustment signals, and verifies complete
candidate readback. Recent and older candles share one new native revision;
no inferred adjustment factor is applied. It leaves the main API untouched.
Publication still requires worker checks, current before-image/revision checks,
a backup, and an atomic replacement of the complete selected daily series.

The same tool accepts `--symbol 'GC=F' --start-date 2010-01-01` for an isolated
gold-futures check. Yahoo requests honor that lower bound, so unrelated earlier
invalid data does not block the requested window. Failed candidates retain
partial diagnostics and original captures, and never publish to the main index.
The current gold candidate fails because native history omits dates returned by
the public API; permissive date matching is not used to license publication.

Daily Yahoo futures quotes preserve their supplied close even outside the traded
high/low range. Futures settlements can be calculated independently of traded
prices ([CME gold settlement rules](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457088147/Gold)).
This preserves a provider quote; it does not certify that every Yahoo historical
close is an exchange settlement. Open must remain inside high/low, prices must
be finite, and volumes must be nonnegative. Stocks, crypto, SJC and all futures
intraday candles retain their existing range checks. Quotes are never clamped
or rewritten to pass validation.

```sh
uv run aipa-api import-csv /path/FPT-1D.csv --source vn --symbol FPT --interval 1D --split-retention
uv run aipa-api import-legacy --source vn --symbol FPT --interval 1D --years 2020,2021,2022 --dry-run
uv run aipa-api import-legacy --source vn --symbol FPT --interval 1D --years 2020,2021,2022 --split-retention --older-only
uv run aipa-api import-legacy --source vn --symbol FPT --interval 1m --start-date 2025-01-02 --end-date 2025-01-03 --split-retention
uv run aipa-api --database ./data/api-daily-migration.sqlite3 import-legacy --from-api --api-format json --api-read-backend database --source vn --symbol GEX --interval 1D --years 2019 --split-retention --revision legacy-api-daily-20261004
uv run aipa-api --database ./data/migration.sqlite3 import-legacy --from-api --source vn --symbol FPT --interval 1m --start-date 2025-10-03 --end-date 2026-10-02
uv run aipa-api --database ./data/migration.sqlite3 import-legacy --from-api --source vn --symbol VCB --interval 1m --start-date 2025-10-03 --end-date 2026-10-02 --api-batch-days 31
uv run aipa-api --database ./data/json-migration.sqlite3 import-legacy --from-api --api-format json --source yahoo --symbol SPY --interval 1m --start-date 2026-09-28 --end-date 2026-10-02 --api-batch-days 7 --revision legacy-api-json-global-minute-20261003
uv run aipa-api reconcile --source vn --symbol FPT --interval 1D --archives-only
uv run aipa-api archive
uv run aipa-api archive --source crypto --symbol BTCUSDT --interval 1m
uv run aipa-api archive --source crypto --interval 1m --execute --prune
uv run aipa-api archive --execute --prune
uv run aipa-api --allow-direct archive-repair
uv run aipa-api --allow-direct archive-repair --source vn --symbol VCB --interval 1D
uv run aipa-api --allow-direct archive-repair --source vn --symbol SHS --interval 1D --restart
```

`archive` defaults to a dry run. Optional `--source`, repeated `--symbol`, and
`--interval` filters restrict the stored series selected for publication and
verified pruning. All selected series use one captured UTC retention cutoff;
omitting the filters retains the all-market maintenance behavior.

Rehearse a dated rollover on a populated copy before scheduling maintenance:

```sh
uv run python scripts/check_retention_rollover.py --source crypto --cutoff-date 2026-10-04 --output ./data/crypto-rollover-review
```

The output directory must be new. The checker creates consistent SQLite copies
and a unique archive prefix, publishes and prunes only the copy, compares every
surviving candle field/version with its before-image, verifies boundary history,
and restores the manifest into fresh SQLite. The supplied date simulates the UTC
retention cutoff; it does not change the main database's maintenance schedule.
Its JSON report and checksummed before/after backups remain in the output directory.

`import-legacy --dry-run` checks explicit source URLs with HEAD requests. Minute
imports require an inclusive UTC date range of at most 366 days; daily/hourly
imports use explicit years. The parser accepts the legacy headerless six-column
CSV and named local/API exports, rejects conflicting duplicates and invalid
values, and checks every file against its requested date bounds. Verified
downloads are frozen per provider/revision in `data/legacy-downloads/`; SQLite
receipts checkpoint completed months/years. Resuming fills missing rows without
overwriting later corrections. Unavailable files and empty API responses are
retried and never prove complete session coverage. Use a new explicit revision
and a separate migration database to capture another source snapshot.

Use `--from-api --api-format json` to retain the old HTTP API's full price
precision. Its CSV export rounds prices, which can prevent exact provider
verification for global assets. The default remains CSV for existing migration
receipts. JSON downloads preserve checksummed original responses and use the
same date, OHLCV, empty-response retry, and 10,000-row truncation checks. Changing
the format of an existing period receipt requires a new revision and separate
migration database. Verify the new snapshot before publishing it over old data;
preserving precision does not resolve other provider or timestamp disagreements.

Public market-data migration uses `https://api.aipriceaction.com/tickers`
and the existing public archive. An unavailable legacy PostgreSQL connection
does not block these imports. Private sync records still require a separate
export before production cutover.

For an existing frozen public minute series, stage a fresh snapshot separately:

```sh
uv run python scripts/stage_public_minute_history.py --source yahoo --symbol 'GC=F' --start-date 2025-10-03 --end-date 2026-10-02 --revision public-gold-minute-20261004 --output data/gold-minute-current-candidate-20261004
```

This script captures bounded six-day JSON exports, original responses and the
previous snapshot in an isolated SQLite/filesystem archive. It never publishes
to the main database. Its `passed` field verifies a nonempty candidate retaining
all original timestamps; inspect value differences and empty ranges separately
before publication. Empty responses do not establish complete calendar coverage.
The legacy API's zero-volume, flat-OHLC Yahoo futures observations retain their
original seconds in minute and hourly snapshots, with visible quality findings.
Native candles retain strict minute alignment.

For minute handoff disagreements, compare Yahoo request shapes separately:

```sh
uv run python scripts/check_yahoo_minute_query_shapes.py --symbol 'GC=F' --date 2026-10-02 --output data/gold-minute-query-shapes
```

This read-only diagnostic captures the current one-day range used by the legacy
worker, an explicitly dated day and the replacement's bounded six-day response.
The current range need not cover the explicit day. Raw bodies, requested URLs,
null-close counts, original timestamp labels and exact differences are retained.
Rust extraction null defaults are replayed for diagnosis only; this never
licenses a handoff or writes inferred prices/volumes to the application database.
Gold's observed range response matches retained data while the dated/wider
responses differ, including a first-row volume difference. Resolve that request
policy before interpreting every disagreement as a historical adjustment.

Verify the complete staged minute history through the local HTTP API:

```sh
uv run python scripts/check_public_minute_snapshot.py --candidate data/gold-minute-current-candidate-20261004 --source yahoo --symbol 'GC=F' --report data/gold-minute-http-verification.json
```

This compares every timestamp and OHLCV field in bounded exports for minute,
15-minute and 30-minute intervals. Gold's published public snapshot contains
198,752 observations through October 2, 2026; the full HTTP checks, installed SDK
and actual web chart pass. Every original timestamp and volume is retained, and
recovered JSON prices round exactly to the original two-decimal values. Native
gold minute updates remain frozen pending provider reconciliation. Available
history begins March 8 at 22:10 UTC; empty earlier exports do not prove a full
year of data.

Use `--api-read-backend database` for a consistent database-backed public export.
This sends `redis=false&snap=false&cache=false` to the existing HTTP API; it does
not open a PostgreSQL connection. The legacy API can otherwise serve recent
ranges from Redis and wider ranges from its database, with different values.
The default retains existing export behavior and frozen receipt compatibility.
Backend choice is recorded with the snapshot revision; changing it within an
existing snapshot, including when adding a new year/month, is rejected. Use a
new revision and separate migration database for another read path.

`--api-batch-days` optionally combines up to 31 adjacent minute-export dates
within each receipt month. Its default remains one day; S3 daily object paths
remain unchanged. A response reaching the API's 10,000-row limit fails before
publication. Use smaller batches for assets with long trading sessions.
`scripts/migrate_vn_minutes.py` applies these bounded imports to the selected
VN universe, preserves an earlier month in S3 for indicator warm-up, reports
absent observed dates, and optionally executes only exactly verified handoffs
against VPS, VNDirect, and DNSE in configured order. Explicit dates and revision
make restarting reproducible:

```sh
uv run python scripts/migrate_vn_minutes.py --start-date 2025-10-02 --end-date 2026-10-02 --revision legacy-api-vn-minute-20261003 --adopt --allow-direct
```

Run this as the single archival writer. Existing independent series are skipped,
and failed handoffs preserve imported data. The legacy HTTP API is a migration
source; ordinary API serving reads only local SQLite and indexed archive objects.

`--older-only` preserves the live SQLite window while converting old history.
An imported legacy revision remains independent until verified. To re-fetch old
partitions from the current ready provider, use `reconcile --archives-only`, then
run bounded `archive-repair` commands. Each replacement must reproduce the old
partition’s exact timestamp set; failed downloads retain the original object and
report pending repair. Successful repairs do not require rebuilding recent data.
Before publishing, a fresh completed-price overlap verifies the current provider
against retained candles. Changed prices schedule retained recovery first;
unverifiable overlap leaves the old object pending. Completed downloads survive
verification outages and resume publication without downloading older pages again.
Replacement publication rejects a stale, superseded target. A retry may publish
the same already-active candidate after a manifest failure. Repair cleanup
retires only unleased work whose immutable target is no longer active; it retains
the original archive objects and audit errors.

Daily repair includes the entire final market date before normalizing provider
timestamps, so DNSE's 02:00 UTC candle is included. After fixing a failed
download, use `--restart` with an explicit source, symbol, and native interval to
discard that series' unleased failed staging and retry from its archive end.
Published candles, original objects, and verification requirements remain intact.
Ordinary retries preserve completed downloads after a verification outage.

For bounded daily migration across the configured VN universe:

```sh
uv run python scripts/migrate_vn_daily_history.py --years 2019,2020,2021,2022,2023 --revision legacy-daily-history-20261003 --reconcile-pages 6 --allow-direct
```

Use repeated `--symbol` arguments or `--max-symbols` to limit a rehearsal. Run
this as the single archival writer. Existing coherent years are reported and
preserved; configured pre-listing years are skipped. Each requested year has its
own receipt/error, so a corrupt year does not block later valid years. Provider
reconciliation requires the exact original timestamp set and completed retained
prices; disagreements remain explicit in the report and quality findings.
Skipping an existing year alone does not prove that its history is complete.

An invalid daily CSV can be recovered separately from the series' pinned
provider, using its dates as the expected coverage:

```sh
uv run aipa-api --allow-direct recover-legacy-daily ./data/legacy-quarantine/VNINDEX-1D-2019.csv --symbol VNINDEX --year 2019
```

This command covers one explicit archived year, bounded by `--max-pages`
(default four). It validates the original timestamps, requires valid provider
candles on exactly those dates, and verifies completed retained OHLC before
publication. It preserves the original CSV and checksummed recovery evidence
in S3. Missing dates or changed recent prices leave the candidate unpublished.
`restore-index` verifies these records and original snapshots before restoring
the index. This does not certify a provider's corporate-action policy.

A reviewed exception is available for the explicitly verified 2018 HOSE closure
placeholders in FPT, VCB, MBB, VIC, and HPG:

```sh
uv run aipa-api --allow-direct recover-legacy-daily ./data/fpt-1d-2018-original.csv --symbol FPT --year 2018 --exclude-verified-sessions
```

The flag applies only to those five tickers on January 23–24, 2018. The original rows
must have positive, identical OHLC and zero volume; any provider-returned row on
those dates must pass the same guard. The receipt preserves the original file,
the [broker closure notice](https://www.vndirect.com.vn/vndirect-thong-bao-ve-viec-tam-ngung-giao-dich-tren-so-giao-dich-chung-khoan-thanh-pho-ho-chi-minh-ngay-24-01-2018/),
and historical exchange evidence. Every remaining timestamp must match exactly.
Unreviewed tickers/years, nonzero-volume placeholders, missing ordinary dates, or
altered evidence fail validation. Absent candles alone do not establish a closure.

For the configured global tickers' existing hourly snapshots:

```sh
uv run python scripts/migrate_global_hourly.py --years 2023,2024,2025,2026 --revision legacy-global-hourly-20261003
```

The runner checkpoints each year, preserves independent existing series, and
records unavailable or invalid inputs. Use `--symbol` or `--max-symbols` to
bound a rehearsal. Imported snapshots remain frozen until an ongoing provider
handoff is verified; their candle dates are visible in health coverage.

Check current hourly source behavior before replacing those snapshots:

```sh
uv run python scripts/check_yahoo_hourly.py --symbol AAPL --symbol 'GC=F' --start-date 2026-09-28 --end-date 2026-10-02 --output data/hourly-source-check
```

This read-only check preserves original public/native responses and compares
native hourly values, the stored hourly series, and minute aggregation. Yahoo
hourly timestamps follow the legacy worker's whole-hour labels; source OHLCV
values are preserved, rather than regrouped from minute candles. The command
reports exact differences alongside material differences using the existing
handoff price tolerance (absolute `1e-8`, with exact volume comparison). It exits
unsuccessfully for missing public timestamps, material native differences or a
changed canonical epoch during the check. Missing flat, zero-volume observations
remain missing; their separate count does not waive coverage. Neither input is
rounded or changed. That result is diagnostic evidence, not permission to discard older
timestamps or a certificate for an ongoing provider handoff.

The six chosen stock/index hourly series now use complete public JSON snapshots
through October 2, 2026, with every previously stored timestamp preserved.
Their `1h` and derived `4h` histories are verified through HTTP and the existing
SDK. SPY now has a verified native hourly handoff and an ordinary worker update.
Other imported hourly snapshots remain frozen until independently verified;
the worker records `handoff_required` before making upstream requests. Gold's
complete hourly public snapshot is also current through October 2. It preserves
279 timestamped legacy quote-shaped observations with their original seconds:
only `legacy-api` Yahoo futures minute/hourly rows with zero volume and identical OHLC
receive this compatibility rule. They are recorded as `legacy_quote_events`
quality findings rather than certified as trades. Native,
non-flat and nonzero-volume timestamps retain their strict validation. Gold
still requires a native handoff; the latest dry run disagrees with the snapshot.
Current evidence is in `VALIDATION.md`.

Browser rehearsals can enforce freshness as well as populated responses:

```sh
uv run python scripts/check_web_client.py --market global --symbol AAPL --interval 1h --minimum-chart-date 1D=2026-10-02 --minimum-chart-date 1h=2026-10-02 --report data/web-hourly-freshness.json
```

Bounds apply to the selected chart and both global benchmarks. Reports include
the observed first/last timestamps; choose an explicit completed-session date
for each requested interval rather than treating a successful HTTP status as
evidence of current data. Playwright is an optional verification dependency.

When using a separate database, keep its archive backend/prefix isolated from the
main database. Two database indexes must not publish different manifests to the
same S3 prefix. The API migration example above imports recent minute rows only;
for older trial imports, use `--archive-backend filesystem` with a separate object
directory or a separate `S3_PREFIX`.

`archive` defaults to a dry run. Execution uploads immutable objects, verifies
read-back values and checksums, validates and commits the local index, publishes
the manifest, then optionally prunes
only the exact exported row versions. Concurrent corrections survive pruning.
Parquet creation streams a typed temporary CSV into DuckDB in bulk, preserving
float precision, 64-bit integers and quoted metadata. Temporary files are removed
after the write. The full gold snapshot reproduces the previous Parquet bytes
exactly while writing substantially faster; temporary-space and observed memory
costs are recorded in `VALIDATION.md`.
Run archival maintenance with `worker --archive-daily` or schedule the explicit
`archive --execute --prune` command. Without the flag, the worker does not prune.
Interrupted uploads retain local data. Older objects remain
available while their replacements are being checked.

To verify recurring crypto updates against the populated database:

```sh
uv run python scripts/check_crypto_worker.py --cycles 75 --output ./data/crypto-worker-review
uv run python scripts/check_crypto_worker.py --cycles 75 --archive-daily --output ./data/crypto-worker-maintenance-review
uv run python scripts/check_crypto_worker.py --verify-only --output ./data/crypto-worker-review
```

The first command runs the ordinary crypto worker and updates this database;
use a new output directory. It captures Binance responses and consistent
before/after backups. Checks require successful updates for initially due
series, repeated minute updates, continuous stored timestamps, native completed
candle parity, preserved completed history and other-market candles, and
unchanged series, jobs, handoffs, import receipts and archive index. Daily series
whose cooldown has not expired remain scheduled. The second command rechecks
the saved evidence without running ingestion. A bounded run does not install a
supervisor. Retention pruning occurs only with the explicit `--archive-daily`
option; the checker verifies its existing archive metadata stays unchanged when
no rows are expired.

If manifest publication fails, the verified local object/index and local candles
remain available. A retry can finish publication and pruning. A candidate rejected
by a concurrent series repair never advances the S3 manifest pointer.

To retry metadata publication without downloading or pruning candles:

```sh
uv run aipa-api publish-index
```

Run this against the same SQLite database and S3 prefix used by the failed
publication. It publishes the current archive index, verified handoff/recovery
receipts, and known unavailable ranges under the shared archive-writer lease.
If another writer holds the lease, the command reports that it is busy; repeat
after that writer finishes. A recovery whose final metadata publication failed
keeps its verified local receipt and resolved range, so this command can finish
publication without downloading that year again. It does not prune local rows.

Periodically compact rollover fragments within their existing price basis:

```sh
uv run aipa-api compact-archives
uv run aipa-api compact-archives --execute --max-groups 20
```

The default is a dry run. Each group stays within one yearly daily or monthly
intraday partition and one provider/revision. Pending repairs and objects
spanning several periods are preserved. Compaction reads existing verified
objects, retains newer corrections/provenance, and atomically replaces only
unchanged index entries. Original S3 objects remain available for evidence and
rollback. No upstream history download is required. The row/fragment/group
budgets bound each run; re-running finishes a failed manifest publication even
when the local compaction has already committed.

The filesystem archive backend supports offline operation and tests:
`aipa-api --archive-backend filesystem ...`. For another S3 service, configure
endpoint, bucket, region, and credentials. For AWS IAM credentials, set an empty
endpoint and empty explicit key settings so boto3 can use its credential chain.

## Backups and rollback

```sh
uv run aipa-api backup ./backups/first.sqlite3
uv run aipa-api restore ./backups/first.sqlite3 --destination ./data/restored.sqlite3
uv run aipa-api restore-index
docker compose stop rustfs
```

Backups use SQLite's online backup API. Backup and restore destinations must be
new files. `restore-index` verifies archive objects from the manifest; it does
not restore recent candles or sync records. Back up the SQLite database and
retain S3 objects together. `docker compose stop` preserves named-volume data.

For cutover, freeze a verified migration snapshot, validate the selected
universe and historical ranges, back up SQLite/S3, then point the existing
reverse proxy at this service. Restore routing to the existing backend if an
acceptance check fails. Keep the legacy database and CSV archive available until
its consumers and adjustment revisions have been verified. Runtime topology is
one host with local SQLite and one archival writer; multi-host writers are
outside this design.

## Verification

```sh
uv run pytest
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv build
```

Optional read-only provider and legacy-response probes live in `scripts/`.
`check_web_query_matrix.py` checks every configured ticker with no indicators,
SMA and EMA. Use explicit dates to audit historical or recent session ranges:

```sh
uv run python -m scripts.check_web_query_matrix --interval 1D --interval 15m --interval 1h --start-date 2026-09-28 --end-date 2026-10-02 --report data/dated-web-audit.json
```

The report and its response-capture directory must be new. Empty ranges fail
the coverage audit; compare them with bounded public API requests to determine
whether they are missing migration data or also absent from the legacy source.
The checker verifies requested date bounds and the existing SJC quote/futures
settlement price rules. Its unchanged-state evidence covers VN daily versions,
total candle counts and operational tables including the epoch; it does not
individually hash every other candle or certify legacy numerical parity.

`check_sdk_client.py` runs in the existing SDK environment and checks sampled
daily/minute/15-minute candles plus SMA/EMA against the local API.
Use `--start-date` and `--end-date` for archived SDK ranges. The SDK selects
the latest 20 rows inside the range; HTTP requests with a start date select the
earliest. The checker verifies exact SDK/HTTP parity and independently compares
the SDK candles with the complete dated range's tail. It records differences
between full-range and tail-query moving averages separately because their
indicator context can differ.
`check_indicator_context.py` compares daily tail/full-range SMA/EMA responses
with the live legacy endpoint, preserves raw responses, and separates candle
differences from EMA seed decay. It does not alter the finite-lookback policy:
the legacy API also produces history-dependent EMA values.

```sh
uv run python scripts/check_indicator_context.py --source crypto --symbol BTCUSDT --start-date 2022-01-01 --end-date 2022-12-31 --report data/btc-indicator-context.json
```

`check_web_client.py` uses optional Playwright/Chromium in an isolated browser;
it routes only public API reads to the loopback replacement and exercises actual
daily/15-minute chart controls and volume profiles. Reports/screenshots belong
under ignored `data/`. Neither script changes production routing or accounts.
The browser checker selects watchlist rows explicitly; dialog triggers and
benchmark selectors can carry the same ticker label. A displayed chart may
reuse its initial response when the default ticker is selected again.
`check_rustfs.py write --state /tmp/aipa-rustfs.json`, a RustFS restart, then
`check_rustfs.py read --state /tmp/aipa-rustfs.json` test local persistence,
range reads, Parquet boundary queries, and manifest recovery. It uses an
isolated validation prefix and requires a loopback endpoint.

RustFS configuration follows the official
[Compose example](https://github.com/rustfs/rustfs/blob/main/docker-compose-simple.yml).
The archive design uses standard
[Parquet support in DuckDB](https://duckdb.org/docs/stable/data/parquet/overview).
Plain Parquet plus a manifest keeps this initial system small; adding a lakehouse
catalog or table format is unnecessary for the tested single-host workflow.
