# Implementation plan

This is the current roadmap for the FastAPI replacement. Checkboxes certify
only the stated checks, not full data coverage or production readiness. Earlier
staging narratives and superseded checklists remain in `VALIDATION.md` and Git
history through `eb814ab`; they are not additional current open tasks.

## Current verified checkpoint — 2026-10-04 ICT

- FastAPI, SQLite retention, Parquet/DuckDB history, three non-VCI VN adapters,
  workers, operational CLI and web/SDK interfaces are implemented locally.
- The scoped VN/crypto/global workers advance the live database; populated
  backup images and their exact scope are recorded in `VALIDATION.md`.
  The S3 index has 901 active objects, 69 handoff certificates, 34 recoveries and
  eight invalid-date/basis markers preserving VND and older VNINDEX observations.
- There are 58 VN minute handoffs and four Yahoo minute handoffs (AAPL, SPY,
  S&P and Dow). VNINDEX, MSFT/NVDA and gold minute snapshots remain frozen.
  SPY also has a verified native hourly handoff; five other current stock/index
  hourly snapshots remain frozen. Gold hourly history has a verified native
  handoff pinned to the legacy five-day request policy, with all 279 legacy
  quote events preserved explicitly and `1h` enabled in its watchlist entry.
  Gold minute history is also current as a public snapshot, with all 241 legacy
  quote-event seconds preserved; native gold minute ingestion remains frozen.
- The latest complete API suite passes 529 tests; lint/format/offline builds pass.
  Real worker, HTTP, SDK, public-web and populated restoration evidence is
  recorded with its scope and limits in `VALIDATION.md`.
- Actual scoped Git commits include `c037c53` (storage), `23fc706` (workers/CLI),
  `b8d74b5` (API), `0a3724f` (SDK), `27fe152` (daily handoffs), `44cba6a`
  (browser selection), `5f6b93d` (AAPL/SPY updates) and `eb814ab` (Yahoo restart
  overlap). Proposed commit labels below describe implementation units; they
  do not imply those exact titles were all committed separately.

## Remaining acceptance gates

- [x] Verify cooperative termination and real local restart of all three scoped
  native workers with the latest shutdown correction. Confirm old processes are
  terminal before replacements start; all 57 stock, four crypto and four enabled
  global minute series record fresh successes on their existing provider/revision.
  Consistent images preserve formerly verified completed values, every frozen
  and index record version, series states, archives, certificates and imports.
  Provide a reusable read-only verifier and reject stale/wrong-frame successes
  and changed protected records. API PID 16546 remains running. This verifies
  one restart; real supervisor recovery and multi-day operation remain open.

- [x] Preserve VNINDEX's verified 2015–2018 legacy daily history: 995 valid
  public candles in four frozen yearly objects, with four invalid dates retained
  as explicit guards and immutable evidence. Preserve all original active
  objects and index record/series versions. Add 200 native 2018 context candles
  with exact expected timestamps and a fresh forty-record retained comparison;
  do not claim complete native years or mix public/native rows. Recent controls,
  the full 2017/2018 raw years and full 2018 SMA pass. The running API and fresh
  901-object restoration reproduce thirteen historical/context/guarded cases.
  Prior FPT/VND/VNINDEX controls remain valid. Earlier invalid context, complete
  native older years and cross-frame continuity remain separate open gates.

- [x] Restore bounded VNINDEX 2020 daily reads through a separate coherent
  frozen public capture: all 252 original year dates plus the preceding session.
  Fix the reader's false rejection when a verified pending archive's relevant
  timestamps are already covered, while retaining guards for absent dates and
  failed archive verification. Populated rehearsal changes only archives/meta;
  actual publication preserves both indices' recent record versions, states and
  all 895 original active objects. Full raw year and December SMA serve through
  the local API; twelve recent controls stay exact. Fresh restoration verifies
  all 896 objects, 69 certificates, 34 receipts and six raw evidence readbacks.
  Native primary repair, early SMA/EMA context and the rejected hourly
  replacements remain open; the wider public capture contains eight invalid rows.

- [x] Recover 251 valid VND 2020 daily records from an exact public API capture
  matching the original CSV. Preserve February 19's invalid record, immutable
  inputs and the full-year native/mixed basis guard. Isolated full-table checks
  preserve every retained version. Local publication preserves all 894 original
  active objects; twelve recent HTTP controls remain exact and four recovered
  historical controls pass. Invalid-date/context controls remain HTTP 503.
  Fresh RustFS restoration verifies all 895 objects, 69 certificates, 34 receipts
  and both markers; historical SMA remains readable with the invalid date guarded.
  This partial frozen-year recovery does not heal the invalid date or license
  combining the snapshot with native data.

- [x] Rehearse all 13 configured native global series and enable their scoped
  continuous worker. Seven daily, two hourly and four minute checks pass with
  exact protected data. Three fresh gold daily windows corroborate one completed
  volume correction without price changes or missing retained dates; keep the
  original before-image and captures. Actual ingestion preserves every original
  timestamp and frozen global version; all 34 dated SDK cases pass. Reuse one
  native rehearsal script for VN/global, preserving the previous VN entry point.
  Multi-day supervision and unconfigured/frozen handoffs remain open.

- [x] Rehearse every eligible native VN refresh on a populated isolated copy:
  all 170 checks succeed (59 daily, 53 hourly, 58 minute), with 218 captured
  provider responses, zero changed OHLCV values, no new/changed jobs and exact
  protected non-VN versions/archive/certificate/import tables. Main VN versions
  stay unchanged during rehearsal. Start one continuous worker for 57 stock
  tickers, excluding both indices; 24 live installed-SDK cases pass during
  ingestion. Keep four unresolved hourly bootstrap jobs guarded. This verifies
  current refresh behavior, not complete exchange sessions or multi-day uptime.

- [x] Recheck real crypto scheduling against the frozen-history checkpoint:
  125 cycles, 20 successful attempts, three minute updates per ticker and 1,242
  completed native-response rows verified. All noncrypto versions, previously
  completed crypto values and protected operational tables remain exact; twelve
  retained crypto series stay continuous. Thirty-two installed SDK SMA/EMA cases
  and nine frozen FPT HTTP responses pass during ingestion. Leave one scoped
  crypto worker running with daily maintenance; actual multi-day/restart
  supervision remains open.

- [x] Provide a supported operational command for frozen public history:
  `import-history` reads single-symbol JSON/CSV captures, requires an explicit
  revision/capture time, defaults to a validated dry run and publishes only cold
  data. Reject primary revision reuse, different captures and conflicting values;
  repeat capture checks under the archive writer lease. Twelve CLI regression
  cases cover publication, restoration, idempotence and rejection. An isolated
  dry run plans all 678 records in the captured FPT historical response.

- [x] Serve explicitly bounded expired FPT minute history from a separate,
  coherent public snapshot in S3. Four frozen objects preserve 10,397 candles,
  including 4,746 previously absent August timestamps; every original candle
  version and archive remains intact. Nine historical HTTP/indicator cases match
  the public candidate, and complete 894-object restoration reproduces them.
  All 633 recent compatibility responses remain byte-identical. This does not
  license replacing recent VPS data or crossing incompatible adjustment frames.

- [x] Enable gold's verified native hourly updates using the legacy five-day
  request policy. Keep the 100-bar/five-date/tail/race guards, pin the policy in
  restorable certificates and use it for live/expanded/archive-head checks.
  Populated rehearsal and canonical publication preserve all 10,288 timestamps
  and OHLCV values, every unrelated candle version/table and full HTTP output.
  The ordinary update succeeds; all 890 objects/69 certificates restore from
  RustFS. The watchlist now has 199 configured states. Multi-day operation and
  other global handoffs remain separate gates.

- [x] Confirm public `/tickers` JSON exports work without a legacy PostgreSQL
  connection; a fresh bounded VCB request returns HTTP 200.
- [x] Distinguish exact hourly float noise from material differences without
  rounding records or ignoring missing timestamps; three regression tests pass.
- [ ] Reconcile Yahoo hourly request-window behavior before S&P/Dow handoff.
  Eight captured requests reproduce identical OHLC with zero versus nonzero
  volume for the same September 24 observation depending on request start date.
  Preserve public snapshots; do not trim proof windows to obtain acceptance.

- [x] Finish VPL's hourly bootstrap at its verified listing-day prefix:
  recognize DNSE's explicit empty arrays/zero timestamp marker, bind the empty
  prefix to the configured sourced listing date and earliest current/staged bar,
  and preserve existing observed-session/revision/record guards. Eighteen new
  regression cases pass; populated rehearsal and canonical application preserve
  every candle and full hourly/four-hourly HTTP output. Seven jobs remain; this
  does not certify every exchange slot or remove unrelated history findings.
- [x] Retire inherited unstarted PLX/SSI minute bootstrap placeholders after
  their already verified snapshot handoffs. Match the current revision/provider
  certificate and rolling floor, reject started/newer/staged jobs, and preserve
  every candle and other table. Populated rehearsal and canonical application
  pass full-table hashes, idempotence and a checked backup. Eight pending jobs
  remain; cancellation does not certify full session coverage.
- [x] Exercise dated recent web requests across all 71 selected tickers:
  633 requests for daily/15-minute/hourly controls, without indicators and with
  SMA/EMA, through October 2, 2026. All pass nonempty ordered OHLCV/date-bound
  checks. The latest corresponding October 1–3, 2025 audit has 585 passes and
  48 empty responses; the older FPT EMA guard is resolved for that bounded range.
  The broader ten-interval audit has 2,093/2,103 recent passes, with ten existing
  VNINDEX/VND long-indicator guards, and 1,541/2,103 historical passes, with 558
  empty ranges and four long-indicator guards. Public checks reproduce the 14 empty global
  ranges, but both missing index hourly ranges have public candles. Exact wider
  numerical parity, freshness and full-year coverage remain separate gates.
- [x] Add opt-in daily maintenance to the worker without another service:
  scoped verified publication/pruning, once-per-UTC-day tracking, retry cooldown,
  restart idempotence and concurrent-correction retention/re-export. Test outage
  recovery and ingestion progress. Verify a real 75-cycle run and a populated
  simulated next-day S3 rollover/902-object restore. Actual multi-day supervised
  operation remains unproven.
- [x] Execute the remaining-market rollover: 12,387 rows in 177 partitions,
  exact cold readback and all surviving candle versions, 890-object manifest
  restoration, and 177 HTTP boundary queries covering 24,980 records. No expired
  local rows remain at the checked UTC cutoff. Unattended scheduling remains open.
- [ ] Reconcile FPT context across the cold/recent boundary. The expired October
  1–3, 2025 range now has a coherent frozen public view, but the October 1–10
  range retains three incompatible-context guards (15-minute EMA, 30-minute
  SMA/EMA). Pinned VPS dated August/September probes return no data. Preserve
  both frames; do not relabel or mix revisions.
- [x] Execute the due crypto rollover after its populated rehearsal: preserve
  5,860 expired rows in 12 immutable Parquet objects, prune exact local versions,
  verify every surviving record and restore the 713-object canonical manifest.
  All 12 boundary HTTP cases and archived SDK checks pass. Subsequent native
  updates add 68 current candles through October 4 at 00:10 UTC without changing
  completed history, archive metadata or other markets. Other-market rollover
  and unattended scheduling remain separate gates.
- [x] Refresh all four configured crypto tickers on the pinned Binance basis:
  3,895 new minute and 64 new hourly candles through October 3 at 23:33 UTC.
  All 12 native series have continuous retained timestamps; previously completed
  crypto OHLCV and all other markets stay unchanged. Full populated backups,
  24 HTTP checks, 40 SDK SMA/EMA cases and the actual BTC web controls pass.
  Current daily/hourly/minute bars remain provisional; this bounded refresh
  does not certify unattended future ingestion or unrelated markets.
- [x] Restore selected crypto daily history: 7,777 older Binance candles in
  25 yearly objects; exact retained overlap, full-range HTTP and cold/warm checks.
  Preserve and document 30 older legacy/native volume differences.
- [x] Restore SJC and both global index daily histories: 21,582 older candles
  in 83 yearly objects; full-range HTTP, SDK and manifest recovery checks pass.
- [x] Rebuild AAPL/MSFT/NVDA/SPY daily histories from complete native snapshots:
  37,206 dates including 34,194 cold candles in 138 objects. Preserve every
  original/public date and before-image; worker, HTTP, SDK, AAPL web and complete
  manifest recovery checks pass. Existing adjustment thresholds remain unchanged.
- [x] Preserve daily Yahoo futures settlement-style closes independently of the
  traded range, while keeping open/high/low and all intraday checks strict.
  Honor explicit Yahoo floors and preserve failed candidate diagnostics.
- [x] Backfill GC=F native daily history: 3,456 older candles in 14 objects,
  with all 757 existing rows unchanged and every older public date covered.
  Full HTTP, SDK, gold web, all seven global 2022 ranges and recovery checks pass.
- [ ] Reconcile gold's recent public/native calendar and price differences:
  30 recent public dates remain absent natively; originals and differences are
  retained separately, without splicing contract frames or declaring parity.
- [x] Restore six current stock/index hourly controls from complete public JSON,
  preserve all old dates, and verify HTTP, SDK and actual browser freshness.
- [x] License SPY native hourly updates with 200 exact bars across 29 completed
  UTC date partitions, preserving all existing dates/values and verifying an
  ordinary update, populated backup and complete S3 manifest restoration.
- [x] Restore current gold hourly history from complete public JSON, preserve
  all 6,290 original dates/values, add 3,998 newer observations and retain all
  279 quote-event timestamps without rounding. Verify SQLite/Parquet, full
  yearly HTTP, SDK, actual web freshness and populated backup evidence.
- [x] Restore current gold minute data from complete bounded public JSON exports:
  198,752 observations, all original timestamps/volumes retained, 241 quote events
  preserved, full minute/15-minute/30-minute HTTP parity, existing SDK, actual
  browser freshness and populated backup verified. Full one-year coverage is
  still unproven; native gold minute updates remain frozen.
- [ ] Independently verify remaining hourly handoffs. Legacy quote events and
  native/public value disagreements remain visible; minute aggregation does not
  reproduce existing native hourly bars.
- [x] Publish verified older FPT native hourly bootstrap progress with fresh
  provider overlap, immutable before-images and populated SQLite backups.
  FPT now serves 3,732 rows; all 500 original rows and unrelated records remain
  unchanged. Full HTTP, SDK and visible daily/hourly web controls pass.
- [x] Extend guarded hourly progress to the other 52 selected VN stocks:
  append 165,510 rows with 10,400 fresh exact native matches; preserve every
  original record and operational table, and verify populated backups. Full
  HTTP checks cover all 191,510 native hourly and 76,788 derived four-hour bars;
  representative SDK historical ranges and actual ACB web controls pass.
- [ ] Complete selected VN hourly backfills and reconcile public-only dates.
  FPT's bootstrap job is complete after the guarded rolling-floor transition;
  three public timestamps remain absent from its native window. Four stock
  hourly jobs remain pending: IDC, GEE, VGI and VTP. VPL completed at its verified
  listing-day prefix. Completed bootstrap
  jobs do not certify complete coverage or close the quality findings. All 53 stock
  hourly prefixes now reach their applicable retention/listing date; VNINDEX
  and VN30 still begin after that date. Session completeness remains unproven.
- [x] Diagnose index hourly internal gaps independently of prefix bounds:
  VNDirect returns sparse older windows; the next DNSE backfill pages contain
  seven invalid VNINDEX bars and five invalid VN30 bars. Preserve raw witnesses
  and stage complete public candidates (3,448 rows each) in isolated SQLite and
  Parquet, with full FastAPI checks and immutable local before-images.
- [ ] Reconcile and publish coherent VNINDEX/VN30 hourly replacements. Each
  public candidate has 2,727 timestamps absent locally, but omits 45 local
  timestamps and changes many shared fields; it is not an append candidate.
  Existing source/session findings remain open; traversal alone cannot certify
  complete recent history.
- [x] Prevent native refresh/fallback from replacing imported VN hourly
  snapshots without a verified handoff. All three legacy provider aliases
  are covered; six VN/Yahoo guard cases pass without upstream calls or repair
  jobs, and the real CLI preserves both isolated index candidates.
- [x] Rehearse both index candidates through an isolated loopback API: existing
  web daily/hourly controls, eight SDK SMA/EMA cases, four older SDK ranges,
  and every captured hourly record pass. Main data stays unchanged.
- [ ] Obtain explicit approval required by automatic review for the proposed
  canonical index snapshot replacement. It changes shared values and omits
  45 current native timestamps per index from the primary view, while retaining
  all original records in immutable before-images and backups. The rejected
  publication command has not run; isolated rehearsal is separate.
- [x] Implement native VN hourly handoff with market/interval-bound evidence,
  100 exact bars, five observed completed dates and minute-aligned labels.
  Both isolated index snapshots pass VPS with 200 exact bars across 34 dates;
  VNDirect/DNSE disagreement remains rejected. Isolated adoption, normal
  40-bar refreshes, populated backup and full 6,896-row/two-certificate S3
  restoration pass; main data and the original candidates remain unchanged.
- [x] Inventory all 71 selected tickers and 207 published series, including
  all 198 configured ingestion states, archive bounds and pending jobs. There
  were 58 series with pending work at that inventory checkpoint; the current
  database has seven pending jobs after rolling-floor completion, guarded
  cancellation of two obsolete placeholders and VPL listing-boundary completion. Readiness alone
  does not prove coverage.
- [ ] Verify complete coherent VND/VNINDEX older daily history; their public
  invalid rows persist and two archived-history gates remain open.
- [ ] Reconcile VNINDEX minute auction/timestamp/aggregate differences and the
  MSFT/NVDA/gold material minute disagreements before licensing native updates.
- [x] Retain enough Yahoo futures minutes for the five-date-partition handoff
  proof without expanding its six-day upstream range or relaxing OHLCV checks.
  Gold's real wider capture has 6,881 rows, ten conflicting records and 21 native
  timestamps absent from the public snapshot; its handoff remains rejected.
- [x] Trace gold minute discrepancies through legacy extraction and captured
  query variants. The current one-day response matches 1,020 retained records;
  dated/wider queries differ, including their first-row volume. Preserve raw
  response evidence and leave request-policy reconciliation open.
- [ ] Finish provider adjustment/session/calendar and independent minute/daily
  quality checks. Preserve wider historical availability across the selected
  universe; the available global minute histories do not prove a full year.
- [x] Audit all 59 selected VN tickers' observed daily/minute session pairs in
  the retained minute window: 14,632 sessions and no unmatched observed dates.
  Preserve 1,537 price-basis review findings across 12 tickers, including six in
  the latest 20 reference sessions. Dated TPB responses prove its recent daily
  and minute price bases differ even on VPS and the public legacy API; alternate
  providers use different minute adjustments. The real 443-record regression
  reports the difference without changing candles or creating inferred repairs.
  This does not prove an independent exchange calendar or resolve all bases.
- [ ] Complete wider web/CLI/SDK and cross-market historical/performance acceptance;
  preserve documented numeric/source/latency differences instead of hiding them.
  The installed SDK now passes 68 dated SMA/EMA cases for all 12 selected
  non-VN tickers' 2022 history, with independent complete-range OHLCV tail checks.
  Local S3 cold/warm reads pass for those tickers; actual BTC, VCB and NVDA web
  daily/15-minute/hourly controls pass. These checks leave wider dates, freshness
  and capacity open. EMA history-length dependence remains measured:
  full-range versus tail-query EMA200 differs by up to 32.20380231006493 in the
  captured BTC daily case; no candle disagreement is hidden by this result.
- [x] Compare indicator context with the live legacy API in all four markets.
  Both APIs exhibit SMA-seeded finite-lookback EMA differences that follow seed
  decay. BTC/Dow/SJC tail OHLCV and all five EMAs match exactly across APIs;
  full-range warmup differences and VCB's native/legacy candle disagreements
  remain recorded. Preserve inherited EMA behavior; reconcile those actual
  warmup/provider differences under the wider numerical acceptance gate.
- [x] Measure current selected VN local cold/warm reads, actual archive downloads,
  memory/database size and bounded HTTP load across all 59 configured tickers.
  Keep cloud costs/production capacity and missing cross-market ranges separate.
- [x] Replace per-candle archive inserts with a typed local CSV bulk load.
  Full 198,752-row Parquet output is byte-identical; local writing takes about
  one second versus 137 seconds, with full RustFS preparation/readback verified.
  Temporary-disk and observed process-memory costs remain explicit.
- [ ] Inventory/export private sync records and other data absent from public
  endpoints; verify counts and authentication behavior before production cutover.
- [ ] Finish production acceptance and review the prepared cutover/rollback
  result. Local implementation does not authorize deployment or routing changes.

## Agreed scope

- Project: `aipriceaction-api/`, Python with FastAPI.
- Keep existing web routes, request parameters, response shapes, CSV exports,
  aggregation behavior, sync authentication, and errors compatible.
- SQLite stores a rolling three-calendar-year daily window and one-calendar-year
  minute window. Compute cutoffs in UTC using calendar arithmetic, with an
  explicit leap-day rule. Archive older candles rather than deleting history.
- Historical and boundary-crossing API requests read S3 as needed. Indicator
  lookback can extend beyond local retention.
- Use plain compressed Parquet in S3 and embedded DuckDB initially.
- Maintain a selected ticker universe; preserve access to historical tickers.
- Favor coverage and freshness across more selected tickers over automatic
  full-history rebuilds. Corporate-action repairs are bounded by retained windows
  plus required lookback, but archived/local adjustment consistency is mandatory.
- Exclude VCI completely from new providers, fallback chains, and workers.
- Keep the existing Python `aipa` CLI and SDK; adapt compatibility where needed.
- Compose runs RustFS only. API and workers initially run locally. SQLite and
  DuckDB are libraries/files, not network services.
- Leave the legacy backend, production database, and old archive intact while
  building and verifying the replacement.

## Implementation defaults and outstanding data decisions

- [x] Choose an editable watchlist: 59 VN, four crypto, seven global daily
  series, and SJC daily quotes. Broader metadata remains discoverable.
- [x] Use VPS, VNDirect, and DNSE as the three implemented VN providers. This
  default was stated during autonomous implementation; Vietstock is excluded.
- [x] Default hourly retention to three calendar years; archive older hourly
  history. This is configurable and preserves the hourly API interface.
- [x] Implement one host, one archival writer, and local SQLite. Multi-host
  writers remain outside this design.
- [x] Measure representative archive sizes and queries to select yearly daily
  files and monthly intraday files, separated by source/ticker/interval and
  sorted by timestamp. Cold/warm monthly and one-year profile batches cover all
  55 selected VN tickers at four-reader concurrency. The active 389 Parquet
  objects used 3,881,127 bytes at that earlier 389-object checkpoint. Current
  archive inventories and transfer measurements are in `VALIDATION.md`; retained
  versions/evidence/manifests are separate. Production cost/load checks remain open.
- [ ] Establish each chosen provider's adjustment policy separately for daily and
  intraday data. Define how affected S3 history becomes consistent after an
  adjustment: validated revision from that provider or a verified adjustment
  mapping. A guessed scaling factor is not an acceptable shortcut.
- [x] Match legacy Yahoo hourly timestamp normalization and capture current
  public/native/minute-aggregation differences with original response evidence.
- [x] Publish six current stock/index hourly snapshots, preserve all 17,894
  previously stored dates, and verify complete hourly/four-hour HTTP exports,
  existing SDK SMA/EMA behavior, and actual hourly web controls with explicit
  freshness bounds. The publication adds 1,248 newer dates and records every
  retained correction, original before-image and populated backup.
- [x] Restore current gold hourly data without silently rewriting the 279
  quote-event timestamps in its 2026 public export. Permit only the observed
  legacy futures/hourly/zero-volume/flat-price shape and record explicit findings.
- [x] Extend exact-overlap certificates to Yahoo hourly snapshots with interval
  finality, race/job guards and restoration validation; enable and verify SPY.
- [ ] Verify and enable remaining native Yahoo hourly handoffs. Imported hourly
  snapshots stop before upstream reads or repair scheduling until independently
  verified. AAPL/MSFT/NVDA/S&P/Dow retain observed disagreements in longer native
  windows. Gold now has a current published tail, but its native dry run still
  disagrees with completed snapshot OHLCV.

## Phase 0 — Isolated plan and local infrastructure

### Commit 01 — `chore(api): add rewrite plan and RustFS development compose`

- [x] Add this roadmap, README, environment template, and local ignores.
- [x] Add only RustFS to Compose, with isolated volumes and loopback ports.
- [x] Validate Compose configuration without starting or pulling containers
  (`docker compose config --quiet` passed).
- [x] During the first runtime integration milestone, verify named-volume
  permissions, S3/console readiness, persistence across restarts, and S3 range
  reads. Pin the working image version/digest after this check.

Acceptance: Compose validates; S3 and console health, named-volume permissions,
restart persistence, range reads, and the pinned digest are verified.

## Phase 1 — Contract and Python foundation

### Commit 02 — `test(api): capture legacy web and SDK contracts`

- [x] Inventory `/tickers`, `/health`, `/tickers/group`, `/tickers/name`,
  `/tickers/info`, `/tickers/refresh`, `/analysis/top-performers`,
  `/analysis/ma-scores-by-sector`, `/analysis/volume-profile`, `/analysis/rrg`,
  `/sync/{key}`, `/explorer`, and `/public` behavior.
- [x] Read the actual web client and retain representative request/response
  fixtures; never record credentials or user sync payloads.
- [x] Capture repeated `symbol` parameters, modes/aliases, default limits, date
  bounds, chronological ordering, optional MA fields, SMA/EMA, legacy prices,
  CSV column order/precision, CORS, and relevant response headers.
- [x] Inventory direct SDK archive reads: legacy CSV paths, metadata, hashes,
  fundamental files, and live overlay. Preserve existing read access without
  introducing new VCI fetching for fundamental data.
- [x] Define meaningful compatibility checks for valid requests and error cases.

Acceptance: tests describe observed behavior and identify intentional differences
explicitly. No claims of drop-in compatibility based on route names alone.

### Commit 03 — `feat(api): add FastAPI package and application lifecycle`

- [x] Add Python packaging/lockfile, settings, consistent logging, app factory,
  shared error formatting, and resource lifecycle management.
- [x] Keep imports free of provider calls, database mutation, and network setup.
- [x] Add explicit operational entry points for API and worker processes.
- [x] Support local paths and configurable S3-compatible endpoints. Keep secrets
  out of logs and tracked files.
- [x] Start with `pytest` and `ruff`; avoid adding unnecessary tooling/services.

Acceptance: the app starts/shuts down cleanly and tests run without production
services. Existing `aipa` entry points remain intact.

## Phase 2 — Storage proof before the full rewrite

### Commit 04 — `feat(storage): add SQLite schema and candle repository`

- [x] Add versioned migrations for tickers, OHLCV, provenance, worker checkpoints,
  quality findings, sync records, and published archive objects.
- [x] Enforce unique `(source, symbol, interval, time)` candles. Store timestamps
  consistently and normalize daily candles without inventing prices.
- [x] Use WAL, busy timeouts, short transactions, controlled writes, and indexed
  source/symbol/interval/time queries.
- [x] Implement idempotent upserts, range reads, lookback reads, and stable
  multi-symbol queries. Do not conflate provider identity with market source.
- [x] Persist per-series update attempts, successful provider/revision checks,
  completed overlap bounds/counts, and provisional returned rows atomically with
  publication. Keep imports unverified, preserve earlier success on failed
  updates, invalidate proof after later writes/revisions, and preserve records
  through schema upgrades/backups. This tracks bounded live overlaps, not a
  certification of every retained candle or expected trading session.
- [x] Track provider and adjustment revision per series; stage replacements
  independently of the currently published candles.

Acceptance: duplicate imports are harmless, writes recover after interruption,
and API reads can continue while workers write.

### Commit 05 — `feat(archive): add Parquet storage and S3 object index`

- [x] Use DuckDB to write compressed, timestamp-sorted Parquet and read exact
  object lists. Avoid bucket-wide wildcard scans on ordinary API requests.
- [x] Index each published object by source, ticker, interval, time bounds, row
  count, schema version, checksum, and immutable object key.
- [x] Include provider/adjustment revision and pending-repair state in the archive
  index so readers cannot silently combine incompatible price histories.
- [x] Back up the archive index/manifest to S3 and implement reconstruction on a
  fresh SQLite database. Stage unpublished objects separately from active reads.
- [x] Add a bounded local file cache, atomic downloads, and checksum verification.
- [x] Exercise S3-compatible endpoint, credentials, path-style access, and range
  reads against RustFS. Use boto3 transfers and local DuckDB reads so runtime `httpfs` extension
  installation is unnecessary.

Acceptance: verified local/S3 round trips preserve candle values and timestamps;
missing or corrupt objects produce explicit failures rather than empty success.

### Commit 06 — `feat(storage): unify SQLite and archived history reads`

- [x] Select local/archive ranges by actual indexed coverage, including holes and
  transitions; do not assume a retention date proves archive availability.
- [x] Merge and deduplicate by candle identity with deterministic precedence for
  reconciled local data. Filter/limit after obtaining required history.
- [x] Check adjustment revisions before merging or loading indicator warm-up.
  Pending archive repairs may use a previously verified coherent view; if none
  exists, report the affected historical request as unavailable instead of
  returning a fabricated price discontinuity. Recent coherent reads remain usable.
- [x] Load indicator warm-up and aggregation bucket boundaries across both stores.
- [x] Support old date ranges, requests without dates, archived-only tickers, and
  limits extending beyond SQLite's recent window.
- [x] Run blocking DuckDB/file operations outside the async event loop, with
  bounded concurrency, memory, cache size, and query timeouts.
- [x] Benchmark representative recent, cold historical, cached historical,
  multi-ticker, and retention-boundary requests; record latency/memory evidence.
  Populated profile batches also cover 229,589 monthly and 2,843,368 one-year
  native candles across the 55 selected VN tickers. Broader historical datasets
  and sustained concurrent HTTP load remain acceptance work.

Acceptance: a narrow working prototype returns the same candle sequence for
recent, archived, and mixed queries. Stop and revise storage choices here if
correctness or measured performance is inadequate.

## Phase 3 — Web API replacement

### Commit 07 — `feat(api): implement candles metadata and health contracts`

- [x] Implement `/tickers` JSON/CSV, metadata routes, explorer/static support, and
  health fields using the shared history reader.
- [x] Expose observed per-source/interval date bounds and ingestion times under
  the existing health storage field. An old imported candle date remains visible
  independently of its recent ingest time; gather these counts in one SQL pass.
- [x] Add per-series health details so aggregate freshness cannot hide frozen
  snapshots. Report the last attempt/result and successful completed overlap;
  advancing the clock alone does not claim a completed provider recheck.
- [x] Implement native and aggregated intervals, including distinct `1m`/`1M`,
  VN session alignment, and weekly/monthly bucket behavior.
- [x] Port SMA/EMA, scores, changes, and price scaling against fixed legacy
  fixtures. Respect MA warm-up independently of output limits/date filters.
- [x] Add bounded response caching with data-version invalidation, preserving
  requested cache/Redis/snapshot flag semantics as documented by contract tests.
- [x] Preserve discovery of registered/imported/restored historical tickers in
  the existing name map. Expose disabled/archive-only status, archived bounds,
  local ingestion dates, and dated provider checks without claiming freshness.

Acceptance: web candle and metadata requests work without frontend changes,
including historical chart/CSV requests and boundary calculations.

### Commit 07a — `fix(metadata): discover historical tickers and archived coverage`

- [x] Merge historical identities into `/tickers/name` without changing its map
  envelope or source modes. Preserve catalog names, known registered names,
  and symbol fallback when company metadata is unavailable.
- [x] Include archive-only series in operational/HTTP health with separate
  published-object counts/bounds and pending-repair counts. Exclude superseded
  objects; preserve existing local counts and dated provider-check meanings.
- [x] Keep historical discovery independent of watchlist activation. Restored
  archive identities must not create workers, local candles, or live proof.
- [x] Verify discovery/source precedence, actual cold reads before/after index
  restoration, pending/superseded handling, and unchanged populated metadata.
- [x] Verify all 240 tests, lint/formatting, offline build, and actual packaged
  CLI/HTTP discovery and reconstruction. Rehearse 137 archive-only series and
  all 59 selected VN recent daily responses on local HTTP.
- [x] Verify unchanged public VNINDEX daily/15-minute charts and volume profile,
  plus six actual FPT SDK SMA/EMA cases, without changing production routing.

Acceptance: an available historical ticker remains discoverable after archival
or index restoration, and archived coverage cannot masquerade as a fresh update.

### Commit 08 — `feat(analysis): implement performers sectors volume profile and RRG`

- [x] Implement both RRG algorithms, benchmark alignment/trails, performers,
  sector MA scores, and volume-profile calculations.
- [x] Honor historical dates, source modes, thresholds, filters, and envelopes.
- [x] Use the shared reader so archived minute data supports old volume-profile
  requests and archived daily data supports historical analysis.
- [x] Compare sampled volume-profile and both RRG results with the legacy API;
  record explicit numeric tolerances and input precision differences.

Acceptance: analysis contract tests pass for recent and historical data, not just
empty envelopes. No silent fallback from minute profiles to daily estimates.

### Commit 09 — `feat(api): implement sync authentication and refresh scheduling`

- [x] Preserve UUID keys, bearer tokens, secret hashes, timestamps, JSON values,
  error statuses, and disabled-endpoint behavior for `/sync/{key}`.
- [x] Make sync secret verification/update atomic to avoid overwrite races.
- [x] Port refresh authorization and interval/source selection to worker
  schedules in SQLite; preserve the existing response contract.

Acceptance: existing web sync flows work; invalid tokens/secrets cannot read or
overwrite data. Authorized refresh requests schedule the correct workers.

## Phase 4 — Providers, recent-history quality, and operations

### Commit 10 — `feat(providers): add selected non-VCI VN and other market adapters`

- [ ] After provider selection, probe coverage/reliability for daily, hourly,
  minute, index, adjusted-price, cursor, proxy, and timestamp behavior.
- [x] Implement exactly the selected VN adapters with per-interval primary and
  fallback rules based on evidence. Never use VCI.
- [x] Implement required Binance, Yahoo, and SJC adapters for the active universe.
- [x] Add retries, backoff, rate limits, timeouts, and cursor-progress guards.
- [x] Reject conflicting duplicate timestamps inside requested provider pages,
  and reject internal gaps in continuous crypto pages. Excess unrequested old
  rows do not make a clean requested page fail validation.
- [x] Normalize the verified VN daily timestamp conventions (UTC midnight and
  DNSE session start) to the market date. Reject unknown/date-shifting conventions,
  including pure Vietnam-midnight replies, without guessing a date conversion.
  Preserve valid requested pages when unrelated excess old rows are unverified.
- [x] Retain provider provenance; report disagreements and incompatible adjustment
  bases rather than stitching incompatible candles into one series.
- [x] If fallback is necessary during a replacement download, restart that
  replacement from the selected fallback provider or verify equivalent adjustment
  semantics first. Do not mix provider pages merely because their dates align.

Acceptance: mocked provider tests cover parsing/failure modes, and controlled
read-only live probes establish available coverage. Missing provider history is
an explicit quality finding, not a claim of complete coverage.

### Commit 11 — `feat(workers): add bootstrap reconciliation and maintenance commands`

- [x] Bootstrap only configured tickers and required recent windows, with durable
  checkpoints, bounded concurrency, leases, restart recovery, and idempotency.
- [x] Advance leased configured bootstrap/repair floors with rolling retention;
  finish crossed bounds without obsolete fetches, preserve older published rows,
  and guard empty current windows and missing observed VN hourly session dates.
  Rehearse then complete 48 selected stock-hourly jobs with every candle/version
  unchanged. Hold IDC, GEE, VGI, VTP and VPL for remaining coverage/boundary proof.
- [x] Activate watchlists atomically and tolerate actual removals during another
  worker's run; test concurrent readers and preserve metadata/schedules/history.
- [x] Accelerate selected crypto minute bootstrap with checksummed Binance
  monthly files, bounded cache/ZIP sizes, complete sequence validation, and live
  API fallback for unpublished months. Verify sampled real API/file parity.
- [x] After a crypto-worker outage, enlarge the bounded live page to fill the
  elapsed gap. Queue durable recovery when one page cannot bridge it, preserving
  published data instead of appending across a hole. Verify a real restart.
- [x] Audit local continuous-market gaps/staleness and absent observed VN daily
  dates with bounded SQL. Flag weekend provider bars separately and resolve
  fixed audit findings without hiding independent repair findings.
- [x] Add explicit `refresh` passes for one source/native interval, optional
  configured ticker filters, and independent per-series outcomes. Preserve
  leases, frozen handoff guards, prior successful check records, and historical
  jobs; verify all 55 daily/52 adopted minute series and the actual CLI command.
- [ ] Re-fetch an overlap window for live updates/corrections; distinguish partial
  sessions from finalized candles and use market timezone/session calendars.
- [ ] Audit duplicates, stale series, expected-session gaps, invalid OHLCV, and
  corporate-action adjustments. Listing dates, suspensions, and legitimate
  no-trade periods must not be classified as missing data automatically.
- [x] Add operational commands: initialize DB/bucket, import, bootstrap, worker,
  reconcile, quality report, status, backup, and restore.
- [x] Adapt the existing Python CLI/SDK only where needed; keep user-facing
  commands and legacy archive reads compatible.

Acceptance: restart resumes correctly; gaps/conflicts are actionable and dated;
backup/restore is tested; no synthetic candles or automatic historical wipe.

### Commit 11a — `feat(workers): add bounded corporate-action recovery`

- [x] Capture the useful invariants of the existing Rust implementation in
  `../aipriceaction/src/workers/vci_shared.rs`, `vci_dividend.rs`, and
  `../aipriceaction/src/queries/vci_recovery.rs`: ignore unfinished candles,
  compare matching completed dates, require corroborating historical changes,
  keep replacement history on a consistent provider, validate newest coverage,
  publish atomically, invalidate caches, and resume repair after interruption.
  These are design references, not permission to use VCI.
- [ ] Treat price revisions as suspected adjustments/corrections, not proof of a
  dividend. Detect changes in either direction; distinguish provider switches,
  rounding, normal live updates, and isolated corrections from series-wide changes.
  Set tolerances/confirmation rules from provider evidence and regression fixtures.
- [x] Re-check completed overlapping candles during normal ingestion; add low-cost
  periodic historical samples to detect revisions outside the ordinary overlap.
- [x] Detect small corroborated revisions in either direction using a relative
  tolerance of `1e-6`; ignore representation noise and require three completed
  matching candles. Test staged recovery for a 0.1% change without overwriting
  published history. Provider-specific adjustment semantics remain unverified.
- [x] On confirmation, rebuild only the retained daily window and required
  indicator lookback. Repair retained minute/hourly windows only when their
  provider data actually changed; do not presume identical adjustment policies.
- [x] Download into staging, check cursor progress, coverage, valid values, and
  no regression of the published latest candle. Validate provider end-of-history
  claims rather than accepting a truncated replacement as complete.
- [x] Before atomic publication, require every completed published candle within
  the replacement window to remain present, including holes inside an intraday
  session. Sparse replacements stay pending and preserve the old revision;
  known invalid VN weekend daily bars are excluded from this guard.
- [x] Publish each verified interval revision in a short SQLite transaction and
  invalidate all dependent candle, aggregation, indicator, and response caches.
  On failure keep the previous verified revision, with explicit repair status.
- [x] Persist repair cursors, retries, revision IDs, and per-ticker leases. Give
  ordinary updates and other tickers bounded scheduling opportunities so one
  repeatedly failing repair cannot monopolize workers or provider budgets.
- [x] Mark affected S3 partitions pending and repair/revise them in a separate,
  bounded lower-priority queue. Do not trigger synchronous all-years re-downloads
  on every detection or user request, and do not overwrite legacy CSV snapshots
  with a mixture of old/new adjustment bases.
- [x] Test false positives, repeated detections, a provider failing halfway,
  changed prices in either direction, partial coverage, process interruption,
  stale cache invalidation, queue fairness, and adjustment boundaries in S3/SQLite.

Acceptance: retained data becomes coherent after a confirmed adjustment without
starving other tickers. Failed repair does not erase published data. Historical
charts and indicators never silently join incompatible adjustment revisions.

## Phase 5 — Migration, archival, and cutover evidence

### Commit 12 — `feat(migration): import legacy history and archive safely`

- [x] Inventory selected public API/S3 coverage per ticker/interval. Preserve
  original CSV/metadata and explicitly record unavailable or unverified ranges.
  Direct PostgreSQL is not a prerequisite for public market-data migration.
- [x] Stage frozen minute snapshots directly from public `/tickers` JSON in an
  isolated SQLite/filesystem archive, retaining original responses and timestamps.
  Gold's 198,752 observations through October 2 are now published and verified
  locally; full one-year coverage remains a separate acceptance gate.
- [x] Implement recent-window SQLite imports and older Parquet publication under
  a separate prefix, preserving legacy CSV URLs/metadata for direct SDK consumers.
- [ ] Complete remaining selected historical coverage and continuous-basis gates;
  do not infer full history from sampled imports or empty successful responses.
- [x] Make explicit public S3/HTTP CSV imports resumable, validate file date
  bounds/OHLCV/duplicates, freeze checksummed downloads per snapshot, checkpoint
  complete periods, and preserve newer local corrections on resume. Support
  per-day minute archives and legacy API exports for S3 gaps. PostgreSQL remains
  a migration source only, never a runtime dependency.
- [ ] Inventory/export private production data not exposed by public endpoints,
  including sync records, and verify source counts against imported snapshots.
- [x] Implement archive publication: snapshot eligible rows, upload immutable
  objects, verify by reading back, publish manifest/index, then prune only rows
  whose versions still match the exported snapshot. Prevent reconciliation races.
- [x] Add source/ticker/interval archive filters and rehearse a populated scoped
  UTC rollover: 5,860 crypto candles in 12 partitions, exact boundary queries,
  every surviving candle/version unchanged, and 713 objects restored from S3.
  This isolated dated rehearsal does not establish unattended scheduled operation.
- [x] Verify the ordinary crypto scheduler over 75 cycles with captured responses
  and populated backups: repeated minute updates, due hourly updates, honored
  daily cooldowns, continuous stored timestamps and protected history/metadata.
  Keep long-running supervised ingestion and scheduled canonical rollover open.
- [x] Add bounded compaction of verified fragments within yearly daily/monthly
  intraday partitions. Preserve corrections/provenance, reject concurrent repair
  changes before manifest publication, retain original immutable objects, and
  verify real rollover/compaction/index restoration.
- [x] Handle historical corrections as new partition revisions; keep referenced
  old objects while readers/migration need them. Retain local rows on failures.
- [x] Add archive-only reconciliation to re-fetch legacy partitions from the
  current provider without rebuilding recent data; prove it on all five migrated
  FPT daily partitions with exact timestamp coverage and coherent boundary reads.
- [x] Add bounded selected-universe daily migration, independent yearly errors,
  configured listing bounds, and scoped archive repair. Rehearse five additional
  VN tickers' 2019–2023 partitions with exact provider timestamp coverage.
- [x] Reverify completed retained prices immediately before archive publication.
  Changed prices trigger retained recovery first; an overlap outage preserves
  completed staging for a publication retry without another historical download.
- [x] Reject replacement of a superseded target by a different candidate before
  advancing the manifest pointer; permit retry of the same published candidate.
  Retire only unleased jobs whose immutable targets are missing/superseded.
- [x] Include the complete final daily date when fetching a session-start
  timestamp; provide an explicitly scoped restart of unleased failed archive
  staging. Verify SHS 2023 publication without changing recent data or originals.
- [x] Support explicitly reviewed 2018 closure exclusions, with original flat
  zero-volume placeholders, primary references, exact remaining timestamp
  coverage, fresh retained checks, and revalidation during index restoration.
- [x] Extend FPT daily history through 2014, preflight all ten archives and the
  retained window on VNDirect, replace the entire daily basis coherently, and
  verify HTTP indicators, ordinary updates, fresh index restoration, and backup.
  Preserve measured legacy value disagreements as an open quality finding.
- [ ] Validate wider provider/adjustment revisions across imported legacy history
  and newly fetched recent windows before publishing them as continuous series.
  The isolated October 4 FPT public minute candidate supplies missing August
  warmup and makes the older 15-minute EMA query readable, preserving every
  retained timestamp. It changes 459 existing candles and fails native VPS
  handoff on six recent candles; it is not published. Preserve both snapshots
  and obtain adjustment/correction witnesses before accepting a replacement.
  Fresh DNSE witnesses agree with the public API at those six candles but have
  two other recent differences; VNDirect matches public data at those two and
  differs at 42 candles across its wider overlap. No provider handoff is licensed
  by these contradictory witnesses. Validation now exposes the specific reason.
- [ ] Recover the remaining VND 2020 unavailable range and VNINDEX 2020 pending
  partition through complete coherent validated history. Preserve invalid public
  originals and reject guessed corrections or partial incompatible splices.
  Fresh public JSON checks reproduce VND's invalid February 19 candle; VNINDEX
  public JSON exactly preserves the pending partition, while its pinned native
  source fails September 29. Alternate sources provide valid 2020 records but
  disagree with retained data; wider VND candidates remain incomplete. See the
  October 4 daily archive evidence in `VALIDATION.md`; PostgreSQL is not blocking
  these public imports.
- [x] Add `recover-public-year` for exact public/original CSV daily-year captures:
  preserve invalid records and immutable originals, publish valid dates on a
  separate frozen revision, and retain full-year native/mixed-window guards.
  Test atomic publication, failed readback, manifest retries/restoration,
  unchanged retained records, explicit invalid-date and indicator-context gates.
  Complete coherent recovery of the invalid VND date remains open.
- [x] Provide dry-run plans for migration/pruning and test interruption at every
  publication stage. No production writes/deletion in development.

Acceptance: historical data remains accessible before/after migration and pruning;
failed uploads, stale exports, and restarts cannot lose or silently truncate data.

### Commit 12a — `feat(migration): verify minute snapshots before provider handoff`

- [x] Verify an exact completed-session overlap before handing imported minute
  data to VPS/VNDirect/DNSE. Preserve original prices and provenance.
- [x] Persist snapshot/overlap hashes, bounds, session counts, capture cutoff,
  provider choice, and the limited scope of the observed equivalence.
- [x] Reject mismatches, truncated overlap, concurrent snapshot changes, active
  repairs, and later imports reusing an already adopted snapshot revision.
- [x] Carry evidence through SQLite backups and S3 manifest/index recovery;
  retain detection of later completed-price revisions after adoption.
- [x] Verify the real FPT handoff and a subsequent normal VPS update; verify
  the actual RustFS index and SQLite backup retain the evidence.
- [ ] Resolve VNINDEX's auction/session/volume differences before its native
  handoff. The refreshed public snapshot and all three native replies remain
  independently preserved; omitted auction rows and aggregate differences are
  unverified. See current diagnostic evidence in `VALIDATION.md`.

Acceptance: each adopted series has explicit, recoverable evidence and an
operational update path. Rejected series remain unchanged with actionable
quality findings; a sampled overlap does not prove full-history equivalence.

### Commit 12b — `feat(migration): verify complete sparse trading sessions`

- [x] Add an explicit complete-session handoff option alongside the existing
  1,000-candle default. Require every original timestamp in at least five
  completed weekday sessions, positive minute volume, and exact OHLCV matches.
- [x] Compare each day's aggregated minute OHLCV, including total volume, with
  fresh provider daily candles and a ready retained daily revision. Reject
  missing sessions and any concurrent minute/daily correction or state change.
- [x] Store compared original/provider minute and daily records, bounded counts,
  checksums, finality and revision metadata. Replay all certificate checks
  during S3 index restoration; reject checksum-valid malformed evidence before
  changing the target index. Keep imported candle values/provenance intact.
- [x] Test successful append, missing/truncated/extra minutes, daily identity,
  price/volume/coverage differences, sparse session counts, zero volume,
  concurrent daily changes, and receipt restoration/corruption.
- [x] Finish real handoff/update/HTTP and populated restoration checks for all
  passing sparse series; preserve measured mismatches in the remaining series.
- [x] Resolve VGC with independently corroborated bounded corrections, and
  PLX/SSI with complete refreshed public snapshots plus exact complete-session
  native handoffs. Preserve original evidence; do not infer minute allocations.
- [ ] Resolve VNINDEX timestamp and aggregate disagreements independently.

Acceptance: sparse observations license updates only through the full-session
proof, with replayable evidence and unchanged published prices/volume. This
does not establish a provider's lifetime adjustment policy or certify holidays.

### Commit 12c — `feat(migration): corroborate bounded minute corrections`

- [x] Require an explicitly selected second provider to confirm every changed
  native candle in the complete-session window. Preserve the original records;
  never insert/drop timestamps or infer a dividend factor.
- [x] Require identical original/candidate 15-minute OHLCV and the existing
  fresh/retained daily corroboration. Replay both witnesses during restoration.
- [x] Publish corroborated corrections and the provider handoff together in one
  transaction, retaining original provenance for all unchanged candles.
- [x] Verify the actual VGC case, subsequent updates, API reads, and populated
  evidence restoration; reject the uncorroborated PLX/SSI cases.

Acceptance: each changed native candle has a second-provider witness, retained
original evidence, and an atomic publication. Larger-interval equivalence alone
cannot authorize a correction or a provider handoff.

### Commit 12d — `feat(data): expand the verified VN daily and minute universe`

- [x] Preflight OCB, NAB, PNJ, and DGC daily windows in isolated SQLite, then
  capture full-precision legacy minute JSON and compare all observed daily dates.
- [x] Require default exact overlaps or the existing complete-session proof,
  including fresh/retained daily OHLCV. Archive bounded minute indicator lookback
  before handoff; verify ordinary daily/minute updates without changing revisions.
- [x] Reconcile selected older daily partitions on each pinned provider before
  publishing an addition. Initially hold NAB back after its original 2022 CSV and
  pinned VPS recovery both fail OHLC validation; retain the original bytes.
  Later coherent native recovery publishes its recent windows and restores the
  original 2022 dates; that checkpoint remains in `VALIDATION.md`.
- [x] Publish OCB, PNJ, and DGC candle/state/check/adoption/archive metadata
  atomically after a populated before-backup and immutable S3 upload/readback.
  Explicitly select daily/minute ingestion; preserve native hourly limitations.
- [x] Verify recent/cold/boundary HTTP reads, SDK SMA/EMA parity, the unchanged
  PNJ public chart/profile, the packaged watchlist, S3 metadata reconstruction,
  and a populated SQLite restore. Record the measured legacy-value differences.

Acceptance: each published addition has verified recent windows, recoverable
provider evidence, coherent selected cold history, and an ordinary update path.
Failed candidates remain isolated; these checks do not certify every corporate
action, holiday, historical year, or provider adjustment convention.

### Migration quality and historical corrections

- [x] Audit retained VN minute/daily basis and volume disagreements separately
  from coverage. Preserve independent findings when a provider handoff succeeds.
- [ ] Resolve the independently recorded minute/daily disagreements and establish
  provider/session semantics; an exact bounded handoff is not lifetime proof.
- [x] Persist unavailable-history records and pending-repair references through
  manifests/restores. Return explicit historical failures instead of silently
  empty data or unverified indicator seeds.
- [x] Publish verified complete native/public daily revisions atomically, with
  readable-date preservation, immutable before-images, fresh overlap checks and
  populated backups. Verify recovered EIB/HHS dates and the repaired GEX/HAG/SHS
  partitions; preserve FPT pre-2018 and other earlier verified archives.
- [x] Scope repairs to retention/listing floors, include verified final dates,
  detect source timestamp conflicts, and preserve originals during failures.
- [x] Serve hourly aggregates for minute-only selected entries. Preserve native
  hourly limitations and honest metadata/health state.
- [x] Pin public API capture read paths/revisions; reject backend or format
  changes within frozen snapshots. Public candles migrate without a PostgreSQL
  client; private sync inventory remains separately required before cutover.
- [x] Catch up bounded VN/Yahoo outages with observed overlap and source pinning;
  verify native rehearsals and preserve unavailable close-time rows as explicit
  limits rather than inferred data. Distinguish committed recent observations
  from optional historical-probe failures.

Acceptance: each published revision preserves all previously readable dates or
has explicit verified exclusion/recovery evidence. Failed replacements and
independent quality findings remain visible. Historical data is not wiped to
make recent checks pass.

### Commit 13 — `test(api): verify replacement and document deployment`

- [x] Run compatibility, archive-boundary, worker-recovery, and concurrency checks.
- [x] Include an adjustment occurring after history was archived and a provider
  switch during repair; verify coherent recent/history reads and honest repair
  status before claiming replacement compatibility.
- [ ] Compare selected real recent/historical responses with the old backend;
  audit numerical discrepancies before calling the API a drop-in replacement.
- [ ] Exercise the actual web client against the new API and the existing CLI/SDK
  against retained legacy archives plus the new API.
- [x] Rehearse selected public chart controls (daily/15-minute) and volume
  profiles with isolated browser routing; verify sampled SDK candles/SMA/EMA
  and an existing `aipa get-ohlcv-data` command against the local API.
- [x] Fix SDK stale-tail mixing without changing methods/CLI options; retain
  archive-only behavior and whole-series fallback with explicit provenance.
- [x] Support full-precision legacy API JSON migration, preserving frozen raw
  responses and validating symbols, dates, OHLCV, truncation, empty retries,
  and receipt format changes. Rehearse an isolated SPY session/week capture.
- [x] Recapture full-precision AAPL/SPY snapshots and license verified Yahoo
  updates; preserve every existing timestamp/volume and old rounded originals.
- [ ] Resolve actual MSFT/NVDA/gold timestamp/volume/price disagreements before
  licensing live Yahoo updates; keep the failed candidates and frozen data.
- [x] Extend the default exact-overlap handoff to Yahoo with UTC minute finality,
  market/provider validation, preserved provenance, race/lease guards, and
  restoration checks. Enable and verify the two passing index minute series;
  preserve failed stock/gold snapshots and restrict VN correction proofs to VN.
- [x] Preserve undefined API indicators as missing SDK values. Rehearse crypto
  daily/minute/15-minute and global daily/weekly SMA/EMA and public web controls.
- [x] Record populated local recent, multi-ticker, cold/warm historical, and
  retention-boundary latency, memory, database size, and cached object bytes.
  Keep concurrent-load and full-universe S3 transfer-cost acceptance open.
- [x] Run a bounded concurrent HTTP rehearsal covering all 59 selected VN
  daily/15-minute series plus historical/crypto/global/health requests. Compare
  stable candle payload hashes, disable response caching, and record failures
  and observed per-request latency. Longer production-scale load and cold/cloud
  transfer measurements remain open.
- [x] Add a reproducible native-history benchmark with downloaded object-byte
  measurements, full candle/provenance identity, successful timings separated
  from errors, source-backed pre-listing exclusions, and unchanged-main checks.
  Verify all 58 eligible 2022 daily histories and all 59 selected archived-minute
  series; verify the 2020 check fails only on VND/VNINDEX after documented
  SSB/GEE/VPL/OCB pre-listing exclusions. Keep cloud billing/production load open.
- [x] Record current selected VN cold/warm latency, memory, SQLite size and
  actual archive transfer bytes; verify the direct and module benchmark commands.
- [ ] Restore missing selected cross-market archives and measure their cold reads;
  do not count empty ranges as fast successful reads. Complete deployment/cloud
  cost and capacity checks separately; local timings do not imply identical latency.
- [x] Document startup, backups, archive-index restore, runtime flags, and rollback.
- [x] Decide whether optional API/worker containers are useful; keep RustFS the
  only mandatory Compose dependency. Pin tested dependencies/images.
- [x] Prepare a concrete production cutover/rollback procedure for review. Do not
  switch the live endpoint or remove old data merely because tests pass locally.

Acceptance: recent and old web requests work end to end, operational recovery is
demonstrated, known data-quality/coverage limits are documented, and rollback
retains the existing backend and archive.

## Working rules

- Complete one proposed commit's scope and relevant checks before expanding it.
- Use ordinary workspace edits and short, separate commands. Avoid `sudo`,
  recursive host permission fixes, destructive volume commands, and unnecessary
  approval-dependent operations.
- Do not weaken sandbox/approval controls or assume every command can be auto
  approved. If a restriction blocks a step, report it and continue unaffected work.
- Make local Git commits for cohesive, verified changes, as explicitly requested
  by the user. Proposed phase titles do not authorize production changes or pushes.
- Keep unresolved user decisions visible; proceed with independent work rather
  than repeatedly requesting confirmation for routine reversible implementation.
