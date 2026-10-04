# Validation — 2026-10-03

The replacement runs locally. This report separates implementation checks from
full data coverage and production cutover acceptance.
Earlier snapshot parity checks describe their recorded fixtures. The current
provider comparisons below record remaining price/volume differences
explicitly and do not claim exact numerical identity with the legacy API.

## Public migration and hourly request-window evidence — 2026-10-04 ICT

A direct public `/tickers?symbol=VCB&source=vn&interval=1D&limit=2&format=json`
request returns HTTP 200 and a JSON object keyed by `VCB`. The implemented public
JSON importer remains the primary migration path. No legacy PostgreSQL connection
is needed for these exports. Existing provider disagreements concern ongoing
ingestion compatibility, not permission to download public data.

The hourly diagnostic now retains exact differences and separately reports
material differences under the existing handoff tolerance: absolute price
`1e-8`, exact volume. Missing flat zero-volume observations remain coverage
failures. Canonical epoch changes also fail the check. Three focused regression
tests pass, with lint and formatting checks; the last complete application suite
remains the 451-test run recorded below.

Fresh S&P and Dow hourly handoff proofs both reject a single material volume
difference in their 200 returned bars, with no missing native timestamps. At the
September 24, 2026 13:00 UTC canonical hour label, public volume is zero; wider
native requests return 530,160,293 for S&P and 81,932,011 for Dow. OHLC prices
agree within the handoff tolerance. Frozen-body replay evidence is
`data/global-index-hourly-handoff-probe-20261004/replay-comparison.json`.

Eight additional read-only requests establish a request-window dependency for
that same raw 13:30 UTC observation. Requests starting September 23 return those
nonzero volumes, while requests starting September 24 return zero, with identical
OHLC. Both `60m` and `1h` wire intervals produce the same respective result.
Original responses and URLs are captured in
`data/global-index-hourly-volume-windows-20261004/report.json`; canonical epoch
remains 3526 before and after. This demonstrates that disagreement need not mean
an adjustment. It does not establish which volume is authoritative or license
replacement, selective request-window trimming, or native handoff. Public
snapshots remain preserved while the request policy is reconciled.

## Verified VPL hourly listing boundary — 2026-10-04 ICT

The [regulator's listing notice](https://ssc.gov.vn/webcenter/portal/ubck/pages_r/l/chitit?dDocName=APPSSCGOVVN1620154820)
and [HOSE's May 13 trading report](https://staticfile.hsx.vn/Uploads/UploadDocuments/2374107/20250513%20Tong%20hop%20thong%20tin%20giao%20dich.pdf)
confirm VPL's first trading day as May 13, 2025. Its configured floor is midnight
UTC that day; the earliest staged/published native hourly bar is 02:00 UTC.
A dated DNSE request returns the two first-day bars. The request ending before
the earliest bar returns all six explicit empty arrays and integer `nextTime=0`,
without TradingView's status field. The old adapter incorrectly reports that
well-formed response as invalid/missing arrays. Original bodies are retained in
`data/vpl-listing-boundary-probe-20261004/`.

The adapter now recognizes that exact empty DNSE shape. It continues rejecting
missing arrays, nonzero/boolean hints, error fields and the same undocumented
shape on other providers. The worker can finish a VN hourly bootstrap's empty
listing-day prefix only when its linked configured date equals the current
floor, cursor equals the earliest staged/published bar on that same day, and
the pinned provider/current ready revision match. Ordinary no-data exhaustion,
later-day gaps, repairs and unsourced dates remain incomplete. Existing observed
daily-date and atomic record-preservation guards still apply. No timestamp,
price or volume is invented and no adjustment factor is applied.

The full suite passes **451 tests**, including **18 new regression cases**;
focused provider/worker tests pass **142 tests**. Lint, formatting and offline
package builds pass. The known Starlette/httpx deprecation warning remains.

Populated rehearsal and canonical completion preserve every version of all
**6,019,512** candle records, indexed in logical primary-key order. Only VPL's
job/staging/schedule, related resolved findings and epoch change. Other jobs,
staging, tickers, quality records, providers, revisions, archives, handoffs,
recoveries and sync data are exact. Its **1,733** staged rows are cleared after
successful publication, preserving identical served records. Full **1,733-bar**
hourly and **697-bar** four-hourly HTTP responses are byte-identical before and
after. Canonical epoch advances **3525 → 3526**; pending jobs fall **8 → 7**.
Observed-date completion is not proof of every exchange-calendar slot.

Evidence is `data/vpl-listing-completion-rehearsal-20261004/report.json` and
`data/vpl-listing-completion-canonical-20261004/report.json`, with original
checksummed HTTP/provider responses. The consistent before-image is the
rehearsal's `before.sqlite3`. The checked canonical after-backup is
`data/vpl-listing-completion-canonical-20261004/after.sqlite3`, SHA-256
`c75448cb85868013cfba7e43f99c56ff9a9f13b3647bf8796a3cc1aa590a5291`.
SQLite `quick_check` returns `ok`; four stock hourly jobs and three other jobs
remain pending. The first canonical script invocation stopped at a syntax
error before execution; the corrected script was compiled before its successful
run. The previously rejected index replacement remains untouched.

## Inherited bootstrap placeholder cancellation — 2026-10-04 ICT

PLX and SSI had never-started minute bootstrap jobs created before their
complete public snapshot migrations and verified VPS handoffs. Their obsolete
job revisions differ from the currently served revisions. A restarted worker
could otherwise repeatedly fetch an obsolete backfill that cannot finish.
Normal bootstrap startup now cancels such untouched placeholders atomically
only when the current ready provider/revision has valid adoption evidence,
the snapshot starts at or before the configured floor, and the job predates
verification by at least one recorded second. Attempts, cursor, chosen provider,
leases, staging, later jobs, repairing states and missing evidence prevent
cancellation. This is not a new coverage certificate or a provider change.

The full suite passes **433 tests**, including the inherited publication case,
idempotence and 12 protected cases. Lint, formatting and offline package builds
pass. The known Starlette/httpx deprecation warning remains unchanged.

The populated rehearsal verifies hashes/counts of every row in every table.
Only `jobs` and the epoch in `meta` change; all **6,019,512** candle versions and
all staging, archives, handoffs, recoveries, sync records, schedules and quality
findings are exact. Repeating bootstrap makes no additional changes and performs
no provider requests. The canonical application reproduces that result: only
the two job rows' status/reason/update time change, epoch **3523 → 3525**, and
pending jobs **10 → 8**. The index publication and historical quality gates
remain untouched.

Evidence: `data/unstarted-bootstrap-cleanup-rehearsal-20261004/report.json` and
`data/unstarted-bootstrap-cleanup-canonical-20261004/report.json`. The canonical
before-image is the rehearsal's consistent `before.sqlite3`; its unchanged
snapshot was checked before application. The canonical checked after-backup is
`data/unstarted-bootstrap-cleanup-canonical-20261004/after.sqlite3`, SHA-256
`bfced2fff1a6fc25b76cbd4991ab3f29a5d87d8e5a1d9f703bb9a6c4562529d2`.
SQLite `quick_check` returns `ok` and the saved backup contains eight pending
jobs. Original cancelled job identities and cancellation reasons remain stored.

## Dated selected-universe web query coverage — 2026-10-04 ICT

The read-only web query matrix now accepts `--start-date`/`--end-date`, verifies
response bounds, and distinguishes empty history from wrong-symbol responses.
It uses the existing quote rules for SJC's previous-midpoint open and Yahoo's
daily futures settlement close, while retaining strict traded ranges for stocks
and intraday futures. Finite negative futures prices remain valid. Explicit
quote/settlement/negative-price and invalid traded-range smoke checks, lint and
formatting pass; API source code is unchanged by this verifier extension.

For **September 28–October 2, 2026**, all **633 requests** pass across the
configured **71 tickers**: **531 VN**, **36 crypto**, **63 Yahoo** and **3 SJC**.
Daily, 15-minute and hourly controls are tested with no indicators, SMA and EMA;
SJC remains daily-only. The responses contain **9,465 records**, ordered inside
the requested dates. A newly started current-code API on loopback port 3003
passes the same 633 requests, byte-identical to the older local process on 3001.
The isolated current-code server shuts down cleanly after verification.
This proves the sampled dates/schema and local version parity, not every
exchange session, live freshness, full-year coverage or numerical legacy parity.

For **October 1–3, 2025**, **584** of **633 requests** pass; **48** return HTTP 200
with `{}` and the historical FPT 15-minute EMA case returns its existing revision
guard with HTTP 503. The empty responses represent 14 global intraday/hourly
ranges and two index hourly ranges, each with three indicator settings.
Bounded database-backed public JSON checks return no candles for all 14 global
ranges, matching local availability for that capture. Both VNINDEX and VN30
public hourly ranges return **15 records each** and are genuine local coverage
gaps covered by the already staged index replacements. This investigation does
not authorize the previously rejected canonical index replacement.

Evidence: `data/web-historical-retention-matrix-20261004.json`,
`data/web-historical-empty-public-probe-20261004/report.json`,
`data/web-recent-session-matrix-20261004.json`,
`data/web-current-code-session-matrix-20261004.json` and
`data/web-current-code-session-parity-20261004.json`, with checksummed original
responses. Canonical epoch remains **3523**. The unchanged-state guard compares
all VN daily record versions, total candle counts and operational tables,
including epoch; it does not individually hash other candle versions.
There are **10 pending jobs**, including five stock hourly jobs; FPT's earlier
bootstrap job completed under the rolling-floor guard. Its three public-only
hourly timestamps and the wider quality findings remain separate open work.

## Extended FPT public minute candidate — 2026-10-04 ICT

Bounded database-backed JSON requests to the public `/tickers` API produce an
isolated new revision, `legacy-api-fpt-extended-20261004`. PostgreSQL is not used.
The candidate contains **66,198 records**, with **55,801** in SQLite and **10,397**
in four filesystem Parquet objects. It preserves every canonical timestamp and
adds **4,746** August 2025 records. All records pass normal candle validation.
The original capture report stops at its mixed-revision comparison guard; its
downloads remain intact. The subsequent raw-inventory verification uses the
normal History reader for candidate calculations and preserves the original
failed report.

`scripts/check_staged_public_history.py` verifies four historical requests:
minute/15-minute SMA and EMA, each returning 20 records through October 3, 2025.
The canonical 15-minute EMA request fails with incompatible adjustment revisions;
the extended candidate returns all 20 records with sufficient coherent warmup.
The other three reads succeed on both stores. This is a data-layer check, not a
completed HTTP/web/SDK replacement rehearsal.

The full retained comparison reports **459 changed candles**: **453** on
January 2–3, 2025 and six on September 28/30 and October 1, 2026. A fresh VPS
response covers **1,130** records across five completed sessions with no missing
timestamps. It agrees with canonical OHLCV within the existing absolute price
tolerance of `1e-8` and exact volume checks. The new public candidate has six
material mismatches against that native response; the provider adoption dry run
rejects it. Raw exact differences, including floating-point representation, are
preserved separately. No native values are rounded or overwritten.

Evidence is retained in `data/fpt-extended-public-snapshot-20261004/`,
`data/fpt-extended-public-verification-20261004/report.json` and
`data/fpt-extended-native-verification-20261004/report.json`, with original
checksummed response bodies. Canonical epoch remains **3523** and the canonical
FPT record/provenance digest is unchanged. The candidate remains unpublished;
historical adjustment and recent correction witnesses still need verification.

The subsequent three-provider investigation preserves bounded original responses
and uses a separate candidate database seeded with retained daily records. DNSE
agrees with the public snapshot at all six recent VPS disagreements. Therefore
those six VPS corrections lack independent DNSE witnesses; complete-session
adoption is rejected before publication. A wider **2,000-record** DNSE response
also differs from the public snapshot at two other September 30 minutes, Unix
timestamps `1790741220` and `1790741280`. A fresh DNSE adoption attempt rejects
that snapshot. VNDirect matches the public snapshot at those two minutes, so it
does not corroborate the DNSE correction either. Its complete observed
2,000-record overlap has **42** material price/volume differences from the public
snapshot. These are provider disagreements, not a verified dividend event or a
license to infer historical scaling.

Original response bodies and candidate/replay results are under
`data/fpt-extended-corroborated-candidate-20261004/`,
`data/fpt-extended-dnse-adoption-20261004/` and
`data/fpt-extended-vndirect-witness-20261004/`. All canonical epochs remain
**3523**. No candidate receives a provider adoption certificate; canonical data
and its existing provider remain unchanged. Neither the failed complete-session
attempt nor the head comparisons prove a full historical adjustment basis.

Adoption validation now preserves the underlying reason, for example
`Invalid snapshot adoption evidence: Minute correction is not independently corroborated`,
instead of returning only the generic evidence error. Both actual publication
attempts and restored/tampered receipts continue rejecting a changed witness.
The full suite passes **420 tests**; focused adoption tests pass **47 tests**,
lint and formatting pass. The existing Starlette/httpx deprecation warning remains.

## Opt-in worker daily retention maintenance — 2026-10-04 ICT

`worker --archive-daily` runs the existing verified archive publication and
exact-version pruning after ingestion cycles, once per captured UTC date.
Market/ticker/interval flags scope its stored-series selection. An unavailable
object store defers maintenance while subsequent ingestion cycles continue;
retry attempts are spaced at least 60 seconds apart. Concurrent corrections
remain local and keep maintenance incomplete until a later export/prune succeeds.
Restarting rechecks the database and does not duplicate existing objects.
The default worker behavior and HTTP API interfaces remain unchanged.

The full suite passes **420 tests**, including actual filesystem Parquet rollover,
partial object-store failure/recovery, same-day/restart idempotence, preserved
concurrent corrections and continued worker cycles during archive failure. Lint,
formatting, CLI option discovery and offline package builds pass.

A real populated crypto worker runs **75 cycles** with the opt-in flag from
October 4 **00:28:58 to 00:30:28 UTC**. All **12 scheduled updates** succeed: two
minute attempts and one hourly attempt per ticker; daily cooldowns are honored.
It adds **78 minute candles**, bringing the main database to **6,019,512 records**.
BTC/ETH minute tails reach **00:29 UTC**, BNB/SOL **00:30 UTC**. All stored crypto
series remain continuous. **547 completed native candle observations** match
captured Binance responses exactly. Completed crypto OHLCV/provenance, all
noncrypto candle fields/versions, series, jobs, archive index, handoffs and import
receipts stay unchanged. No rows were expired during this run, so canonical
maintenance publishes no objects.

A populated isolated rehearsal exercises the scheduler's simulated **October 5
UTC** tick. It publishes and prunes **12 crypto partitions / 5,860 expired rows**
only in the copy, preserves exact storage-boundary reads and every surviving
candle/update version, and passes `quick_check`. Same-day calls skip further
work; a restarted scheduler rechecks and publishes zero objects. A fresh
database restores **902 objects** from its unique rehearsal manifest and reads
all moved records exactly. The canonical epoch remains **3523** throughout.
This proves populated scheduled-retention mechanics, not an actual overnight
or multi-day supervised daemon.

Backups, native responses and reports remain under
`data/crypto-worker-daily-maintenance-20261004/` and
`data/crypto-daily-maintenance-rehearsal-20261004/`. Immutable RustFS receipt:
`data/worker-daily-maintenance-evidence-20261004.json`. Latest canonical backup
SHA-256: `522496c1ef13a2d9ac32d5ca900925ead85504a1bad6d68ed9af28e5f29dd92c`.
Historical/provider coverage, actual multi-day supervision and production
cutover gates remain open.

## Executed remaining-market rollover — 2026-10-04 ICT

The due Vietnam/global/gold rollover publishes **177 immutable Parquet objects /
12,387 expired records**: 58 VN daily candles, 260 VN hourly candles, 12,061 VN
minute candles, seven Yahoo daily candles and one SJC daily quote. Exact exported
versions are pruned after upload/readback verification. No expired local rows
remain at the checked October 4 UTC retention cutoff. This does not establish
full coverage where an upstream series begins after the configured floor.

Cold readback preserves every moved value, provider, revision and update version.
Every partition's boundary query includes the first remaining local observation,
including the weekend between the expired VN Friday and retained Monday minute
sessions. Exhaustive before/after comparisons preserve every surviving candle
and version, with no new records and exactly the 12,387 selected missing local
rows. Series, source checks, jobs, quality records, ticker schedules, handoffs
and import receipts remain exact; original archive-index records are preserved.
SQLite passes `quick_check`. The main record count falls from **6,031,821 to
6,019,434**, and the epoch advances **3157 to 3511**. Index observations are moved
without changing their values or replacing the held hourly snapshots.

The canonical manifest restores **890 objects** into fresh SQLite with a separate
cold cache; every moved record reads back exactly. All **177 HTTP boundary queries
/ 24,980 records** match the populated before-image through the unchanged API.
Installed SDK checks pass **eight SMA/EMA cases / 152 records**: archived FPT
native minutes, a current short FPT 15-minute session, and NVDA/SJC daily history
spanning the boundary. SDK/API fields match exactly in these cases. Full-range
indicator context differences remain recorded separately.

The verifier initially assumed 20 records for a valid 16-record VN aggregate
session. Its first adjustment used a forward query, changing indicator context.
The final verifier matches the SDK's actual backward request and local start-date
filter; it verifies shorter ranges without trimming unexpected API output.
Checker lint, formatting and offline package builds pass.

A distinct historical FPT 15-minute EMA request ending October 3, 2025 still
fails its revision guard: the 600-aggregate lookback reaches an older January
`legacy-snapshot` archive beyond the coherent adopted September data. Direct
before/after snapshot queries produce the identical error, proving it predates
this rollover. Dated pinned VPS probes return no August/September 2025 minutes,
while the October 2, 2026 head returns 100 observations. Captures are preserved;
no missing data, adjustment factor or revised provenance is invented. This
older indicator-context gate remains open despite raw boundary preservation.

Backups, publications, HTTP responses, SDK results and error diagnostics remain
under `data/all-market-canonical-rollover-20261004/` and
`data/fpt-older-warmup-native-probe-20261004/`. Immutable RustFS receipt:
`data/all-market-canonical-rollover-evidence-20261004.json`. Canonical after-backup
SHA-256: `fac9974d7e840e3d63729b2b09005bce7cdbbd350ad2547b5ca33b6150065229`.
Other historical/provider-basis gates, unattended scheduling and production
cutover remain open.

## Executed canonical crypto rollover and subsequent ingestion — 2026-10-04 ICT

After the dated isolated rehearsal, `archive --source crypto --execute --prune`
executes the due October 4 UTC rollover on the main local database. It publishes
**12 immutable Parquet objects / 5,860 expired candles** to canonical RustFS:
four daily candles, 96 hourly candles and 5,760 minute candles. Each object is
uploaded and verified before its exact exported local versions are pruned.
SQLite now begins crypto daily/hourly data at October 4, 2023, and minute data
at October 4, 2025. A follow-up eligibility check finds no expired crypto rows.

Cold object readback and boundary history match the populated before-image
exactly, including every value, provider, revision and update version. An
exhaustive comparison finds no changed surviving candles or new records and
exactly the 5,860 selected missing local rows. All other operational metadata
and original archive-index records remain exact. SQLite passes `quick_check`.
The canonical manifest restores **713 objects** into fresh SQLite with a separate
cold cache, preserving exact access to every moved candle. The main record count
falls from **6,037,613 to 6,031,753** and the epoch advances **3121 to 3145**.

The running FastAPI passes **12 native boundary queries / 11,720 records** against
the before-image. Installed SDK checks pass **four BTC minute/15-minute SMA/EMA
cases / 80 records** across the archived boundary. Full-range versus tail EMA
contexts retain the documented inherited seeding behavior; they are not asserted
to be numerically identical.

All **12** pinned Binance daily/hourly/minute refreshes succeed after pruning,
adding **68 candles**: 60 minute candles, four hourly candles and four daily
candles. Minute tails reach October 4 **00:10 UTC**; daily/hourly tails reach
October 4 **00:00 UTC**. This brings the main database to **6,031,821 records**.
Completed crypto OHLCV/provenance and all noncrypto candle values/versions remain
exact. Series, jobs, archive index, handoff certificates and import receipts
remain unchanged; separate comparisons preserve noncrypto schedules, source
checks and quality records. Installed SDK latest daily/hourly/4-hour SMA/EMA
checks pass **six cases / 120 records** with zero HTTP field differences.

Populated backups, native responses, HTTP/SDK results and restoration reports
remain under `data/crypto-canonical-rollover-20261004/` and
`data/crypto-post-rollover-refresh-20261004/`. **31 JSON artifacts** are preserved
with immutable RustFS readback; receipt
`data/crypto-canonical-rollover-evidence-20261004.json`. After-rollover backup
SHA-256: `862add9f859d2451b0dca4baee17f618e01569131cff21dcab54b5fcfe6876ef`.
Latest post-refresh backup SHA-256:
`6d44a917109de06818d192c6b50c4b19a985b5ca0c571ce7dc465d9357d1a7c2`.
This is an executed local crypto rollover and subsequent bounded ingestion;
other-market rollover, unattended supervision and production S3/cutover remain
open. No production route or legacy database was changed.

## Rolling job bounds and observed VN hourly coverage — 2026-10-04 ICT

A read-only inventory compares **39,160 daily dates** with **39,155 hourly dates**
across **53 selected VN stock tickers** inside the rolling hourly window. Five
daily dates have no hourly observations: IDC May 15, 2025; GEE April 17 and
November 28, 2024; VGI and VTP October 13, 2023. No hourly date lacks a daily
observation. This is observed-date agreement, not an independent exchange
calendar, complete hourly slots or price/volume convention parity.

Dated DNSE, VPS and VNDirect requests fail to provide hourly rows on these five
dates. The database-backed public API exposes **five hourly bars each** for IDC,
VGI and VTP, confirming a native coverage gap; those raw records are preserved
as candidates rather than spliced across unverified adjustment bases. Public
hourly queries are empty on both GEE dates. Its local daily bars are flat with
volume zero and one respectively; this remains a no-trade/provider-convention
review, without synthesized intraday candles.

Configured worker jobs now advance their leased recent-data floor with rolling
retention. At October 4 UTC, 52 stock-hourly staged cursors already cross the
current three-year bound, while their old jobs still target October 2, 2023.
Completion avoids another obsolete provider request, preserves all published
older data, rejects an empty current window and checks every completed observed
weekday daily date against staged or published hourly observations. Missing-date
findings retain the current provider/revision instead of triggering fallback.

A populated isolated rehearsal completes **48 jobs**, holds IDC, GEE, VGI and
VTP for the observed gaps and VPL for its remaining history-start boundary, and
makes zero provider requests. The same guarded 48 transitions then run against
the main database. Exhaustive before/after comparisons preserve every one of
the **6,037,613 candle records and update versions**, all series/source checks,
archive objects, handoff certificates and import receipts. Unselected jobs and
quality records, including both index snapshots awaiting separate approval,
remain exact. SQLite passes `quick_check`; pending jobs fall from **58 to 10**,
and the epoch advances from **3073 to 3121**.

Inventory/probes, rehearsal and canonical transition reports/backups remain in
`data/vn-hourly-session-inventory-20261004/`,
`data/vn-hourly-missing-date-probes-20261004/`,
`data/vn-hourly-rolling-job-rehearsal-20261004/` and
`data/vn-hourly-completed-jobs-20261004/`. Immutable RustFS evidence receipt:
`data/vn-hourly-rolling-completion-evidence-20261004.json`. Canonical after-backup
SHA-256: `d6583cfa002cd6eb069e0729a6cdff82cc9475fba2c1d40146df57e6bdeb6ee9`.
**416 tests**, lint, formatting and offline package builds pass. Job completion
does not certify legacy numerical parity, every hourly slot, provider adjustment
history or the broader historical/cutover gates.

## Bounded recurring crypto scheduler — 2026-10-04 ICT

The ordinary worker runs **75 cycles** from October 3 **23:54:26 to 23:55:55 UTC**.
It completes **12 successful scheduled updates**: two minute updates and one
hourly update for each of BTC, ETH, SOL and BNB. Daily updates are correctly not
due during this run; their existing one-hour cooldowns remain in force.
The worker adds **88 minute candles**, 22 per ticker, bringing the main database
to **6,037,613 records**. Each minute tail reaches **23:55 UTC**, with **527,036
rows** per ticker. All 12 stored crypto series retain continuous fixed-step
timestamps and their pinned Binance revisions.

Checks against consistent populated before/after backups preserve all noncrypto
candle fields/versions and previously completed crypto OHLCV/provenance. Series,
jobs, archive indexes, handoff certificates and legacy import receipts are exact.
Separate comparisons also preserve all noncrypto ticker schedules, source checks
and quality records. **12 captured native responses / 556 completed candle
observations** match stored Binance values exactly, excluding potentially forming
page tails. SQLite passes `quick_check`. The running FastAPI returns BTC minute
records at 23:54 and 23:55 with exact SQLite OHLCV parity.

The initial checker incorrectly required a daily refresh despite future due
times. It rejected the completed worker run; its report and error are preserved.
The corrected checker honors before-image schedules and rechecks the same
responses/backups through `--verify-only`, without restarting ingestion. Saved
evidence is under `data/crypto-scheduled-worker-20261004/`; **17 JSON artifacts**
are preserved with immutable RustFS readback, receipt
`data/crypto-scheduled-worker-evidence-20261004.json`. The after-backup SHA-256 is
`d8e62714caa9be692f0c9aac2a4e38270a6b641f7611405e77a7fafd2868dc6c`.
Checker lint, formatting, argument handling and offline package builds pass.
The bounded worker has stopped; long-running supervision, future daily/hourly
cycles and unattended canonical retention remain unproven.

## Populated scoped retention rollover rehearsal — 2026-10-04 ICT

`scripts/check_retention_rollover.py` rehearses the October 4 UTC retention cutoff
on consistent copies of the populated **6,037,525-candle** database. It publishes
**12 crypto partitions / 5,860 expired rows** to a unique local RustFS prefix:
four daily rows, 96 hourly rows and 5,760 minute rows across BTC, ETH, SOL and BNB.
Only the candidate copy is pruned. Native record readback and queries spanning
the archive/local boundary match the original records exactly, including
provider, revision and update versions.

An exhaustive keyed comparison with the before-image finds zero changed
surviving records, zero unexpected new records, and exactly the selected 5,860
missing local records. Candidate SQLite passes `quick_check`. A fresh database
restores **713 archive objects** from the rehearsal manifest, and all expired
selected records read back exactly with a separate cold cache. The canonical
database epoch remains **3061**; its before-image checksum matches the preceding
crypto refresh backup:
`fa9a5cbcf447ec27cdbf00e3593db08ed6c7639965759ec723156144afbc9b54`.

Report and populated before/after backups remain under
`data/crypto-scoped-rollover-rehearsal-20261004/`. The report is preserved and
readback-verified as immutable RustFS evidence; its receipt is
`data/crypto-scoped-rollover-evidence-20261004.json`, report SHA-256
`f0d382fa49ae164677c34fcdc2fc2ac797ab70823951d7bd0491b0dcd5531c08`.
Lint, checker argument handling, and offline package builds pass. This proves a
simulated isolated rollover and restoration, not an unattended scheduled run or
canonical pruning; the broader coverage and provider-basis gates remain open.

## Public migration access and scoped archive controls — 2026-10-04 ICT

A fresh bounded request to `https://api.aipriceaction.com/tickers` returned HTTP
200 and two FPT daily records dated October 1 and October 2, 2026. Public candle
migration already uses `import-legacy --from-api`; unavailable PostgreSQL access
does not block this path. Private sync records still require a separate export.

Archive maintenance now accepts market, repeated ticker, and native interval
filters and captures one UTC cutoff for the whole plan. Regression checks cover
both dry-run selection and publication/pruning, preserve other markets, intervals
and recent rows, and restore the exact exported records through history queries.
The full suite passes **412 tests**; lint, formatting and offline package builds
pass. A canonical BTC minute dry run selected no expired partitions at the time
of the check. No canonical retention pruning was performed in this check.

## VN daily/minute session and corporate-action basis audit — 2026-10-04 ICT

A read-only inventory compares **14,632 observed minute sessions** across all
**59 selected VN tickers** with daily observations inside the retained minute
window, October 3, 2025 through the completed October 2, 2026 session. Every
observed daily date has minute data and every observed minute date has daily
data. This proves observed-date agreement, not an independent exchange calendar,
complete minute slots or matching price/volume conventions.

Using the existing **1%** material-price review threshold, **1,537 session
comparisons** differ across **12 tickers**: BSR, CTG, GAS, GEE, MWG, SHB, TCB,
TPB, VHM, VN30, VND and VNINDEX. Six findings lie within the last 20 VNINDEX
reference sessions beginning September 7: four TPB dates (September 28–October
1) and two VNINDEX dates. Full session aggregates, missing-date checks, prices,
volumes and native series identities are retained in
`data/vn-session-basis-inventory-20261004/`. No correction factor is inferred.

Dated three-provider/public API probes isolate TPB on September 30 and October
2. Its local daily and minute candles match VPS and database-backed public JSON
exactly on both dates, including all **217/224 minute records**. On September
30, VPS daily OHLC is **12,255 / 12,255 / 12,087 / 12,129** while minute-session
OHLC is **14,600 / 14,600 / 14,400 / 14,450**. This is present in the upstream
and legacy API as well as the replacement. VNDirect and DNSE provide all 217
minute timestamps with different adjusted prices; they are incompatible splices.
VNDirect's daily prices match VPS but its volume differs; DNSE rounds prices
differently. On October 2 all daily replies match; VPS/DNSE minute records match
the local series exactly, while VNDirect changes four records, including one
price record. Envelope agreement alone does not establish minute parity.

The [official VSDC notice](https://vsdc.vn/vi/ad/200697), published September 25,
documents TPB's October 5 record date for cash and stock dividends. Its HTML and
checksum are preserved as corporate-action context. That notice does not prove
each provider's historical adjustment formula or license scaling minute data.
The runtime continues to preserve the captured interval-specific values and
report the basis issue. Broader historical provider consistency remains open.

`tests/fixtures/tpb_interval_basis.json.gz` freezes **443 native VPS records**
from the two dates. The new regression verifies that auditing reports September
30's actual basis difference, excludes the matching October 2 session, preserves
all candles and series state, and creates no repair job. Commit: `3971e4c`.
The full suite passes **410 tests**; lint/format checks pass for **71 Python
files**, and offline wheel/source builds pass. The source distribution includes
the captured fixture and regression test.

The first probe attempt failed in the diagnostic request wrapper; the second
preserved public matches but lacked an explicitly permitted direct VN route.
Both are retained as incomplete witnesses. The completed third attempt enables
direct requests only in its read-only probe settings; persisted worker routing
is unchanged. All **58 inventory/probe artifacts** have content-hashed RustFS
readbacks in `data/vn-session-basis-evidence-20261004.json`. Main data, metadata
and the publication epoch remain unchanged.

## Native crypto freshness and continuous retained timestamps — 2026-10-04 ICT

The existing bounded worker refresh succeeds for all four configured Binance
tickers on daily, hourly and minute intervals. It adds **3,895 minute candles**
and **64 hourly candles**, bringing the main database to **6,037,525 rows**.
All four minute tails reach October 3 at **23:33 UTC** and hourly tails **23:00**;
the current October 3 daily candles are rechecked. Every previously completed
crypto OHLCV record, provider and revision remains unchanged; the previously
provisional daily/hourly/minute observations may update as they complete.
Every noncrypto candle, including its version, is preserved exactly.

The populated after-backup verifies every retained crypto timestamp. Each ticker
has **1,097 daily**, **26,328 hourly** and **527,014 minute** observations.
Counts equal the entire first-to-last fixed-step ranges, timestamps are aligned,
and uniqueness follows the database key. Daily/hourly windows start October 3,
2023; minute windows October 3, 2025. This proves continuous stored timestamps
through the captured tail, separately from independent OHLCV/provider quality
or future freshness. Evidence: `continuous-timestamps-proof.json` under
`data/crypto-recent-refresh-v2-20261004/`.

All 12 source checks succeed without new repair jobs. Existing archive objects,
handoff certificates and import receipts stay unchanged, as do noncrypto series,
checks, jobs, quality records and ticker schedules. HTTP health reports **700
published archives plus one pending repair**; the index has **701 active objects**
and the existing **58 pending jobs** remain. CLI status and the running API agree
on **54,394 daily / 331,516 hourly / 5,651,615 minute** records.

Checks against the captured Binance pages verify **4,832 records/buckets across
24 HTTP requests**, covering every native response and complete 15-minute,
30-minute and four-hour buckets. The installed SDK passes **40 cases / 800 rows**
with exact OHLCV and MA10/20/50/100/200 SMA/EMA parity. The actual BTC web page
passes daily, 15-minute and hourly controls and volume profile without page,
forwarding or write errors. Its intraday tails reach **23:30 / 23:00 UTC**.
These checks use the existing runtime implementation; no calculation or storage
policy changes were required. The API runs independently; a bounded refresh
does not prove unattended future worker operation.

The first diagnostic wrapper incorrectly omitted a positional request argument,
so it failed before fetching data. Its backups prove zero candle changes and
preserve the failed checks. After correcting the wrapper, the successful retry
and its twelve native responses are recorded separately; it clears the failed
provider findings through ordinary successful verification.

The complete after-backup passes SQLite integrity/count checks. It has
**911,589,376 bytes**, SHA-256
`fa9a5cbcf447ec27cdbf00e3593db08ed6c7639965759ec723156144afbc9b54`,
at `data/crypto-recent-refresh-v2-20261004/after.sqlite3`. Both attempts retain
their complete before/after backups. The final evidence receipt
`data/crypto-recent-refresh-evidence-v2-20261004.json` preserves **51 artifacts**
with content-hashed RustFS readbacks; the earlier 50-artifact receipt remains
unchanged. Main data/metadata stay unchanged during evidence preservation.

## Live legacy indicator context diagnosis — 2026-10-04 ICT

Fresh daily requests compare the replacement and live public legacy API for
BTC, Dow, SJC gold and VCB: SMA/EMA, a 20-row tail ending December 31, 2022,
and the complete 2022 range. Legacy requests explicitly disable Redis/snapshot
reads through HTTP; no direct PostgreSQL connection is used. All 32 final
responses and their raw checksums are preserved in
`data/indicator-context-v2-20261004/`.

Both APIs exhibit history-dependent EMA seeding. Within each origin, the
20 shared tail candles are identical between query shapes, and all five EMA
discrepancies follow `(1 - 2/(period+1))` decay across those dates within the
recorded numerical tolerance. The legacy/local EMA200 context differences are
respectively 32.53590324943434/32.20380231006493 for BTC,
9.68325184557034/9.661306931382569 for Dow,
4.337447099387646/26.26838631182909 for SJC and
13.783961000146519/13.597970688024361 for VCB.
Making every request history-invariant would alter existing behavior; this
diagnosis leaves runtime calculation and the finite lookback policy unchanged.
Rust's `constants.rs`, `queries/ohlcv.rs` and `models/indicators.rs` also show
the finite 600-observation EMA budget and SMA-seeded recurrence.

Across origins, all 20 tail candles and every tail EMA match exactly for BTC,
Dow and SJC. Their complete-range candles also match, but maximum full-range
EMA200 differences remain 10.461610647820635, 0.22108664059487637 and
683.9788919389248 respectively. The differences follow seed decay; the exact
older warmup inputs are not yet compared, so their cause is not certified as
equivalent history selection. VCB has 20 tail/249 full-range candle differences
between native and legacy adjustment bases; no indicator-only parity claim
is made for it. This evidence narrows the open gate to actual full-range
warmup/provider compatibility rather than imposing EMA history invariance.

The reusable read-only tool is `scripts/check_indicator_context.py`, commit
`75a87d5`. It preserves both query shapes, separately reports missing dates,
OHLCV differences, undefined indicator disagreements and seed-decay residuals,
and verifies the local publication epoch remains unchanged. All 72 initial and
final artifacts have immutable RustFS readbacks in
`data/indicator-context-evidence-20261004.json`; main data/metadata remain
unchanged during preservation. Lint/format checks pass for 71 Python files.
This is a diagnostic change, not a claim that full numerical cutover acceptance
has passed.

## Cross-market clients and archived reads — 2026-10-04 ICT

The actual public web app passes daily, 15-minute and hourly controls for BTC,
VCB and NVDA with reads routed to the loopback replacement. NVDA's S&P/Dow
benchmark charts also pass all three intervals. VCB and all global chart tails
reach October 2; BTC's captured intraday tail is October 3 at 07:15 UTC for
15-minute and 07:00 for hourly data. This is compatibility evidence, not proof
that crypto ingestion is current continuously. All three successful rehearsals
have no page errors, blocked writes or forwarding errors.

The first BTC attempt timed out because the verification tool clicked the
existing chart's dialog trigger instead of its watchlist row. DOM inspection
preserves the three distinct BTC-labelled controls; the checker now excludes
dialog/combobox triggers. It also permits the selected default chart to reuse
its populated initial response. The corrected selector passes on all three
markets. The failed attempt remains preserved separately.

The installed Python SDK passes **68 historical cases / 1,360 returned rows**
for all four selected cryptocurrencies, seven Yahoo assets and SJC gold in
2022. Daily, weekly and two-week intervals cover both SMA/EMA; SJC uses daily
only. Every timestamp, OHLCV and MA10/20/50/100/200 value exactly matches the
corresponding tail HTTP query, and `data_source` is `api`. A complete bounded
dated HTTP export independently proves latest-in-range OHLCV selection.
The SDK's documented direction differs from HTTP start-date direction; the
checker now accounts for that difference explicitly rather than comparing
different candles. Implementation commit: `070f309`.

Full-range indicator context differs from a tail request in **31 cases**.
Maximum SMA absolute difference is **5.820766091346741e-11**; maximum EMA
difference is **32.20380231006493**, BTC daily EMA200. EMA seeding with a
different history length remains an open consistency limitation. Exact SDK
parity with its corresponding HTTP query does not certify history-length
invariance. Per-field changed counts and absolute deltas are preserved in
`data/cross-market-sdk-history-v3-20261004/`.

Fresh isolated-cache 2022 daily reads pass for every selected non-VN ticker,
with identical cold/warm values and provenance. Crypto downloads four objects
(38,983 bytes), Yahoo seven (67,164 bytes), SJC one (5,955 bytes); warm reads
download zero objects. Cold wall times are respectively 56.20, 81.32 and
16.74 milliseconds, warm times 28.27, 42.76 and 9.47 milliseconds at four-reader
concurrency. These are local RustFS observations, not cloud billing or production
capacity estimates. Each benchmark records unchanged main data/metadata.
Reports live under `data/cross-market-history-20261004/`; browser reports and
screenshots under `data/cross-market-web-20261004/`. The supplementary immutable
readback receipt is `data/cross-market-client-evidence-20261004.json`.
Lint and formatting pass for all 70 Python files. Only verification tools and
documentation change; canonical candles, handoffs and archive manifests remain
unchanged.

## Public migration and remaining daily archive gates — 2026-10-04 ICT

A fresh bounded request to the live public `/tickers` endpoint returns HTTP
200 and FPT daily data through October 2. Public candle migration already uses
this endpoint; unavailable direct PostgreSQL access is not a blocker.

Fresh complete 2020 exports contain 252 records for each of VND and VNINDEX.
VND has an invalid February 19 candle in both default and database-backed public
exports. VNINDEX's public export exactly matches its 252-row pending legacy
partition, but its pinned VNDirect source still returns an invalid September 29
candle. VPS returns all 252 valid VNINDEX dates with identical prices and 11
volume changes; its recent 40-record overlap disagrees with the current native
series on every record. That evidence does not license a provider splice.

For VND, VNDirect and DNSE both return 252 valid 2020 records. VNDirect's
`stock_prices` endpoint independently returns the same adjusted OHLCV records
as its chart endpoint. A wider isolated VNDirect traversal stops at an invalid
November 29, 2019 candle after 1,500 validated records; DNSE stops on conflicting
December 27, 2022 records after 500. Their retained overlaps change 664 and 414
records respectively. Neither candidate proves a complete coherent replacement.
Single-day requests reproduce all three invalid native candles; smaller query
windows do not resolve them. The two 2020 acceptance gates therefore remain open.

Provider validation errors now identify provider, market, symbol, interval and
exact Unix timestamp, preserving the original status and cause. Implementation
commit: `4d5fea9`. The packaged provider module matches the tested source exactly.
All 60 raw captures, normalized alternatives, isolated Parquet candidates and
reports have content-hashed RustFS readbacks in
`data/daily-archive-gates-evidence-20261004.json`. The first failed diagnostic
attempt is retained as a failed witness, separately from the completed recheck.
The main publication epoch remains unchanged. Detailed evidence lives under
`data/daily-archive-gates-recheck-v2-20261004/`,
`data/vnd-native-daily-candidates-20261004/`,
`data/daily-archive-gate-head-proofs-20261004/` and
`data/invalid-daily-dated-probes-20261004/`.

## Native VN hourly handoff and isolated restoration — 2026-10-04 ICT

The existing explicit adoption command now supports **VN hourly snapshots**
using VPS, VNDirect or DNSE. It requires **100 exact bars across five observed
completed VN session dates through the published tail**. Native identities,
ordered unique timestamps, price/volume agreement, snapshot stability and job
leases are checked. VN minute-aligned labels are preserved, including 02:15
UTC index observations; Yahoo's whole-hour overlap requirement remains intact.
Hourly correction/complete-session options remain rejected.

VN hourly certificates use `exact_vn_hourly_snapshot_overlap` and retain the
completed VN session cutoff. Validation rejects wrong market/interval/provider,
insufficient overlap and forged/future finality bounds. The certificate permits
ordinary native updates within the unchanged revision and survives cold
restoration; it does not certify all historical adjustments or session calendars.
Implementation commit: `f83ee67`.

Live preflight of both unchanged isolated index snapshots passes **VPS with
200 exact bars across 34 observed completed dates per index**. VNDirect matches
only **one VN30 / zero VNINDEX records** in its 200-row samples; DNSE matches
**ten VN30 / one VNINDEX records**. Both are rejected on completed OHLCV
disagreement. All raw and normalized witnesses and detailed differences are
preserved in `data/vn-hourly-handoff-preflight-20261004/`; its input database
rows, states, certificates and epoch remain unchanged.

A fresh isolated clone then executes both VPS handoffs. Adoption itself leaves
all **3,448 records per index** unchanged. Ordinary provider-pinned refresh
verifies **40 records per index**, preserves every timestamp/OHLCV value, and
updates only those records' verified provider/capture provenance. This closed-
market rehearsal appends **zero new timestamps**; synthetic tests separately
exercise future appends. Its populated backup passes integrity/count and exact
record checks with **6,896 rows**.

Using a unique candidate S3 prefix, four provider-homogeneous archive objects
are published and pruned in the clone, then restored into fresh SQLite. Both
certificates and all **6,896 rows** restore exactly, including update versions;
full four-hour query results also match. The main archive index stays at
**701 objects**, the main database stays unchanged, and both original public
candidates remain frozen and unmodified. Authoritative evidence:
`data/vn-hourly-native-handoff-rehearsal-20261004/report.json`.
All **19 supplementary artifacts** have immutable RustFS readbacks recorded in
`data/vn-hourly-handoff-evidence-20261004.json`.

The full suite passes **409 tests**, including **17 new VN hourly cases** for
all three providers, historical labels, append/restoration, conflicts,
duplicates/order, insufficient dates, races, leases, forged evidence and
unfinished-session exclusion. Lint/format checks pass for **70 Python files**;
offline wheel/source builds pass, and packaged adoption/storage modules match
the tested source exactly.

This supersedes the unsupported-VN-hourly-command limitation recorded below.
It does not publish the canonical index replacement: explicit approval for the
previously rejected primary-view timestamp/value change remains pending.

## VN hourly snapshot safeguard and isolated client review — 2026-10-04 ICT

Worker refresh now protects imported **VN hourly** snapshots for all three
legacy provider aliases (`legacy-api`, `legacy-s3`, `legacy`), as it already did
for Yahoo. It records `handoff_required` before querying native sources, keeps
the original candles and series state, does not queue a fallback repair, and
releases its series lease. Six parameterized VN/Yahoo tests exercise this
behavior; the full suite passes **392 tests**. Lint/format checks pass for
**68 Python files** and offline wheel/source builds pass. The wheel's worker
module exactly matches the tested source. Implementation commit: `02a9fd1`.

Timestamp reconciliation confirms that each public index candidate omits
**45 current native timestamps**, comprising **eight at 02:00 UTC** and
**37 at 08:00 UTC**. Every affected date appears in the public candidate;
this proves observed-date preservation, not equivalence of the bars or an
auction-policy explanation. Original records remain complete in the existing
immutable before-images. Evidence:
`data/vn-index-hourly-public-candidates-20261004/timestamp-reconciliation.json`.

The proposed canonical replacement was rejected by automatic approval review
because it changes shared values and removes timestamps from the primary view
without explicit authorization for that exact mutation, despite backups.
The publication command did **not** execute. Explicit approval is pending;
no indirect canonical replacement has been made.

Independent work uses a **separate review SQLite copy**, never copied back to
the main database. A temporary loopback API on port 3002 serves both candidates
alongside copied reference data. Full HTTP checks match every one of the
**3,448 hourly records per index**. The installed SDK passes **eight hourly /
four-hour SMA/EMA cases**, plus four explicit June 2024 requests with **240
returned bars**. Both actual public-web daily/hourly charts pass through
October 2 with no page/network errors or browser writes. These checks validate
the isolated candidates; they do not imply main publication. Reports are under
`data/vn-index-hourly-review-20261004/`.

The real operational CLI refreshes the two isolated hourly series and returns
`handoff_required` for both, retaining all **3,448 rows per series**. Complete
main index records still match their native before-images and remain on
VNDirect; main candle count remains **6,033,566**. The review server is stopped
after verification. The main loopback API restarts with the tested guard.
All **14 supplementary reports/screenshots** have immutable RustFS readbacks
in `data/vn-index-hourly-review-20261004/evidence-receipt.json`.

Native VN hourly snapshot adoption remains unsupported by the current hourly
adoption command; its implementation and source/session proof remain open.
The guard prevents an unverified fallback from silently replacing snapshots
while that work is pending. No production routing or deployment changes occur.

## VN index hourly source gaps and isolated candidates — 2026-10-04 ICT

Both VNINDEX and VN30 currently serve **766 native VNDirect hourly records
across 138 observed dates**. Their repair jobs are marked complete, but their
date distributions show internal gaps: before April 2026 they contain isolated
dates, rather than a full continuous history. The existing unresolved
`audit_observed_sessions` findings each identify **609 daily reference dates**
without hourly records. These are observed-date review findings, not invented
trades or a verified exchange calendar.

The read-only dated diagnostic requests three historical boundaries from all
three permitted providers. Before April 1, 2026, VNDirect returns **30 bars on
six dates**, while DNSE returns **500 bars on 100 dates**. Before December 1,
2024, their respective results are **70 bars on 14 dates** and **500 bars on
101 dates**. Before October 31, 2023, VNDirect returns **65 bars on 13 dates**;
DNSE returns **500 bars on 101 dates**. VPS returns no records in those historical
samples. Current-window probes return **372 VPS / 500 VNDirect / 500 DNSE bars**
per index. These observations demonstrate provider differences; they do not
prove that a shorter response is an ingestion pagination bug.

Strict DNSE walks validate the first **500 recent rows** per index, then reject
the next page. Raw selected-page witnesses identify **seven invalid VNINDEX
bars** and **five invalid VN30 bars**, all at 07:00 UTC in April 2026. Their
closes lie outside their reported high/low ranges. Each timestamp has a valid
public record with a different range and volume. No candle is clamped, omitted
to bypass validation, or combined with another provider. Reports and raw
captures: `data/vn-index-hourly-windows-20261004/`,
`data/vn-index-hourly-current-window-20261004/` and
`data/vn-index-hourly-native-walk-20261004/`, including
`invalid-candle-witnesses.json` in the last directory.

Full public exports for October 3, 2023 through October 2, 2026 contain **3,448
valid hourly records across 747 observed dates per index**. Each public window
has **2,727 timestamps absent locally**, while **45 local timestamps are absent
from the public window**. Shared fields differ at **721 VNINDEX / 708 VN30
timestamps**. These are complete captured comparisons, not permission to infer
price factors or splice providers. Evidence:
`data/vn-index-public-hourly-window-20261004/report.json` and its two complete
comparison files and original JSON bodies.

Both captured public windows are staged in **isolated SQLite** with exact
timestamp/OHLCV preservation. Four complete local Parquet images preserve both
native before-images and both public candidates, with exact readback including
capture versions. Candidate FastAPI checks match all **3,448 hourly / 1,494
derived four-hour records per index**. These use the in-process ASGI interface;
they are not claims of a browser or serving-database publication. The candidate
backup passes integrity/count checks with **6,896 rows**. Evidence:
`data/vn-index-hourly-public-candidates-20261004/report.json` and
`asgi-checks.json` in that directory. All **50 captured evidence artifacts**,
including Parquet images and the candidate backup, have immutable RustFS
readbacks in `data/vn-index-hourly-evidence-20261004.json`.

The serving database epoch and both original index series remain unchanged.
The candidates are explicitly marked unsuitable for an append; coherent
replacement, local/public timestamp reconciliation and session/source policy
remain open. Lint/format checks pass for **68 Python files**. Application code
retains its existing **389-test checkpoint**; this change adds a read-only
diagnostic without changing API interfaces or deploying anything.

## Selected VN hourly progress publication — 2026-10-04 ICT

The bounded local batch runner applies the existing guarded progress command
independently to configured VN watchlist symbols with older staging. Both its
dry run and execution pass for **52 additional stocks**, each with **200 fresh
exact DNSE matches across 40 or 41 observed UTC dates**. Execution appends
**165,510 older rows** to their existing **26,000 rows**. All **5,868,056 original
database candles**, including provider/revision/update versions, are unchanged;
all prior tickers, jobs, staging, series, sync records, imports, adoptions,
source checks, archive metadata and quality findings are also unchanged. The
main database now contains **6,033,566 candles**. Publication is atomic per
series; the whole pass is deliberately not presented as one transaction.

Every published SQLite series matches its complete readback-verified immutable
replacement image. Before/after populated database backups are retained under
`data/vn-hourly-progress-publication-20261004/`. The after backup passes its own
integrity/count/selected-record checks and is **910,966,784 bytes**, SHA-256
`80b0a3ccc885e8d6a4cbd682134d375717cfba75e993e048ece36d1a0229c710`.
The final publication receipt and all fresh native captures have exact RustFS
readbacks; the active archive index remains unchanged. Authoritative reports:
`data/vn-hourly-progress-preview-20261004/report.json` and
`data/vn-hourly-progress-publication-20261004/report.json`.

Full-range HTTP validation passes **104 requests**, matching every timestamp
and OHLCV field across **191,510 native hourly** and **76,788 four-hour** records.
Four-hour expectations are computed independently from stored native records
using the existing VN 02:00 UTC bucket anchor, first/last prices, price extrema
and summed volume. Evidence: `data/vn-hourly-progress-http-20261004.json`.
The installed SDK passes **12 recent SMA/EMA cases** for ACB, HPG and VPL. It
also passes four explicit June 2024 ACB/HPG hourly/four-hour requests covering
**280 returned bars** via the API. These are request-contract checks, not an
independent certificate of source prices. Reports:
`data/sdk-acb-hourly-progress-20261004.json`, the corresponding HPG/VPL files,
and `data/sdk-vn-hourly-older-ranges-20261004.json`.

The actual public VN web renders ACB daily/hourly charts through October 2
with no page/network errors or writes. Four-hour UI checks remain unavailable
because the public web has no visible control for that interval. Its report
is `data/web-acb-hourly-progress-20261004.json`. **62 supplementary artifacts**,
including dry-run raw responses, client reports, updated inventory and browser
screenshots, have immutable RustFS readbacks recorded in
`data/vn-hourly-progress-client-evidence-20261004.json`.

The new inventory still covers **71 selected tickers / 207 series / 198
configured ingestion states**, none missing, with **58 series having pending
jobs**. Of **55 VN hourly series**, all **53 stock series** now reach their
applicable retention/listing date; VNINDEX/VN30 begin later. The inventory
proves observed date span only: internal sessions, adjustment consistency and
legacy-only timestamp reconciliation remain open. All bootstrap jobs and
quality findings stay intact. Evidence:
`data/selected-window-inventory-after-hourly-progress-20261004.json`.

The eight existing progress guard tests pass again. Lint/format checks pass
for **67 Python files**; the existing **389-test API checkpoint** is unchanged.
The batch adds no dependency or service and does not change production routing.

## Verified FPT hourly bootstrap progress — 2026-10-04 ICT

The explicit `publish-bootstrap-progress` command now publishes older validated
VN hourly staging without pretending the bootstrap has completed. A dry run
is the default. Execution requires an idle job and series lease, the same pinned
native provider/revision, exact staged overlap and fresh confirmation of at
least 100 published bars across five observed dates through the completed tail.
Immutable before/replacement Parquet images and an intent receipt are readback
verified before a second transactional snapshot check and insert-only publication.
Jobs, staging and unresolved quality findings remain unchanged.

The actual FPT operation confirms **200 fresh DNSE bars across 40 observed UTC
dates** and appends **3,232 older records**. It now serves **3,732 native hourly
records**, starting October 3, 2023 at 02:00 UTC through October 2, 2026 at
07:00 UTC. All **500 original records**, including their update versions, and
all **5,864,324 unrelated candles** remain exactly unchanged. Prior operational
records and the **701 active archive entries** also remain unchanged. Total
local candles are now **5,868,056**. Evidence:
`data/fpt-hourly-progress-publication-20261003T222757Z/report.json`.

Full-range HTTP checks match all **3,732 hourly** and **1,494 derived four-hour**
records. The existing SDK passes eight SMA/EMA cases. The actual public VN web
passes its visible daily/hourly controls, both current through October 2, with
no page/network errors or writes. An earlier browser attempt requesting `4h`
fails because that control is not visible; four-hour verification is limited
to HTTP/SDK. Reports: `data/fpt-hourly-progress-http-20261004.json`,
`data/sdk-fpt-hourly-progress-20261004.json`,
`data/web-fpt-hourly-progress-supported-20261004.json` and the preserved failed
attempt `data/web-fpt-hourly-progress-20261004.json`.

The after-publication SQLite backup passes its own integrity check and exact
selected-record/count verification. It is **891,277,312 bytes**, SHA-256
`98ecc50dff4627335b4f419ad7d11afa21c4e3f65fdc7497b2e3b7d81b62fe03`.
All **43,636 VN daily records** retain their previous checksum. The full suite
passes **389 tests**; lint/format checks pass for **66 Python files**, and offline
wheel/source builds pass. Eight new cases cover dry-run behavior, preservation,
conflicts, inadequate native evidence, active leases and staging races.

This is partial progress: the FPT job remains pending and its three public-only
timestamps remain unresolved. Dated probes of all three VN providers find one
June 2026 timestamp at VNDirect but none of the three at DNSE; VPS returns empty
samples. DNSE minute requests for the same dates return invalid/missing arrays.
The public samples have nonzero volume and cannot be dismissed as quote events.
No source splicing or invented candles are used. Raw witnesses are preserved
under `data/fpt-hourly-missing-witnesses-20261004/` and
`data/fpt-hourly-minute-witnesses-20261004/`. Supplementary reports/captures and
screenshots have immutable RustFS readbacks recorded separately in
`data/fpt-hourly-progress-client-evidence-20261004.json`.

## Selected-window inventory and VN hourly backfill gap — 2026-10-04 ICT

The new read-only SQLite inventory covers **71 selected tickers / 207 published
series**, including all **198 configured ingestion states** with none missing.
It records local/archive bounds, staged rows, configured retention/listing
floors and pending work without initializing or updating SQLite. **58 series**
have pending jobs: **53 VN hourly, three VN minute, one VN daily and one SJC
daily**. These counts identify work, not proof that every job corresponds to
missing candles. The database epoch remains unchanged. Evidence:
`data/selected-window-inventory-v2-20261004.json`.

All selected VN minute series have observations on the one-year retention
boundary date; all selected daily series reach their applicable retention or
listing date. This establishes observed span only, not internal session
completeness. VN hourly local prefixes remain short, and all seven Yahoo minute
series begin after the one-year boundary. Archived bounds are recorded separately.

At this pre-publication inventory checkpoint, FPT serves **500 native DNSE hourly records**. The public legacy
endpoint also returns **320 hourly records for 2023**, beginning September 11,
and **920 for 2026** through October 2. Its overlapping 2026 fields change
**450 of the 500 native rows**, so these sources cannot be joined as one exact
revision. A read-only DNSE query before the current local first timestamp returns
another **500 older records**: native history is available beyond the served
window. The existing pending bootstrap job already contains **3,732 staged
native rows**, all 500 currently served timestamps/OHLCV unchanged, but remains
stalled on an empty/invalid page near an earlier retention floor.

Complete public 2023–2026 exports contain **3,410 records** within the current
three-year floor. Comparing that entire captured window with native staging
finds **three public-only timestamps**, **325 native-only timestamps** and
**3,357 shared rows with field differences**. The public-only dates are June 20,
2024 at 05:00 UTC; August 1, 2024 at 04:00 UTC; and June 12, 2026 at 04:00 UTC.
They remain explicit reconciliation requirements for complete legacy coverage.
No staged rows are promoted at this inventory checkpoint and no price factor is inferred.
Evidence: `data/vn-hourly-public-inventory-20261004/report.json`,
`FPT-complete-staging-comparison.json` in the same directory, and
`data/fpt-hourly-native-history-probe-20261004/report.json`, with raw captures.
All **14 inventory/capture/comparison artifacts** are also readback-verified in
RustFS; object keys and checksums are in `data/window-inventory-evidence-20261004.json`.

Lint/format checks pass for **64 Python files**, and offline wheel/source builds
pass. Application code remains at the **381-test** checkpoint. The gold minute
first-date description below is corrected to **March 8 at 22:10 UTC**; March 9
is its Vietnam date, not its UTC date.

## Bulk Parquet preparation with exact readback — 2026-10-04 ICT

Archive writing now streams a local typed CSV into DuckDB with `COPY` instead
of executing one insert per candle. The typed schema, sorted output, ZSTD
compression, row-group setting, verification, publication and pruning rules
remain unchanged. Python's `QUOTE_NOTNULL` and DuckDB's quoted-null handling
preserve empty metadata separately from SQL null; temporary files are removed
when the operation exits. No dependency or network service is added.

The same complete **198,752-row** gold minute snapshot takes **136.886 seconds**
with row inserts versus **0.972 seconds** with the bulk writer. Both full
Parquet readbacks match every original field, including capture versions, and
produce byte-identical **2,232,420-byte** files with SHA-256
`aa060b52fb62b3bd4d0542531711a31ad9990aa695b4a2bec2e415f8cd2e5b7c`.
These are local observations under concurrent work, not a production capacity
guarantee. Whole-process peak RSS, including fixture loading and verification,
is **429.516 MiB** before and **493.484 MiB** after. The bulk path temporarily
writes **33,840,107 CSV bytes** for this fixture. Temporary disk and memory
costs should therefore remain part of production capacity acceptance.

Real local RustFS preparation, upload and verified readback takes **2.120
seconds**; another refreshed read matches all 198,752 rows. The main database
epoch and all **701 active archive records** remain unchanged: this prepares
an immutable image without publishing another index entry or pruning candles.
Evidence: `data/archive-write-before-20261004/report.json`,
`data/archive-write-after-20261004/report.json`,
`data/archive-write-comparison-20261004/report.json` and
`data/archive-write-rustfs-20261004/report.json`.

Three regression cases verify exact archive/publication/pruning readback for
adjacent floating-point values, maximum signed 64-bit volume/version values,
empty strings, literal null markers, quotes, commas, Unicode and multiline
metadata. The full suite passes **381 tests**; lint/format checks pass for
**63 Python files**, and offline source/wheel builds pass. The existing
Starlette/httpx warning remains unchanged.

## Gold minute query-shape diagnosis — 2026-10-04 ICT

The repository's legacy minute worker requests `range=1d` and saves extracted
quotes with minute timestamp normalization. Its pinned `yahoo_finance_api` 4.1.0
extractor omits null closes and defaults other null fields, including volume,
to zero. Sources inspected: `../aipriceaction/src/workers/yahoo_minute.rs`,
`../aipriceaction/src/constants.rs`, `../aipriceaction/src/workers/vci_shared.rs`,
`../aipriceaction/Cargo.lock` and the locally installed crate's `quotes.rs`.
This describes the repository implementation, not every deployed historical
worker version. The new diagnostic replays these defaults only for comparison;
the native adapter's null-data policy remains unchanged.

Repeated captured gold queries reproduce the difference. The current legacy
one-day range contains **1,020 extracted rows**, all exactly matching retained
timestamps/OHLCV, with **419 null-close rows** omitted. The explicitly dated
October 2 response contains **1,259 extracted rows**, **180 null-close rows**,
**ten additional timestamps** and **three changed records**. The bounded six-day
response contains **6,881 extracted rows**, **663 null-close rows**, **21
additional timestamps** and **ten changed records**, reproducing the previous
native preflight. These are response counts, not calendar coverage proofs.

The dated response's first October 2 midnight row has volume **0**, versus
**50** in the retained snapshot and wider capture. This is evidence of a
request-boundary difference; it does not establish a correct trade volume or
authorize rewriting the original. The legacy range's first 04:00 row also
matches the retained zero volume, while the wider response reports **74**.
Query profiles differ in bounds and event parameters, so further controlled
comparison is needed before assigning the cause to one parameter. No dividend
factor, volume correction, timestamp insertion or handoff is published.

The reusable diagnostic's complete evidence is in
`data/gold-minute-query-shapes-v2-20261004/report.json` and checksummed raw and
normalized bodies. It verifies the selected retained records and database epoch
remain unchanged. Failed captures retain a partial report and original bodies.
Lint/format checks pass for **63 Python files**, and offline source/wheel builds
pass. Application code is unchanged from the **378-test** checkpoint.
All seven response/report artifacts are also readback-verified in RustFS;
object keys and checksums are in the same directory's `rustfs-evidence.json`.

## Futures minute handoff window and unresolved native differences — 2026-10-04 ICT

Yahoo futures minute adoption now retains up to **10,000** rows from the
provider's existing bounded six-day response. Keeping only 2,000 rows could
discard enough long-session dates to make the required five UTC date partitions
unreachable even with exact data. This changes retained response selection only:
the upstream date range, minimum **1,000** exact matches, five-date requirement,
published-tail check, identity checks and conflict rejection remain unchanged.
Stock/index minute and hourly request sizes are unchanged.

The real gold capture contains **6,881 native records** across **six** UTC date
partitions from September 27 at 22:10 through October 2 at 20:59 UTC. It exposes
**ten OHLCV conflicts**, all with volume differences and two with price
differences, plus **21 native timestamps absent from the public snapshot**.
The actual adapter response is preserved and replayed through the widened
dry-run handoff gate, which rejects adoption on completed OHLCV disagreement.
The retained 198,752 records, series state and database epoch remain exact.
No native handoff or correction is published. Evidence:
`data/gold-minute-native-preflight-20261003T215843Z/report.json` and checksummed
raw/normalized native responses in the same directory.

Four new regression cases verify adoption and archive-index restoration for
6,900 exact long-session observations, continued rejection with fewer than
five date partitions, and price/volume conflict rejection outside the former
2,000-row tail. The complete API suite passes **378 tests**; targeted intraday
adoption tests pass **37 tests**. Lint/format checks pass for **62 Python files**,
and offline source/wheel builds pass. Native gold minute ingestion remains
frozen pending reconciliation; this change does not certify a complete calendar.

## Public minute migration without PostgreSQL — 2026-10-04 ICT

A fresh bounded FPT daily request to `https://api.aipriceaction.com/tickers`
returns HTTP 200 and the requested two records. Public candle imports use HTTP
exports and public archive objects without connecting to legacy PostgreSQL.

The isolated gold minute candidate contains **198,752 observations**, adds
**175,291 timestamps** and preserves every original timestamp and volume.
Of the original records, **23,286** recover additional JSON price precision;
every recovered price rounds exactly to its original two-decimal value.
All **241** subminute quote observations retain their original seconds. A
regression test preserves an ordinary bar and quote in the same minute as two
distinct observations. The compatibility exception is restricted to flat-OHLC,
zero-volume, `legacy-api` Yahoo futures minute/hourly observations; native
candles retain strict timestamp checks.

Candidate evidence is in `data/gold-minute-current-candidate-20261004/report.json`
and `retained-value-audit.json`. The candidate spans March 8 at 22:10 UTC through October 2,
2026. **28 empty requested ranges** leave full one-year coverage unproven.
Publication now replaces only the selected gold minute series atomically after
state, original-snapshot, archive/series lease and active-job checks. Immutable
original/replacement Parquet images, source bodies including empty responses,
receipts and populated before/after backups are preserved. All **5,666,072
unrelated candle records** and prior operational records remain exact; one
selected minute quote-event finding is added. The **43,636 VN daily records**
retain checksum
`7cb2e73876d943760cf50f08bb320bf1f7e55bfc36ff7fc6fcfb4de973e3bff2`.
Evidence: `data/gold-minute-publication-20261003T215136Z/report.json`, also
readback-verified as an immutable RustFS receipt.

The loopback HTTP rehearsal compares every timestamp/OHLCV field in **198,752
minute / 13,317 fifteen-minute / 6,660 thirty-minute** records across **105**
bounded requests, including all 241 quote observations. The existing installed
SDK passes **ten** gold SMA/EMA checks across daily, minute, fifteen-minute,
hourly and four-hour intervals, including MA10 through MA200. The actual public
gold daily/fifteen-minute/hourly chart and both global benchmarks render through
October 2 with explicit freshness assertions, no page/network errors and no
writes. Reports: `data/gold-minute-publication-20261003T215136Z/http-full-history.json`,
`data/sdk-gold-minute-current-20261004.json` and
`data/web-gold-minute-current-20261004.json`, with screenshots.
All six client reports/screenshots are also preserved and readback-verified in
RustFS; checksums and object keys are recorded in
`data/gold-minute-publication-20261003T215136Z/client-verification-receipt.json`.

Main SQLite now contains **5,864,824 candle/quote records**; active archive
metadata remains **701 objects / 68 adoptions / 34 recoveries / one unavailable
range**, and the remote manifest matches the current local index. The populated
after-backup contains **890,884,096 bytes**, matches all selected candidate
records including capture versions, retains the exact minute quote finding and
passes its own `quick_check`. SHA-256:
`e3b93a63335a615ccd751b9ac08057687114a6fdac7e51a71ab32cdfce39bcb1`.
The final backup verifier initially selected the wrong finding by list position;
it was corrected to match source/symbol/interval/kind and rechecked without
republishing candles. Both populated backups remain available.

The complete application suite passes **374 tests**; targeted quote/import
checks pass **47 tests**, lint/format checks pass for **61 Python files**, and
offline wheel/source builds pass. Native gold minute updates remain frozen
pending provider reconciliation. This public snapshot proves observed history
parity and current chart freshness, not full calendar coverage or production
cutover readiness. Production routing remains unchanged.

## Gold hourly snapshot restored with exact legacy quote timestamps — 2026-10-04 ICT

The rejected gold hourly timestamps are now inspected row by row: all **279**
have zero volume and identical OHLC, and all occur in April 2026. They are
preserved as quote-shaped legacy observations rather than rounded to minutes
or removed. The explicit compatibility rule applies only to `legacy-api` Yahoo
futures `1h` rows with finite valid prices, zero volume and identical OHLC.
The later minute migration extends this rule to `1m` as described above.
Native providers, daily intervals, non-futures symbols, non-flat and
nonzero-volume rows retain strict timestamp validation. This rule describes
the observed legacy representation; it does not independently establish trade
or session validity. Imports record `legacy_quote_events` counts, original
bounds, source checksums and revisions as a visible quality finding.

The complete public 2024–2026 snapshot contains **10,288 observations**, preserves
all **6,290** original timestamps/OHLCV exactly and adds **3,998** newer records
through **2026-10-02 20:00 UTC**. Every quote-event second and supplied field
is retained. SQLite/Parquet readback verifies the complete replacement image;
immutable originals, response bodies/receipts, original/replacement Parquet
images and populated before/after backups are preserved. The gold series alone
is replaced atomically after archive/series lease, current-state, row and active
repair checks. All **5,679,245 unrelated candles** and prior operational records
remain exact. One selected gold quote-event finding is added explicitly.
Evidence: `data/gold-hourly-publication-20261003T212906Z/report.json` and
`data/public-hourly-candidate-20261003/gold-complete-comparison.json`.

Bounded yearly HTTP exports verify all **10,288 hourly / 2,707 four-hour**
timestamps and OHLCV exactly against the captured candidate, including all
279 quote events. These are separate yearly requests because the API retains
its existing 10,000-row single-ticker limit. The existing SDK passes **eight**
gold daily/weekly/hourly/four-hour SMA/EMA checks. The actual public gold hourly
chart and both global benchmarks render through October 2 with explicit
freshness assertions, no page/network errors and no writes. Reports:
`data/gold-hourly-publication-20261003T212906Z/http-full-history.json`,
`data/sdk-gold-hourly-current-20261004.json` and
`data/web-gold-hourly-current-20261004.json` with screenshots.

At hourly publication, SQLite contained **5,689,533 candle/quote records**; active archive metadata
remains **701 objects / 68 adoptions / 34 recoveries / one unavailable range**.
The populated after-backup has **865,873,920 bytes**, matches all 10,288 candidate
records including capture versions, retains the quote finding and passes its
own `quick_check`. SHA-256:
`818a7937749e06e3d702197ef9a10f827e48b081745794b43592d3616e6fe646`.
Evidence: `data/gold-hourly-publication-20261003T212906Z/backup-check.json`.
The quote-specific cold archive/manifest restoration path is covered by an
end-to-end import/restore test; publication verifies the actual complete gold
Parquet image without changing the 701 active archive records.

The complete application suite passes **371 tests**; lint/format checks pass
for **59 Python files**, and offline source/wheel builds pass. New tests verify
lossless JSON import, visible evidence, SQLite/Parquet/archive restoration and
native/four-hour responses, and reject eleven out-of-policy quote shapes.
The existing Starlette/httpx warning remains unchanged.

Gold's hourly snapshot remains frozen: the new real native-handoff dry run
still fails on completed OHLCV disagreement. Its minute history was stale at
that checkpoint and is now restored above; native source-policy reconciliation
remains open. This current public snapshot
does not certify continuous native ingestion, full calendar coverage or the
recent daily/native discrepancies. Production routing remains unchanged.

## SPY native hourly handoff and ordinary updates — 2026-10-04 ICT

Yahoo hourly adoption now uses the existing certificate workflow with explicit
`--interval 1h`. It compares up to **200** native bars, requires **100** exact
OHLCV matches across at least five observed UTC date partitions through the
published tail, and uses completed whole-hour bounds. The minute requirement
remains 1,000. The threshold counts hourly observations separately from minute
observations; it does not assert complete sessions or a trading calendar.
All provider rows must retain source/symbol/interval/provider identity and use
whole-hour labels. Only Yahoo supports this hourly proof; VN hourly and minute
correction flags are rejected. Publication retains the entire original snapshot,
checks state/checksum races and active job leases, and cancels only the adopted
interval's unpublished bootstrap/repair work. No inferred scaling or correction
is allowed by this exact proof.

SPY passes with **200 exact bars across 29 completed UTC date partitions**.
Its **3,195** stored hourly records are unchanged during adoption, including
historical `:30` labels. The first real pinned Yahoo update writes **40** bars
with a successful source check and no pending hourly repair; all original dates
and OHLCV remain exact afterward. Provider/version provenance changes only for
those checked native observations. The source revision is unchanged. The
watchlist and built wheel now enable SPY daily/hourly/minute ingestion.
Evidence: `data/spy-hourly-native-publication-20261003T211936Z/report.json`.

The publisher preserves original JSON, native source responses, populated
before/after backups and immutable RustFS evidence. All **5,682,340 unrelated
candles** and unrelated operational records match the before-backup exactly.
SPY's selected ticker changes only its hourly schedule. Full HTTP exports still
match all **3,195 hourly / 1,085 four-hour** original dates and OHLCV. The existing
SDK passes **eight** daily/minute/hourly/four-hour SMA/EMA checks. The actual
SPY hourly web chart and both global benchmarks pass explicit October 2
freshness bounds, with no page/network errors or writes. Reports:
`data/spy-hourly-native-publication-20261003T211936Z/http-full-history.json`,
`data/sdk-spy-hourly-native-20261004.json` and
`data/web-spy-hourly-native-20261004.json` with screenshots.
The real `aipa-api refresh --source yahoo --symbol SPY --interval 1h` command
also succeeds with 40 completed native bars and no provisional rows; all 3,195
original dates/values remain exact afterward. Supplementary evidence:
`data/spy-hourly-native-publication-20261003T211936Z/cli-refresh.json`.

Complete isolated manifest recovery verifies every object and restores **701
archives, 68 adoptions, 34 recoveries and one unavailable range**, exactly
matching main metadata. The after-backup contains **5,685,535 candles**, is
**865,353,728 bytes**, and passes its own `quick_check`. SHA-256:
`be6f882d1b2488727ba14f66d56f4e5cae24f3ca5fc28a7be5110eabe4abe433`.
Report: `data/spy-hourly-native-publication-20261003T211936Z/backup-and-manifest-restore.json`.

Eleven new tests cover dry-run immutability, exact append and mixed-provenance
archive restoration, historical label preservation, stale/small/value-conflicting
overlaps, snapshot races, leased jobs, invalid interval/market/finality
certificates and unsupported correction modes. The complete application suite
passes **358 tests**; lint/format checks pass for **58 Python files**, and offline
source/wheel builds pass. The known Starlette/httpx warning is unchanged.

Longer primary captures for every chosen Yahoo symbol remain in
`data/hourly-native-handoff-preflight-20261004/`. Replaying each complete raw
response with its original URL/period bounds adds no network assumptions.
Many direct timestamp mismatches reflect older public `:30` labels versus
current whole-hour normalization, so they are not declared missing sessions.
Value disagreements remain for the other stock/index series, and gold's current
native tail has no overlap with its stale local tail. All remaining hourly
snapshots stay frozen. A bounded SPY continuity proof does not resolve wider
historical source corrections, adjustment policies or gold recovery.

## Six hourly snapshots published and freshness verified — 2026-10-04 ICT

The complete public API hourly snapshots for AAPL, MSFT, NVDA, SPY, S&P and Dow
are now published locally in one checked SQLite transaction. All **17,894**
original timestamps remain available, including earlier half-hour labels; no
historical timestamps are relabeled. The replacement contains **19,142 hourly
rows**, adds **1,248 newer dates**, and reaches **2026-10-02 20:00 UTC** for
each symbol. The 27 retained rows whose values differ from the old snapshots
are recorded explicitly. Every original local snapshot, its Parquet before-image,
and the checksummed public response/receipt are preserved locally and in RustFS.
These are complete imported snapshots, not certified native handoffs.

Publication holds the archive and six series leases, checks current state/rows
against the captured originals, rejects active repair leases, preserves a
populated before-backup and atomically replaces only those six hourly series.
All **5,666,393 unrelated candles** match the populated before-backup exactly,
including provenance and nanosecond versions. All unrelated series, tickers,
jobs, staging, quality, sync, source checks, imports, adoptions and archive
records are unchanged. The manifest is published and verified. Evidence:
`data/public-hourly-publication-20261003T210713Z/report.json`.

Full HTTP exports match every timestamp/OHLCV for all **19,142 hourly candles**
and **6,484 derived four-hour candles**. The existing Python SDK passes **24**
hourly/four-hour SMA/EMA checks across all six symbols, including MA10–MA200
and API provenance. Reports: `data/http-public-hourly-20261004.json` and
`data/sdk-public-hourly-20261004/`.

The actual public web app, routed to localhost in isolated browsers, renders
AAPL and NVDA hourly charts and both global benchmarks through October 2 with
no page/network errors or writes. Browser verification now records response
timestamp bounds and can require explicit minimum chart dates. A negative
rehearsal requiring October 3 fails specifically on stale chart data despite
populated HTTP 200 responses. It is an expected verifier failure. Reports and
screenshots: `data/web-public-hourly-aapl-fresh-20261004.json`,
`data/web-public-hourly-nvda-fresh-20261004.json` and
`data/web-public-hourly-freshness-negative-20261004.json`.
The first rehearsal also requested a four-hour control, which the current
global page does not expose; it failed explicitly on the absent control.
Four-hour verification therefore covers HTTP/SDK behavior, not an invented UI
control (`data/web-public-hourly-aapl-20261004.json`).

The worker now stops imported Yahoo hourly snapshots before upstream reads or
repair scheduling, with `handoff_required`. Tests cover public API, S3 and local
legacy providers, unchanged data/state, no jobs or upstream requests, and lease
release. Ongoing native hourly ingestion and current gold hourly data remain
open; a fresh snapshot alone does not establish continuous operation.
The full application suite passes **347 tests**, Ruff lint/format checks pass,
and offline source/wheel builds succeed. The existing Starlette/httpx warning
remains unchanged.

Main SQLite now contains **5,685,535 candles** with **701 active archives**.
The after-backup is **865,353,728 bytes**, SHA-256
`38ee2f6fcf27f3c444358f860e1df4d38004fa2b60e721749fdea6e654faa72c`,
and its own `quick_check` and populated row count pass.
Evidence: `data/public-hourly-publication-20261003T210713Z/backup-check.json`.
Production routing remains unchanged.

## Hourly timestamp compatibility and public API candidates — 2026-10-04 ICT

The Rust Yahoo worker calls `vci_shared::normalize_time`, which rounds native
hourly timestamps down to the whole UTC hour. Python previously retained
the source `:30` anchor. The provider now matches that existing API label while
preserving the source bar's OHLCV. Daily and minute normalization retain their
existing boundaries. Tests check all three intervals and reject conflicting
source bars that would collide at an hourly boundary. Existing frozen hourly
rows are not relabeled or replaced by this code change.

`scripts/check_yahoo_hourly.py` records public JSON and original Yahoo bodies,
compares the normalized native series, local hourly history and minute
aggregation, checkpoints failures, and exits unsuccessfully on source
differences. The captured September 28–October 2 window has four missing
historical closing markers for each stock/index native response. AAPL, SPY,
S&P and Dow otherwise match all shared values in the second captured check;
MSFT and NVDA have five and 28 differing shared rows. Gold has two differing
volume rows. Minute aggregation also differs, so it cannot silently replace
native hourly history. Evidence and original bodies:
`data/yahoo-hourly-source-check-normalized-20261003/`.
An earlier AAPL response differed at the first bar as well; the independently
captured responses are retained in `data/yahoo-hourly-source-check-20261003/`.
The timestamp correction does not establish source immutability.

Complete public JSON exports for 2023–2026 are staged in an isolated filesystem
archive/SQLite database at `data/public-hourly-candidate-20261003/`. The six
stock/index candidates contain **19,142 rows**, preserving all **17,894**
currently stored timestamps and adding **1,248** newer dates through October 2.
They expose 8/9/3/2/2/3 changed retained rows for AAPL/MSFT/NVDA/SPY/S&P/Dow,
respectively. Original local snapshots, public receipts and comparisons remain
available for publication review. This is a candidate capture, not a completed
publication or native provider handoff.

Gold's public 2026 hourly export contains **3,998 rows**, including **279**
timestamps not aligned to a minute. The original response is preserved as
`gold-public-2026-8039cb2d97f9b7d7dc1ab6b90a47adde06f1ddb460b85e84c0db0eb35ad95a3c.json`
inside that candidate directory. Examples include April 2 `20:59:59` and
April 5 `22:03:08`; both have zero volume and identical OHLC quote values.
Strict import rejects that year. No candles are rounded, fabricated or removed
to force acceptance. Gold hourly freshness remains unresolved.

The full application suite passes **344 tests**; Ruff checks/formatting pass
for **57 Python files**, and offline source/wheel builds succeed. One existing
Starlette/httpx deprecation warning remains. Main hourly state and complete
stored records are verified unchanged for all seven symbols; main SQLite
still has **5,684,287 candles** and passes `quick_check`.
Proof: `data/public-hourly-candidate-20261003/main-unchanged.json`.

## Native gold daily history restored and global hourly freshness gap — 2026-10-04 ICT

The bounded primary Yahoo gold snapshot exactly matches every timestamp/OHLCV
of all **757 existing hot/cold rows** and shares the current ready native
provider/revision. It includes every older date served by the public API.
Older native candles can therefore extend the existing basis without joining
the conflicting legacy quote frame or changing recent data. The unresolved
30 recent public dates and older public/native value differences remain
preserved in the original response/diagnostics; no complete parity is claimed.

Local publication adds **3,456 daily candles**, from **2010-01-04 through
2023-09-29**, in **14 yearly Parquet objects**. The existing 2023-10-02 boundary
object is unchanged. Every older public date is included, with additional
actually observed native dates rather than inferred sessions. All quote fields
pass the explicit daily futures policy and are retained without clamping.
The publisher holds archive/series leases, rejects active repair leases,
checks current state/hot/index before-images, preserves a populated backup and
original sources, verifies object readback and commits all index entries
atomically. SQL comparisons with the before-backup prove all existing candles,
tickers, series, source checks, jobs, staging, quality, sync, imports and adoptions
unchanged. Main SQLite `quick_check` passes. An initial local preparation had
an unused reference to another migration receipt and exited before any index
mutation; the corrected publisher and both preparation artifacts are preserved.
Evidence: `data/gold-native-daily-history-publication-20261003T205136Z/report.json`.

The full historical HTTP export verifies **4,213 native dates** with every
timestamp/OHLCV exactly matching the captured response. The existing SDK passes
gold daily/weekly SMA/EMA checks, including MA10 through MA200, and the real
public web app passes gold daily/weekly and global benchmarks in an isolated
browser routed to localhost. Reports:
`data/http-gold-daily-backfill-20261004.json`,
`data/sdk-gold-daily-backfill-20261004.json` and
`data/web-gold-daily-backfill-20261004.json` with screenshots.

The complete chosen global 2022 cold/warm check now succeeds for **all seven
series**, with **251 candles each**, no failed requests and matching full-record/
provenance hashes. Actual cold transfers total **67,164 bytes** in seven objects;
warm transfers are zero. Cold/warm medians are **42.73/21.04 ms**, with peak RSS
**160,055,296 bytes**. Main metadata remains unchanged during that benchmark.
Report: `data/history-global-complete-backfill-20261004.json`.

Main SQLite remains **5,684,287 candles**; active archives rise to **701**.
The populated after-backup is **865,296,384 bytes**, SHA-256
`b4d6fff0df74ad500aafd1714dbebbfebcaee725cd9a1a5d1ee926b6fe216ee6`, and passes
`quick_check`. Isolated manifest recovery restores all 701 archive records,
67 adoptions, 34 recoveries and one unavailable range exactly. Report:
`data/gold-native-daily-history-publication-20261003T205136Z/backup-and-index-restore.json`.
Application code is unchanged in this publication; the latest full suite remains
340 tests. Production routing is unchanged.

An actual local HTTP freshness check now identifies a separate web-data defect:
AAPL/MSFT/NVDA/SPY/S&P/Dow minute tails reach **2026-10-02 20:00 UTC**, but
their hourly controls return **2026-08-26 20:00 UTC**. Gold minute/hourly tails
are **2026-03-31 23:59 UTC / 2025-12-31 21:00 UTC**, despite its current daily
tail. Every HTTP request returns 200, so populated-response checks alone do
not detect this stale interval selection. Evidence:
`data/global-interval-freshness-20261004.json`.

Live AAPL and gold hourly exports, with both default and explicitly database
read paths, reach **2026-10-02 20:00 UTC** and use whole-hour timestamps. The
two paths return identical captured payload hashes for each symbol. A naive
UTC minute-to-hour derivation matches AAPL's 20 timestamps but differs on
**17 candles**, including prices and volumes; gold's derived latest 20 rows
are entirely older than those public rows. Thus blindly preferring minute
aggregation cannot certify the inherited hourly contract. Complete original
hourly responses, hashes, times and computed comparisons remain at
`data/global-hourly-live-comparison-20261003T205530Z/`. Next, verify native-hour
session/bucket semantics and capture coherent current hourly snapshots while
preserving older availability, then recheck actual web control freshness.

## Daily futures quote validation and bounded Yahoo reads — 2026-10-04 ICT

CME distinguishes traded highs/lows from calculated settlement prices and
documents gold settlement methods using trades, spreads, bids/asks or previous
settlements ([CME gold rules](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457088147/Gold)).
This means close outside the traded range alone is insufficient to declare
a daily futures quote corrupt. It does not certify every Yahoo historical
close as an exchange settlement. Yahoo metadata labels GC=F as `FUTURE`.
Original quote values must remain observable rather than clamped or inferred.

`Candle.validate` now accepts a finite daily Yahoo futures close independently
of the trade range. Open remains within high/low, high cannot be below low,
volume must be nonnegative, and all non-futures/intraday range checks remain
unchanged. The existing SJC quote representation is unchanged. New tests prove
the daily exception cannot license bad opens, reversed ranges, negative volume,
nonfinite closes, stocks, crypto, SJC, or hourly/minute futures. A captured
2010-11-01 gold row passes SQLite-to-Parquet-to-weekly-response readback with
all prices and volume preserved exactly. VND/VNINDEX invalid-stock tests still
reject their original bad rows.

Yahoo requests now honor the caller's explicit lower bound and validate only
the requested page. A server returning an unrelated older invalid open cannot
block the desired window; an invalid open inside the window still fails.
The isolated candidate tool passes its requested floor, supports explicit GC=F,
and writes partial captures/discrepancies on failure. Code/test/documentation
commit: `09bdbb9`. **340 tests pass in 23.18 seconds**, with the existing single
Starlette/httpx deprecation warning; all 56 Python files pass Ruff formatting,
lint passes, and offline wheel/sdist builds pass.

An unbounded alternate Yahoo reply contains **6,548 observed rows**, **441**
rejected by the original range rule and **56** with opens outside high/low.
Every such bad open predates 2010; the rule remains strict and these rows are
not imported. Its old values also differ from the public API after 2020, so
the response is not treated as a validated legacy contract frame. Original
response/metadata and computed diagnostics remain at
`data/gold-daily-semantics-20261003T204023Z/`.

The real bounded primary Yahoo request starts **2010-01-01** and ends after
**2026-10-02**, excluding unrelated earlier data. Parsing now succeeds and
matches every timestamp/OHLCV of all **757 existing hot/cold rows** exactly;
the existing adjustment detector finds no signal. The full public snapshot
contains **4,242 dates**, with **30 recent dates absent in native history** and
**1,551 differing rows**. All absent dates are in 2026; the bounded native
response includes all older public dates. The full-history candidate correctly
fails before writing candidate candles/objects or changing the main index,
instead of dropping dates or guessing a conversion. Preserved report:
`data/gold-daily-bounded-candidate-20261004/report.json`.
Source/diagnostic files also have content-hashed RustFS copies and readback
checks listed in that directory's `evidence-receipt.json`.

The local API restarts with the tested code. Populated FPT/VCB and AAPL/GC=F
daily payloads remain byte-for-byte identical before/after restart; production
routing is unchanged. Evidence:
`data/api-futures-validation-before-restart-20261004.json` and
`data/api-futures-validation-after-restart-20261004.json`.
Main candle/archive counts remain **5,684,287 / 687**. Next, archive the older
native gold dates on the verified existing basis, preserving originals and
current data; keep the wider public/native calendar and price discrepancies
separate rather than declaring complete cross-source parity.

## Four coherent Yahoo stock/ETF daily histories published — 2026-10-04 ICT

`scripts/stage_yahoo_daily_history.py` builds isolated full daily candidates for
AAPL/MSFT/NVDA/SPY using the existing Yahoo normalization code. Raw native and
public API responses and exact served before-images are checksummed and retained.
The staged snapshots preserve every retained and public date, use a new native
revision per symbol, and pass complete SQLite/Parquet readback. The main API is
unchanged during staging. Tool/usage commit: `27eccd2`; Ruff lint/format pass.
Stage evidence: `data/yahoo-daily-coherent-candidates-20261004/report.json`.

| Symbol | Complete dates | Recent SQLite rows | Cold rows | New yearly objects |
| --- | ---: | ---: | ---: | ---: |
| AAPL | 11,544 | 753 | 10,791 | 44 |
| MSFT | 10,218 | 753 | 9,465 | 38 |
| NVDA | 6,967 | 753 | 6,214 | 25 |
| SPY | 8,477 | 753 | 7,724 | 31 |

The snapshots change adjusted prices on **342/394/366/380 original retained
rows**, respectively, with exact original volumes. Computed maximum relative
price differences are **3.2568537229746277e-7**, **3.4396855459126105e-7**,
**3.082077497283551e-7** and **3.530119035277521e-7**. The existing adjustment
detector returns no corroborated revision signal for any symbol; its threshold
is unchanged. Full native snapshots replace the selected histories coherently;
no old price is multiplied by an inferred factor, and strict legacy parity is
not claimed. Earlier public adjusted values remain in immutable source evidence.

Before publication, the real worker successfully refreshes **40 completed daily
candles per candidate** and runs its historical probe. Every fresh price/volume
matches the staged hot snapshot exactly, date sets remain unchanged, successful
source checks use the new revision, and no repair job is queued. All archive
objects are read back against the candidate. The publisher preserves original
hot before-image Parquet, original cold objects, raw API/native bodies, worker
response bodies and a populated before-backup. Archive and four series leases,
current state/row/index checks and active-job checks protect one atomic SQLite
replacement. Four old boundary objects become superseded; 138 new yearly objects
provide the complete cold histories under the same revisions as recent data.

**37,206 dates** now read exactly from the published candidates; **34,194 cold
candles** include all four original boundary dates. All **5,681,275 unrelated
candles** match the before-backup in every field, including provider/revision and
write version. Unrelated series/source checks/archive entries, all ticker/job/
staging/quality/sync/import/adoption records, all recoveries and unavailable-history
records remain exact. Existing independent findings are not cleared. Evidence:
`data/yahoo-daily-history-publication-20261003T203538Z/report.json`.

Eight bounded historical HTTP exports verify all **37,206 timestamps/OHLCV**
exactly. All four symbols pass daily/weekly SMA/EMA SDK checks with exact
timestamps/OHLCV/MA10 through MA200. The real public web app, routed only in an
isolated browser to localhost, passes AAPL daily/weekly and global benchmark
requests without page/network/write errors. Reports:
`data/http-yahoo-four-stock-daily-backfill-20261004.json`,
`data/sdk-yahoo-daily-backfill-{AAPL,MSFT,NVDA,SPY}-20261004.json`, and
`data/web-aapl-daily-backfill-20261004.json` with screenshots. This browser
rehearsal covers AAPL; it is not a claim of all global web controls passing.

The seven-series 2022 global cold/warm runner now succeeds for **six symbols**
and explicitly fails only **GC=F**, so its overall exit remains nonzero. It
downloads **60,342 bytes** across six objects cold and zero warm; successful
cold/warm medians are **43.83/20.125 ms**. Full-record/provenance hashes match;
main data/metadata is unchanged during the benchmark. Report:
`data/history-global-four-stock-backfill-20261004.json`.

Main SQLite remains **5,684,287 candles**; active archives rise to **687**.
The populated after-backup is **865,284,096 bytes**, SHA-256
`e3eb3ca9806f7a302c0220d1eb62ef1fd3745ca11fb37f7b4a3f95f4eb5488fb`, and passes
`quick_check`. Isolated manifest recovery restores all 687 archive records,
67 adoptions, 34 recoveries and one unavailable range exactly. Report:
`data/yahoo-daily-history-publication-20261003T203538Z/backup-and-index-restore.json`.
The original publication receipt is preserved independently; supplementary
diagnostics/recovery proof use a separate content-hashed RustFS evidence key.

GC=F is the remaining selected cross-market daily backfill. Its older quote
semantics and the previously documented VN/global-minute disagreements remain
open. API application code is unchanged; the latest full suite remains 335 tests.
Production routing is unchanged.

## SJC and global index daily history restored — 2026-10-04 ICT

Bounded database-backed public exports capture the selected global/SJC daily
history before 2006 and from 2006 through 2026-10-02. Original Yahoo chart
responses are also preserved and normalized using the existing provider logic.
SJC's complete observed public snapshot matches all **1,371 retained hot/cold
rows** exactly; both indexes match all **754 retained rows each** exactly.
Dow's observed public/native history matches exactly. S&P has one older low
serialization difference on 2010-07-06 (**1018.3499755859376** public versus
**1018.3499755859375** native); this is recorded without claiming strict parity.
Capture report: `data/cross-market-daily-full-preflight-20261003T202627Z/report.json`.

Publication adds **21,582 older candles in 83 yearly Parquet objects**, retaining
existing provider/revision identities and all existing archive objects:

| Series | First newly archived observed date | Last newly archived date | New cold rows | Objects |
| --- | --- | --- | ---: | ---: |
| S&P 500 | 1980-01-02 | 2023-09-29 | 11,030 | 44 |
| Dow | 1992-01-02 | 2023-09-29 | 7,996 | 32 |
| SJC-GOLD | 2016-01-01 | 2022-12-31 | 2,556 | 7 |

These first dates describe the captured source's availability, not a verified
listing or lifetime coverage claim. SJC uses its existing frozen `legacy-api`
snapshot basis; this does not enable its unavailable native endpoint. All
observed dates remain preserved without inferred holidays or missing quotes.

The first publisher prepared/verified all objects but rejected a queued SJC
bootstrap job; its index transaction rolled back. Inspection confirms that
job is pending with no active lease. The retry reuses the same verified uploaded
objects, holds archive/series leases, rejects active job leases, checks unchanged
series/hot/archive before-images and atomically inserts all 83 index records.
The pending job remains unchanged. Raw source responses and the publication
receipt have RustFS copies with hash/readback checks. Full SQL comparisons with
the before-backup prove all existing candles, tickers, series, jobs, staging,
quality, sync, import receipts, adoptions and source checks unchanged.
Evidence: `data/cross-market-daily-history-publication-20261003T202846Z/report.json`;
the earlier failed preparation receipt remains preserved separately.

Six bounded HTTP requests verify **24,461 observed candles**, with every date
and OHLCV field exactly matching the chosen snapshots (including one empty
pre-history SJC range). S&P and SJC SDK daily/weekly SMA/EMA checks pass with
exact timestamps/OHLCV/MA10 through MA200. Reports:
`data/http-index-sjc-backfill-20261004.json`,
`data/sdk-index-daily-backfill-20261004.json` and
`data/sdk-sjc-daily-backfill-20261004.json`.

The 2022 SJC cold/warm check succeeds, downloading **5,955 bytes** cold and zero
warm; medians **16.31/8.63 ms**. The global runner succeeds for both indexes,
downloading **15,446 bytes** cold and zero warm; successful medians
**24.905/11.58 ms**. That global report still exits nonzero because all five
other selected global ranges remain absent; empty ranges never count as fast
successful reads. Full-record/provenance hashes match cold/warm and main
metadata is unchanged during benchmarks. Reports:
`data/history-sjc-backfill-20261004.json` and
`data/history-global-partial-backfill-20261004.json`.

Main SQLite remains **5,684,287 candles**, with **553 active archive objects**.
The after-backup is **865,091,584 bytes**, SHA-256
`f37b557e1ab46582d8ed06b9da9db55a8a56a197f7aa3527b3b942bdac77e55d`, and passes
`quick_check`. Isolated manifest restoration validates all objects and restores
all 553 archive records, 67 adoptions, 34 recoveries and one unavailable range
exactly. Evidence:
`data/cross-market-daily-history-publication-20261003T202846Z/backup-and-index-restore.json`.

AAPL/MSFT/NVDA/SPY wide native captures preserve all retained dates/volumes but
change adjusted prices on **366/337/324/364 retained rows**, respectively.
Computed maximum relative differences are **4.1146692484844646e-7**,
**2.4997264885773704e-7**, **3.385101311960505e-7** and
**3.3265877526505293e-7**. These fail the strict absolute `1e-8` preflight;
no tolerance is widened or old values scaled. Next, publish one coherent native
daily snapshot per symbol, with all served dates and original before-images.
GC=F's public snapshot has **26 older rows rejected by the existing OHLC range
validator**, mostly with close outside high/low. These need separate semantics
verification: CME documents traded high/low and settlement as separate fields
([CME settlement definitions](https://www.cmegroup.com/trading/about-settlements.html)),
which alone does not prove the Yahoo rows correct. Preserve them and investigate
the actual provider representation before relaxing validation or excluding data.
Detailed computed diagnostics:
`data/cross-market-daily-full-preflight-20261003T202627Z/discrepancy-diagnostics.json`.
Existing VN and minute disagreements remain open; production routing is unchanged.

## Selected crypto daily history restored — 2026-10-04 ICT

Read-only captures preserve the full public database-backed API snapshot and
original Binance kline responses through 2026-10-02. All **4,384 retained SQLite
daily candles** match Binance exactly in timestamp/OHLCV. Public/native dates
and all prices match across **12,165 candles**; **30 older volume differences**
remain explicit (BTC 9, ETH 9, SOL 5, BNB 7). Strict legacy parity checks therefore
exit nonzero. These differences do not constitute an adjustment-price mismatch:
native archives use the existing Binance provider/revision, with both original
sources preserved. No public volume is silently labeled identical to Binance.

`scripts/check_crypto_daily_history.py` makes that distinction reviewable:
`passed` requires strict parity, while `native_basis_verified` verifies exact
retained overlap, continuous native daily timestamps and public price/date
parity. All four native bases pass. Raw responses/checksums/full discrepancy
reports: `data/crypto-daily-history-native-basis-20261004/report.json`.
The script and its usage pass Ruff lint/format and were committed as `2d23042`.

Local publication adds **7,777 candles in 25 yearly Parquet objects**:

| Symbol | First observed native date | Last newly archived date | New cold rows | Objects |
| --- | --- | --- | ---: | ---: |
| BTCUSDT | 2017-08-17 | 2023-10-01 | 2,237 | 7 |
| ETHUSDT | 2017-08-17 | 2023-10-01 | 2,237 | 7 |
| SOLUSDT | 2020-08-11 | 2023-10-01 | 1,147 | 4 |
| BNBUSDT | 2017-11-06 | 2023-10-01 | 2,156 | 7 |

Four existing 2023-10-02 boundary objects remain unchanged and also match native
OHLCV. New objects join continuously to that boundary; no pre-first-candle date
is inferred. The publication holds archive/series leases, validates ready
revisions and unchanged before-images, rejects active repair jobs, verifies
each uploaded object's full readback, and commits all index entries atomically.
Raw API/native evidence and a publication receipt are copied to RustFS with
readback checks. SQL comparisons against the immutable before-backup prove all
existing candles, tickers, series, jobs, staging, quality, sync, import receipts,
adoptions and source checks unchanged. SQLite `quick_check` passes.
Evidence: `data/crypto-daily-history-publication-20261003T202224Z/report.json`.

All four full historical HTTP queries return **12,165 candles** with every
timestamp/OHLCV exactly matching native captures. The previously missing 2022
ranges now pass cold and warm checks for all four symbols: **38,983 actual cold
download bytes**, zero warm downloads, matching full-record/provenance hashes;
cold/warm medians **38.15/18.685 ms**. BTC SDK daily, weekly and 15-minute SMA/EMA
checks all pass with exact timestamps/OHLCV/MA10 through MA200. Reports:
`data/http-crypto-backfill-20261004.json`,
`data/history-crypto-backfill-20261004.json`, and
`data/sdk-crypto-backfill-20261004.json`.

Main SQLite candle count remains **5,684,287**; active archive count rises to
**470**, with the existing handoffs/recoveries/unavailable-history records
preserved. A populated after-backup is **865,046,528 bytes**, SHA-256
`3b64fe0d7a05cc921c21e93209e329ccca87ebde1322c469ba5405811f9170be`, and passes
`quick_check`. Restoring the RustFS manifest into a separately initialized
database verifies all 470 archive records, 67 adoptions, 34 recoveries and one
unavailable-history record exactly. The first recovery attempt reached all
object validation but lacked destination tables; initializing the isolated
schema and rerunning completes it. Backup/recovery report:
`data/crypto-daily-history-publication-20261003T202224Z/backup-and-index-restore.json`.
Seven global and SJC older daily gaps remain open, alongside the
earlier provider and production acceptance gates. Production routing is unchanged.

## Current-universe local performance and missing cross-market archives — 2026-10-04 ICT

A read-only HTTP rehearsal covers all **59 selected VN daily/15-minute series**,
bulk daily, six older VN ranges, four crypto minute queries, global weekly and
health. All **133 sequential baselines** and **296 concurrent requests** pass;
every case appears in the concurrent run and all candle payload fingerprints
remain stable. Four clients run for **30.50 seconds**, with response caching
disabled and archive-file caches warmed by the baselines. Selected medians/P95:
VN daily **50.70/107.09 ms**, VN 15-minute **746.21/883.77 ms**, bulk daily
**1,842.82/2,000.33 ms**, older daily **364.64/455.54 ms**, and health
**2,313.56/2,321.85 ms**. This is observed local workload evidence, not a
production capacity assertion. Report: `data/http-current-universe-20261004.json`.

Native-history runners use fresh temporary caches and compare full candle
records/provenance on cold and warm reads, leaving main data/metadata unchanged:

| Range | Successful series | Candles | Cold/warm median ms | Cold object bytes | Warm bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| VN daily, full 2022 | 58 | 14,401 | 42.405 / 23.94 | 407,078 | 0 |
| VN minute, September 2025 | 59 | 244,702 | 152.91 / 121.11 | 1,832,421 | 0 |

The daily range excludes only VPL, with its previously verified listing source.
All other queries succeed; cold/warm full-record hashes match. Peak measured
process RSS is **188,661,760 bytes** for daily and **189,726,720 bytes** for minute;
SQLite is **865,026,048 bytes**. Reports:
`data/history-current-daily-20261004.json` and
`data/history-current-minute-20261004.json`. Downloaded bytes are measured on
local RustFS, with validation and hashing included in query timings. They do not
measure cloud billing or remote network latency.

The first direct history-script invocation could not import its shared helper;
module invocation already worked. The runner now supports both invocation paths,
without masking unrelated missing dependencies. Both complete direct workloads
pass, module `--help` passes, and the changed script passes Ruff lint/format.
Application code is unchanged; the latest complete API suite remains 335 tests.

Broader cross-market 2022 checks expose **12 absent local daily ranges**: all
four selected crypto assets, all seven global assets and SJC. Both cold and warm
runs correctly return nonzero with no successful requests/downloaded objects;
these are missing archives, not fast data reads. Reports:
`data/history-current-crypto-20261004.json`,
`data/history-current-global-20261004.json` and
`data/history-current-sjc-20261004.json`.

The explicitly database-backed live `/tickers` API returns valid 2022 JSON for
every affected series: **365 rows per cryptocurrency**, **251 per global ticker**,
and **364 SJC rows**, totaling **3,581 observed validated candles**. Complete
raw responses, checksums and date bounds remain preserved at
`data/cross-market-history-availability-20261003T201301Z/report.json`. These
prove an outstanding older-history migration gap; they are not yet published
or proven compatible with each current native adjustment basis. Next, capture
complete coherent snapshots or verify pinned native older history and retained
boundaries, then archive with originals/backups preserved. No row or unavailable
date is inferred. The current plan explicitly retains this gate alongside the
VND/VNINDEX and native-minute provider issues; production remains unchanged.

## Remaining global disagreements and current roadmap — 2026-10-04 ICT

A numerical audit separates strict Python equality differences from material
changes in the saved Yahoo/public-API captures. Most MSFT/NVDA field mismatches
are tiny decimal serialization differences below the existing absolute `1e-8`
price comparison threshold. MSFT still has one candle with materially different
open/high/low; NVDA has six materially different price or volume candles; gold
has two material candles. The native handoff failures are therefore not resolved
by normal float tolerance, and no threshold is weakened.

Eight read-only Yahoo window checks use a day-end bound and a nearby intraday
bound for each affected UTC date. All 18 target comparisons are present, match
the earlier native capture within the existing price tolerance and exact volume,
and retain material differences from the public snapshot. These responses rule
out a request-window difference in these observed cases; they do not independently
certify which source values are correct. Main daily/operational hashes and
production remain unchanged. Full native replies and comparison rows are
preserved at `data/global-native-window-consistency-20261003T195748Z/report.json`.

`TODO.md` is reconciled into the current roadmap rather than carrying superseded
staging narratives as additional open tasks. Original architecture, contract,
retention, provenance, provider, worker/recovery, migration, authentication,
restoration and acceptance requirements remain. Resolved EIB/HHS sessions and
recovered historical partitions are no longer presented as current blockers.
Unverified history, native handoffs, provider/calendar semantics, wider client/
performance acceptance and private inventory remain explicitly open. Historical
milestones and their evidence remain in this report and Git history through
`eb814ab`. This documentation change does not complete or shrink the goal.

## Yahoo outage overlap and close-time rows — 2026-10-04 ICT

Yahoo updates previously used only the recent 40-candle page, without the
observed-tail guard already applied to VN updates. After an outage they could
append newer candles across an unverified hole and record success. Yahoo
now also expands at most once within the current provider/retained window,
up to 1,000 candles. A still-disjoint reply queues durable staged recovery,
records the coverage finding/repair outcome, and preserves the published data.
The whole expanded stored overlap is checked for corroborated price changes
before publication, including records outside the usual last 50.

Daily Yahoo replies must include the exact stored tail. Actual five-session
captures show later intraday replies omit four earlier close-time timestamps
retained by the public API. Requiring only that exact last timestamp would queue
repair despite a matching adjacent regular bar. Yahoo hourly/minute therefore
accept only a stored overlap within one interval of the published tail. They
preserve the original close-time row; an older, nonadjacent overlap still cannot
license appends. VN/crypto behavior remains covered by existing regressions.

Eleven added Yahoo cases cover bounded daily/hourly/minute expansion, capped
replies and source-check/job outcomes, preserved omitted close-time originals,
strict daily and nonadjacent minute rejection, and older expanded price revisions.
The complete suite passes **335 tests in 22.69 seconds**, with the existing
Starlette/httpx deprecation warning. Ruff lint and formatting pass for **58 Python
files**, and offline wheel/sdist builds pass.

Fresh isolated native rehearsals:

- AAPL daily starts with **653 of 753 reference candles**, omitting the latest
  100 observed dates. Requests expand **40 to 185**, pinned to Yahoo with the
  retention start; all 753 dates/OHLCV return exactly, with no findings or repair
  jobs. Its optional ten-row historical sample is recorded separately from the
  expanded recent request.
- SPY minute starts with **55,893 of 56,675 reference candles**, through the
  observed **2026-09-30 20:00 UTC** close-time row. Requests expand **40 to 1,000**.
  The expanded page lacks that close-time row but matches its adjacent stored
  **19:59 UTC** bar. Every seeded timestamp/original remains present; all
  observed native values match exactly. The result contains **56,674 rows**.
  Yahoo does not provide the later **2026-10-01 20:00 UTC** public close-time
  record during catch-up; it is not invented. This is verified native observed
  continuity, not exact equality with every later public snapshot timestamp.

Both isolated series remain ready, without findings/jobs; main daily/operational
hashes and production remain unchanged. Evidence:
`data/yahoo-outage-native-rehearsal-20261003T195349Z/report.json` and hashed
provider captures/databases. The earlier isolated daily run at
`data/yahoo-outage-native-rehearsal-20261003T195255Z/` remains preserved; its
checker incorrectly counted the optional historical sample as a recent request.
The corrected checker records those requests separately and verifies both
markets without changing main data. Provider/calendar and unresolved historical
acceptance gates remain open.

## AAPL/SPY full-precision minute publication — 2026-10-04 ICT

A fresh read-only five-series Yahoo/public-API probe shows AAPL and SPY now
match all **1,951 shared recent minute candles each** on both default and
explicitly database-backed API paths. Each public snapshot retains four extra
original close-time timestamps; the existing exact-overlap handoff preserves
those originals and verifies every native/shared candle through the published
tail. MSFT matches 1,590 shared candles, NVDA 460, and gold 1,988 of 1,990 shared
candles, with ten native timestamps absent from gold's API response. These other
series remain unadopted. No changed price, volume, or timestamp is guessed.
Evidence: `data/global-minute-current-preflight-20261003T194148Z/report.json`.

Complete served AAPL/SPY history is freshly captured through frozen,
database-backed JSON exports: **56,671 AAPL rows and 56,675 SPY rows**. Both
preserve every original timestamp and volume; every old CSV OHLC price equals
the new full-precision JSON price rounded to two decimals. These precision
changes do not represent a new inferred dividend or scaling factor. Each
snapshot passes a replayable Yahoo `exact_snapshot_overlap` certificate with
1,951 exact native candles across five observed UTC date partitions through its
tail, plus isolated native 40-row updates. This establishes bounded append
continuity, not a lifetime provider-policy or full exchange-calendar assertion.
Captures: `data/aapl-spy-complete-api-minutes-20261003T194236Z/`.

Only the two minute series are atomically published into main local SQLite.
Their original CSV hot-row before-images, raw exports, certificate, publication
receipts and before-backup remain preserved in local RustFS/storage. There are
no old cold objects for these two histories. The shared writer publishes the
main manifest with the new certificates. Publication and verification evidence:
`data/api-aapl-spy-minute-publication-20261003T194337Z/`.

The first post-publication checker assumed empty historical responses contained
the requested symbol key. Publication and manifest verification had already
succeeded. A read-only continuation accepts the existing omitted-symbol empty
contract and verifies all **28 monthly windows**, including timestamp equality
and all **113,346 populated OHLCV rows**; publication is not repeated.

All **5,570,941 unrelated full candle records** remain exact versus the
before-backup, including provider, revision and update timestamp. Unrelated
operational records and the VN daily checksum remain exact. All twelve SDK
daily/minute/15-minute SMA/EMA cases match timestamp, OHLCV and five moving
averages exactly. Both selected public-web daily/15-minute chart rehearsals and
their S&P/Dow benchmarks pass with populated successful responses, no page or
network errors, and no blocked writes.

The watchlist now enables daily/minute updates for AAPL/SPY alongside the two
verified indexes. The actual operational CLI refresh publishes **40 completed
rows per series**, with successful Yahoo/provider/revision observations and no
provisional rows. Only AAPL/SPY ticker metadata changes versus the pre-publication
backup. MSFT/NVDA/gold remain daily-only. Two watchlist tests pass; offline wheel
and sdist builds pass. An isolated wheel-config smoke validates **71 configured
tickers** and exactly the four licensed Yahoo minute entries, with packaged
watchlist bytes identical to the workspace configuration.

An isolated index restore exactly recovers **445 active objects**, **67 handoffs**,
**34 recoveries** and **one unavailable-history record**. The populated
post-CLI backup restores with identical bytes/checksum, exact quality/archive
metadata and `quick_check=ok`: **865,026,048 bytes**, SHA-256
`953f978efd1c14b17557c8afdb4192f3638d26f96e27437c3efdaf4ad08b26dc`.
Main still holds **5,684,287 candles**.

Available served AAPL/SPY minute history begins March 9, 2026. Capturing it
completely does not prove the unavailable preceding portion of a year. MSFT,
NVDA and gold minute handoffs, VNINDEX minute/daily semantics, VND/VNINDEX old
daily history, wider provider/session/private-inventory and production
acceptance remain open. The last complete implementation suite remains 324
passing tests; this checkpoint changes local data, watchlist and documentation.
Production routing remains unchanged.

## PLX minute publication and VNINDEX diagnostics — 2026-10-04 ICT

PLX's complete frozen default-path public `/tickers` minute capture preserves
all **57,473 existing timestamps**, with **53,131 hot rows and 4,342 older rows
in two objects**. Four volumes change; all OHLC and other volumes remain exact.
The capture's selected read path is recorded in every frozen receipt. Its
**1,099 VPS minute candles across five complete sessions** match the snapshot
and both fresh/retained daily OHLCV aggregates exactly. Its certificate records
`exact_complete_sessions`. A native 40-row rehearsal and another fresh check
immediately before publication preserve every snapshot value/date.

One SQLite transaction publishes only PLX minute rows, state, source check,
certificate, archive references and publication receipt. Original hot before-image,
old cold objects, raw exports and before-backup remain preserved; the main S3
manifest is published under the shared writer lease. Evidence:
`data/remaining-complete-api-minutes-20261003T193635Z/PLX/report.json` and
`data/api-plx-minute-publication-20261003T193718Z/`.

Verification passes:

- Fourteen monthly HTTP reads preserve the full published history.
- All **5,631,156 unrelated candle records** match the before-backup exactly,
  including provenance and update timestamps; unrelated operational rows and
  the daily checksum remain exact.
- Six installed-SDK daily/minute/15-minute SMA/EMA comparisons match timestamp,
  OHLCV and five moving averages exactly, through the replacement API.
- PLX's selected public-web daily/15-minute charts and volume profile load with
  populated successful replies, no page/network errors and no blocked writes.
- An isolated archive-index restore recovers **445 active objects**, **65
  handoffs**, **34 recoveries** and **one unavailable-history record** exactly.
  All 4,342 restored PLX cold rows match full records exactly.
- A populated after-backup restores with identical bytes/checksum and
  `quick_check=ok`: **864,010,240 bytes**, SHA-256
  `5d1eb06dbf98cb23bc925e39584ef7a0d52548168aba671d7164efaae494becf`.

The same capture run independently stages a complete database-backed VNINDEX
minute snapshot, **61,548 timestamps**, preserving all **61,539 originals** and
adding nine observed **15:05 ICT** bars. It changes three opens, one high, one
low and six volumes; closes are unchanged. The snapshot remains isolated.
Complete-session VPS adoption rejects mismatched coverage; VNDirect/DNSE reject
OHLCV disagreement. Evidence:
`data/remaining-complete-api-minutes-20261003T193635Z/VNINDEX/report.json`.

A separate read-only five-session diagnostic captures each selected native
provider. VPS supplies **1,138 candles** matching the refreshed snapshot's
common OHLCV exactly, but omits two snapshot auction candles at **14:45 ICT on
2026-09-28 and 2026-10-01**. VNDirect supplies 1,191 candles with 686 common
volume disagreements, and DNSE supplies 1,140 with 1,057. Minute aggregates also
disagree with their native and retained daily replies. These observed differences
do not establish which exchange/session/volume semantics are correct. No
missing candle or volume factor is inferred, and main VNINDEX remains unchanged.
Evidence: `data/vnindex-native-session-diagnostics-20261003T193814Z/report.json`
and hashed full provider replies.

Fresh public daily probes on both default and explicitly database-backed paths
still return invalid OHLC bounds for VND **2020-02-19** and VNINDEX
**2019-06-24/25/26** and **2021-08-23**. Complete coherent daily replacements
remain unverified. Raw responses and exact violating rows are preserved at
`data/public-daily-invalid-recheck-20261003T193925Z/report.json`.

Main still holds **5,684,287 candles**. There are now **58 native VN minute
handoffs** and one remaining frozen index snapshot. The existing unavailable
VND 2020 range and pending VNINDEX 2020 object remain open, along with independent
minute/daily findings and wider provider/session and production acceptance.
Private inventory access is separate from public candle migration. Production
routing is unchanged. This checkpoint changes local data and documentation;
the last complete implementation suite remains 324 passing tests.

## SSI minute public-API publication — 2026-10-04 ICT

A read-only audit of **111 ready native VN intraday series** (55 hourly and
56 minute; 61 DNSE, two VNDirect and 48 VPS) verifies **4,440 overlapping candles**
with exact OHLCV and observed published tails. This audit precedes SSI's new
handoff. All main data/metadata hashes remained unchanged during the audit.
Evidence: `data/intraday-native-overlap-20261003T192248Z/report.json` and hashed
provider captures.

A fresh five-session public `/tickers` comparison exposed changes in the served
SSI snapshot: both default and explicitly database-backed paths now agree on
six corrected opens and 14 volumes. PLX and VNINDEX read paths still differ;
their findings remain preserved. Evidence:
`data/frozen-minute-api-paths-20261003T192557Z/report.json`.

SSI's complete database-backed minute history was recaptured in 15 frozen,
checksummed monthly export batches. Its **61,005 unique timestamps** exactly
retain the old served date set: **56,033 hot rows** and **4,972 cold rows in two
objects**. Only the same six opens and 14 volumes change; high/low/close and
all other values remain exact. The first isolated candidate verified a 1,130-row
exact overlap. A second candidate reused the same frozen exports and proved
**five complete sessions**, all **1,130 minute OHLCV candles**, and their native
VPS/retained daily aggregates exactly. The replayable certificate records
`exact_complete_sessions`; it licenses native appends to this snapshot without
inferring historical adjustment factors. Both candidates and originals remain
preserved:
`data/ssi-complete-api-minute-20261003T192800Z/report.json` and
`data/ssi-complete-session-api-minute-20261003T192959Z/report.json`.

The stronger candidate passed a native 40-row worker update and another fresh
40-row check immediately before publication. A populated SQLite backup,
original hot-row Parquet before-image, old cold objects, content-addressed raw
exports, certificate and publication receipt were preserved. One SQLite
transaction replaced only SSI minute rows/state/check/certificate and cold
references. The main S3 manifest was published under the existing shared writer
lease, and the old objects remain available as superseded originals.

Publication evidence:
`data/api-ssi-minute-publication-20261003T193245Z/`.

- Fourteen monthly HTTP reads verify all 61,005 published OHLCV rows.
- All **5,628,254 unrelated candle records** match the before-backup exactly,
  including provider, revision and update timestamp. Unrelated operational rows
  and the main daily checksum also remain exact.
- All six installed-SDK daily/minute/15-minute SMA/EMA comparisons match time,
  OHLCV and five moving averages exactly, using the replacement API.
- The unchanged public web UI loads SSI daily and 15-minute charts and its
  volume profile with populated successful responses and no page/network errors.
  The first rehearsal mis-selected another chart after seeing SSI's initial
  watchlist request. The helper now explicitly selects SSI before exercising its
  controls; the rerun passes. Its lint/format checks pass. This is a selected
  ticker check; it does not certify every unpopulated catalog series.
- An isolated archive-index restore exactly recovers **445 active objects**,
  **64 handoffs**, **34 recoveries**, and **one unavailable-history record**;
  its 4,972 SSI cold rows match full records exactly.
- The populated after-backup restores with identical bytes/checksum and
  `quick_check=ok`: **862,093,312 bytes**, SHA-256
  `c5b710cf3670a1e7df784f188177668e1362515ecc9263bc2e76bce8208e7f42`.

Main still holds **5,684,287 candles**. SSI adds one licensed minute handoff;
57 VN minute series now have native handoffs, while PLX/VNINDEX remain frozen.
VND's unavailable 2020 daily history, VNINDEX's pending 2020 object, independent
minute/daily discrepancies, wider provider/calendar policy, private sync
inventory and production acceptance remain open. Production routing is unchanged.
The previous complete implementation suite remains **324 passing tests**;
this checkpoint changes data, documentation and the browser rehearsal helper.

## Complete OHLC revision detection — 2026-10-04 ICT

The historical revision detector previously compared only closes. Three or
more completed open/high/low corrections could therefore pass through a live
update or historical sample without queuing coherent recovery. The detector
now compares all four OHLC fields, recording the largest observed relative field
change per candle. These ratios remain diagnostics; workers never multiply
other candles by an inferred factor. Existing provider/snapshot identity,
completed-candle cutoff, representation-noise tolerance, and three-candle
corroboration remain unchanged.

Nine added regression cases reproduce previously missed opens, highs, and lows
in the detector, older historical samples, and live updates after daily snapshot
adoption. The tests verify incomplete/insufficient/unverified-provider/noise
replies do not license recovery, while corroborated corrections queue repair
without overwriting retained or immutable historical originals. The full suite
passes **324 tests in 22.79 seconds**, with the existing Starlette/httpx warning.
Ruff lint and formatting pass for **58 Python files**, and distributions build
offline.

Fresh isolated native VPS updates for EIB/HHS/GEX/HAG/SHS each verify **40 rows**
and remain ready. All **747 retained rows per ticker** preserve their dates,
OHLCV, provider and revision identities; only verified native update timestamps
can advance. Main daily/operational hashes remain unchanged, and no production
routing or archive publication occurs. Evidence:
`data/ohlc-revision-native-rehearsal-20261003T192016Z/report.json` and its isolated
database. This fixes missed price-field corrections, while wider provider policy
and VND/VNINDEX historical acceptance remain open.

## Daily restart coverage — 2026-10-04 ICT

VN hourly/minute updates required observed overlap after a prolonged outage,
but daily updates could append a disjoint recent 40-candle page without bridging
the published tail. The worker now applies the same guard to daily data. It
makes at most one larger request, pinned to the current provider and bounded by
the configured retained window and **1,000 candles**. An unavailable or capped
reply cannot publish an unverified tail; the original data stays intact and
missing overlap queues the existing durable staged recovery. Sparse/weekend
replies with observed overlap need no expansion or inferred exchange calendar.
The entire stored overlap within an expanded page is checked for corroborated
price revisions, including candles outside the usual 50-candle comparison.

Eight added daily cases cover successful outage bridging, exhausted page budget,
capped replies, sparse weekend observations, expanded provider failures,
fallback providers, and material versus representation-sized old-price changes.
Existing hourly/minute cases continue to pass. The full suite passes **315 tests
in 22.46 seconds**, with the existing Starlette/httpx deprecation warning. Ruff
lint and formatting pass for **58 Python files**, and distributions build offline.

An isolated FPT database starts with **647 actual rows**, omitting the last
**100 observed sessions** from its complete 747-row reference. Real VNDirect
requests expand from **40 to 184 candles**, carrying the configured retention
start. The worker restores every missing observed date; all **747 OHLCV/provider/
revision records** match the complete main reference exactly. Its source check
succeeds, its historical probe completes, and it records no quality findings.
Main daily/operational hashes and production remain unchanged.

Evidence: `data/daily-outage-native-rehearsal-20261003T191634Z/report.json` and
checksummed native captures. The earlier pre-retention-bound rehearsal at
`data/daily-outage-native-rehearsal-20261003T191442Z/` remains preserved.
The preceding outage/failure report's provider label is corrected to VNDirect
from its recorded request and candle provenance; neither rehearsal used VPS.
VND/VNINDEX history and wider provider-policy acceptance remain unresolved.

## Daily observation and historical probe failures — 2026-10-04 ICT

A daily update previously committed its recent candles and successful source
observation, then could overwrite that observation as failed if its subsequent
historical dividend/correction probe raised. It also returned zero updated rows
despite the committed data. The worker now ends the recent attempt after the
atomic commit, gives the historical probe at most 30 seconds and half the
remaining live-update budget, and records probe failures separately as
`historical_probe_failure`. Caller cancellation still propagates and live leases
are released, while the committed recent observation remains successful.

Five regressions cover an unavailable historical provider, sanitized unexpected
exceptions, caller cancellation, a probe exceeding its own budget, and an actual
corroborated historical revision outside the live overlap. The latter still
queues staged repair and preserves observed prices. This changes failure
accounting and timing; it does not establish new adjustment policies or change
the existing comparison/provenance rules.

An isolated FPT rehearsal fetches **40 actual recent VNDirect candles**, then injects
an explicit historical-probe outage. The worker returns 40, its recent source
check remains successful with 40 completed rows, and only the historical failure
is recorded. All **747 retained OHLCV/provider/revision records** remain exact;
main daily and operational hashes stay unchanged. Production data/routing is
unchanged. Evidence: `data/daily-probe-failure-rehearsal-20261003T191105Z/report.json`
and its checksummed native recent candle capture.

The full suite passes **307 tests in 22.33 seconds** with the existing benign
Starlette/httpx deprecation warning. Ruff lint and formatting pass for all
**58 Python files**; source and wheel distributions build offline. Remaining
VND/VNINDEX history and broader provider/calendar/adjustment acceptance are open.

## Native archive measurements and listing scope — 2026-10-04 ICT

The new `scripts/benchmark_history.py` measures actual S3 download bytes using a
fresh temporary cache and four readers. It compares every returned candle field,
including provenance/revision/update timestamps, between cold and warm reads,
checks the main daily/operational snapshot remains exact, preserves existing
reports, and exits nonzero on any eligible empty/error result. Successful-query
timings exclude failures and include validation and payload hashing. Configured
history-start exclusions retain their evidence links; provider no-data alone
never becomes a listing date.

| Native history range | Successful requests | Candles | Cold / warm batch ms | Cold / warm median success ms | Downloaded cold / warm bytes |
| --- | ---: | ---: | --- | --- | --- |
| VN daily, 2022 | 58 / 58 eligible | 14,401 | 653.51 / 349.52 | 44.395 / 23.45 | 407,078 / 0 |
| VN minute, October 2, 2025 | 59 / 59 selected | 11,866 | 704.88 / 369.60 | 43.25 / 22.90 | 338,101 / 0 |

The daily run downloads 58 objects and peaks at **149,553,152 bytes RSS**;
the minute run downloads 59 objects and peaks at **155,402,240 bytes RSS**.
SQLite occupies **860,274,688 bytes**. VPL's configured listing is after 2022,
so it is explicitly not applicable to that daily range. Both warm phases download
zero object bytes and reproduce complete cold payload/provenance fingerprints.
These local RustFS observations do not establish cloud costs or production SLAs.

An initial 2020 daily run flags unexplained empty SSB/GEE ranges, in addition to
the known VND/VNINDEX failures. Primary sources establish SSB's first exchange
trading on **March 24, 2021** ([regulator](https://ssc.gov.vn/webcenter/portal/ubck/pages_r/l/chitit?dDocName=APPSSCGOVVN162138012))
and GEE's first UPCoM trading on **March 8, 2022** ([exchange](https://www.hnx.vn/vi-vn/chi-tiet-tin-60014034-0.html)).
The watchlist now records those dates and sources. GEE uses its earlier UPCoM
start rather than its later HOSE transfer. The corrected 2020 run has
55 eligible tickers: **53 successes and two failures**, with SSB/GEE/VPL/OCB
reported separately as pre-listing. It returns exit status 1 despite equal
cold/warm results. No main candle, state, archive, quality or receipt changes.
Eight operational tests, Ruff lint/format checks, and offline distribution builds
pass after the watchlist/tool changes.

Fresh read-only public captures also reproduce three invalid VNINDEX opens in
June 2019, its invalid August 23, 2021 candle, and VND's invalid February 19,
2020 candle. Public VNINDEX 2020 is valid. Native 2020 probes return 252 valid
VNINDEX/VPS rows, invalid VNINDEX/VNDirect OHLC, only 236 VNINDEX/DNSE rows,
invalid VND/VPS OHLC, and 252 valid VND/VNDirect and VND/DNSE rows. These findings
retain the complete-history/provider-basis repair gates; they do not license
mixing isolated valid years with an incompatible retained revision. The newer
official [DNSE SDK](https://github.com/dnse-tech/openapi-sdk/blob/main/python/README.md)
documents authenticated market data; no unauthenticated alternate is assumed.

Evidence: `data/native-daily-history-benchmark-eligible-vn-2022-20261004.json`,
`data/native-daily-history-benchmark-listing-verified-vn-2020-20261004.json`,
`data/native-minute-history-benchmark-all-vn-20251002-20261004.json`,
`data/remaining-api-year-probe-20261003T190029Z/`, and
`data/native-remaining-year-probe-20261003T190140Z/`. Earlier failed benchmark
attempts remain preserved: the volume-profile runner uses minute history, so a
2022 profile request is not a daily archive performance check.

## Main public-API daily publication — 2026-10-04 ICT

Complete coherent public `/tickers` snapshots for EIB/HHS/GEX/HAG/SHS are now
published into the main local replacement. Every earlier served date is retained,
including SHS's 2018 history; the four missing EIB/HHS sessions are recovered.
The snapshots contain **9,915 candles**, split into **3,735 hot rows** and
**6,180 older rows in 26 immutable Parquet objects**. Each passes a fresh exact
40-completed-candle VPS check immediately before atomic publication. Snapshot
provenance and revision identities remain explicit; no inferred scaling or
lifetime corporate-action policy is claimed.

Original hot rows are backed up and stored as verified immutable before-images;
old archive files remain preserved behind superseded index entries. Frozen API
exports and checksummed per-ticker publication receipts are retained in S3.
The local transaction commits candles, state, archive identities, adoption
certificates, source checks, receipts, and only the verified resolved findings.
The temporary publication helper then tried to reacquire its own archive-writer
lease. This stopped metadata publication after the SQLite commit. Its `finally`
block released the leases; a metadata-only retry published and read-back verified
the current manifest without repeating candle replacement.

**Forty-one full-year HTTP queries** match the frozen candidates exactly.
**Sixty SDK cases** match equivalent API OHLCV and indicator scopes exactly:
30 recent daily/weekly/two-week cases and 30 historical/boundary SMA/EMA cases.
Forward and backward EMA requests can have different warmup seeds; comparisons
use the SDK's actual request scope. The SDK does not expose monthly intervals,
and the public web chart has no monthly control; initial rehearsals requesting
those unsupported controls are preserved separately from supported checks.

All five unchanged public-web rehearsals load their selected daily and weekly
charts and volume profiles from the local API, with no browser exceptions,
blocked writes, or network errors. Each still records failed VNINDEX benchmark
calls caused by its pending historical archive. Selected chart success does not
certify the entire page or benchmark acceptance.

SQL comparison against the pre-publication backup proves **5,680,552 unrelated
candles** retain all values, provenance, revisions, and update timestamps.
Unrelated operational records also remain exact. The main database now contains
**5,684,287 candles / 43,636 VN daily rows**. Its daily SHA-256 is
`7cb2e73876d943760cf50f08bb320bf1f7e55bfc36ff7fc6fcfb4de973e3bff2`;
its operational metadata SHA-256 is
`f83ce662bb3195f5e6efd638c17e21fbea836308e3e4c990cb8552d757176e72`.

A fresh S3 index reconstructs **445 active objects / 346,571 indexed rows**,
**63 handoff certificates**, **34 recoveries**, and **one unavailable-history
record**, with exact metadata. A new populated backup restores to a fresh
destination with identical SHA-256
`49c4bfbd8264bccc1af24cae55d613ff119871e3a59a5e456e04e84299c0fbfe`,
exact quality/archive/evidence records, and `quick_check=ok`.

The configured matrix now passes **2,091 of 2,103 requests**, improving from
2,059 passes. The remaining **12 HTTP 503 responses** are VND and VNINDEX weekly,
two-week, and monthly SMA/EMA requests: VND has an invalid 2020 candle and
VNINDEX has one pending 2020 archive. Matrix reads leave the current main
snapshot exact. These remaining failures and independent provider/calendar and
minute/daily basis findings prevent claiming full replacement acceptance.
The unchanged implementation's latest suite passes 302 tests; this publication
adds no runtime or schema change. Production routing is unchanged.

Evidence: `data/api-daily-publication-20261003T185405Z/report.json`, its original
pointer, per-ticker receipts, restored index and `backup-restore.json`;
`data/sdk-api-daily-publication-{EIB,HHS,GEX,HAG,SHS}-20261004.json`;
`data/sdk-api-daily-history-publication-20261003.json`;
`data/web-api-daily-weekly-publication-{EIB,HHS,GEX,HAG,SHS}-20261004.json`
and screenshots;
`data/web-query-matrix-api-daily-publication-20261004.json` and response captures.
Before/after populated backups are in `backups/` with the publication identifier.

## Daily public-snapshot handoffs — 2026-10-04 ICT

`adopt-snapshot --interval 1D` now supports frozen VN daily API snapshots. It
requires **40 exact completed native candles** through the imported tail,
including volume and provider identity. A replayable certificate stores the
original retained rows, native overlap, snapshot checksums, finality, and archive
identities. Execution rechecks all rows/state/archives under a short SQLite
transaction, rejects active live/recovery leases, preserves original values and
provenance, and changes only the allowed live provider. Original archive objects
remain readable. Later corroborated revisions still queue staged recovery;
newly inserted legacy data cannot masquerade as the verified frozen snapshot.
Unadopted daily snapshots now report `handoff_required` without initiating a
provider switch. Default minute adoption and its correction policy remain intact.

**Seventeen new regressions** exercise inspection/execution, exact overlap,
truncation/missing dates, future rows, provider identity, price/volume differences,
concurrent candle/archive changes, active worker/job leases, foreign/pending
archive bases, certificate replay/tampering, mutation after adoption, appending,
and later revision recovery. The full API suite passes **302 tests**; Ruff lint
and all **57 Python files** pass formatting, and source/wheel builds pass offline.

All five isolated EIB/HHS/GEX/HAG/SHS candidates execute real VPS handoffs and
bounded live workers successfully: each verifies 40 completed candles, preserves
all 1,933 original OHLCV rows/dates, and leaves its five cold objects readable.
The manifest reconstructs **25 objects and five adoption certificates** in a
fresh database. **Twenty FastAPI requests** cover daily SMA, daily EMA, 2019
history, and weekly charts across the five tickers; all return HTTP 200.
These observations prove the bounded transition, not complete historical
corporate-action or calendar semantics. Main SQLite remains exact.

Main archive inventory additionally shows SHS's served history begins in 2018.
Its first candidate covered only 2019 onward and is therefore insufficient for
main replacement. A fresh database-backed 2018-through-current SHS snapshot
imports **2,183 candles**, preserving every previously stored unique date,
matches a separate wide API request exactly, and executes a fresh 40-candle VPS
handoff. It has **747 hot rows and 1,436 cold rows in six objects**; the original
main fragments remain untouched. Together with the other four candidates, this
gives **9,915 candles / 26 cold objects** for subsequent publication checks.

Evidence: `data/complete-live-api-20261003T183159Z/daily-handoffs-184223.json`,
the preserved snapshot/index-restore databases and immutable manifest under
`validation-complete-api-20261003T183159Z`, plus
`data/shs-complete-api-20261003T184500Z/report.json` and its frozen API responses
under `validation-shs-api-20261003T184500Z`. Main daily and operational hashes
stay exact. Main adoption/publication, broader SDK/browser flows, remaining
invalid VNINDEX/VND history, and full production acceptance remain open.

## Consistent complete public snapshots — 2026-10-04 ICT

Complete yearly exports from 2019 through the current year reveal a legacy read-
path difference: SHS has ten differing rows between default yearly requests and
an independent database-backed wide request (some are float representation noise;
others are changed prices/volumes). The Rust handler permits recent dated reads
from Redis but falls back to PostgreSQL outside Redis coverage. A snapshot must
choose one read path explicitly rather than treating HTTP 200 as consistency.

`import-legacy --from-api --api-read-backend database` now sends `redis=false`
and `snap=false` alongside the existing `cache=false`. It uses the public HTTP
API, with no direct database connection. The default remains compatible with
existing frozen exports. Receipts record the read backend and revision. A change
within an existing snapshot is rejected before publication, including when a
different month/year would otherwise have no previous period receipt. Old
receipts resume their default path; archive-only snapshots are also protected.
Seven regressions cover request flags, frozen-cache resume, existing/new-period
switches, invalid/non-API inputs, and old hot/cold receipts.
All **285 API tests pass**; Ruff lint and all 55 Python format checks pass, and
source/wheel distributions build offline.
An actual `aipa-api import-legacy` invocation with the new option also exports
and archives all 250 GEX 2019 candles into isolated filesystem/SQLite storage;
its receipt records `api_read_backend=database` and the explicit revision.

The new mode captures **1,933 daily candles each for EIB, HHS, GEX, HAG, and SHS**:
**9,665 total**. Every value matches a separate wide API request. Their isolated
database holds **3,735 recent rows**; RustFS holds **25 published Parquet objects
/ 5,930 older rows**. All five VPS head checks match every OHLCV field across
**40 completed candles**, through the imported tail. DNSE also matches all 40
for EIB/HHS/GEX/HAG, but differs on one SHS open. VNDirect has independently
recorded volume differences. No provider handoff is yet licensed, no factor is
inferred, and no main data is changed. At that checkpoint, daily adoption was
the next implementation step; its later verified implementation is recorded above.

The complete VNINDEX import fails strict OHLC validation in 2019 before any
period is published. Its earlier isolated 2020 capture remains preserved; a
valid individual year must not be presented as a complete multi-year snapshot.
VND 2020 retains its separately recorded invalid candle.

Evidence: `data/complete-live-api-20261003T182812Z/report.json` (default-path
comparison), `data/complete-live-api-20261003T183159Z/report.json` (database-backed
comparison), their frozen download receipts, independent wide responses, and
native head captures. The latter prefix is `validation-complete-api-20261003T183159Z`.
Before/after main daily and operational hashes are exact in both rehearsals.
The isolated CLI proof is preserved under `data/live-api-cli-proof-20261004/`.

## Live API candle migration — 2026-10-04 ICT

The user directed migration through the live public `/tickers` endpoint instead
of requiring PostgreSQL. That is sufficient to export public candle data;
private sync inventory is a separate concern. Seven fresh JSON requests all
return HTTP 200. Six ranges pass strict OHLCV validation: EIB/HHS each return
15 candles spanning May 22 through June 11, 2025, including all four missing
sessions; GEX 2019 and HAG 2019 each return 250 candles, SHS 2022 returns 249,
and VNINDEX 2020 returns 252. VND 2020 retains one invalid February 19 candle:
close 2,760.51 exceeds high 2,711.91. HTTP success alone does not validate that row.

The existing `LegacyImporter` then performs six actual full-year JSON imports
from the public API into a fresh isolated database and local RustFS prefix:
EIB/HHS 2025 each import 249 recent candles into SQLite; GEX/HAG 2019, SHS 2022,
and VNINDEX 2020 archive **1,001 candles in four Parquet objects**. The combined
**1,499 imported candles** are returned by the real FastAPI app with HTTP 200
and exact OHLCV for every row. Receipts and checksummed originals are preserved,
and the isolated archive manifest is published. No PostgreSQL connection or
query is used. Main daily/operational hashes remain exact throughout.

This proves an actionable migration path without PostgreSQL. It does not yet
license inserting the four EIB/HHS sessions into the main provider revision:
all 13 surrounding overlapping rows for each ticker differ from the main
snapshot in at least one OHLCV field. A complete coherent snapshot and verified
provider transition are needed before replacing that series. Likewise, the
isolated older exports retain explicit `legacy-api` provenance rather than
being relabeled as independently verified native-provider candles.

Evidence: `data/live-api-recovery-20261004/report-181600.json`, its checksummed
raw JSON captures and `basis-comparison.json`, plus
`data/live-api-migration-20261003T181830Z/report.json` and download receipts.
Isolated S3 prefix: `validation-live-api-20261003T181830Z`. The next migration
steps use the public API as the primary candle source; private sync-record
migration must not be confused with public price-history availability.

## Previous completion blocker checkpoint — 2026-10-04 ICT

The committed implementation has a clean worktree and the local API remains
reachable (`/health` returns HTTP 200). A fresh read-only audit at **18:09 UTC on
October 3** reproduces **nine HTTP 503 ranges** across seven Vietnamese tickers.
SQLite quick-check returns **ok**; daily and operational metadata hashes remain
unchanged before and after the probes. The active archive index still contains
**442 published objects / 345,571 rows** and **four pending objects / 1,001 rows**,
with **five typed unavailable findings**.

Fresh native-provider probes return 59 valid VPS rows each for EIB/HHS from
April through June 2025, still omitting both May 22 and June 11. GEX/VPS 2019,
HAG/VNDirect 2019, VNINDEX/VNDirect 2020, and VND/VPS 2020 still fail strict OHLC
validation. SHS/DNSE 2022 still contains conflicting records on December 27.
Earlier captured alternate-provider checks remain preserved: those candidates
do not resolve the failures while retaining a verified coherent history.
Independent dated replies do not establish a safe DNSE duplicate-selection,
timestamp-shift, or cumulative-volume policy. No correction is guessed.

The actual legacy configuration is `aipriceaction/.env`. Its PostgreSQL endpoint
at loopback port 5432 still returns `ConnectionRefusedError` on a TCP probe.
The first audit examined the workspace-root configuration, where no database
URL is set; the separate legacy probe uses the actual configuration. No database
credentials, private sync payloads, PostgreSQL queries, or production writes
are involved.

| Required outcome | Current evidence | Acceptance |
| --- | --- | --- |
| FastAPI, workers, and Python operational CLI | Committed code; 278 API tests, 251 offline SDK tests, lint/format, source/wheel builds | Implemented locally |
| Existing web API and CLI/SDK flows | Contract tests, frozen fixtures, selected actual browser/CLI checks, configured 2,103-query matrix | Partial: 44 history/lookback failures and wider private flows remain open |
| Three daily years and one minute year in SQLite | Integrity and retention audits; observed-session comparisons | Storage bounds pass; four known daily sessions, intraday basis disagreements, global minute coverage, and provider/calendar semantics remain open |
| Older accessible history in S3 | Verified published Parquet/index and restore rehearsals; current HTTP probes | Partial: four pending objects, unavailable years, and private legacy inventory remain open |
| Simple infrastructure and selected Vietnam providers | RustFS-only Compose, embedded SQLite/DuckDB, VPS/VNDirect/DNSE adapters | Implemented locally; no VCI provider |
| A complete replacement ready for cutover | Full inventory, coherent history, population-wide performance, private records, and reviewed routing/rollback execution | Unproven; production stays on the legacy backend |

The same upstream-data and unavailable-inventory blockers have persisted across
repeated recovery and verification turns. Further publication would require
unverified candle corrections, loss of readable history, or assumptions about
private production data. Completing the original goal requires verified source
history/provider semantics and a read-only legacy inventory/export. No successful
unit test or narrower local rehearsal substitutes for those requirements.

Evidence: `data/goal-gate-audit-20261003T180930Z.json`,
`data/legacy-inventory-gate-20261003T181010Z.json`, the preserved native responses
in `data/completion-blocker-revalidation-20261003/`, and independent dated
witnesses in `data/dnse-duplicate-date-context-20261003/`. The data/evidence files
remain ignored; this report records their conclusions for review.

## Local Git checkpoints — 2026-10-04

The user requested actual commits after implementation had accumulated. The
rewrite is now grouped into storage (`c037c53`), provider workers/operational CLI
(`23fc706`), FastAPI/web compatibility (`b8d74b5`), and SDK compatibility
(`0a3724f`), followed by a documentation and migration-tooling commit.
Fresh scoped API runs pass **59 + 178 + 41 = 278 tests**. The SDK offline suite
passes **251 tests**, with its four existing `TestRealS3` fundamental tests
deselected. Ruff lint/format checks and the offline source/wheel build pass.
The packaged VN catalog has normalized LF endings with identical parsed rows,
its JSON grouping file has a normal data-file mode, and an empty HTML line has
its trailing spaces removed. No candle values change in this checkpoint.

Only source, packaged catalogs/static assets, the frozen regression fixture,
tests, dependency/configuration files, scripts, and documentation enter Git.
Local credentials, databases, backups, built distributions, and raw rehearsal
evidence remain ignored. These commits do not resolve the documented upstream
data defects or authorize production cutover.

## Latest bounded VN intraday catch-up

Normal VN minute/hourly updates previously requested only 40 candles after
downtime. A newer page could therefore be appended while the intervening
observed data was skipped. The worker now keeps ordinary checks at 40 candles
and retries once on the same provider when newer data has outrun the published
tail. The retry is bounded at 1,000 candles and must contain the actual stored
tail timestamp. If it still lacks overlap, existing durable recovery is queued
and published data stays intact. Expanded-request failures are dated errors;
alternate-provider failures/switches retain the existing basis-recovery path.
An observed sparse/weekend overlap needs no expansion or calendar guess.
The larger page is compared against all stored overlap before publication.
Corroborated changes older than the usual 50-bar comparison trigger staged
revision recovery; representation noise does not. This avoids silently
overwriting older adjusted prices during an otherwise successful catch-up.
This protects catch-up coverage; it does not prove that every upstream minute
is present or resolve intraday adjustment disagreements.

Fourteen new regression cases cover minute/hourly catch-up and resource limits,
providers ignoring requested counts, sparse weekends, expanded-request errors,
provider switches, source-check outcomes, old-data preservation, and lease
release, including changes outside the normal comparison window. All **278
tests pass in 21.38 seconds**. Ruff lint and the **55-file**
format check pass, and source/wheel distributions build offline.

Six real native FPT pages from **VPS/VNDirect/DNSE**, each for minute/hourly data,
are captured and replayed in isolated databases. All six recover **150 newer
observed timestamps** with exact native OHLCV. Three minute rehearsals expand
40 → 296 candles; hourly rehearsals request up to 1,000. A separate **six-case
actual live-worker rehearsal** also passes: all expand 40 → 1,000 on the seeded
outages. Minute updates publish 1,000 rows each; VPS/VNDirect/DNSE hourly updates
publish **310 / 680 / 1,000 rows** respectively. Every published value matches
the exact final native response; all six recover the 150 newer observed dates.
These controlled outage checks do not establish complete provider hourly history
or a corporate-action policy. Raw pages, request parameters, response checksums,
isolated databases, and reports are preserved.

The actual unpacked wheel passes both catch-up and short-page preservation
scenarios, protects 200 original rows when an older corroborated revision is
detected, and passes existing SQLite/Parquet hourly fixtures, current archive-index
reconstruction, 11 historical-year reads, frozen DNSE index parsing, and gap
guards. The wheel is additionally preserved under
`data/builds/606833fa3ebc8a7979e3e9f008481b18ba2ba7fa79cb0c3c453fb6d8b6ccf092/aipriceaction_api-0.1.0-py3-none-any.whl`
so later rebuilds do not remove this tested artifact.

Current read-only SQLite integrity checking returns **ok**. All **ten populated
source/interval groups** have **zero candles outside retention**, as of October
3, 2026 UTC: three calendar years for daily/hourly, one year for minute data.
This proves storage bounds, not complete coverage inside those bounds. Main
SQLite remains at **5,684,283 candles**, and its daily/operational snapshot is
unchanged during all isolated rehearsals. No main provider/archive publication
or production routing change follows these checks.

Evidence: `data/vn-outage-native-rehearsal-20261003/report.json`,
`data/vn-outage-live-rehearsal-20261003/report.json` and their raw native pages /
isolated databases, `data/vn-outage-revision-safe-live-rehearsal-20261003/report.json`
(all six actual checks repeat successfully after the wider revision guard),
`data/vn-outage-revision-safe-wheel-smoke-20261003.json`,
`data/acceptance-state-20261003.json`, `data/vn-outage-final-audit-20261003.json`,
and `data/vn-outage-{evidence-s3,final-evidence-s3}-20261003.json`.
Native evidence/reports are preserved in immutable RustFS objects with exact
readback; the active archive pointer and main SQLite snapshot stay unchanged.

## Completion audit against the agreed objective

Completion remains unproven. Implementation and scoped runtime checks do not
replace the requirement to keep recent data reliable and served history usable.
The current evidence supports the following states:

| Requirement | Current authoritative evidence | Acceptance state |
| --- | --- | --- |
| Project, phased plan, Python/FastAPI, operational CLI | `pyproject.toml`, FastAPI app, CLI help, `TODO.md`, offline build and packaged checks | Implemented locally |
| RustFS-only Compose; SQLite/DuckDB embedded | `docker-compose.yml`, effective settings, verified RustFS archive restoration | Implemented locally |
| Exactly VPS/VNDirect/DNSE, excluding VCI provider | Actual provider registry/settings and six live intraday worker checks | Implemented; broader provider policies unresolved |
| Three-year daily and one-year minute SQLite windows | Read-only SQL across all ten groups: zero rows before calendar cutoffs | Bounds pass; interior completeness unproven |
| S3 history and boundary/indicator reads | 446 restored objects / 346,572 indexed rows, 11 historical-year checks, boundary regression tests | Partial: four objects pending and five typed gaps |
| Existing web routes, parameters, JSON/CSV and indicators | `CONTRACT.md`, 278-test suite, captured full 2,103-query matrix | Partial: 44 historical requests fail; diagnostic storage header differs |
| Existing web/SDK/analysis CLI usage | Recorded selected browser, unchanged SDK and CLI checks, including new hourly selections | Scoped checks pass; complete production flows unproven |
| More selected tickers with reliable recent daily/minute data | 59 VN selections, 57 matching observed daily date sets, live handoffs and catch-up proof | Partial: four recent EIB/HHS sessions and known basis disagreements |
| Corporate-action detection and coherent publication | Staged-repair, coverage-loss, revision, fairness, archive and native-candidate checks | Mechanism passes; provider policies and four pending archives unresolved |
| Preserve all existing served older history and historical identities | Archive discovery/restoration and preserved originals; partial earlier-year migrations | Incomplete: full private/legacy inventory and wider old history absent |
| Current sync records and authentication continuity | Isolated contract tests; private legacy PostgreSQL connection previously unavailable | Records/export unverified |
| Recovery, backups and rollback | Existing populated restores and current actual packaged index recovery; documented routing rollback | Local recovery passes; no production migration/cutover |
| Chosen-universe load/transfer evidence | Full query capture, local cold/warm profile measurements | Local measurements only; cloud transfer and sustained production load open |

Remaining acceptance depends on complete valid provider history/adjustment
evidence, access to the full legacy inventory, and production migration/runtime
evidence. The catch-up change and its tests do not clear these unrelated gates.

## Previous minute-derived hourly compatibility and full query matrix

OCB/PNJ/DGC/NAB previously returned HTTP 200 with no candles for `1h` and `4h`:
these selections ingest daily/minute data only. The reader now derives those
intervals from minutes when there is no existing hourly series or active hourly
archive. Existing hourly data stays preferred, and pending hourly repairs still
reject affected requests. UTC-hour and Vietnamese 02:00 UTC four-hour boundaries,
directional limits, complete buckets, and revision checks remain enforced.
Available minute coverage limits these derived intervals; older hourly history
is not fabricated. No candle, provider, archive, or worker metadata is changed.

The shadow API on localhost:3002 serves all ten supported intervals for the
configured 71 series, except SJC's daily-only cases. Four bounded readers execute
**2,103 uncached queries**, capturing every response. **2,059 pass** the ordered,
nonempty OHLCV schema checks; **44 return HTTP 503**, with no transport/schema
failures. The failing symbols are VNINDEX, EIB, HHS, VND, SHS, GEX, and HAG.
Their daily/weekly/biweekly/monthly requests touch known recent missing dates,
unavailable years, or pending archive repairs. Short-history undefined MA values
are recorded rather than invented. This audit proves sampled query behavior,
not freshness, full history coverage, or numerical identity with the legacy API.

All **843 responses** from the preceding four-interval audit remain byte-identical,
including error responses. All **396 existing hourly responses** also remain
byte-identical to localhost:3001. The **24 previously empty queries** for the four
minute-only tickers now have 20 bars each, with OHLCV matching an independently
grouped native-minute reference. **24 unchanged-SDK checks** reproduce exact
dates/OHLCV/SMA/EMA and report API provenance. The public website's daily/one-hour
charts and volume profiles pass for all four tickers with isolated browser/API
routing. A separately preserved initial browser attempt fails because the public
site exposes no four-hour button; four-hour functionality is tested through
the API/SDK rather than claimed as a visible web control.

All **264 API tests pass in 21.42 seconds**, including five new storage cases and
one HTTP aliases/legacy-CSV regression. Ruff lint and **55-file** formatting pass;
source and wheel distributions rebuild offline. The actual unpacked wheel serves
hourly fixtures from SQLite and compressed Parquet, restores **446 archive
objects / 346,572 indexed rows**, serves the same **11 historical years**, replays
500 frozen DNSE index rows, and retains the known historical HTTP 503 guards.

A fresh object-cache benchmark queries October 2, 2025 archived minute profiles
for all **59 VN tickers** with four readers: all succeed, covering **11,866 minutes**
with identical cold/warm results. Cold/warm total wall times are
**815.65 / 381.49 ms**, median requests **47.9 / 22.92 ms**, and maximum requests
**157.4 / 39.58 ms**. Cached Parquet occupies **338,101 bytes**; peak process RSS
is **112,115,712 bytes**, and SQLite occupies **857,845,760 bytes**. These are
local RustFS measurements for one observed day, not cloud transfer costs or
production capacity. A separate 2020-minute experiment returns no data for all
59 tickers and transfers zero object bytes. Its equal cold/warm values are equal
errors/empty results, not a successful historical-data benchmark.

After the shadow checks, the usual localhost:3001 API is refreshed with the
tested code and the temporary localhost:3002 process is stopped. All 24 corrected
hourly responses and seven representative historical error responses remain
byte-identical to the recorded shadow results. The full audit and subsequent
comparisons leave the main SQLite daily/operational snapshot unchanged. The
active S3 index pointer is also unchanged, SHA-256
`64dacd233188c10a9c996fad8983e1c7b785690c389dcf551d5045c75039561d`.
Production routing remains unchanged. Four pending archives,
five typed unavailable ranges, and previously recorded provider/interval-basis
disagreements still prevent complete replacement acceptance.

Evidence: `data/web-query-matrix-20261003.json`,
`data/web-query-all-intervals-20261003.json` and their raw response directories,
`data/minute-hourly-acceptance-20261003.json`, `data/sdk-minute-hourly-20261003.json`,
`data/web-minute-derived-1h-{ocb,pnj,dgc,nab}-20261003.json` with screenshots,
`data/web-minute-hourly-ocb-20261003.json` (the absent four-hour UI control),
`data/minute-hourly-wheel-smoke-20261003.json`,
`data/minute-hourly-final-audit-20261003.json`, and
`data/archive-profiles-all-59-{2020,20251002}-20261003.json`.

## Previous verified DNSE index parsing and remaining candidate checks

The DNSE VNINDEX response contains daily timestamps at **02:15 UTC / 09:15 ICT**,
alongside UTC-midnight and 02:00 UTC timestamps. A preserved native response has
**1,019 rows**: **663** at offset **8,100 seconds**, **355** at **7,200 seconds**,
and one at UTC midnight. Its observed transition to 02:00 UTC is **May 5, 2025**.
The 02:15 UTC bars encode the same market dates; accepting this observed convention
does not shift them to another date. [DNSE's trading-hour guide](https://hdsd.dnse.com.vn/man-hinh-giao-dich/cac-quy-dinh-ve-giao-dich-chung-khoan/1.-nguyen-tac-giao-dich-chung/1.1.1.-thoi-gian-giao-dich-tren-thi-truong)
provides market-session context, while the actual timestamp convention is verified
from captured endpoint data, not inferred from that guide.

The parser now accepts 02:15 UTC **only for DNSE VNINDEX daily bars**, preserving
dates, index price units, volume, and backward cursor. Other providers/symbols
retain their previous timestamp checks. Invalid OHLC and conflicting duplicates
remain errors. Six new timestamp regression cases verify the native transition,
provider/symbol scope, and invalid/conflicting bars. A seventh regression case
extends completed-observed-coverage protection to daily worker replacements.

A fresh real DNSE worker repair parses its first **500 rows**, then refuses
publication because its **746 staged rows** omit the previously observed
**August 12, 2024** date. The isolated candidate retains all **747 original dates
and OHLCV/provider/revision values**, while its job remains pending with
`Replacement drops completed observed coverage at 1723420800`. Seeding the
isolated database assigns clone update timestamps; that is separate from the
worker's refusal to replace the original values. The main VNINDEX series remains
ready on VNDirect revision `be58cc6c-ed45-42d8-867d-057344f8b35c`. Main daily data
and operational metadata remain unchanged throughout all isolated rehearsals.
This fixes parsing support; it does not certify complete DNSE index coverage or
resolve VNINDEX's pending 2020 archive.

Further isolated native candidates also remain unpublishable:

- **GEX/DNSE** completes its **747-date** recent window, with **641 changed price
  rows / 645 volumes** and maximum relative OHLC difference
  `0.055693140586304146`. Its 2023 partition reconciles with all 185 original
  dates, but December 27, 2022 has conflicting UTC-midnight / 02:00 UTC bars.
  Raw OHLC **7.94 / 8.12 / 7.74 / 7.9**, volume **7,834,800**, conflicts with
  **7.9 / 8.34 / 7.62 / 8.32**, volume **29,264,000**. Multiple request windows
  reproduce the conflict. Replacing would lose readable 2022 history.
- **SHS/VNDirect** fails recent OHLC validation. May 5, 2025 has open **12.4 >
  high 12.3**; May 12 has open **12.3 < low 12.4**. Neither bar is clamped or
  excluded from the requested recent window.
- **VNINDEX/VPS** completes **747 recent dates**, with **185 changed price rows /
  747 volumes** and maximum relative OHLC difference `0.01842041968051711`.
  Its 2023/2022 partitions reconcile, but August 23, 2021 has open **1,329.43 >
  high 1,326.07**. Two captured archive windows reproduce it. Switching would
  lose a currently readable 2021 year before reaching the pending 2020 year.
- **HAG/DNSE** completes **747 recent dates**, with **two changed price rows /
  13 volumes** and maximum relative OHLC difference `0.021897810218978075`.
  The 185-row 2023 archive reconciles; the readable 2022 archive fails on
  conflicting December 27 native candles. Its existing VNDirect series stays intact.

No main provider switch or archive publication follows these results. Existing
readable data and original archive bytes remain preserved. The main index stays
at **446 objects / 346,572 rows**, including **442 published / four pending**,
**58 handoffs**, **34 dated recoveries**, and **five unavailable ranges**. Main
SQLite stays at **5,684,283 candles / 43,632 VN daily rows**, **936 archive
metadata rows**, **736 quality rows**, and **1,277 import receipts**.

All **258 API tests pass** in **20.62 seconds**. Ruff lint and the **54-file**
format check pass; source and wheel distributions rebuild offline. The actual
unpacked new wheel replays **500 frozen native index rows** through its parser
with exact dates/OHLCV and at least one 02:15 timestamp. Its actual CLI restores
the index with **zero hot candles / jobs**, serves **11 historical years**, and
retains older receipts and VND/EIB HTTP 503 gap guards.

**53 evidence files / 46 distinct checksums** are uploaded to immutable RustFS
objects and read back exactly. The active `LATEST.json` pointer remains byte
identical, SHA-256
`64dacd233188c10a9c996fad8983e1c7b785690c389dcf551d5045c75039561d`.
Main SQLite's daily/operational snapshot also stays identical. The local API
process and production routing are unchanged; new workers and rebuilt packages
use the corrected parser. Remaining range/provider/private-inventory limitations
still prevent declaring a complete production replacement.

Evidence: `data/remaining-pending-archive-candidate-preflight-20261003.json`,
`data/remaining-pending-archive-native-failure-diagnostics-20261003.json`,
`data/final-daily-alternatives-candidate-preflight-20261003.json`,
`data/final-daily-alternatives-failure-diagnostics-20261003.json`,
`data/vnindex-dnse-verified-time-candidate-preflight-20261003.json`,
`data/verified-index-time-native-preservation-audit-20261003.json`,
`data/verified-index-time-wheel-smoke-20261003.json`,
`data/verified-index-time-candidate-evidence-s3-20261003.json`,
`data/verified-index-time-final-audit-20261003.json`, and the
corresponding separate candidate databases/native-capture directories.

## Previous verified ACB and LPB pending-year reconciliation

ACB and LPB now use coherent VNDirect daily revisions
`5283b13e-dcec-47c9-a58d-ef0166076cf2` and
`5a28b40c-f161-4ce4-aec1-6f0ab1c7c5f4`. Each retains **747 recent candles**;
their five older partitions contain **1,181 / 1,176 rows**. All ten original
archive date sets remain exact, including previously published dates. The pending
2020 partitions become readable with **247 ACB / 242 LPB dates**. Independent
wide native responses reproduce all **1,928 / 1,923 recent/cold OHLCV rows
exactly**, with no absent candidate dates or mismatched values.

Recent ACB comparisons have **621 changed price rows / 675 volumes**, maximum
relative OHLC difference `0.00007838219156597326` and volume difference
`0.004136490583936281`. LPB has **568 / 652**, with maxima
`0.00010207206287637938` and `0.008647132580394623`. Neither recent window has
a volume difference above **1%**. Two older ACB volume changes exceed that
threshold: September 12, 2022 changes **1,416,163 → 1,390,800**, and September
13 changes **1,486,664 → 1,469,500**. Independent DNSE requests reproduce both
candidate volumes exactly. LPB has no older volume change above 1%.

Older prices are materially different from the former series. ACB's maximum
relative OHLC differences are `0.20025784271594327` in 2019 and
`0.20221223643276875` in 2021. LPB's 2019 maximum is
`0.010073614877954329`. Targeted DNSE reads of these exact maximum-difference
dates give maximum relative differences versus the candidate of
`0.0007238508867173366`, `0.0004185851820845965`, and
`0.004363347877826218` respectively. Their OHLC values are **not exactly equal**;
all responses and comparisons are preserved. No factor is inferred, and this
targeted evidence does not establish lifetime adjustment equivalence or exact
legacy-price compatibility. Full numeric comparisons remain in the receipts.

Minute data and handoffs stay unchanged. Each audit covers **248 completed
observed dates**, with **55,664 ACB / 47,476 LPB minute candles**. Both old and
candidate bases have **zero differences above 1%**. ACB's maximum relative
minute/daily difference changes from `0.000048236358154873926` to
`0.000024434941966910984`; LPB's changes from `0.00001966955153420713` to
`0.000013394770681518509`. These audits do not prove an independent calendar.

Publication holds scoped daily/archive-writer leases and atomically replaces
both daily revisions and matching metadata. Original hot before-images, old
archives, ten replacement partitions, and immutable rebaseline receipts are
verified in RustFS. **28 raw captures/reports** are checksummed and read back;
four later targeted price-evidence files are separately preserved in immutable
S3 objects. Fresh **40-completed-candle** checks pass before and after publication.
Only the two old archive jobs are retired. Normal `next_1d` schedules may change;
other ticker fields and unrelated operational rows remain exact. The other
**5,682,789 candles** match the rollback backup exactly, including provenance and
update timestamps; all **57 other recent HTTP responses** remain identical.

Ten actual yearly HTTP reads serve both symbols' 2019–2023 history with exact
candidate OHLCV. **Twelve recent SDK cases** and **twelve historical SMA/EMA
cases** match equivalent backward API requests exactly for dates, OHLCV,
MA10–MA200, and API provenance. Every checked historical MA200 is defined.
Forward requests reproduce OHLCV; their different EMA warmup scopes can differ
numerically. The unchanged public daily/15-minute/weekly charts and profiles
pass for both tickers, without JavaScript/network failures or writes. Known
unrelated bulk EMA and VNINDEX weekly failures remain open.

The frozen 59-ticker legacy replay finishes without errors or local mutations:
**57 observed date sets match**, **four EIB/HHS dates remain missing**, and there
are **zero extra dates**. Of **43,629 valid comparable rows**, **9,772 match OHLCV
exactly**, **30,872 price rows exceed the representation threshold**, **5,343
price differences exceed 1%**, and **778 volume differences exceed 1%**. Three
invalid legacy OHLC rows remain identified. Date coverage does not imply
numerical identity with the old API.

At the ACB/LPB checkpoint, SQLite contained **5,684,283 candles / 43,632 VN daily rows**, **207 series**,
**137 source checks**, **736 quality rows**, **1,277 import receipts**, and **936
total archive metadata rows**. The active index remains **446 objects / 346,572
rows**: **442 published / 345,571 rows**, **four pending / 1,001 rows**, **58
handoffs**, **34 dated-year recoveries**, and **five unavailable ranges**. Pending
objects are GEX 2019, HAG 2019, SHS 2022, and VNINDEX 2020. Cold reconstruction
matches all archive/adoption/recovery/gap metadata exactly with zero hot candles
or ingestion jobs. The actual unpacked wheel's CLI reconstructs this index and
serves **11 checked historical years**, preserving older recovery evidence and
the VND/EIB missing-range HTTP 503 guards.

The populated backup and restored file are **857,845,760 bytes** each, SHA-256
`e2011bb90bf3a595810ba0d1741dd4d4f3e1f446d6cddc04251579eb8235bf65`.
Schema **2**, quick-check, full daily/operational metadata, archive evidence, and
all quality findings restore exactly, including the unchanged CTR/VTP findings.
Runtime code remains unchanged from the 251-test checkpoint. Ruff lint and the
54-file format check pass; no runtime code or test changes require a repeated suite.

GEX's alternate recent candidate fails on **May 13, 2025**, with raw VNDirect
OHLC **19.562 / 19.562 / 18.948 / 18.172**: its close is below its low. The raw
response is preserved; its current ready VPS series and archives remain intact.
Read-only probes of VNDirect's `data-api.vndirect.com.vn/v4/stock_prices` alias
for VND November 29, 2019, EIB September 13, 2022, and HHS December 19, 2019 all
return **HTTP 401**, each preserving the exact 13-byte response and SHA-256.
No substitute candles are obtained. [DNSE's current OHLC documentation](https://developers.dnse.com.vn/docs/dnse/get-ohlc-history/)
still identifies `/price/ohlc`; earlier normal unauthenticated 401 captures remain
applicable, so the same protected requests are not repeated. Production routing
is unchanged. VND 2020, recent EIB/HHS sessions, four pending archives, provider
discrepancies, private inventory, and full replacement acceptance remain open.

Evidence: `data/{acb,lpb}-pending-archive-preflight-20261003/`,
`data/acb-lpb-pending-archive-rebaseline-publication-20261003.json`,
`data/acb-lpb-pending-archive-rebaseline-other-candles-preservation-20261003.json`,
`data/acb-lpb-pending-archive-rebaseline-index-restore-20261003.json`,
`data/acb-lpb-pending-archive-rebaseline-backup-restore-20261003.json`,
`data/acb-lpb-pending-archive-rebaseline-wheel-smoke-20261003.json`,
`data/acb-lpb-pending-archive-final-audit-20261003.json`,
`data/sdk-acb-lpb-pending-archive-rebaseline-20261003.json`,
`data/sdk-{acb,lpb}-pending-archive-rebaseline-recent-20261003.json`,
`data/web-{acb,lpb}-pending-archive-rebaseline-20261003.json`,
`data/retained-vn-daily-after-acb-lpb-20261003.json`,
`data/acb-lpb-historical-price-corroboration-20261003/`,
`data/acb-lpb-historical-price-corroboration-s3-20261003.json`,
`data/gex-pending-archive-preflight-20261003/failure-diagnostics.json`, and
`data/vndirect-rest-alias-preflight-20261003.json`.

## Previous verified NAB 2022 recovery and VND candidate checks

NAB now uses one VNDirect daily revision,
`bb48bb11-622d-411a-b8f2-6c2dc67ccd5c`, across **741 retained candles** and
**four older partitions / 744 rows**. Independent original CSV/API captures agree
on all **249 missing 2022 dates**, which now serve valid native candles. Every
original recent date and previously published archive date remains present.
A separate wide provider response reproduces all **1,485 recent/cold OHLCV
candles exactly**, without absent candidate dates. Only NAB's verified 2022
unavailable-range marker is cleared.

Against the old VPS retained window, **607 price rows / 540 volumes** differ,
with maximum relative differences `0.000145369966565001` and
`0.007835091414682971`. No measured recent or older volume difference exceeds
**1%**. The older maximum relative OHLC differences are
`0.001953670108847394` (2023), `0.006641870350690748` (2021), and
`0.006558173427252845` (2020). Original values and provider replies remain
preserved without inferred factors, overrides, or claimed dividend equivalence.
Both old and candidate minute/daily bases have **zero price differences above
1%** across **248 completed observed dates / 37,759 minute candles**. Maximum
relative OHLC differences are `0.00009224360245996266` and
`0.000040000000000040004` respectively. Minute data and its handoff remain exact;
these threshold/observed-date checks do not establish an independent calendar.

Publication holds the daily/archive-writer leases, verifies RustFS readback of
original hot before-image, preserved old archives, four native replacement
partitions, and immutable recovery/rebaseline receipts, then atomically updates
the daily revision and matching metadata. **13 raw captures/reports** are
uploaded with SHA-256 and byte-count/readback verification. Fresh
**40-completed-candle** provider checks pass before and after publication.
NAB's normal `next_1d` schedule may change; other ticker fields and unrelated
operational rows remain exact. The other **5,683,542 candles** match the rollback
backup exactly in OHLCV, provider, revision, and update timestamp, with **zero
differences**. All **58 other recent HTTP responses** remain identical.
Original archives and earlier recovery receipts remain recoverable.

Actual raw historical reads return NAB 2020 **60**, 2021 **250**, 2022 **249**,
and 2023 **249** rows. Its **six recent SDK cases** (daily/minute/15-minute,
SMA/EMA) and **six historical cases** (2022, early 2023, and the retention
boundary) match equivalent backward API requests exactly for dates, OHLCV,
and MA10–MA200 with API provenance. All checked historical MA200 values are now
defined, including the **36-row** early-2023 cases. Forward queries reproduce
OHLCV; their different EMA warmup seed produces a maximum relative EMA200
difference `0.0000487576498579001` at the 2023 boundary. No SDK/runtime code changed.
The unchanged public NAB daily, 15-minute, weekly charts and volume-profile
checks pass through isolated local routing, without JavaScript/network failures
or writes. Other known bulk EMA/VNINDEX weekly errors remain open.

The frozen **59-ticker** legacy replay finishes with zero errors and unchanged
local data/metadata: **57 observed date sets match**, **four EIB/HHS sessions
remain missing**, and there are **zero extra dates**. Of **43,629 valid comparable
rows**, **9,816 match OHLCV exactly**, **30,844 price rows exceed the representation
threshold**, **5,343 price differences exceed 1%**, and **778 volume differences
exceed 1%**. Three invalid original OHLC rows remain identified. These numerical
discrepancies remain distinct from observed date coverage and replacement acceptance.

VND's VNDirect and DNSE isolated candidates both complete **747-row** retained
windows and independently recover all **252 original 2020 dates**. Neither
preserves every currently readable older year. VNDirect reconciles three of
four old objects, but its **November 29, 2019** bar has **open 2,628 > high 2,618**
(low **2,571**, close **2,618**, volume **255,980**), reproduced in two captured
request windows. This is inside the requested 250-row 2019 archive, so its
replacement is rejected. DNSE reconciles 2023, then repeats two different
**December 27, 2022** candles at the same date: OHLC **10,400 / 10,640 / 10,400 /
10,530**, volume **8,521,500**, versus **10,530 / 11,170 / 10,530 / 11,170**,
volume **22,800,600**. Multiple captures reproduce the conflict. DNSE also
contains invalid 2019 rows outside its requested 2020 recovery; they do not
license dropping or changing dates when that older year is requested.

VND's VNDirect retained comparison has **584 changed price rows / 664 volumes**,
maximum relative OHLC difference `0.0000935191246609346`; DNSE has **659 / 661**,
maximum `0.021007786102541504`. Both candidates remain isolated. Main VND's
**747 recent candles**, VPS revision `ec77a776-40b8-460e-a211-3bc3eb55fce0`,
and four readable archives remain exact, including provenance and update times.
No clamp, arbitrary duplicate choice, inferred factor, or cross-provider archive
mix is applied. The 2020 gap remains explicit; staged native availability alone
does not establish a complete publishable replacement.

At the NAB checkpoint, SQLite retained **5,684,283 candles / 43,632 VN daily rows**, with **207
series**, **137 source checks**, **736 quality rows**, **1,275 import receipts**,
and **926 total archive metadata rows**. The active index has **446 objects /
346,572 indexed rows**: **440 published / six pending**, plus **58 handoffs**,
**34 dated-year recovery receipts**, and **five unavailable ranges** (VND 2020
and four EIB/HHS sessions). Fresh reconstruction matches exactly. The actual
unpacked wheel's CLI restores these records with **zero hot candles / jobs**;
its packaged HTTP serves **nine checked historical years**, retains old recovery
evidence, and preserves VND/EIB HTTP 503 guards. Cold reconstruction restores
archive/recovery/gap metadata; populated SQLite backups preserve all generic
quality findings, including the existing CTR/VTP disagreements.

The populated backup and restored file are **857,563,136 bytes** each, SHA-256
`3e04fd0304055eabbdaecae8ce5fbd9caa2c3642f4cfbe2754200672a9232987`.
Schema **2**, quick-check, complete daily/operational metadata, archive evidence,
and all quality findings restore exactly. Runtime remains unchanged from the
**251-test** checkpoint. Ruff lint passes, **54 files** pass format checks, and
source/wheel distributions rebuild offline from the existing cache. Production
routing is unchanged. VND 2020, recent
EIB/HHS sessions, six pending objects, provider/minute consistency, private
production inventory, and full replacement acceptance remain open.

Evidence: `data/nab-historical-gap-preflight-20261003/` (including original
CSV/API captures, numeric comparison, native verification, and minute audit),
`data/nab-historical-rebaseline-publication-20261003.json`,
`data/nab-historical-rebaseline-other-candles-preservation-20261003.json`,
`data/nab-historical-rebaseline-index-restore-20261003.json`,
`data/nab-historical-rebaseline-backup-restore-20261003.json`,
`data/retained-vn-daily-after-nab-20261003.json`,
`data/sdk-nab-historical-rebaseline-recent-20261003.json`,
`data/sdk-nab-historical-rebaseline-20261003.json`,
`data/web-nab-historical-rebaseline-20261003.json`,
`data/nab-historical-rebaseline-wheel-smoke-20261003.json`,
`data/vnd-historical-gap-preflight-20261003/`,
`data/vnd-dnse-historical-gap-preflight-20261003/`, and
`data/vnd-historical-candidate-failure-diagnostics-20261003.json`.
Rollback backup: `backups/local-rehearsal-before-nab-historical-rebaseline-20261003.sqlite3`.
Verified populated backup: `backups/local-rehearsal-nab-historical-rebaseline-20261003.sqlite3`.

## Earlier verified VIB/VTP daily rebaselines and historical recoveries

VIB and VTP now each use a coherent VNDirect daily revision. VIB's revision is
`d14dcd2e-d562-40aa-a0c7-a043e3533d2b`; VTP's is
`3d2790fb-0161-4b2f-8d5c-aec29a9c3398`. Their retained windows contain **747 /
740 candles**, with **five / six older partitions** containing **1,179 / 1,212
rows** respectively. All original retained dates and all previously published
older dates remain present. Independently captured original CSV/API timestamps
recover **VIB 2019: 250**, **VTP 2019: 250**, and **VTP 2022: 249** native candles;
only those three unavailable-range markers are cleared. VIB's previously pending
2020 archive also becomes readable with its exact **245 original dates**.
Separate wide native responses reproduce all **1,926 VIB / 1,952 VTP OHLCV
candles exactly**, without absent candidate dates.

Relative to original hot VPS data, VIB has **686 changed price rows / 730 changed
volumes**, with maximum relative differences `0.0024007682458386803` and
`0.0030992396603592987`. No VIB retained volume difference exceeds **1%**.
VTP has **641 changed price rows / 584 changed volumes**, with maximum relative
differences `0.00015156107911495909` and `0.023327236185443634`; **12 retained
volume differences exceed 1%**. Historical price differences also remain measured:
maximum relative OHLC differences reach `0.28925237250212177` for VIB 2021
and `0.21613462650138093` for VTP 2018. VIB's original pending legacy 2020
partition differs by up to `0.09033471830440076`. These are preserved provider
differences, without claimed dividend equivalence or inferred scaling.

DNSE independently corroborates VIB's one larger historical volume change:
**July 7, 2021**, original **1,431,700**, native **1,467,100**, computed relative
difference `0.02472585038765107`. VTP has **38 volume changes above 1%** across
recent and older original data; DNSE corroborates **35**. Three remain unresolved:

| VTP date | Original VPS volume | Published VNDirect volume | DNSE volume |
| --- | ---: | ---: | ---: |
| 2020-11-20 | 99,400 | 100,562 | 92,562 |
| 2023-09-22 | 1,804,963 | 1,768,263 | 1,804,200 |
| 2026-07-15 | 216,200 | 218,385 | 216,200 |

Independent targeted VNDirect queries reproduce all three published native
volumes exactly, matching the wide response. Each disagreement remains an
unresolved `provider_volume_disagreement` SQLite finding and part of immutable
S3 rebaseline evidence. Original values and raw provider replies are preserved;
no override, dividend factor, or alternate provider's OHLC is mixed into the
published native series. CTR's previously recorded disagreement remains unchanged.

Both minute/daily audits compare **248 completed observed dates**. VIB retains
**53,232 minute candles**, with old/candidate maximum relative OHLC differences
`0.0000708399595199527` / `0.000035853468433266755`; VTP retains **45,244**, with
`0.000018026966870809957` / `0.00010143774025839214`. Both old and new bases have
**zero price differences above the explicit 1% threshold**. Minute rows and
handoffs remain exact. Threshold/observed-date agreement does not prove a complete
independent exchange calendar or exact provider adjustment equivalence.

Publication holds both affected daily leases and the archive-writer lease,
verifies RustFS readback of original hot before-images, retained old archives,
11 replacement partitions, and immutable receipts, then atomically updates both
daily revisions and matching metadata. **73 raw captures/reports** are uploaded
with SHA-256 and byte-count/readback checks, and indexed inside the immutable
receipts. Fresh **40-completed-candle** provider checks succeed before and after
publication. Only the affected normal `next_1d` schedules may change; unrelated
operational rows and other ticker fields stay exact. One obsolete VIB archive
repair job is retired after its verified replacement is published.

SQLite retains **5,684,283 candles / 43,632 VN daily rows**. The other
**5,682,796 candles** match the rollback backup exactly in OHLCV, provider,
revision, and update timestamp, with **zero differences**. The other **57 recent
HTTP responses** remain identical. Raw historical reads pass for **11 dated
symbol/year cases**, including all repaired years, previously published history,
and both hot/cold 2023 boundaries. Original archive bytes and old recovery
receipts remain recoverable even when their active objects are superseded.

The unchanged SDK passes **12 recent cases** (daily/minute/15-minute SMA/EMA)
and **12 historical cases** (2019, 2022, and 2023 retention-boundary SMA/EMA for
both symbols). Dates, OHLCV, and MA10–MA200 match equivalent backward API queries
exactly, with API provenance. Early 2019 correctly retains **199 VIB / 173 VTP
missing MA200 values** in each checked SMA/EMA case; missing lookback is not
invented. Forward query OHLCV matches; different EMA warmup seeds produce maximum
relative EMA200 differences `0.00040096643618370287` (VIB) and
`0.000438175946178454` (VTP) at the 2023 boundary. No SDK or runtime code changed.

The unchanged public daily, 15-minute, weekly charts and volume profiles pass
for both selected symbols with isolated local routing, no JavaScript/network
failures, and no writes. Existing unrelated bulk EMA and VNINDEX weekly reads
retain known HTTP 503 errors; selected successes do not imply every web request
passes. The frozen **59-ticker** legacy replay finishes with zero errors and
unchanged local data/metadata: **57 observed date sets match**, **four EIB/HHS
sessions remain missing**, and there are **zero extra dates**. Across **43,629
valid comparable rows**, **9,820 match OHLCV exactly**, **30,843 price rows exceed
the representation threshold**, **5,343 price differences exceed 1%**, and
**778 volume differences exceed 1%**. Three invalid original OHLC rows remain
identified; remaining discrepancies are still required work.

Current SQLite metadata contains **207 series**, **137 source checks**, **736
quality rows**, **1,273 import receipts**, and **922 total archive metadata rows**.
The active archive index has **445 objects / 346,323 indexed rows**, comprising
**439 published / six pending**, plus **58 handoffs**, **33 dated-year recovery
receipts**, and **six unavailable ranges** (VND 2020, NAB 2022, and four EIB/HHS
sessions). Fresh reconstruction matches exactly. The actual unpacked wheel's CLI
restores all these records with **zero hot candles / ingestion jobs**; packaged
HTTP serves **eight checked historical years**, validates old recovery receipts,
and retains VND/NAB/EIB missing-range HTTP 503 guards. Cold reconstruction covers
archive/recovery/gap metadata; generic volume findings are preserved by the
populated SQLite backup and immutable S3 receipts/raw evidence.

The populated backup and restored file are **857,477,120 bytes** each, SHA-256
`b19617157e67c553d7ae363bad60e05ed0df726f2fec39dc599c90b57ccab027`.
Schema **2**, quick-check, complete daily/operational metadata, archive evidence,
and **all quality findings** restore exactly. Runtime remains unchanged from the
**251-test** checkpoint. Ruff lint passes, **54 files** pass format checks, and
source/wheel distributions rebuild offline from the existing cache. Production
routing is unchanged. Remaining old years,
recent gaps, six pending objects, provider consistency, private production
inventory, and full replacement acceptance remain open.

Evidence: `data/vib-historical-gap-preflight-20261003/` and
`data/vtp-historical-gap-preflight-20261003/` (including original CSV/API captures,
numeric comparisons, native/volume verification, and minute-basis reports),
`data/vib-vtp-historical-rebaseline-publication-20261003.json`,
`data/vib-vtp-historical-rebaseline-other-candles-preservation-20261003.json`,
`data/vib-vtp-historical-rebaseline-index-restore-20261003.json`,
`data/vib-vtp-historical-rebaseline-backup-restore-20261003.json`,
`data/retained-vn-daily-after-vib-vtp-20261003.json`,
`data/sdk-{vib,vtp}-historical-rebaseline-recent-20261003.json`,
`data/sdk-vib-vtp-historical-rebaseline-20261003.json`,
`data/web-{vib,vtp}-historical-rebaseline-20261003.json`, and
`data/vib-vtp-historical-rebaseline-wheel-smoke-20261003.json`.
Rollback backup: `backups/local-rehearsal-before-vib-vtp-historical-rebaseline-20261003.sqlite3`.
Verified populated backup: `backups/local-rehearsal-vib-vtp-historical-rebaseline-20261003.sqlite3`.

## Earlier verified HCM daily rebaseline and 2020 recovery

HCM now uses one VNDirect daily revision,
`9fbc98cc-ce4c-457c-acbe-23c0b077bfd1`, across **747 retained candles** and
**five older partitions / 1,186 rows**. Every original retained date and every
previously published archive date remains present. Independent original CSV/API
captures agree on all **252 timestamps in the missing 2020 year**; valid native
candles now serve those dates. A separate wide native response reproduces all
**1,933 recent/cold OHLCV candles exactly**, with no absent candidate dates.
Only HCM's verified 2020 unavailable-range record is cleared.

Compared with its previous VPS retained window, **645 price rows** differ, with
maximum relative OHLC difference `0.0008160410352062719`. **693 volumes** differ;
none exceeds **1%**, and their maximum relative difference is
`0.0041068111332143165`. Older partitions retain their original date sets while
using the same new revision. Their maximum relative OHLC differences are
`0.0001116196004018466` (2023), `0.00016647244880974021` (2022),
`0.012341992634617327` (2021), and `0.01067932364283597` (2019).
These measured provider differences are retained without claimed adjustment
equivalence, inferred dividend factors, or mixed-provider OHLC.

The one historical volume difference above **1%** occurs on **March 14, 2019**:
original VPS **606,620**, native VNDirect **632,930**, computed relative
difference `0.04337146813491155`. DNSE independently returns **632,930** on that
date. All 2021 volumes match; the measured 2022/2023 differences stay below 1%.
Original values and checksummed provider captures remain preserved. The existing
CTR July 15, 2026 unresolved volume finding is unchanged by this publication.

The minute/daily audit compares **248 completed observed dates / 54,415 minute
candles**. Neither original nor candidate basis has a price difference above the
explicit **1%** threshold. Maximum relative OHLC differences are
`0.000053533190578214374` and `0.000026504108136871096` respectively. HCM minute
rows and its provider handoff remain exact. These are observed-session and
threshold checks; they do not establish an independent exchange calendar.

Publication holds the daily and archive-writer leases, verifies RustFS readback
of the original hot before-image, preserved old archives, new native partitions,
and immutable evidence receipts, then atomically updates the daily revision and
matching archive metadata. Fresh **40-completed-candle** provider comparisons
pass immediately before and after publication. HCM's normal `next_1d` schedule
can change; other ticker fields and unrelated operational records remain exact.
SQLite retains **5,684,283 candles / 43,632 VN daily rows**. The other
**5,683,536 candles** match the rollback backup exactly in OHLCV, provider,
revision, and update timestamp, with **zero differences**. The other **58 recent
HTTP responses** remain identical. Old archive bytes and recovery receipts survive.

Raw historical HTTP requests return 2019 **250**, 2020 **252**, 2021 **250**,
2022 **249**, and 2023 **249** HCM rows on the coherent native basis. Six recent
SDK daily/minute/15-minute SMA/EMA cases and six historical cases (2020, 2022,
and the 2023 retention boundary) match equivalent backward API queries exactly
for dates, OHLCV, and MA10–MA200, with API provenance. Forward queries still
reproduce the same OHLCV; their different EMA warmup seed produces maximum
relative EMA200 differences `0.0001964288711489015` for 2022 and
`0.0008853716035817083` at the 2023 boundary. No SDK/runtime behavior changed.

The unchanged public HCM daily, 15-minute, weekly charts and volume-profile
checks pass with isolated local API routing, no JavaScript/network failures,
and no writes. Other recorded bulk EMA and VNINDEX weekly requests still return
their known HTTP 503 errors. Successful selected controls do not imply every
public request passes. The frozen **59-ticker** legacy replay finishes with
zero errors and unchanged local data/metadata: **57 observed date sets match**,
**four EIB/HHS sessions remain missing**, and there are **zero extra dates**.
Of **43,629 valid comparable rows**, **10,515 match OHLCV exactly**, **30,190
price rows exceed the representation threshold**, **5,343 price differences
exceed 1%**, and **766 volume differences exceed 1%**. Three invalid original
OHLC rows remain identified; broader numerical differences remain open.

Current SQLite metadata has **207 series**, **137 source checks**, **733 quality
rows**, **1,268 import receipts**, and **911 total archive metadata rows**.
The active archive index has **442 objects / 345,574 indexed rows**, comprising
**435 published / seven pending**, plus **58 handoffs**, **30 dated-year recovery
receipts**, and **nine unavailable ranges** (five older years and four recent
sessions). Fresh index reconstruction matches exactly and creates zero hot
candles. The actual unpacked wheel's packaged CLI restores these records without
creating ingestion jobs; its packaged HTTP reads serve HCM 2020/2022 and the
checked CTR/FPT 2018 years while preserving VTP/EIB HTTP 503 guards and the
existing FPT receipt. Cold reconstruction restores archive/recovery/gap metadata;
generic quality findings are preserved separately by populated SQLite backups
and the original immutable publication evidence.

The populated backup and restored SQLite file are **857,141,248 bytes** each,
SHA-256 `337f292a25ad349ab7680e9b072d9cd7c413f56b7324eb78841ecc1ea73fffb8`.
Schema **2**, quick-check, exact daily/operational metadata, archive evidence,
and the existing unresolved CTR volume finding restore successfully. Runtime
remains unchanged from the **251-test** checkpoint. Ruff lint and all **54**
format checks pass; source and wheel distributions rebuild offline from the
existing cache. Production routing is
unchanged. Other unavailable years, recent gaps, pending objects, minute/provider
consistency, private production inventory, and replacement acceptance remain open.

Evidence: `data/hcm-historical-gap-preflight-20261003/` (including original
CSV/API captures, all-archive numeric comparison, independent native/volume
verification, and minute-basis report),
`data/hcm-historical-rebaseline-publication-20261003.json`,
`data/hcm-historical-rebaseline-other-candles-preservation-20261003.json`,
`data/hcm-historical-rebaseline-index-restore-20261003.json`,
`data/hcm-historical-rebaseline-backup-restore-20261003.json`,
`data/retained-vn-daily-after-hcm-20261003.json`,
`data/sdk-hcm-historical-rebaseline-recent-20261003.json`,
`data/sdk-hcm-historical-rebaseline-20261003.json`,
`data/web-hcm-historical-rebaseline-20261003.json`, and
`data/hcm-historical-rebaseline-wheel-smoke-20261003.json`.
Rollback backup: `backups/local-rehearsal-before-hcm-historical-rebaseline-20261003.sqlite3`.
Verified populated backup: `backups/local-rehearsal-hcm-historical-rebaseline-20261003.sqlite3`.

## Earlier verified CTR daily rebaseline and historical recovery

CTR now uses a single VNDirect daily revision,
`7131455e-b664-48c7-aa49-f03e601623b1`, across **747 retained candles** and
**six older Parquet partitions / 1,435 rows**. All original retained dates and
previously published older dates remain present. Independently dated original
CSV/API evidence recovers **250 candles in 2019**; the previously pending
**248-candle 2022** partition is also readable. Only CTR's recovered 2019 gap
marker is cleared. A separate wide native response reproduces all **2,182
candidate OHLCV candles exactly**, with no missing or extra candidate dates.

Against its old VPS retained window, **617 price rows** differ, with maximum
relative OHLC difference `0.0005611745513867117`. **691 volumes** differ; **five
exceed 1%**, with maximum relative difference `0.018634615384615305`. DNSE
independently corroborates four of those five native volumes. One remains
unresolved: **July 15, 2026**, VNDirect **158,907** versus original VPS and DNSE
**156,000**. Targeted and wide VNDirect responses agree with each other.
Publication preserves the native VNDirect value, the original values, both raw
provider replies, and an unresolved `provider_volume_disagreement` finding in
SQLite and immutable S3 rebaseline evidence. No dividend factor or override is
inferred. This is measured disagreement, not certification of every volume.

The completed minute/daily audit compares **248 observed dates / 41,528 minute
candles**. Neither old nor candidate daily basis has a price difference above
the explicit **1%** audit threshold; their maximum relative OHLC differences are
`0.000013379045070704976` and `0.000006705783738403248` respectively. CTR's minute
rows and handoff remain unchanged. Threshold agreement does not establish an
independent holiday/suspension calendar or exact provider equivalence.

Publication verifies RustFS readback of the original hot before-image, preserved
old archives, six new native partitions, and immutable receipts; affected daily
and archive-writer leases protect the atomic SQLite change. Main SQLite retains
**5,684,283 candles**, including **43,632 VN daily rows**. The other **5,683,536
candles** match the rollback backup exactly, including OHLCV, provider, revision,
and update timestamp. All **58 other recent HTTP responses** and unrelated
operational rows remain exact. CTR's normal update changes its `next_1d`
schedule; a fresh **40-completed-candle** provider comparison succeeds on the
new revision. Original archive bytes and old recovery receipts remain preserved.

Raw HTTP requests now return 2018 **250**, 2019 **250**, 2020 **252**, 2021
**250**, 2022 **248**, and 2023 **249** CTR candles. The full frozen 59-ticker
replay finishes with zero errors and verifies unchanged local data/metadata:
**57 date sets match**, **four EIB/HHS dates are missing**, and **zero extra
dates** exist. Across **43,629 valid comparable rows**, **10,566 OHLCV rows match
exactly**, **30,144 price rows exceed the representation threshold**, **5,343
price differences exceed 1%**, and **766 volume differences exceed 1%**.
The original three invalid legacy OHLC rows remain identified. Date coverage
and numerical parity are separate conclusions; broader discrepancies remain open.

The unchanged SDK passes **six recent cases** (daily/minute/15-minute, SMA/EMA)
and **six historical cases** (2019, 2022, and a 2023 retention-boundary range).
Returned dates, OHLCV, and MA10–MA200 match **equivalent backward API queries
exactly**, with API provenance. The SDK queries backward from the end date,
then filters the requested start and tail limit. Comparing it to a forward
start-date query uses a different EMA warmup seed: OHLCV still matches, but
maximum relative EMA200 differences reach `0.000617794044242892` for 2022 and
`0.00016603915723334417` at the 2023 boundary. The verification script now
compares equivalent requests; no SDK or runtime code changed to conceal this.

Selected unchanged public CTR daily, 15-minute, weekly chart and volume-profile
checks pass with local API interception, no JavaScript/network failures, and no
writes. Other VNINDEX weekly requests retain known unavailable-history errors.
Actual default 59-ticker bulk daily **SMA JSON/CSV return HTTP 200**, whereas
**EMA JSON/CSV return HTTP 503** because their 600-bar lookback crosses the
recorded EIB May 22 / June 11, 2025 gaps. These checks do not establish that all
public web requests pass; the existing unavailable-range guards remain intact.

Current metadata contains **207 series**, **137 source checks**, **733 quality
rows**, **1,266 import receipts**, and **906 total archive metadata rows**.
The active index has **441 objects / 345,322 indexed rows**: **434 published /
seven pending**, with **58 handoffs**, **29 dated-year recovery receipts**, and
**10 unavailable ranges** (six older years and four recent sessions).
Fresh index reconstruction matches those records exactly. The actual unpacked
wheel restores the index without creating hot candles or jobs, serves checked
CTR 2018/2019/2022 and FPT 2018 years, preserves old FPT/CTR recovery evidence,
and retains VTP 2019 and EIB missing-session HTTP 503 guards. Cold-index restore
reconstructs archive/recovery/gap metadata; the separate populated SQLite backup
and immutable rebaseline receipt preserve the generic volume disagreement.

The populated backup and restored file are **857,055,232 bytes** each, SHA-256
`91b3790f9345ac7e61ba80f0a0e846bfb94cfe6cdb05a9e107c8be08dd98b3b2`.
Schema **2**, SQLite quick-check, daily/operational metadata, archive evidence,
and the unresolved volume finding restore exactly. Runtime remains unchanged
from the **251-test** checkpoint. Ruff lint passes, **54 files** pass format
checks, and source/wheel distributions rebuild offline from the existing cache.
Production routing is unchanged. Complete
history, the four recent gaps, pending objects, minute/provider consistency,
private production inventory, and replacement acceptance remain open.

Evidence: `data/ctr-historical-gap-preflight-20261003/` (including native window,
volume corroboration, minute-basis audit, and preserved originals),
`data/ctr-historical-rebaseline-publication-20261003.json`,
`data/ctr-historical-rebaseline-other-candles-preservation-20261003.json`,
`data/ctr-historical-rebaseline-index-restore-20261003.json`,
`data/ctr-historical-rebaseline-backup-restore-20261003.json`,
`data/retained-vn-daily-after-ctr-20261003.json`,
`data/sdk-ctr-historical-rebaseline-recent-20261003.json`,
`data/sdk-ctr-historical-rebaseline-20261003.json`,
`data/web-ctr-historical-rebaseline-20261003.json`,
`data/ctr-historical-rebaseline-bulk-check-20261003.json`, and
`data/ctr-historical-rebaseline-wheel-smoke-20261003.json`.
Rollback backup: `backups/local-rehearsal-before-ctr-historical-rebaseline-20261003.sqlite3`.
Verified populated backup: `backups/local-rehearsal-ctr-historical-rebaseline-20261003.sqlite3`.

## Earlier selected-universe 2018 archive extension

A bounded nine-ticker pass recovers **eight complete original 2018 date sets**:
DGC **250**, BSR **213**, VGI **69**, SHS **250**, CEO **250**, IDC **250**,
CTR **250**, and VTP **26**—**1,558 candles** total. Separate frozen public CSV/API
responses agree on every date. Native data retains every original timestamp
without exclusions or guessed missing sessions. The new Parquet partitions use
each existing daily revision: VPS for five tickers, VNDirect for CEO/IDC, and
DNSE for SHS. Actual ordinary **40-candle updates** pass in isolated storage;
fresh completed-provider comparisons pass again immediately before publication.

DGC's original and native **OHLCV match exactly**. The other overlapping price
rows differ; maximum relative OHLC differences are `0.011327759438085505` (BSR),
`0.00008462825411914565` (VGI), `0.07788240042869776` (SHS),
`0.0017764294579050155` (CEO), `0.06238667500528661` (IDC),
`0.31363216707522845` (CTR), and `0.265500158674181` (VTP). These measured
differences are preserved without inferred dividend factors or claimed numerical
identity. Published history follows the existing native daily series coherently.

All volumes match except BSR on August 15, 2018: legacy `2,353,749`, native
`2,299,149`, with computed relative difference `0.023197035877657313`.
Both VNDirect and DNSE independently return the same dated native volume. Their
prices differ, so no alternate provider's OHLC is mixed into the pinned VPS
partition. Checksummed source captures and original legacy values remain intact.

GEE's 2018 CSV returns **403**; a separate bounded old API request returns
**HTTP 200 with zero rows**. Both actual responses are preserved. Neither proves
a listing date or a complete absence of private history. No GEE candles, exclusion
policy, or history-gap record is fabricated from these replies.

Publication verifies immutable originals, native Parquet, and recovery proofs
through RustFS readback, holds archive-writer and affected daily leases, and
atomically adds only archive metadata and receipts after checking ready series.
All **5,684,283 existing hot candles** match the rollback backup exactly, including
OHLCV, provider, revision, and update timestamp, with **zero differences**.
Existing series/checks/quality/jobs/tickers/handoffs match exactly. Recent HTTP
responses for **all 59 selected VN tickers** are unchanged; the eight dated raw
requests change from empty to their verified original-date counts.

The current snapshot has **1,264 import receipts**, **900 total archive metadata
rows**, and **440 active objects / 345,072 indexed rows**: **432 published / eight
pending**. Fresh index reconstruction exactly preserves these objects plus **58
handoffs**, **28 dated-year recovery receipts**, and **11 unavailable ranges**.
The actual unpacked wheel's packaged CLI restores the current index into fresh
SQLite, serves **nine checked 2018 years** including existing FPT, retains known
CTR/VTP/EIB HTTP 503 guards, and creates no hot candles or ingestion jobs.

The existing SDK passes **28 historical comparisons** across full 2018 and six
available early-2019 ranges with SMA/EMA, exact returned OHLCV/MA values, and API
provenance. Its original tail-limit and short-series indicator behavior remain
intact. CTR/VTP's independently unavailable 2019 years remain explicit HTTP 503
and `AIPriceActionError`; neither request falls back silently to old archives.
The newly verified preceding year does not clear a different year's gap record.

The populated backup and restored file are **856,915,968 bytes** each with SHA-256
`182154a8c8bd581eff53f291f906a3fb03ad42c8bca7c9f47c8020f73b1b9296`.
Schema **2**, quick-check, complete operational metadata, and archive evidence
match exactly. Runtime code is unchanged from the **251-test** checkpoint.
Broader historical coverage, known gaps, adjustment differences, and production
replacement acceptance remain open.

Evidence: `data/older-daily-selected-2018-preflight-20261003/` (including
`volume-disagreements.json` and `volume-provider-checks/`),
`data/older-daily-selected-2018-publication-20261003.json`,
`data/older-daily-selected-2018-original-candles-preservation-20261003.json`,
`data/older-daily-selected-2018-index-restore-20261003.json`,
`data/older-daily-selected-2018-backup-restore-20261003.json`,
`data/sdk-older-daily-selected-2018-20261003.json`, and
`data/older-daily-selected-2018-wheel-smoke-20261003.json`.
The rollback backup is
`backups/local-rehearsal-before-older-daily-selected-2018-20261003.sqlite3`;
the verified populated backup is
`backups/local-rehearsal-older-daily-selected-2018-20261003.sqlite3`.

## Earlier verified pre-2018 daily archive extension

VCB/MBB/VIC/HPG now each retain an additional **250 daily candles in 2017** and
**251 in 2016**: **eight Parquet partitions / 2,004 rows**. Separate original
public CSV/API captures agree on every date. Native VPS replies reproduce all
original dates without exclusions and preserve the current daily provider and
revision. Each isolated candidate passes an actual ordinary **40-candle update**;
fresh completed-provider comparisons also pass immediately before publication.
No recent daily or minute series is replaced.

All overlapping 2016 volumes match exactly. For 2017, **two volume differences**
occur on August 15: HPG legacy `4,397,780` versus native `4,573,670`, and VIC
legacy `388,640` versus native `508,250`. Relative differences computed from
these values are `0.039995179385962976` and `0.3077655413750515` respectively.
Both VNDirect and DNSE return the same dated native volumes, independently
corroborating the published VPS values. Their actual response captures remain
checksummed in the isolated evidence directory. No provider's OHLC is mixed into
the pinned VPS series. The original differing values are preserved as evidence.

Every overlapping legacy price row differs. Maximum relative OHLC differences
for VCB/MBB/VIC/HPG are respectively `0.008366019149847803`,
`0.0004644681839294229`, `0.00006282964451254092`, and
`0.36017400391617294` in 2017; in 2016 they are
`0.008379471624849977`, `0.00048323947967920944`,
`0.00008101101749835582`, and `0.3601742532499064`.
These measured disagreements are recorded without guessed scaling or claimed
corporate-action equivalence. The new history follows the existing native basis.

Each publication verifies uploaded originals, Parquet, and recovery proofs by
reading them back from local RustFS, holds the global archive-writer and affected
daily leases, and atomically adds only metadata/receipts after checking unchanged
ready series. Each full indexed before/after candle join proves **all 5,684,283
hot candles unchanged**, with **zero differences** in OHLCV, provider, revision,
or update timestamp. Existing series/checks/quality/jobs/tickers/handoffs match
exactly; all **59 recent HTTP responses** remain identical. Eight dated requests
that were empty now return their **250 / 251 verified rows**.

The current snapshot has **1,256 import receipts**, **892 total archive metadata
rows**, and **432 active objects / 343,514 indexed rows**: **424 published / eight
pending**. Its **58 handoffs**, **20 dated-year recovery receipts**, and **11
unavailable ranges** reconstruct exactly. Both checkpoints have verified populated
backup restores. The actual unpacked wheel's packaged CLI restores the latest
index into fresh SQLite; five checked 2016 years (including existing FPT) read
successfully, the known EIB missing session remains HTTP 503, and no hot candles
or ingestion jobs are created by index restoration.

The unchanged SDK passes **32 additional historical comparisons**, covering full
2016/2017 and their following early-year ranges with SMA/EMA and API provenance.
Returned OHLCV and MA values are exact. Early-year MA200 lookback now draws from
the preceding verified cold partition. Independently aggregating frozen native
daily inputs across December 2016–February 2017 also matches **12 actual HTTP
cases**: four symbols each at `1W`, `2W`, and `1M`, with **13 / six / three bars**
respectively. Complete buckets and the explicit end-truncated final bucket match
OHLCV exactly. No numerical identity with the old adjusted-price basis is claimed.

The latest populated backup and restored file each contain **856,903,680 bytes**,
with SHA-256 `c7fd3cd7fffff91812228cb7ac79dd633cc91e31a40fa66c122dbbabac5e3dd7`.
Schema **2**, quick-check, complete operational metadata, and archive evidence
match exactly. The runtime is unchanged from the
**251-test** suite checkpoint. Production routing remains unchanged. Older served
years, broader selected-universe history, complete indicator warmup, and adjustment
semantics remain required work.

Evidence: `data/older-daily-2017-preflight-20261003/` (including
`volume-disagreements.json` and `volume-provider-checks/`),
`data/older-daily-2016-preflight-20261003/`,
`data/older-daily-{2017,2016}-publication-20261003.json`,
`data/older-daily-{2017,2016}-original-candles-preservation-20261003.json`,
`data/older-daily-{2017,2016}-index-restore-20261003.json`,
`data/older-daily-{2017,2016}-backup-restore-20261003.json`,
`data/sdk-older-daily-{2017,2016}-20261003.json`,
`data/older-daily-{2017,2016}-wheel-smoke-20261003.json`, and
`data/older-daily-pre2018-aggregation-20261003.json`.
Rollback backups are
`backups/local-rehearsal-before-older-daily-{2017,2016}-20261003.sqlite3`;
verified post-publication backups are
`backups/local-rehearsal-older-daily-{2017,2016}-20261003.sqlite3`.
Braced years identify the two separately retained files, not a literal filename.

## Earlier verified 2018 daily archive publication

VCB, MBB, VIC, HPG, and VHM now have five additional 2018 Parquet partitions,
containing **248 / 248 / 248 / 248 / 161 candles** respectively (**1,153 total**).
Each uses its existing verified VPS daily revision and extends previously
reconciled 2019–2023 cold history. Separate original public CSV and API captures
agree on the original dates. Actual native responses and ordinary completed
**40-candle updates per ticker** verify provider identity and the current basis
before isolated recovery and again before local publication.

The four full-year originals contain flat, positive-OHLC, zero-volume placeholders
on January 23–24, 2018. The [VNDirect closure notice](https://www.vndirect.com.vn/vndirect-thong-bao-ve-viec-tam-ngung-giao-dich-tren-so-giao-dich-chung-khoan-thanh-pho-ho-chi-minh-ngay-24-01-2018/)
documents both dates. Historical HOSE membership is independently supported by
[Vietcombank's issuer history](https://vietcombank.com.vn/vi-VN/Ve-Vietcombank),
[MB's issuer article](https://news.mbbank.com.vn/news/thuong-tuong-le-huu-djuc-vi-tuong-dji-tu-chien-truong-djen-thuong-truong-1703061927),
[Vingroup's issuer prospectus](https://ircdn.vingroup.net/storage/Uploads/0_Quan%20he%20co%20dong/0_Vingroup_2022/TP/121004/2.%20BCB%20Niem%20yet%20VICB2124001%20-%2015.06.2022.pdf),
and [Hoa Phat's issuer article](https://www.hoaphat.com.vn/tin-tuc/hoa-phat-duoc-chap-canh-boi-ttck.html).
The policy explicitly adds only VCB/MBB/VIC/HPG to the existing FPT exception.
VHM needs no exclusion. Every remaining original date and volume matches native
data. The **eight excluded placeholders** remain in immutable original S3 bytes
and per-partition receipts; no other missing date or guessed closure is licensed.
Direct HTTP captures of the closure page and an earlier VIC prospectus returned
403; those capture receipts are not document evidence. The cited closure content
and 2022 issuer prospectus were verified separately through web results.

Exact numeric price identity with the legacy provider is **not established**.
Every overlapping price row differs; measured maximum relative OHLC differences
are `0.00834588601400077` (VCB), `0.00024066915849307868` (MBB),
`0.00003403273528113093` (VIC), `0.36017349564170265` (HPG), and
`0.500021175843056` (VHM). All compared volumes match exactly. These are measured
provider/basis disagreements, not inferred corporate-action factors. Published
history follows the current native series coherently and preserves original prices
as evidence; this checkpoint does not resolve dividend semantics.

After verifying object/evidence checksums and reading them back from RustFS,
publication holds the archive-writer and five daily leases, rechecks original
ready series, and atomically adds only archive metadata and recovery receipts.
It never updates hot candles. A full indexed before/after join verifies
**all 5,684,283 hot candles unchanged**, including provider, revision, and update
timestamps, with **zero differences**. Existing series, checks, quality, jobs,
tickers, and handoffs are exact. All **59 recent HTTP responses** are exact,
while the five raw dated 2018 requests change from empty to their verified counts.

The local snapshot now has **1,248 import receipts** and **884 total archive
metadata rows**, of which **424 objects / 341,510 indexed rows** are active:
**416 published / eight pending**. Its **58 handoffs**, **12 dated-year recovery
receipts**, and **11 unavailable ranges** reconstruct exactly in fresh SQLite.
The previous FPT receipt still validates. The actual unpacked wheel's CLI restores
this same index from RustFS, serves all six checked 2018 years, leaves the known
EIB missing session at HTTP 503, and creates no hot candles or ingestion jobs.

The existing Python SDK passes **20 historical comparisons** across five symbols,
full 2018 and early 2019 ranges, with SMA/EMA, API provenance, and exact returned
OHLCV/MA values. The reference respects the SDK's existing tail limit within an
explicit date range, while the HTTP contract selects forward from its start date.
Rust's documented short-series behavior is preserved: VHM's 161-bar 2018 request
has expanding SMA200 values and EMA200 seeded on its last available bar. No
synthetic prior candles are added. This verifies interface and indicator parity,
not equality with original legacy-provider adjusted prices.

The populated backup and restored file are each **856,903,680 bytes**, with SHA-256
`08c339633e377c8f52c7ea9108445d300ebce0a31a3e3162af0991f3308860b6`.
Schema version **2**, SQLite quick-check, operational metadata, and archive
evidence match exactly. Full API tests pass **251 cases in 20.64 seconds**;
lint/format checks pass across **54 Python files**, and the distribution builds
offline. Production routing remains unchanged. Pre-2018 history beyond FPT,
remaining minute coverage, frozen series, and provider discrepancies stay open.

Evidence: `data/older-daily-2018-preflight-20261003/`,
`data/older-daily-2018-publication-20261003.json`,
`data/older-daily-2018-original-candles-preservation-20261003.json`,
`data/older-daily-2018-index-restore-20261003.json`,
`data/older-daily-2018-backup-restore-20261003.json`,
`data/sdk-older-daily-2018-20261003.json`, and
`data/older-daily-2018-wheel-smoke-20261003.json`.
The rollback backup is
`backups/local-rehearsal-before-older-daily-2018-20261003.sqlite3`;
the verified post-publication backup is
`backups/local-rehearsal-older-daily-2018-20261003.sqlite3`.

## Earlier scoped VN repair pagination

The repair worker previously validated a complete 500-row provider page even
when its oldest candles preceded the requested retention floor. The DNSE
EIB/HHS trial failed on December 27, 2022 duplicates while trying to reach the
October 3, 2023 floor. VN repairs now pass that floor to normalization. `Page`
retains its oldest selected native timestamp as a separate pagination cursor;
only requested candles undergo OHLC/duplicate validation and staging. Every
requested invalid or conflicting candle still rejects the repair. Unverified
boundary timestamp conventions remain errors. Cursor progress indicates observed
pagination, not a verified exchange calendar.

An older terminal page with no requested rows can finish previously validated
staging; an old-only initial page or no-data without a boundary cannot replace
published data. The existing non-advancing-cursor, observed completed-coverage,
provider identity, revision, lease, and current-tail checks remain in force.
No service, public API parameter, database schema, or provider is added.
Six regression cases cover old defects/missing values, a requested duplicate,
invalid OHLC at the floor, an empty terminal page, and an old-only initial response.
Numeric parsing also occurs only after selecting the requested timestamps, so a
null price outside that scope cannot reject the recent page. The complete suite
passes **246 tests in 20.43 seconds**. Lint/format checks pass across
**54 Python files** and the distribution builds offline.

The actual EIB/HHS worker jobs resume their exact checksummed second DNSE pages
and existing **500-row** staging. Each stages **247 additional requested rows**
and completes its isolated **747-candle** daily replacement, preserving every
original observed date and recovering May 22 / June 11, 2025. No outside-floor
candle is staged, and no conflicting 2022 row is arbitrarily selected. Each
candidate passes an actual ordinary **40-candle completed update** on its same
revision before and after cold reconciliation. Eight isolated HTTP checks pass
for recovered dates and recent SMA/EMA. Relative price differences from the
original VPS data reach `0.021220159151193574` for EIB and
`0.022222222222222254` for HHS; **241 / 209** overlapping OHLC rows and **600 / 597**
volumes differ respectively. These measured basis disagreements are preserved
without inferred dividend factors.

Of their **ten older objects**, **six reconcile** with exact original timestamps
(2020, 2021, and 2023 for each ticker). Both 2022 objects still reject conflicting
December 27 candles; both 2019 objects reject invalid OHLC. Those defects are
inside the requested historical partitions. All originals were previously
readable, so **neither candidate is eligible for main publication**. Original
archive bytes, failed responses, and staging remain preserved. Main local VN
daily values/provenance and all operational metadata match exactly before/after;
the current four session guards, provider revisions, and S3 pointer remain
unchanged.

The actual unpacked wheel initializes a fresh SQLite file through its packaged
CLI and replays both entire native repairs using verified frozen captures:
**500 + 247** rows each, **1,494 retained rows** total, and **80 ordinary update
candles** on unchanged revisions. The imported module path points inside the
unpacked wheel. Neither this replay nor the live candidate trial changes the
main API database.

Evidence: `data/retained-daily-gap-dnse-scoped-repair-20261003.json`,
`data/retained-daily-gap-dnse-scoped-captures-20261003/`,
`data/retained-daily-gap-dnse-preflight-20261003.sqlite3`, and
`data/scoped-repair-wheel-final-smoke-20261003.json`.

## Earlier retained VN daily publication and recovery

Five complete VNDirect daily candidates—HAG, MSN, STB, VDS, and VPL—are now
published in the local rehearsal. They replace **3,328 original hot rows** with
**3,337**, recovering **nine missing sessions** without removing any original
date. Before and after publication, actual ordinary updates verify **40 completed
candles per ticker** on the unchanged candidate revisions. The transaction
rechecks original values/state, replaces only the five daily series, supersedes
their old readable archive metadata, and resolves only the proven nine gap
records. It preserves all minute handoffs. The global archive-writer and daily
series leases protect publication. One obsolete HAG archive job is cancelled.

The seven staged candidates' **30 older objects / 7,116 indexed rows** undergo
actual native worker reconciliation. **27 reconcile** with the exact original
timestamps. VNDirect returns invalid OHLC for **EIB September 13, 2022**, and
**HAG/HHS December 19, 2019**. Raw checksummed responses are preserved; no candle
is dropped, clamped, or corrected with a guessed factor. EIB 2022 and HHS 2019
were readable under the original basis, so those two candidates remain isolated
to avoid losing older availability. HAG 2019 was already pending and retains
that status. Its **250-candle 2021 archive** is now recovered. The five published
series use **19 verified native cold replacement objects** and the unchanged
pending HAG 2019 object. Every old archive byte remains preserved; all original
hot rows have immutable S3 Parquet before-images and per-ticker JSON evidence
receipts, plus a populated rollback backup and the previous manifest pointer.

Reusing all **59 initial frozen legacy captures** in a separate after-report
compares **43,632 local rows / 43,636 observed legacy dates**. **57 date sets
match**, with **four missing / zero extra dates**. EIB/HHS still lack May 22 and
June 11, 2025. The three invalid legacy CEO/IDC rows remain excluded from numeric
comparisons. Of **43,629 compared rows**, **10,614** match OHLCV exactly,
**30,106** exceed the 1e-7 relative OHLC representation threshold, and **5,343**
exceed 1% OHLC difference; **5,360** volumes differ, **761** by more than 1%.
Date parity remains an observed-feed comparison; provider/adjustment differences
are still material and do not prove a dividend factor or complete calendar.

**90 real local HTTP cases** produce **83 successful responses / seven explicit
503 errors**. All nine recovered dates match native JSON OHLCV, and CSV responses
match the existing precision/column contract. Recent daily, minute, 15-minute,
and weekly SMA/EMA checks pass except the existing HAG weekly EMA read requiring
its pending 2019 lookback. The four EIB/HHS missing-date reads remain unavailable.
Full HAG 2019 and first-candle 2020 reads retain explicit 503 errors: the latter
requires one prior candle for change metrics even with moving averages disabled.
The other 18 archive-partition reads succeed, including HAG 2021. Daily SMA/EMA
reads at the three-year retention boundary succeed for all four older published
tickers. Health matches all **11 typed gaps** exactly and reports the five daily
series' current VNDirect verification. No claim of full historical coverage.

The actual existing Python SDK passes **30 comparisons**: five tickers × daily,
minute, and 15-minute intervals × SMA/EMA, with **20 rows each**, API provenance,
and zero differences across timestamps, OHLCV, and all five moving averages.
An actual browser loads the existing public HAG daily/15-minute charts and
volume profile with isolated API reads redirected to localhost; page/network
errors and blocked writes are empty. Production routing is unchanged.

An indexed full comparison proves all **5,680,946 unrelated original candles**
remain identical, including OHLCV, provider, revision, and update timestamps.
Unrelated operational rows remain exact. SQLite now has **5,684,283 candles**,
**207 series**, **137 source checks**, **732 quality records**, **1,243 import
receipts**, **58 handoffs**, and **459 jobs**. The S3 index has **419 active
objects / 340,357 indexed rows**, with **411 published / eight pending**.
A fresh empty index restores all objects, **58 handoffs**, **seven dated-year
recovery receipts**, and **11 gap records** exactly, including 250 readable HAG
2021 candles and zero local candles. The five new rebaseline receipts remain
in SQLite and immutable S3 evidence; they are distinct from dated-year recovery
receipts reconstructed by `restore-index`.

The actual CLI restores a new populated backup. Both files measure
**856,903,680 bytes** and share SHA-256
`70732c2bb7404c4a415fa88cd493db0c9ca548b8574d263fe80a0a123161262c`.
All table counts, complete local VN daily data/provenance, operational metadata,
archive objects, handoffs, recovery receipts, and gap records match exactly;
schema version remains **2** and `quick_check` is **ok**. The files are
`backups/local-rehearsal-retained-daily-repairs-20261003.sqlite3` and
`data/restored-rehearsal-retained-daily-repairs-20261003.sqlite3`.

The comparison helper adds `--captures` to preserve the initial report while
reusing frozen evidence. Lint/format pass across **54 discovered Python files**,
and the distribution builds offline. Runtime code is unchanged in this
checkpoint; its preceding full suite has **240 passing tests**.

Evidence: `data/retained-daily-cold-reconcile-20261003.json`,
`data/retained-daily-cold-invalid-native-20261003.json`,
`data/retained-daily-repairs-publication-20261003.json`,
`data/retained-vn-daily-after-repairs-20261003.json`,
`data/retained-daily-repairs-http-verification-20261003.json`,
`data/retained-daily-repairs-other-candles-preservation-20261003.json`,
`data/retained-daily-repairs-index-restore-20261003.json`,
`data/retained-daily-repairs-backup-restore-20261003.json`, the five
`data/sdk-*-retained-daily-repairs-20261003.json` reports, and
`data/web-hag-retained-daily-repairs-20261003.json`.

The follow-up actual DNSE worker trial for EIB/HHS remains isolated. Each
stages **500 daily candles** from September 30, 2024 through October 2, 2026.
Its second **500-row page** includes two conflicting normalized candles for
**December 27, 2022**. Validation rejects both complete repair attempts rather
than arbitrarily selecting one duplicate. This conflict lies outside the
required three-year retention floor, so it does not prove a recent missing
session; narrower remaining-window pagination still deserves investigation.
It also blocks a straightforward coherent 2022 archive replacement. The raw
responses, staging, errors, and main-state equality proof remain preserved.
No additional main candle, provider, job, or gap change occurs. Evidence:
`data/retained-daily-gap-dnse-preflight-20261003.json` and
`data/retained-daily-gap-dnse-conflicts-20261003.json`.

## Earlier retained VN daily audit and isolated recovery

The reproducible `scripts/check_retained_vn_daily.py` opens SQLite in read-only
mode and compares **all 59 selected VN tickers** within the three-calendar-year
window, respecting VPL's verified first-listing bound. At the October 3, 2026
checkpoint it compares **43,623 local rows** with **43,636 captured legacy dates**.
**52 date sets match**, with **13 missing / zero extra dates** across seven
tickers: EIB, HAG, HHS, MSN, STB, and VPL each lack **May 22 and June 11, 2025**;
VDS lacks **June 11**. There are **zero request errors or duplicate legacy dates**.
Legacy CEO has invalid OHLC on May 14/16, 2025; IDC has an invalid May 14 bar.
Those **three malformed rows** are preserved and excluded from numerical
comparison, while their dates remain observational coverage evidence.

Among **43,620 numerically compared rows**, **9,729** match OHLCV exactly,
**30,106** exceed the explicit **1e-7** relative OHLC representation threshold,
and **5,343** have OHLC differences above the separate **1% review threshold**.
Volumes differ on **5,842 rows**, including **764 above 1%**. Relative OHLC
differences use the legacy value as denominator. The largest observed difference
is VHM's `0.5000276251312193`. These measurements describe provider/basis
disagreements, not proof that a revision is a dividend or license for a factor.
Legacy date parity does not establish a complete exchange calendar. All **59
checksummed response receipts** replay successfully offline; the runner verifies
unchanged complete local VN daily values/provenance and all operational metadata.

Bounded native daily probes cover the seven affected symbols on VPS, VNDirect,
and DNSE. VPS omits every identified session while matching surrounding original
OHLCV exactly. Both alternatives return traded candles on every missing date,
but their surrounding volumes differ; DNSE also has meaningful price differences.
No individual alternative-provider row was inserted under a VPS revision.

In isolated SQLite/filesystem storage, seven actual worker repairs rebuild each
entire retained daily window on VNDirect: **4,818 original rows become 4,831**,
with all original dates preserved and all **13 missing dates recovered**. Six
repairs use **two pages**; VPL uses **one page** and its original listing bound.
Each passes an ordinary **40-candle completed update** on its new unchanged
revision (**280 candles total**), exact dated HTTP reads, and recent SMA/EMA
indicators. Maximum relative price differences from the original VPS data are
zero for STB/VPL/MSN/HAG, `6.692992436918566e-05` for EIB,
`0.00020584602717166334` for HHS, and `9.773260359646763e-05` for VDS.
Volume changes are recorded separately. No shared price difference exceeds 1%.
The seven candidates' **30 older archive objects / 7,116 indexed rows** still
need coherent reconciliation before publication. The main provider revisions,
candles, minute handoffs, and jobs remain unchanged.

The existing typed guards now record the 13 confirmed missing sessions in one
transaction, preserving the seven older unavailable years. **33 actual HTTP
cases** (daily JSON/CSV and weekly) change from **200 empty/partial success** to
explicit **503**. Recent daily output for **all 59 selected VN tickers** matches
exactly before/after. All **5,684,274 candles**, **207 series**, **137 source
checks**, **1,238 import receipts**, **58 handoffs**, and **459 jobs** remain
unchanged; the **732 quality rows** now include **20 typed unavailable ranges**.
Every original quality finding is preserved. The global archive-writer lease
protects metadata publication. A fresh S3 index matches **419 active objects**,
handoffs, recovery receipts, and all 20 gap records exactly; health metadata
matches the stored records. The original manifest pointer and pre-publication
SQLite backup remain preserved. A populated new backup/restore preserves the
updated gap/index metadata, exact local daily values, and operational rows.
Backup/restored files measure **856,797,184 bytes**, share SHA-256
`2c4aecb1fc62d4560f99256a8e09cce624a3f71ddc7e844103a54111e0c00f94`,
retain schema version **2**, and pass `quick_check`. They are
`backups/local-rehearsal-retained-daily-gap-guards-20261003.sqlite3` and
`data/restored-rehearsal-retained-daily-gap-guards-20261003.sqlite3`.

Only the verification runner and local data/docs change in this checkpoint.
The preceding runtime suite has **240 passing tests**. Lint/format checks pass
across **50 files**, and the distribution builds offline. No new runtime service,
schema, provider mixing, or production routing change is introduced.

Evidence: `data/retained-vn-daily-legacy-comparison-20261003.json` and its
checksummed capture/receipt directory,
`data/retained-daily-gap-provider-preflight-20261003.json` and native captures,
`data/retained-daily-gap-repair-preflight-20261003.json`,
`data/retained-daily-gap-repair-preflight-20261003.sqlite3`,
`data/retained-daily-gap-guards-publication-20261003.json`,
`data/retained-daily-gap-guards-before-pointer-20261003.json`,
`data/archive-index-retained-daily-gap-guards-restored-20261003.sqlite3`, and
`data/retained-daily-gap-guards-backup-restore-20261003.json`.

## Latest historical discovery and archive-only health

The name route previously used only the packaged catalog. Registered/imported
tickers with available history could be absent from discovery, and a reconstructed
archive-only index had no per-series health entries. `/tickers/name` now merges
registered and archived identities with the catalog, keeping the same map and
source-mode contract. Catalog names retain precedence. Known registered names
are preserved locally; an archive identity without company metadata uses its
symbol. Discovery does not activate ingestion or invent company information.

Operational status and `/health.storage.series` include archive-only identities
with `archived` or `archive_pending` status. The separate published-object count,
indexed row count, and minimum/maximum archived dates exclude pending/superseded
objects; pending repairs have their own count. Existing local counts and dated
provider checks retain their meaning. Indexed object rows may overlap and are
not a deduplicated candle count or proof of continuous trading-session coverage.
Archive-only identities carry no local-ingestion or successful-provider date and
no live-verification claim. No schema or Compose service changes.

Three added regressions cover archive-only name/health discovery and exact reads
after reconstruction, source-mode/name precedence, and published/pending/
superseded separation. The complete API suite passes **240 tests**, with one
existing Starlette test-client deprecation warning. Lint and formatting pass
across **49 files**, and the distribution builds offline. The unpacked-wheel
smoke uses the actual packaged CLI to restore an index, verifies name fallback
and HTTP archived bounds, and reproduces exact cold candles with no network.
The packaged watchlist still contains **59 selected VN entries**.

The restarted local API preserves every prior metadata entry and exposes **22
previously missing VN index names**, including VNINDEX, VN30, and sector indices.
Five actual HTTP cases compare payloads before/after: recent daily output for
**all 59 selected VN tickers**, FPT 2017 cold history, PNJ 15-minute indicators,
NAB recent daily indicators, and VTP 2022's explicit unavailable-range error.
Every sampled payload matches exactly. All table counts and every ticker,
series, source-check, quality, job, receipt, adoption, archive, and epoch row
remain unchanged. The main database retains **5,684,274 candles / 207 series**.
Its health separates **410 published objects / 338,124 indexed rows** from
**nine pending repairs**; the combined active index remains **419 objects /
340,357 rows**.

A separate copied restored index was served temporarily on loopback port 3002.
It exposes **137 archive-only series**, all with `archived` status and no live
proof, across **410 published objects / 338,124 indexed rows** plus **nine
pending repairs**. Every archive ticker appears in the correct source name map.
There are **zero local candles, series states, provider checks, jobs, or enabled
tickers**. FPT's **250 daily candles in 2017** exactly match the main API; NAB
2022 returns **503**. The temporary server was stopped after verification; the
main loopback API continues serving on port 3001. Production routing is unchanged.

The unchanged public web passes VNINDEX daily/15-minute chart controls and volume
profile with isolated API routing. Page-error, network-error, and blocked-write
lists are empty. The existing Python SDK passes six FPT cases (daily/minute/
15-minute, SMA/EMA), with exact candles/indicators and API provenance. VNINDEX's
minute series remains a frozen preserved snapshot; successful rendering does
not license its unresolved provider handoff or establish live freshness.

Evidence: `data/historical-discovery-http-before-20261003.json`,
`data/historical-discovery-http-verification-20261003.json`,
`data/historical-discovery-index-http-20261003.json`,
`data/archive-index-historical-discovery-restored-20261003.sqlite3`,
`data/historical-discovery-wheel-smoke-20261003.json`,
`data/web-vnindex-historical-discovery-20261003.json` and its screenshots, and
`data/sdk-fpt-historical-discovery-20261003.json`.

## Latest metadata publication recovery

The actual `aipa-api publish-index` command retries archive metadata from the
current SQLite index without provider downloads, Parquet uploads, or pruning.
It uses the existing global archive-writer lease. Recovery's final receipt/gap
publication previously called the manifest writer without that lease; it now
uses the shared helper, as does compaction's final metadata publication.
If another writer is active, a verified completed recovery keeps its local
receipt and resolved gap, preserves the existing remote pointer, and reports
the busy writer. The new command can publish that state after the lease clears.

Three added regressions verify failed pointer writes with later metadata retry,
refusal to publish under another writer's lease, and completed recovery state
surviving that race before exact index reconstruction. At this checkpoint the full API suite passed
**237 tests**. Lint/format checks cover **49 files**; the distribution builds
offline. The unpacked wheel executes the actual publication/restoration commands
and HTTP read/health guards with no network. Its seeded candles, series, checks,
jobs, quality rows, archive index, and epoch are unchanged after publication.
It embeds the **59-ticker** VN watchlist and uploads no Parquet object.

The populated local SQLite/RustFS rehearsal preserves **all 5,684,274 candles**,
**207 series**, **137 source checks**, **719 quality rows**, **1,238 import
receipts**, **58 handoffs**, and **459 jobs**, including exact series/check/
quality/archive metadata and epoch. Idempotent publication leaves the remote
pointer byte-identical. A fresh reconstruction matches **419 objects / 340,357
rows**, **58 handoffs**, **seven recovery receipts**, and **seven typed gaps**
exactly. Existing populated backups remain preserved; no candle data changed.

Evidence: `data/publish-index-rehearsal-20261003.json`,
`data/publish-index-before-pointer-20261003.json`,
`data/archive-index-publish-index-restored-20261003.sqlite3`, and
`data/publish-index-wheel-smoke-20261003.json`.

## Latest alternative endpoint evidence

Read-only probes of DNSE's alternative Entrade mirror return **250 daily rows**
each for SHS and NAB in 2022, repeating the conflicting **December 27, 2022**
date. Each conflicting pair has different OHLC/volume values. The rows pass
individual OHLC checks, but no evidence licenses choosing one duplicate.
A bounded VND 2020 request to VNDirect's separate `stock_prices` endpoint ends
in `ConnectTimeout`. It does not provide a substitute historical series.

[DNSE's official Python SDK](https://github.com/dnse-tech/openapi-sdk/tree/main/python)
documents API-key/secret credentials. Its
[client implementation](https://github.com/dnse-tech/openapi-sdk/blob/main/python/dnse/api/client.py)
defines `/price/ohlc` and `/market/working-dates`. Normal unauthenticated GET
probes of both endpoints return **HTTP 401**, `OA-401`, and
`X-API-Key header required`. The exact responses and checksums are preserved;
no key was spoofed and no authenticated data was obtained.

[VNDirect's adjusted-price disclosure](https://www.vndirect.com.vn/thong-bao-ve-thay-doi-cua-website-vndirect/)
describes corporate-action-adjusted closes for technical charts. That broad
description does not establish identical lifetime daily/minute or volume
conventions for the currently probed endpoints. No main provider revision,
candle, quality marker, or job changes on this evidence. The missing years
remain unavailable; these checks do not prove a calendar or adjustment policy.

Evidence: `data/daily-provider-endpoint-preflight-20261003.json`,
`data/dnse-official-read-preflight-20261003.json`, and their corresponding
directories of immutable raw response captures.

## Latest NAB recent-window publication

The selected VN universe now contains **59 tickers**. NAB's staged daily/minute
data passed fresh ordinary VPS updates before and after publication: **40
completed candles per interval**, on unchanged revisions. Its existing verified
handoff compares **1,046 exact native candles across five completed sessions**.
NAB's first listing date is **October 9, 2020**, corroborated by the
[bank's original UPCoM announcement](https://www.namabank.com.vn/hon-389-trieu-co-phieu-cua-nam-a-bank-nab-chinh-thuc-giao-dich-tren-upcom).
Daily/minute ingestion is enabled explicitly; native hourly ingestion is not.

The local publication adds **741 daily candles**, **37,759 minute candles**, and
**five active archive objects / 4,027 rows**, including minute indicator lookback
and reconciled daily 2020/2021/2023 partitions. Every one of the **248 observed
minute dates** matches the daily date set within the one-year window. The
completed-session OHLC audit finds no disagreement above its explicit **1%
review threshold**. This does not certify a complete exchange calendar or every
provider's lifetime adjustment policy.

The invalid **2022** original CSV and failed pinned-VPS recovery remain unresolved.
The exact original bytes are preserved locally and in S3 at
`archive-v2/evidence/legacy-daily/48f16ba9cc8819541f7d31d4db0200b46c76a2caa6e0bdaf141f8825a585d376.csv`.
A seventh typed unavailable range blocks dated daily/weekly reads requiring that
year. Recent SMA/EMA indicators work, and raw daily reads at the retention floor
work. Early daily SMA/EMA requests that require the missing 2022 lookback return
HTTP 503 instead of computing indicators over a skipped year. NAB was initially
held back pending these explicit, recoverable historical-error guards.

The running API passes **15 NAB HTTP cases**: **ten successful recent/cold/boundary
reads**, **three 2022 rejection cases**, and **two early daily indicator-lookback
rejections**. Six actual SDK cases cover daily/minute/15-minute SMA and EMA with
exact candles/indicators and API provenance. An isolated browser verifies the
unchanged public daily and 15-minute charts plus NAB's volume profile; page,
network, and blocked-write error lists are empty. The rebuilt wheel embeds all
59 selected VN entries and its actual packaged CLI initializes the watchlist.
NAB's packaged bootstrap queues only daily/minute jobs with the configured floors.
At this checkpoint the implementation suite remained **234 passed**; lint/format checks passed across
49 files. This checkpoint changes the watchlist/data/docs, not API implementation.

The earlier NAB checkpoint database contained **5,684,274 candles**, **207 series**,
**137 source checks**, **719 quality rows**, **1,238 import receipts**, **58
handoffs**, and **459 jobs**. VN retention comprises **43,623 daily** and
**3,004,784 minute** rows. Fifty-six VN minute handoffs use **48 VPS / eight
DNSE** providers; PLX, SSI, and VNINDEX remain frozen. The two global index
handoffs bring the total to 58. Current S3 reconstruction restores **419 objects /
340,357 rows**, **58 handoffs**, **seven recovery receipts**, and **seven typed
gaps**, with exact metadata and a functioning restored gap guard. Of those
objects, **410 are published / nine pending**. Coherent VN daily archives contain
**63,001 rows / 271 objects across 58 tickers**.

A new populated backup and restored copy preserve all counts, exact NAB candles,
every quality/source-check row, and archive/adoption/recovery/gap metadata. Schema
version remains **2** and `quick_check` passes. Both files measure **856,694,784
bytes**, SHA-256
`f805d6e16807cfb04f5ec8927eed5447286b5d2477d8925b9e3dc7b38f36cbd1`.
The before-publication backup remains preserved independently. A full indexed
comparison against it verifies **all 5,645,774 original candles** match exactly,
including prices, volumes, providers, revisions, and update timestamps; **zero
original candles changed**.

Evidence: `data/nab-recent-publication-preflight-20261003.json`,
`data/nab-recent-publication-20261003.json`,
`data/nab-recent-http-verification-20261003.json`,
`data/sdk-nab-recent-parity-20261003.json`,
`data/web-nab-recent-publication-20261003.json` and its screenshots,
`data/nab-wheel-watchlist-20261003.json`,
`data/nab-recent-index-restore-20261003.json`,
`data/nab-recent-backup-restore-20261003.json`, and
`data/nab-original-candle-preservation-20261003.json`.

## Latest alternate-provider missing-year preflight

Read-only native probes reproduce every original date in the **six preceding
missing years** on VNDirect: CTR 2019, HCM 2020, VIB 2019, VND 2020, and VTP
2019/2022. The raw originals and every native JSON response are preserved.
The complete retained daily date sets also match on VNDirect. That does not
establish identical prices or volume conventions:

| Ticker | Retained rows | Changed OHLC rows | Changed volumes | Maximum relative OHLC difference |
| --- | ---: | ---: | ---: | ---: |
| CTR | 747 | 617 | 691 | 0.0005611745513866232 |
| HCM | 747 | 645 | 693 | 0.0008160410352063418 |
| VIB | 747 | 686 | 730 | 0.002400768245838668 |
| VND | 747 | 584 | 664 | 0.00009351912466099317 |
| VTP | 740 | 641 | 584 | 0.0001515610791148833 |

Changed OHLC rows use the explicit **1e-7 relative representation threshold**.
DNSE reproduces the HCM/VND historical dates but fails on CTR/VIB/VTP 2019 invalid
OHLC and conflicting VTP 2022 candles. Its retained VTP page omits one published
date, October 13, 2023. Neither provider licenses silently relabeling historical
candles under the current VPS revision. No main candle, provider revision, source
check, quality record, job, or import receipt changed during these probes.

Evidence: `data/history-gap-alternative-preflight-20261003.json` and the immutable
original/native captures under `data/history-gap-alternative-preflight-20261003/`.

## Latest unavailable-history read guards

Six known failed legacy daily years previously returned **HTTP 200 with zero
rows**: **CTR 2019, HCM 2020, VIB 2019, VND 2020, VTP 2019, and VTP 2022**.
Their existing import-failure findings and absence of usable archived objects
were checked before publishing typed `history_unavailable` ranges in the
existing quality table. No candle, series, provider check, or job changed.
The S3 pointer before metadata publication is retained separately.

Reads requiring a known unavailable range now return **HTTP 503** with a clear
reason. Directional limits check only their required native range; recent
requests remain available when their indicator lookback is present. Broad
history and SMA/EMA lookback cannot skip a missing year. The gap is scoped to
source, ticker, and native interval. Operational status and additive
`/health.storage.history_gaps` metadata expose the records.

The running local API passes **18 historical rejection cases** across daily
JSON, daily CSV, and weekly JSON, plus **ten exact recent SMA/EMA cases** for the
five affected tickers. Independent FPT 2017 cold history and PNJ 15-minute EMA
reads pass. The actual SDK reproduces VTP's recent SMA and EMA candles and
indicators with no differences. This proves error behavior and continued recent
reads; the six missing years remain unavailable.

Failed older-only daily imports automatically persist bounded evidence, excluding
already published current-basis archive ranges. Metadata publication uses the
existing global archive-writer lease. S3 manifests carry an optional range list;
restore validates the entire list before changing the target and merges it.
An older manifest without the field cannot erase newer local observations.
Only a complete verified dated recovery resolves its corresponding year;
incomplete recovery and recovery of another year preserve the marker.

A fresh S3 index restores **414 objects / 336,330 rows**, **57 handoffs**, **seven
recovery receipts**, and all **six unavailable ranges**, with exact metadata.
Reads against the reconstructed index still reject the missing years. A populated
SQLite backup/restore retains **5,645,774 candles**, **205 series**, **135 source
checks**, **716 quality rows**, **1,220 import receipts**, **57 handoffs**, and
**459 jobs**. Every quality/source-check row and archive/adoption/recovery record
matches. Schema version remains **2**; `quick_check` passes. Backup and restored
files share SHA-256
`d1a6d1dd2f93566548e31c8b77f08c02ad3078c76bc5e5e54c6a97ba349c08f7`
and measure **851,214,336 bytes**.

At this checkpoint the full API suite passed **234 tests**. Twenty added regressions cover
range selection, indicator lookback, HTTP errors/health metadata, malformed
manifest evidence, backup/index reconstruction, import failures, and scoped
successful/failed recovery. Ruff lint/formatting checks pass across **49 files**.
The distribution builds offline; an unpacked-wheel smoke verifies the actual
packaged CLI, HTTP health/read behavior, and index-reconstructed read guard
without network access.

Evidence: `data/history-gaps-publication-20261003.json`,
`data/history-gaps-before-manifest-pointer-20261003.json`,
`data/history-gaps-http-before-20261003.json`,
`data/history-gaps-http-verification-20261003.json`,
`data/history-gaps-index-restore-20261003.json`,
`data/history-gaps-backup-restore-20261003.json`,
`data/sdk-vtp-history-gaps-parity-20261003.json`, and
`data/history-gaps-wheel-smoke-20261003.json`.

## Latest retained minute/daily basis audit

The full local comparison covers **14,384 completed date partitions across all
58 VN series**. It exposes **12 interval-basis findings / 1,537 dates** above an
explicit **1% OHLC review threshold**: BSR, CTG, GAS, GEE, MWG, SHB, TCB, TPB,
VHM, VND, VN30, and VNINDEX. Nine stock findings include older close differences;
TPB differs on September 28–October 1. The two indices have high/low differences
while their compared closes remain within the threshold. OCB, PNJ, and DGC have
no differences above this threshold. Most session volumes differ from their
daily provider totals; volume disagreements are not treated as price factors.

The existing `aipa-api audit` now records `audit_interval_basis` observations
with dates, minute counts, both OHLC arrays, daily provider identity, and maximum
relative difference. Only completed local sessions of active tickers participate.
Materialized session aggregates and indexed timestamp lookups prevent SQLite
from reordering this into multiplicative whole-series scans. The measured main
rehearsal takes **10.922 seconds**; its **134,135,808-byte peak RSS includes
separate selected-candle checksum verification**, not just the SQL audit.
No candle, series, source-check, or job counts change. VHM/TPB before/after
checksums and states remain exact. Findings require adjustment/session review;
the audit does not infer scaling, identify a dividend, or queue a repair.

Dated native probes provide stronger evidence than cross-interval arithmetic.
For VHM on July 6, VNDirect and DNSE both return **225 existing timestamps** with
changed prices; their aggregated prices match their fresh daily candles.
VPS returns no minutes for that date. VNDirect preserves the compared volumes;
DNSE changes two minute volumes while retaining the same daily total. On the
oldest retained October 3, 2025 session, no selected provider supplies the
required minutes. This blocks a complete source-backed yearly replacement.

For TPB on September 28, VPS reproduces all **224 published minutes** but their
prices disagree with its own daily series. VNDirect returns adjusted minute
prices with small daily rounding differences and one volume change. DNSE
changes all 224 minute prices, preserves the compared minute volumes, and its
aggregate exactly matches its own daily OHLCV. On October 2, DNSE and VPS both
reproduce the complete current minute/daily session. These observations do not
establish each provider's lifetime adjustment convention.

Isolated complete-window DNSE recovery attempts stage **14,005 VHM minutes in
nine pages** and **13,491 TPB minutes in eight pages**, reaching July 6, 2026.
Both stop on invalid/missing arrays before the requested October 3, 2025 floor.
Neither replacement publishes; the original **55,897 VHM** and **54,557 TPB**
rows remain unchanged. Staging and durable errors remain available for review.
The main database's ready states and jobs are unchanged.

At this preceding checkpoint, the full suite passed **214 tests**. Three new regressions cover unfinished and
inactive observations, unchanged published records/state/jobs, independent
revision findings surviving audit resolution, rounding tolerance, and high/low
disagreements with matching closes. Ruff lint/format checks pass across 48 files.
The distribution builds offline; an unpacked-wheel check invokes the actual
packaged CLI and obtains the expected review-only finding without network access.

A populated backup/restore preserves every quality and source-check row,
including all **12 basis findings**: **5,645,774 candles**, **205 series**, **135
source checks**, **710 quality rows**, **1,220 import receipts**, **57 handoffs**,
and **459 jobs**. It also preserves exact archive/adoption/recovery metadata and
the selected VHM/TPB candles. `quick_check` passes. The backup is **851,214,336
bytes**, SHA-256
`4fdf7e9709d890c5b78b84b8911527c74139e5d842c2038893a7431667e78800`.

Evidence: `data/vn-minute-daily-basis-audit-20261003.json`,
`data/vn-minute-daily-operational-audit-20261003.json`,
`data/vn-minute-basis-provider-preflight-20261003.json`, its dated provider-minute
captures, `data/minute-basis-retained-recovery-preflight-20261003.json`,
`data/minute-basis-audit-wheel-smoke-20261003.json`, and
`data/minute-basis-audit-backup-restore-20261003.json`.

## Preceding OCB/PNJ/DGC selected-universe expansion

OCB, PNJ, and DGC are now published locally; the watchlist contains **58 VN
tickers**. Each addition has **747 recent daily candles** and **248 observed
minute date partitions**, exactly matching the daily dates in the one-year
minute window. OCB retains **43,332 minutes**, PNJ **49,705**, and DGC **36,148**.
These date checks do not establish a full exchange-calendar or every-candle
corporate-action audit. The earlier daily comparison still records ten OCB and
six PNJ volume differences against the old API; DGC volumes match that capture.

PNJ passes the default handoff with **1,114 exact completed minutes across five
sessions**. OCB's **746** and DGC's **85** recent native observations pass the
existing explicit complete-session path: all original minute timestamps and
OHLCV match, and aggregated sessions exactly reproduce fresh and retained VPS
daily OHLCV, including total volume. Each series subsequently passes ordinary
**40-candle daily and minute updates**, with unchanged revisions. The default
1,000-candle criterion and provider validation were not relaxed.

Before handoff, isolated captures archived **4,537 OCB**, **3,719 PNJ**, and
**4,818 DGC** minute warm-up rows from September/early October 2025. Cold daily
reconciliation reproduces original legacy date sets: OCB's 2021–2023 partitions
contain **666 rows**, PNJ's 2019–2023 partitions **1,186**, and DGC's **1,180**.
OCB's configured lower bound comes from its own [listing account](https://ocb.com.vn/en/news-events/news/en-ket-qua-thanh-tuu-ocb-nam-2021).
NAB's isolated lower bound follows its [first UPCoM session](https://www.namabank.com.vn/hon-389-trieu-co-phieu-cua-nam-a-bank-nab-chinh-thuc-giao-dich-tren-upcom);
its later HOSE transfer is not treated as the start of its history.

NAB also passes recent daily/minute checks and a **1,046-minute/five-session**
VPS handoff. Its original 2022 CSV contains invalid OHLC, and recovery through
the pinned VPS provider also rejects invalid OHLC. At that checkpoint NAB remained isolated;
its original bytes are preserved at
`data/legacy-recovery-downloads/NAB-1D-2022-48f16ba9cc8819541f7d31d4db0200b46c76a2caa6e0bdaf141f8825a585d376.csv`.
No guessed correction or provider mixing was applied.

The three passing datasets and their metadata were inserted atomically after a
populated before-backup and immutable object upload/readback. The selected
intervals are explicitly daily/minute; native hourly history is not claimed.
There are **33 passing HTTP checks** across recent daily/minute/15-minute reads,
cold daily reads, and daily/minute retention boundaries. **18 SDK cases** compare
20 rows each with both SMA and EMA; timestamps, OHLCV, and MA10/20/50/100/200 all
have zero differences and report API provenance. The watchlist transaction
tests still pass (**two tests**). The updated distribution builds offline and
its packaged watchlist includes exactly the three new daily/minute entries,
with NAB excluded. The unchanged public PNJ daily/15-minute charts and volume
profile pass with local API routing and no page/network/write errors. Selecting
a replacement chart does not automatically select its separate details panel;
the browser rehearsal now clicks the normal chart interaction surface before
requiring the selected symbol's profile response. It preserves the original
assertion. Earlier failed attempts are retained for review.

The authoritative local database now contains **5,645,774 candles**, including
**42,882 VN daily rows** and **2,967,025 VN minute rows**. It preserves **135
source checks**, **1,220 migration receipts**, and **57 handoffs** (55 VN and two
global). Fifty-five of the 58 VN minute series have ongoing verified providers:
47 VPS and eight DNSE. PLX, SSI, and VNINDEX remain frozen.

Fresh S3 index reconstruction preserves exact metadata for **414 objects /
336,330 archived rows**, **57 handoffs**, and **seven recoveries**. The index
contains **405 published / nine pending objects**. Coherent VN daily archives
contain **62,506 rows in 268 objects across 57 tickers**. A populated backup and
separate SQLite restore pass `quick_check` and exact selected-candle/metadata
comparisons: **849,293,312 bytes**, SHA-256
`b7442863fa6a9fbdd48516e60dd7de318bf8658b7a30484c13ac25d0ac4af3ef`.
Existing historical gaps and production cutover requirements remain open.

Evidence: `data/expanded-vn-minute-preflight-20261003.json`,
`data/expanded-vn-sparse-preflight-20261003.json`,
`data/expanded-vn-publication-preflight-20261003.json`,
`data/expanded-vn-daily-history-preflight-20261003.json`,
`data/expanded-vn-nab-2022-recovery-20261003.json`,
`data/expanded-vn-publication-20261003.json`,
`data/expanded-vn-http-verification-20261003.json`,
`data/expanded-vn-index-restore-20261003.json`,
`data/expanded-vn-backup-restore-20261003.json`, and the three
`data/sdk-{ocb,pnj,dgc}-expanded-parity-20261003.json` reports, plus
`data/web-pnj-expanded-minutes-selected-surface-20261003.json` and
`data/expanded-vn-wheel-watchlist-20261003.json`.
Earlier sections below retain the counts at their respective checkpoints.

## Completed checks

- Python API suite: **240 passed**, covering HTTP contracts, archive boundaries,
  deduplication, checksum failures, atomic sync writes, retention arithmetic,
  backup/restore, lease recovery, provider switches, interrupted repairs,
  independent archive repair checkpoints, bootstrap visibility, and concurrent
  SQLite updates, and bounded explicit refresh passes. A checked-in compressed FPT fixture reproduces SMA/EMA
  parity across an archive boundary offline. Starlette emits a test-client deprecation warning; tests pass.
- Added recovery checks restrict closure exclusions to the reviewed FPT 2018
  dates, require flat zero-volume original/provider placeholders, preserve the
  immutable original snapshot, and reject missing ordinary dates or rehashed
  evidence that expands the scope. Publication tests reject a different candidate
  targeting a superseded object, preserve its manifest pointer, allow the same
  candidate retry, and retire only obsolete unleased archive work.
- Daily archive repair includes the complete final date for a provider using
  a 02:00 UTC session timestamp. Four additional regressions preserve overlapping
  rollover fragments and recent data, restart only explicitly scoped failed
  staging, and honor live leases. Ordinary retry caching remains covered.
- Eight source-check regressions cover atomic publication, stale attempts,
  interrupted updates/lease release, completed rechecks after a provisional
  candle, later import invalidation, provider-switch staging, frozen VN minute
  handoff guards, and schema-upgrade/backup preservation. A clock advance alone
  does not finalize a provider observation; failed updates preserve the dated
  earlier success. Schema version 2 is additive and future versions are rejected.
- Real bounded provider updates publish 40 FPT daily rows, 40 FPT minute rows,
  and 123 BTC minute rows without changing their revisions. The BTC update
  records 122 completed and one provisional returned candle. GEG's legacy
  minute snapshot remains unchanged and does not call an upstream or queue an
  unverified provider switch. Evidence is in
  `data/source-check-live-updates-20261003.json`. A populated backup/restore
  preserves all four source-check records and the candle/archive/adoption data;
  its integrity/count evidence is in
  `data/source-check-backup-restore-20261003.json`.
- The populated `/health` returns 199 per-series records, exposing stale AAPL
  hourly history separately from successful FPT updates and the frozen GEG
  snapshot. Three loopback reads take 4,239.96, 2,200.39, and 2,198.09 ms;
  the response is 135,970 bytes. The source-check fields supplement existing
  aggregate health contracts and do not assert whole-window coverage or a
  freshness SLA. Evidence is in `data/source-check-health-smoke-20261003.json`.
- Further read-only VPS paging returns 855 exact legacy minute matches for
  VPL, 705 for HHS, and 225 for GEG, then no older page for each. Every timestamp
  in their five-session legacy windows is present, with no extras; each day's
  aggregated minute OHLC matches its retained daily candle. These observations
  do not satisfy the existing 1,000-candle handoff rule, which remains intact.
  Evidence is in `data/remaining-minute-page-probes-20261003.json` and
  `data/sparse-minute-complete-session-probes-20261003.json`.
- The revision detector now requires three completed matching candles changed
  by more than `1e-6` relative. Worker regressions confirm that a 0.1% change in
  either direction stages recovery while published values remain intact;
  `1e-7` representation noise does not queue a rebuild. This is an explicit
  detection policy, not proof that a revision is a dividend.
- Publication-race checks confirm that a rejected archive replacement cannot
  advance the S3 manifest pointer. A verified local index survives manifest
  failure, and pending objects cannot license pruning. Retrying preserves both
  exported and concurrently corrected rows.
- Compaction regressions preserve corrected candle values and provenance,
  reject concurrent pending-repair changes before advertising a candidate, and
  preserve a verified readable local index if manifest publication fails.
- Existing SDK offline suite: **251 passed**, with four live S3 fundamental
  tests excluded. New checks cover coherent API ranges, server-warmed MAs,
  whole-series archive fallback, explicit date/limit behavior, large minute
  pagination, interrupted pages, empty aggregation, and visible HTTP 503 errors.
  Older mock URLs were corrected to include the existing `ema=false` parameter;
  the prompt-only test explicitly disables its default reference ticker.
  Sparse API indicators remain missing values: undefined volume change after
  zero volume and an EMA before its seed no longer trigger archive fallback.
  SJC requests retain the existing Yahoo API mode; an actual preserved SJC
  daily snapshot matches SDK candles and both SMA/EMA output.
- Sparse replacement regression checks cover VN hourly/minute, crypto minute,
  and global minute data. Even when a replacement reaches the saved floor and
  published latest candle, an omitted completed timestamp prevents publication.
  The transaction rolls back, original values/versions remain readable, and the
  durable job stays pending with a dated coverage finding. The check uses indexed
  timestamp lookups and excludes known invalid VN weekend daily observations.
- Provider normalization rejects conflicting duplicate candles inside the
  requested range while accepting identical duplicates and ignoring excess old
  conflicts. Crypto daily/hourly/minute pages must be contiguous; regression
  checks reject a missing middle candle without fabricating its value.
  Small live checks after the change returned 40 clean candles each for four
  Binance minute series, FPT daily on VPS, and MBB minute on DNSE.
- VN daily parsing now admits the observed UTC-midnight and DNSE session-start
  conventions and rejects unverified offsets, including pure Vietnam-midnight
  replies that would move a market date backward if floored. Raw DNSE MBB/SHS
  probes show a 02:00 UTC session marker on their recent daily bars; normalizing
  it preserves the date. Live VPS FPT, VNDirect VNINDEX, and DNSE SHS/MBB probes
  pass. Unrelated unrequested old timestamp conventions remain ignorable. This
  output follows the daily UTC-date convention described in
  [TradingView's time documentation](https://www.tradingview.com/charting-library-docs/latest/connecting_data/time-and-sessions/),
  rather than assuming every upstream already conforms. Raw evidence is in
  `data/dnse-daily-raw-timestamp-diagnostics-20261003.json`.
- Ruff lint/format checks pass. Source distribution and wheel build offline
  using the locked dependencies. An isolated unpacked-wheel smoke check passes
  initialization, the packaged 55/4/7 watchlist, compaction dry run, and recovery
  command parsing without modifying the main database or watchlist.
- The existing, unchanged `../aipriceaction/scripts/test-api.mjs` suite passes
  **219 of 220 assertions** against a running FastAPI instance using public
  legacy snapshots. Its remaining assertion requires
  `x-data-source: redis-snap`; the replacement reports `sqlite+s3`. Refresh
  authorization success paths are covered by Python tests; the JavaScript
  success cases were skipped because its refresh secret was unset.
- Eleven public legacy native-candle requests produced **9,959 candle
  comparisons** with identical timestamps and OHLCV. These include daily,
  hourly, minute, indexes, crypto, gold, and historical date ranges. Historical
  FPT daily and VIC minute fixtures were exported to Parquet and pruned locally
  before checking the API reader.
- FPT daily/weekly/fortnightly/monthly SMA and EMA comparisons pass the recorded
  relative tolerance of `1e-3`. Most numeric differences are floating-point
  noise; weekly EMA's maximum observed absolute difference was
  `0.009350771360914223` in an MA value. Full earlier history is essential:
  the initial short-history comparison correctly exposed missing warm-up.
- A completed-date FPT minute volume profile matches the legacy envelope and
  numeric output at relative tolerance `1e-6`, absolute tolerance `1e-8`.
  Both RRG algorithms match sampled FPT/VCB results and two-point trails at
  those tolerances. Analysis universes differ until migration is complete, so
  full-market rankings/sector totals are not claimed identical.
- A separate concurrency check completed **48 concurrent writes/reads** with
  six threads and retained exactly one unique candle.

## RustFS integration

The Compose service started with its default runtime user and fresh named
volumes. S3 health and `/rustfs/console/health` return 200; the console UI at
`/rustfs/console/` also returns 200. The console port's bare `/` returns 403,
so the README links the correct UI path.

The tested image is pinned to:

```text
rustfs/rustfs@sha256:8cc9801755448b71a786705ce76692c77e14936cccd87cf2fc31842e58f4d1ff
```

An isolated synthetic fixture verified upload/download checksums and candle
values, Parquet publication, pruning, manifest discovery, and indicator
lookback across **240 archived + 120 local rows**. After restarting RustFS and
discarding the cached object, an S3 byte-range read returned **206** and the
Parquet `PAR1` signature. A fresh SQLite archive index restored all 240
archived rows. Boundary queries retained 120 output rows with identical values.

For this small fixture, a cold boundary query took **18.33 ms** and a cached
query took **9.23 ms**. These are storage-proof measurements, not throughput,
memory, or transfer-cost evidence for the complete maintained universe.
A final recheck after strengthening manifest recovery measured 22.43 ms cold
and 12.99 ms cached and passed the same persistence/coverage checks.
The runtime uses boto3 transfers followed by local DuckDB reads; it requires
no runtime `httpfs` extension installation.

## Live VN provider probes

Exactly VPS, VNDirect, and DNSE were probed with small public FPT requests and
a VNINDEX daily request. Direct access was explicitly enabled for the probes.
DNSE requires `1D`/`1H` resolutions; VPS/VNDirect use `D`/`60`. Native minute
resolution is `1` for all three. Daily timestamps and stock/index price scales
were normalized successfully.

| FPT sample | VPS | VNDirect | DNSE |
| --- | --- | --- | --- |
| Recent daily | Returned data | Returned data | Returned data |
| Near three-year daily floor | Returned data | Returned data | Returned data |
| Recent hourly | Returned data | Returned data | Returned data |
| Near three-year hourly floor | No data | Returned data | Returned data |
| Recent minute | Returned data | Returned data | Returned data |
| Near one-year minute floor | No data | Missing OHLCV arrays | Missing OHLCV arrays |

VNINDEX recent daily data also returned from all three. These samples establish
availability at the tested points; they do not establish uninterrupted history
for every ticker or prove each provider's adjustment policy. The raw local
probe report is in ignored `data/provider-probes.json`; rerun
`scripts/probe_providers.py` to refresh it.

The main development SQLite database completed bounded daily bootstrap for all
**55 selected VN daily series**, retaining **40,699 rows**. Most series cover
**2023-10-02 through 2026-10-02**, reflecting the saved jobs’ original UTC floor.
VPL has 347 rows from **2025-05-13 through 2026-10-02**, using a listing date
verified in the [regulator’s announcement](https://ssc.gov.vn/webcenter/portal/ubck/pages_r/l/chitit?dDocName=APPSSCGOVVN1620154820).
Saved job status proves configured floor traversal; different row counts still
require a holiday/suspension/no-trade audit. The main SQLite file measured
**8,597,504 bytes** at that earlier stage. Subsequent populated runs below expand
other markets and intraday coverage. RustFS remains running locally.

Live bootstrap exposed invalid old candles outside requested pages: VPS
returned 1,027 rows for a 500-row VNINDEX request, including a bad 2021 bar;
VTP likewise included bad 2022 bars. Validation now checks the final requested
page rather than unrelated excess history. Invalid requested candles still fail.
Regression tests exercise both cases; no OHLC values were clamped or invented.

## Real migration and archive reconciliation

The public legacy S3 archive contains **headerless six-column CSV**. Daily/hourly
files are yearly; minute files are per UTC day. Read-only probes verified these
paths; a yearly minute URL returned 403, while dated minute objects returned 200.
The importer now accepts actual legacy files plus named local/API exports and
validates date bounds, OHLCV, volume integers, and conflicting duplicates.
Checksummed downloads and SQLite period receipts make interrupted runs resumable.
Resume fills missing rows without overwriting newer published corrections.
Empty API replies and unavailable S3 files are retried and never prove coverage.

Five explicit FPT daily files for 2019–2023 produced **1,185 older archived
candles**; 65 recent source rows were skipped to preserve the live SQLite window.
Two 2025 minute files produced **453 archived candles** in one monthly partition.
Before reconciliation, historical HTTP responses matched cached source CSV OHLCV
exactly: **1,001 daily rows** and **453 minute rows**. Cold/warm query times were
**127.98/41.62 ms** daily and **18.92/13.58 ms** minute. These are small local
measurements, not full-universe transfer-cost or throughput evidence.

Imported daily history initially had an independent legacy revision. A query
crossing that revision boundary returned 503 while independent historical and
recent requests remained readable. Archive-only reconciliation then re-fetched
all five old daily partitions from the current **VPS** provider, verifying exact
timestamp sets and publishing immutable replacement objects. Recent SQLite
candles were not rebuilt. All old/current daily partitions now share the same
provider/revision, pending counts and related quality findings are zero, and
the original immutable objects remain retained. An API query crossing
2023-09-01–2023-11-01 returned **42 candles with MAs**, and a weekly query returned
20 candles. Cold boundary latency was **129.38 ms** in that check.

## Wider daily archive migration

The bounded daily-history runner imported and reconciled **25 annual partitions**
for VCB, MBB, VIC, VHM, and HPG, preserving **5,930 older daily rows**. Each
replacement uses the currently pinned VPS provider and exactly reproduces the
legacy partition's timestamp set. Recent SQLite candles are preserved. Each of
the five HTTP historical checks returns **502 candles** for 2019–2020; each
retention-boundary check returns **15 candles** with warmed EMA200. These checks
took **21.56–33.77 ms** with the local object cache already populated.

Archive publication now rechecks completed retained OHLC prices immediately
before publishing a historical replacement. A changed retained basis schedules
recent recovery first; insufficient overlap blocks publication. Regression
checks preserve old data in both cases, scope repairs to the requested series,
and resume a completed staged download after a temporary head-check outage.
Small partitions request their remaining indexed candle count, avoiding an
unrelated preceding range. A regression check verifies that an invalid older
bar outside a one-candle partition does not prevent its valid replacement.

The first broader run completed all 55 configured tickers. Eight encountered
invalid legacy files, and five retained pending archive coverage disagreements.
That checkpoint contains 216 published VN daily objects with 50,743 rows plus
10 pending objects with 2,427 rows; publication alone is not proof that every
independent legacy basis can be joined to recent data. Its report is
`data/migration-vn-daily-history-universe.json`. The resumable follow-up also
completed all 55 tickers and reports to `data/migration-vn-daily-history-resume.json`.
That follow-up had **242 published daily objects / 56,721 rows across 54 tickers** whose
provider and revision match their ready recent series. VPL's configured listing
date excludes this older range. **11 pending objects / 2,668 rows across eight
tickers** and **11 invalid source years** were recorded. All 55 recent daily
series remain ready. These counts establish available coherent objects, not
complete older-year coverage. Invalid CSV years
and timestamp-set disagreements remain explicit. The runner now isolates each year,
so one corrupt input does not block valid later years. A regression check proves
the invalid first year's rejection and the valid next year's archived import.
CSV validation errors include source, symbol, row, and timestamp.

Read-only diagnostics preserved checksummed raw 2019 CSVs for VNINDEX, VN30,
and VIB in `data/legacy-quarantine/`. The indexes contain opens outside the
reported high/low range. VNDirect returns valid old candles with exactly the
same 250-date sets for both indexes. Bounded recovery has now published VNINDEX
2019/2021 and VN30 2019/2020 from their pinned VNDirect provider after matching 40 completed
retained OHLC candles per index. The original CSVs and checksummed recovery
records are preserved in RustFS. The resulting coherent daily archive has
**247 objects / 57,725 rows across 54 tickers**, following rollover/compaction.
The 11 pending objects remain; seven recorded invalid years remain unresolved.
Only the four verified
per-year findings were resolved; generic and other-year findings remain visible.
Actual HTTP reads for June 24–July 1, 2019 return six valid candles per index,
with unchanged recent SQLite data. Evidence is recorded in
`data/recovered-index-http-check.json` and `data/recovery-index-restore-check.json`.
A fresh SQLite index restored and verified **389 objects / 318,910 archived
rows**, all **43 provider handoffs**, and all four recovery records, with matching
archive IDs and an `ok` SQLite integrity check. Corrupt raw/evidence regression
checks fail before partially restoring an index.
The sampled old VPS page for VIB also fails validation. Narrowed requests for
exactly 250 candles still fail on VPS/DNSE but return valid VIB history on
VNDirect; changing its pinned daily basis remains a separate verified operation.
No values were clamped,
dates guessed, or questionable rows published. Public legacy ticker metadata
was read successfully and contains 601 entries; it has no coverage date bounds.

### Further rejected-year checks and IDC recovery

A subsequent bounded preflight checks the six other rejected daily CSVs against
each ticker's existing pinned provider. IDC 2019 returns valid VNDirect candles
with exactly the original **244 dates**, and its latest **40 completed retained
OHLC candles** match. The recovery publishes that year on the existing revision
and replays its original CSV, proof, and Parquet evidence successfully. Hashes of
all **747 recent IDC rows**, including provenance and update timestamps, are
identical before and after publication; the series remains ready on VNDirect.
VND 2020, HCM 2020, CTR 2019, and VTP 2019/2022 still fail upstream OHLC range
validation on their pinned VPS provider. All six original input files are
preserved with checksums, without modifying their bytes or publishing invalid
candles. Six recorded invalid source years remain unresolved, including VIB
2019; the 11 pending historical objects remain.

VIB's separate read-only VNDirect preflight reproduces every existing archive
date, all 250 rejected 2019 dates, and all 747 retained dates in an isolated
repair rehearsal. Timestamp coverage alone does not justify changing the recent
basis. Comparing the public old API's same 747 recent dates, VPS has zero
median relative close difference and a maximum of `0.00003707380570860216`;
VNDirect has median `0.00006715465717547512` and maximum
`0.002400768245838668`. VPS volumes match all 747 observations; VNDirect volumes
differ on 730. The main VIB series and archives remain unchanged. These are
observed provider differences, without inferred corporate-action explanations
or a claim that one source is universally more accurate. Evidence is in
`data/vib-vndirect-whole-series-preflight-20261003.json` and
`data/vib-retained-legacy-provider-comparison-20261003.json`.

Actual IDC HTTP reads return all **244 recovered candles**, exactly matching
the verified Parquet object. Boundary requests for September 25–October 10,
2023 return **12 candles** with SMA200 and EMA200 available. A new S3 index
restore verifies **395 objects / 320,224 archived rows**, **52 provider handoffs**,
and **six recovery receipts**, with exact archive and receipt metadata and an
`ok` SQLite quick check. There are now **384 published / 11 pending objects**;
**253 coherent VN daily objects contain 59,039 rows** across 54 tickers.

A new populated backup and its restored copy have identical SHA-256
`18718b946d3fb649ae232d0015d5d1f4142077b35eca38cff3e2ad9f292bbe88`
and **828,305,408 bytes**. Both preserve **5,513,154 candles**, **12 source checks**,
**1,161 import receipts**, and the same archive/adoption/recovery metadata.
Restored IDC candles match exactly. Reports are
`data/remaining-invalid-daily-originals-20261003.json`,
`data/remaining-invalid-daily-preflight-20261003.json`,
`data/idc-2019-recovery-results-20261003.json`,
`data/idc-2019-http-check-20261003.json`,
`data/idc-2019-index-restore-20261003.json`, and
`data/idc-2019-backup-restore-20261003.json`. No application code changed during
these additional data checks; the previously passing 181-test suite remains
the code validation for that checkpoint. The later explicit-refresh change and
its expanded suite are recorded below.

## Fresh VN rechecks and bounded refresh command

A fresh bounded update passes for all **55 selected VN daily series** and all
**52 adopted VN minute series**. Each rechecks **40 completed candles**, for
**2,200 daily** and **2,080 minute** observations, with no provisional rows,
new timestamps, provider switches, or revision changes. Daily providers are
49 VPS, five VNDirect, and one DNSE; minute providers remain 44 VPS/eight DNSE.
Every daily OHLCV and every minute volume remains identical. On 35 TCB and two
GVR minute candles, multiplying provider prices into VND introduces only
floating-point representation differences, with maximum absolute difference
`3.637978807091713e-12`; all are within the existing absolute `1e-8` handoff
tolerance. This is not an inferred dividend correction.

Actual HTTP checks cover all **107 series**, returning exactly the current
40 retained native candles and reporting `verification_current=true`,
`latest_verification=completed_recheck`, the matching pinned provider/revision,
and the successful dated check. PLX, SSI, and VNINDEX minute health remains
unverified on `legacy-api`. These are fresh overlap checks, not proof of every
candle or calendar date in their retained histories. Reports are
`data/all-adopted-vn-daily-refresh-20261003.json`,
`data/all-adopted-vn-minute-refresh-20261003.json`, and
`data/all-vn-refresh-http-check-20261003.json`.

The new `aipa-api refresh` command requires an explicit source/native interval,
uses optional configured-symbol filters, and refreshes ready series once through
the existing live update path. It bypasses scheduling cooldown while honoring
live leases; it does not consume historical jobs or initialize missing series.
Frozen minute handoffs and revision-repair gates remain active. Eight regressions
cover cooldown/filter behavior with untouched historical staging, a held lease,
independent failures, the frozen handoff guard, uninitialized/repairing series,
connection cleanup, CLI arguments, and rejection of partially matching symbol
filters before fetching. The full suite now passes **189 tests**. A live command
in an isolated database writes 40 FPT minute rows and reports a PLX
`handoff_required` outcome without fetching or changing its snapshot; evidence
is `data/refresh-cli-live-smoke-20261003.json`.
Ruff lint and formatting pass across 52 Python files; the offline distribution
build succeeds. An isolated unpacked-wheel check loads the packaged refresh
method/parser, initializes its own filesystem-backed database, and verifies
that refreshing an uninitialized FPT series returns `not_ready` without
bootstrapping it. Report: `data/refresh-wheel-smoke-20261003.json`.

The populated backup and restored copy have identical SHA-256
`e84daac8c61800d3378847307bcec2c45730cb76bbda22a961f8614ac99d8a1e`
and **828,317,696 bytes**, preserving **5,513,154 candles**, **108 source checks**,
**1,161 import receipts**, **52 handoffs**, and **395 active archive objects**.
All source-check records and archive metadata match exactly; SQLite quick check
is `ok`. Report: `data/all-vn-refresh-backup-restore-20261003.json`.

## SHS historical recovery and daily repair boundary

The six-series pending-date preflight reproduces SHS 2021's **250 original
dates** and SHS 2023's **185 archived dates** on the existing DNSE revision.
The explicit corrupt-CSV recovery publishes 2021 and preserves the original
bytes and checksummed proof. Attempting that path for 2023 correctly rejects
an existing rollover overlap. Ordinary archive repair is the correct path.
Its initial request previously ended one second after normalized midnight,
excluding DNSE's final raw candle at 02:00 UTC. It now includes the full final
date; the exact timestamp-set, fresh retained-overlap, and publication guards
remain unchanged. `archive-repair --restart` resets only unleased failed staging
for an explicitly selected source/symbol/native interval, preserving originals,
published values, and normal retry caching. Four added regression cases pass;
the full suite is **193 passed**, and Ruff lint/format checks pass.

The actual scoped restart publishes the **185-row** 2023 partition, preserving
the superseded legacy object and the existing one-row native rollover fragment.
SHS's **747 recent rows**, including provenance and update timestamps, and its
provider/revision remain identical. Actual HTTP reads return all **250** verified
2021 candles and **184** verified candles from January 4–October 2, 2023.
January 2–5, 2024 returns **four candles with SMA200**. SHS 2022 still fails on
conflicting native candles; requests at the start of 2023 that need a previous
2022 candle still return **503**. Full 2023 availability and EMA200 across that
unresolved boundary are not claimed.

The current active archive index contains **395 objects / 320,224 rows**,
with **386 published / nine pending**. **255 coherent VN daily objects contain
59,474 rows across 54 tickers**. A fresh S3 index restore reproduces every active
object, **52 handoffs**, and **seven recovery receipts**, with exact metadata
and an `ok` SQLite quick check. The populated backup and restored copy have
identical SHA-256
`3cfd4c404f56048260eb93a872d3004d9969745f6705549f1b88dfad99091634`
and **828,317,696 bytes**. They preserve **5,513,154 candles**, **108 source
checks**, **1,162 import receipts**, all archive/adoption/recovery metadata, and
the exact recent SHS rows. Reports are
`data/pending-daily-date-diagnostics-20261003.json`,
`data/shs-2021-2023-recovery-results-20261003.json` (2021 recovery only),
`data/shs-history-http-check-20261003.json`,
`data/shs-history-current-counts-20261003.json`,
`data/shs-history-index-restore-20261003.json`, and
`data/shs-history-backup-restore-20261003.json`.

A read-only decoded-payload capture confirms DNSE's December 27, 2022 conflict
in both the yearly and bounded 20-candle request. The midnight record has close
`6.76` and volume `7,599,200`; the 02:00 UTC record has close `7.44` and volume
`16,687,400`, in the provider's original price units. VPS and VNDirect each return
a single candle with volume `16,691,215`, and differ from DNSE in other OHLC
values. Independent responses therefore do not justify selecting either DNSE
record or mixing providers. Validation continues to reject the year; the main
series and pending object remain identical. Decoded payloads, their SHA-256
checksums, and comparisons are recorded in
`data/shs-2022-conflicting-native-preflight-20261003.json`.

The source distribution and wheel rebuild offline with the repair fix. An
isolated unpacked-wheel smoke check loads packaged code, initializes a separate
filesystem-backed database, parses the scoped restart, rejects missing restart
scope, and preserves an uninitialized series. Report:
`data/shs-repair-wheel-smoke-20261003.json`.

A further whole-series replacement preflight targets all **1,933 existing SHS
daily timestamps**, including **747 retained rows**. Both VPS and VNDirect
reject their first recent 500-candle page with an invalid OHLC range, so neither
licenses replacement. The current DNSE basis differs from the public legacy
API's same recent dates: median relative close difference is
`0.000015163232194526088`, maximum is `0.0004750317495709755`, and volumes
differ on **398** rows. The main series and all archives remain unchanged;
matching dates do not establish perfect historical numerical fidelity.
Report: `data/shs-whole-daily-provider-preflight-20261003.json`.

## Additional VN daily candidates, isolated only

OCB, NAB, PNJ, and DGC each complete an isolated three-year VPS daily bootstrap
in **two pages**. OCB, PNJ, and DGC retain **747 rows** each; NAB retains **741**.
All four have exactly the legacy API's timestamp sets for the requested window.
Maximum relative close differences are respectively
`0.00011708230886309234`, `0.00011943150603133112`,
`0.000020829767756747053`, and `0.000013404646050374502`.
Volumes differ on **10 OCB**, **one NAB**, **six PNJ**, and **zero DGC** rows.
These are observed differences, without an inferred adjustment factor or a
claim that matching dates prove perfect data. The main watchlist remains **55**
VN tickers; minute coverage, historical migration, and ordinary provider
verification must be addressed before claiming expanded coverage. Evidence is
`data/expanded-vn-daily-preflight-20261003.json` and the isolated database
`data/expanded-vn-daily-preflight-20261003.sqlite3`.

## Fresh crypto and global daily updates

All **12 selected crypto series** (four symbols each on daily/hourly/minute)
and all **seven global daily series** pass bounded live updates with unchanged
providers/revisions. Crypto daily updates publish **164 observations**, adding
**four retained rows**; hourly updates publish **196**, adding **36**; minute
updates publish **1,314**, adding **1,154**. Each daily/hourly check has one
provisional observation. Minute checks contain respectively one provisional
BTC, one ETH, zero SOL, and one BNB observation; the SOL minute finalized
while requests were progressing. Clock passage alone does not rewrite that
recorded finality. Corrections to the previously open daily/hourly candles
are retained as actual provider updates, without an inferred dividend factor.

SQL checks verify all **12 entire retained crypto windows**, using unique
timestamps, alignment, count/span equality, and pinned provider/revision.
All are continuous; their **2,104,161 minute rows** include the elapsed gap
filled by this pass. Global daily updates recheck **280 completed candles**,
with no new dates or queued revision repairs. Each stock/index changes one
stored tail candle and gold changes three; these are measured provider updates,
not proof of lifetime adjustment semantics. SJC's official request still fails;
its **1,096-row** retained snapshot and revision remain unchanged, with a dated
failed source check.

Actual HTTP reads reproduce exactly the latest **40 stored candles for all
20 series**, including SJC, and health reproduces the dated success/finality
records for the 19 successful series. SJC remains unverified with its recorded
error. The new populated backup and restored copy have identical SHA-256
`c95175c1ad19ad478df59a156303f432e4a0c412071b119dfbf1ba469b8b2299`
and **828,456,960 bytes**. They preserve **5,514,348 candles**, **127 source
checks**, **1,162 import receipts**, **52 handoffs**, all **395 active archive
objects**, and every recovery receipt, with an `ok` SQLite quick check.
Reports: `data/cross-market-live-refresh-20261003.json`,
`data/cross-market-refresh-http-check-20261003.json`, and
`data/cross-market-refresh-backup-restore-20261003.json`.
No application code changed; the passing **193-test** suite remains the
implementation check for this checkpoint.

## Global minute precision and JSON migration

Read-only Yahoo minute probes return **1,951 shared timestamps across five UTC
date partitions** for each of AAPL, MSFT, NVDA, SPY, the S&P index, and the Dow
index. None exactly match the CSV-imported OHLCV; all six snapshots contain
**four extra timestamps** inside the probed bounds. Gold's 2,000 returned native
minutes do not overlap its stale imported snapshot. The main series and values
remain unchanged. These differences must be understood before a live handoff.
Evidence: `data/global-minute-native-handoff-preflight-20261003.json`, with
checksummed native captures and individual field comparisons.

For SPY's September 28 session, the old API's JSON preserves native precision:
all **390 shared candles** match native Yahoo OHLCV exactly, while none match
the CSV-imported prices exactly. The importer now supports explicit
`--from-api --api-format json`, preserving original response bytes, checksums,
capture timestamps, and full numerical precision. Existing CSV receipts retain
their default behavior. JSON validates the requested symbol, OHLCV, dated
bounds, conflicting timestamps, the 10,000-row truncation guard, and empty-response
retries. Reusing a published period receipt after changing formats is rejected
with a requirement for a new snapshot revision and separate migration database.
Nine additional regressions pass; the full API suite is **202 passed** and Ruff
lint/format checks pass.

The actual CLI imports an isolated **1,955-row** SPY JSON snapshot covering
September 28–October 2 in **two** bounded monthly requests. At all **1,951**
native/shared timestamps, every OHLC price matches exactly. **1,949** rows also
match volume; **two volumes differ** and **four extra legacy timestamps** remain.
Precision preservation therefore resolves a migration error without licensing
an automatic provider handoff or declaring full-year coverage. The main snapshot
remains intact. Reports: `data/spy-legacy-json-precision-comparison-20261003.json`,
`data/spy-json-native-minute-comparison-20261003.json`, and the separate database
`data/global-json-minute-rehearsal-20261003.sqlite3`.

The distributions rebuild offline with the JSON importer. An isolated
unpacked-wheel smoke check executes the packaged CLI with a replayed original
API JSON response, imports all **391 session rows**, and preserves every OHLCV
value exactly. The request uses `format=json` and writes only its temporary
filesystem-backed database. Report:
`data/json-migration-wheel-smoke-20261003.json`.

## Global JSON recapture and verified index minute handoffs

A wider isolated JSON recapture preserves **340,023 candles** across AAPL,
MSFT, NVDA, SPY, the S&P index, and the Dow index. Every series preserves its
existing timestamp set and every volume; every original CSV price equals the
rounded JSON price. The requested year begins October 3, 2025, but available
snapshots begin March 9, 2026. Five older empty monthly batches per series remain
retryable and do not establish a complete year. Native five-date comparisons
contain **1,951 candles each**: **1,942 AAPL**, **1,589 MSFT**, **461 NVDA**,
**1,949 SPY**, and all **1,951 candles for each index** match OHLCV exactly.
The stock disagreements remain uncorrected; their full-precision snapshots
stay isolated. Reports: `data/global-json-minute-year-recapture-20261003.json`
and `data/global-json-csv-rounding-preflight-20261003.json`.

The existing exact-overlap adoption path now accepts `--source yahoo` with
the Yahoo provider. It retains the **1,000-candle/five-date** minimum, exact
prices/volumes through the published tail, snapshot checksum, preserved
provenance, and transaction/lease guards. Yahoo uses completed UTC minute bounds
with finality validated during restoration. VN's complete-session and correction
proofs remain VN-only. Unverified global minute snapshots stay frozen instead
of queuing an unverified annual provider replacement. Nine additional regression
cases cover Yahoo append and index restoration, concurrent correction/leases,
market/finality validation, VN-only proofs, and the frozen-snapshot guard.
The full suite passes **211 tests**; Ruff lint/format checks pass.

Both index snapshots pass the real CLI handoff in the isolated database.
Their main replacements publish atomically after verifying every old timestamp
and volume and confirming each old price equals the rounded JSON price. Original
retained before-images and complete full-precision captures are immutable,
checksummed RustFS objects referenced by their adoption evidence. The S&P index
retains **56,662 rows**, and the Dow **56,672**. Ordinary Yahoo refreshes each
publish **40 completed candles**, preserving their values and revisions.
Their explicit watchlist entries enable daily and minute ingestion; other
global entries retain daily ingestion only. This licenses observed continuity,
without certifying lifetime adjustments or missing earlier sessions.

Actual minute and 15-minute HTTP reads match the stored/aggregated data exactly.
Eight existing-SDK checks compare **20 candles each** on minute/15-minute and
SMA/EMA modes, with no time/OHLCV/indicator differences. The unchanged public
global page renders AAPL and both index 15-minute charts through isolated local
API routing, with no page errors, network errors, or attempted writes. Reports:
`data/global-index-json-publication-20261003.json`,
`data/sdk-global-gspc-minute-json-parity-20261003.json`,
`data/sdk-global-dji-minute-json-parity-20261003.json`, and
`data/web-global-json-index-minutes-20261003.json`.

A fresh S3 restore reproduces **395 archive objects / 320,224 rows**, all
**54 handoffs** (52 VN plus two global), and **seven recoveries**, with exact
metadata including the precision before-image references. The populated backup
and restored copy have identical SHA-256
`f5229686b51ec6ba2a670ccf2da6fa2caeff79fe11d7cd7110b64af472b80aa2`
and **829,550,592 bytes**, preserving **5,514,348 candles**, **129 source checks**,
**1,162 import receipts**, all **54 handoffs**, archive/recovery metadata, and the
exact index candles. SQLite quick check is `ok`. Reports:
`data/global-index-json-index-restore-20261003.json` and
`data/global-index-json-backup-restore-20261003.json`.

The source distribution and wheel build offline. An isolated unpacked-wheel
check loads the packaged Yahoo CLI path and watchlist, verifies that only the
two index entries enable minute ingestion, replays the **1,951-candle** native
S&P capture, and publishes its handoff without changing any candle. Report:
`data/yahoo-adoption-wheel-smoke-20261003.json`.

## Isolated VNINDEX native minute rebuild

A boundary probe returns no VPS minute data and unusable VNDirect arrays,
while DNSE returns 500 valid candles around the one-year floor. Those timestamps
all overlap the retained snapshot, but only 15 OHLCV records match exactly.
An isolated full-series DNSE rebuild stages **23,453 valid rows** across **104
observed date partitions**, then stops on its 48th page with an invalid OHLC
range. Its native May 5, 2026 candle at 07:45 UTC has high `1873.31` below close
`1874.85`; the decoded public response and its checksum are preserved. This
is an actual provider error, without clamping or dropping that candle.

Comparing the **103 fully fetched observed sessions** after the partial oldest
day, DNSE has **23,323 rows** against **23,463 legacy rows**, with **162 missing**
and **22 additional timestamps**. Many differences occur at 04:30/07:30 UTC;
no auction/session conversion has been inferred. Ten close values also differ
across the entire staged overlap, including the partial oldest session.
The main **56,523 retained VNINDEX rows** and ready legacy series remain
identical; the isolated job and staging are retained for diagnosis. This proves
the failed native replacement cannot safely replace the published year.
Evidence is in `data/vnindex-native-minute-history-probe-20261003.json`,
`data/vnindex-dnse-minute-rebuild-rehearsal-20261003.json`,
`data/vnindex-dnse-rehearsal-covered-sessions-20261003.json`, and
`data/vnindex-dnse-invalid-minute-diagnostics-20261003.json`.

DNSE's official [OHLC documentation](https://developers.dnse.com.vn/docs/dnse/get-ohlc-history/)
describes `/price/ohlc` for stocks, futures, and indices. Its
[working-date documentation](https://developers.dnse.com.vn/docs/dnse/get-market-working-dates/)
describes a one-year calendar excluding holidays and weekends. These are separate
from the public chart endpoint tested here; the documentation alone does not
establish volume semantics, adjustment equivalence, or complete retained coverage.

## Extended FPT daily history and provider basis

The public archive denies anonymous bucket listing. Explicit 2015–2018 FPT
files remain readable, while the probed 2006–2014 files return 403. The old API
independently exports 73 valid 2014 candles, which were migrated through that
read path. An inaccessible object is recorded as unavailable, rather than proof
that the year contains no trading data. Evidence is in
`data/legacy-public-inventory-probe-20261003.json`,
`data/migration-fpt-pre2019-20261003.json`, and
`data/migration-fpt-2014-api-20261003.json`.

FPT's original 2018 file contains 250 dates. VPS returns 248, omitting January
23–24. The original rows are flat with zero volume; a contemporaneous
[VNDirect notice](https://www.vndirect.com.vn/vndirect-thong-bao-ve-viec-tam-ngung-giao-dich-tren-so-giao-dich-chung-khoan-thanh-pho-ho-chi-minh-ngay-24-01-2018/)
documents both HOSE closure dates, and the
[2018 fund report](https://www.vietnamholding.com/media/b5qf5kcm/annual-report-30-june-2018.pdf)
identifies FPT's historical exchange. The explicit recovery publishes all 248
remaining dates and preserves the original 250-row CSV and reviewed references
in checksummed S3 evidence. This exception does not generalize to other tickers
or holidays. CTR's five missing 2022 dates remain unresolved; their nonzero-volume
original rows do not pass this exception.

The complete FPT comparison exposed a substantial VPS/legacy historical price
basis difference before part of 2021. VNDirect preflight reproduces the exact
date sets of all ten 2014–2023 partitions, and an isolated retained-window repair
preserves all 747 recent dates. The main daily series was then replaced through
durable staging and all ten archives reconciled to the same VNDirect revision.
The final series has **3,003 candles: 747 in SQLite and 2,256 in Parquet**.
Original VPS objects and recovery evidence remain immutable. A bounded normal
VNDirect update rechecks 40 rows without changing the new revision. Reports are
`data/fpt-vndirect-archive-preflight-20261003.json`,
`data/fpt-vndirect-retained-rehearsal-20261003.json`,
`data/fpt-vndirect-coherent-replacement-20261003.json`, and
`data/fpt-vndirect-followup-update-20261003.json`.

HTTP comparison against the preserved 3,005-row legacy export finds every
ordinary date, with only the two reviewed closure placeholders excluded and no
new dates. Sampled daily SMA, daily EMA, weekly EMA, and archived EMA ranges pass
the earlier `1e-3` relative MA tolerance; the largest sampled MA difference is
`0.0005954546269595561` relative. Prices and volumes are not identical: maximum
relative open/close differences are `0.007076024967067722` and
`0.004429636297768131`. Five historical dates have volume differences above 1%,
including a relative difference of `2.084300424975482` on July 13, 2015.
These remain an explicit `legacy_value_disagreement` finding. No provider
corporate-action or volume semantics are inferred from this comparison. Evidence
is in `data/fpt-vndirect-full-history-http-comparison-20261003.json` and
`data/fpt-vndirect-volume-disagreements-20261003.json`. Nonempty legacy SMA200
values can use partial windows and do not establish 200 earlier candles.

A fresh RustFS index restore verifies **394 active objects / 319,980 archived
rows**, **43 provider handoffs**, and **five recovery receipts**, including the
preserved original FPT 2018 candidate after its later provider replacement.
  Metadata matches exactly and SQLite `quick_check` returns `ok`. The active index
contains 383 published and 11 pending objects; 252 coherent VN daily objects
contain 58,795 rows across 54 tickers. A new populated backup/restore preserves
all **5,513,154 local candles**, four source checks, all active archives, handoffs,
and receipts. Both 826,900,480-byte files have SHA256
`d035eb0553863bdcd4d1773eabcff3a6bef2d18bb4807e808fa58e5074975b8a`.
Evidence is in `data/fpt-vndirect-archive-index-restore-20261003.json` and
`data/fpt-vndirect-backup-restore-20261003.json`.

The rebuilt wheel includes the session-exclusion module and recovery flag; an
isolated unpacked-wheel check initializes its own SQLite database successfully.
After restarting the loopback API, `/health` exposes FPT's ready VNDirect daily
revision with 40 completed rechecked rows, no provisional rows, and a matching
dated successful source check. The health request takes 2,219.85 ms, consistent
with the earlier populated measurements. Evidence is in
`data/fpt-recovery-wheel-smoke-20261003.json` and
`data/fpt-vndirect-health-smoke-20261003.json`.

## Real retention rollover and compaction

The dry run identified expired UTC-floor rows. Execution published **129
verified objects** before pruning their unchanged local versions. Two bounded
compaction batches then replaced **218 fragments with 109 partitions**, reducing
the active index from **498 to 389 objects**. All **32,413 canonical candles**
match the saved pre-compaction checksums, including prices, volume, provider,
revision, and nanosecond update versions. Duplicate timestamps retain the newer
version. No original immutable object was deleted or upstream history fetched.
SQLite's integrity check returns `ok`, and another fresh S3 index restore matches
every active record, provider handoff, and recovery receipt. Evidence is in
`data/retention-rollover-20261003-0210.json`,
`data/compaction-values-before-20261003.json`,
`data/compaction-values-after-20261003.json`, and
`data/archive-index-compacted-restore-check.json`.
The populated SQLite backup/restore also passes: **5,513,071 candles**, 389 active
archive objects, 43 handoffs, and four recovery receipts survive the operational
CLI round trip, with an `ok` integrity check. The backup is
`backups/local-rehearsal-compacted-20261003.sqlite3`; its checksum and restored
counts are recorded in `data/compacted-backup-check-20261003.json` and
`data/compacted-sqlite-restore-check-20261003.json`.

A bounded crypto update filled the outage gap. SQL counts/bounds and alignment
checks prove **525,731 continuous minute rows per selected crypto ticker**,
from October 3, 2025 through October 3, 2026 at 02:10 UTC. This is a dated
rehearsal checkpoint; ongoing worker scheduling still determines freshness.

## Selected global hourly snapshots

The bounded hourly runner imported **24,184 candles across seven configured
global tickers**, preserving original legacy S3 values/provenance and freezing
each checksummed source file. Every 2023 file returned 403 and remains explicitly
unavailable. Gold's 2026 CSV fails at row 809, timestamp `1775163599`, because
it is not minute aligned; no guessed timestamp correction was applied.
The other six hourly snapshots end August 26, 2026; gold's accepted hourly
snapshot ends December 31, 2025. Complete coverage and ongoing Yahoo handoff
remain unverified. Results are in `data/migration-global-hourly.json`.

The deployed web page now renders AAPL and both default index one-hour charts
with populated responses and no page/network errors. The page exposes no visible
four-hour control, so four-hour behavior was checked through the HTTP API.
All seven hourly and four-hour requests return 200 valid candles. Recovered
VNINDEX August 23–26, 2021 and VN30 August 13–18, 2020 date ranges also return
four valid daily candles each. Evidence is in
`data/web-global-hourly-rehearsal.json` and
`data/compacted-hourly-and-index-http-check.json`.

## One-year FPT minute migration trials

Separate SQLite migration files preserved the main database and archive index.
An explicit S3 trial requested **365 dates**, 2025-10-03–2026-10-02. It imported
**50,631 candles across 225 dates**, ending at **2026-08-27 02:41 UTC**. Compared
with dates observed in the current daily FPT feed, **23 later dates** lacked
minute files. Weekend/holiday URLs also returned 403; that status was recorded
as unavailable, not proof of missing trading data.

Read-only late-August minute probes returned no VPS data. VNDirect and DNSE
returned 500 rows each; **301 timestamps overlapped** the legacy snapshot, with
**zero fully matching OHLCV rows** at absolute tolerance 0.011. This observation
requires further investigation of precision, volume, and adjustment semantics;
it does not establish a dividend or justify applying a scaling factor.

The public legacy HTTP API could export recent minute dates absent from S3.
A separate bounded API-export trial imported **56,027 minute candles across
248 dates**, from **2025-10-03 02:15 UTC through 2026-10-02 07:45 UTC**. Every
observed daily FPT date in that window had minute data; no HTTP export hit its
10,000-row limit. The SQLite file measured **7,516,160 bytes**. First/last-date
API checks returned 226 candles each; a 15-minute query returned 20 candles.
The snapshot remains isolated under explicit `legacy-api` provenance.

This proves a workable public export path for PostgreSQL-only history without
adding a PostgreSQL runtime dependency. It does not prove every expected minute
was traded/recorded. Subsequent handoff checks and main-database imports are
described below; wider universe migration remains required. Local coverage reports
are in ignored `data/migration-fpt-coverage.json` and
`data/migration-fpt-api-coverage.json`.

## Verified snapshot handoff and the main minute window

The main database now retains **56,253 FPT minute candles across 249 observed
dates**, 2025-10-02–2026-10-02, matching its actual UTC calendar-year floor.
Another **4,746 prior candles** provide archived September/October warm-up.

A VPS sample contained **1,130 exact OHLCV matches across five completed
sessions**, September 28–October 2, through the published completed tail.
`adopt-snapshot --execute` recorded snapshot/overlap hashes and bounds, provider
choice, capture cutoff, and the explicitly limited scope of this observation.
It changed the ongoing provider to VPS without rewriting imported values or
provenance. A subsequent bounded ordinary worker update succeeded without a
retained-window repair. Original rows keep `legacy-api` provenance; actual
provider updates use VPS within the evidenced revision.

Adoption is rejected for any OHLCV disagreement, incomplete overlap, active
repair, or concurrent snapshot correction. Later imports cannot reuse the
already adopted snapshot revision. Corporate-action comparisons still check
older adopted candles, and unverified provider/revision boundaries still fail.
Tests cover these cases, mixed-provenance aggregation, archive publication,
malformed manifests, and evidence recovery. The VN completed-session guard now
recognizes completed weekday sessions after 15:15 ICT; this is a grace rule,
not a complete holiday/suspension calendar.

This handoff does not prove all historical adjustment policies equivalent.
Separate August probes against the **current HTTP snapshot**, rather than the
stale S3 snapshot discussed above, found 500 overlapping timestamps from both
VNDirect and DNSE. Both had price differences; matched volumes numbered
498 and 500 respectively. VPS supplied no August sample. No inferred scaling
factor was applied.

The main database also imports **56,751 VNINDEX minute candles across 249
observed dates**, plus **4,788 prior archived warm-up candles**. All 366 bounded
date exports returned HTTP 200; 117 were empty and remain retryable rather than
being classified automatically as holidays. The snapshot is readable under
`legacy-api` provenance. Exact handoff was rejected: VPS matched 1,126 of
1,134 overlapping candles, VNDirect 716 of 1,905, and DNSE 156 of 1,991.
Observed differences include opening/auction OHLCV and session timestamps.
An actionable quality finding records this disagreement. The snapshot remains
unchanged; ongoing index-provider adoption is still unresolved.

A real RustFS manifest recovery reconstructed **10 archive objects and one
adoption certificate** on a fresh temporary SQLite index. An online SQLite
backup retained the same certificate and **153,703 candle rows**. The main
database and backup each measured **21,020,672 bytes** at that check.

## Complete-session handoff for sparse series

The new explicit `adopt-snapshot --complete-sessions` path verifies every
original timestamp in at least five completed weekday sessions through the
published tail. Every positive-volume provider minute matches the original
OHLCV. Each day's aggregated minutes must reproduce fresh provider daily
candles and the ready retained daily revision, including the full volume total.
The default 1,000-candle path remains available. This is a separate complete-
session proof, rather than a smaller sample-count threshold.

Receipts contain the compared original/provider minute and daily records,
checksums, finality bound, and revisions. Index restoration replays those
comparisons and rejects malformed, incomplete, or rehashed inconsistent proof
before changing the target index. Publication checks both minute and daily
snapshots again inside the SQLite transaction. Fourteen new regressions cover
successful sparse append, truncated/missing/extra candles, missing/changed daily
records, zero minute volume, insufficient sessions, concurrent daily value/state
changes, and receipt restoration/corruption. The full API suite passes **170**.

All eight real handoffs to VPS pass the complete five-session proof:

| Symbol | Exact minute matches | Retained minute rows | Ordinary update rows |
| --- | ---: | ---: | ---: |
| VPL | 855 | 43,945 | 40 |
| HHS | 705 | 40,286 | 40 |
| GEG | 225 | 25,459 | 40 |
| CTR | 674 | 41,528 | 40 |
| NKG | 972 | 49,573 | 40 |
| SBT | 817 | 31,042 | 40 |
| VDS | 659 | 37,523 | 40 |
| VGI | 937 | 53,361 | 40 |

Adoption leaves imported values/provenance intact; every subsequent bounded
update preserves the full retained timestamp/OHLCV checksum and revision while
recording actual VPS provenance for the rechecked rows. Evidence is in
`data/sparse-complete-session-adoption-preflight-20261003.json`,
`data/sparse-complete-session-adoption-results-20261003.json`,
`data/remaining-minute-complete-session-probes-20261003.json`, and
`data/additional-complete-session-adoption-results-20261003.json`.

At that checkpoint, the selected VN minute universe has **51 of 55** verified ongoing handoffs:
43 VPS and eight DNSE. PLX, SSI, VGC, and VNINDEX disagree with every tested
provider in the current overlap; their original snapshots remain preserved.
This proof does not establish lifetime adjustment equivalence, certify all
historical sessions, or license guessed timestamp/price transformations.

Sixteen real native/15-minute HTTP requests pass for these eight series. Their
health entries expose 40 completed rechecked rows with current VPS evidence and
no provisional rows. A fresh S3 index reconstructs all **394 objects**, **51
handoff receipts**, and **five legacy recovery receipts**, with exact metadata
and an `ok` SQLite integrity check. The populated backup/restore preserves all
**5,513,154 candles**, **11 source checks**, and those archive/evidence records.
Both 827,899,904-byte files have SHA256
`de42eb67980b9dc1e94097825d63c1167e37aa1479fb54aa7d2e1be36fed984a`.
Reports are `data/all-complete-session-http-smoke-20261003.json`,
`data/all-sparse-complete-session-index-restore-20261003.json`, and
`data/complete-session-backup-restore-20261003.json`.

Read-only diagnostics narrow the remaining disagreements. VPS reproduces all
PLX and VGC timestamps in five completed sessions. PLX differs only in four
minute volumes; VGC differs in two volumes and one minute's high/close. Both
reproduce every 15-minute/hourly/daily aggregate, including fresh and retained
daily OHLCV. SSI has six minute-open and 14 volume differences on VPS; one
15-minute/hourly open differs, while daily aggregates match. VNINDEX has missing
and extra minute timestamps and disagreements at aggregated and daily levels.
Testing candidate offsets of minus/plus one minute does not reproduce the full
original series. These observations do not establish which vendor's individual
minute allocation is correct; no values or timestamps were transformed.
Reports are `data/remaining-minute-value-diagnostics-20261003.json` and
`data/remaining-minute-aggregate-diagnostics-20261003.json`.

Ruff lint/format checks and the offline distribution build pass. An isolated
unpacked-wheel smoke check initializes its own database and loads the packaged
complete-session validator and CLI flag. Its report is
`data/complete-session-wheel-smoke-20261003.json`.

## Independently corroborated native-minute corrections

Read-only comparison finds two changed VGC candles that agree on both VPS and
DNSE. PLX's four VPS volume differences are not all confirmed by DNSE, and SSI's
four DNSE volume differences are not all confirmed by VPS. The original records
and raw witnesses are in
`data/minute-correction-corroboration-preflight-20261003.json`.

The explicit `--complete-sessions --corroborate-provider` path requires every
changed native candle to match a distinct approved provider on timestamp and
OHLCV. It preserves original/candidate 15-minute OHLCV and all existing fresh/
retained daily checks. Corrections and the handoff publish together in one
SQLite transaction after checking both snapshot revisions and values again.
Unchanged candles retain their provenance. Receipts preserve the original
records and second-provider witnesses; index restoration replays the checks.
No timestamps are inserted/dropped and no scaling factor is inferred.

Eleven additional regressions cover dry-run preservation, atomic corrections,
retained provenance, missing/disagreeing/wrong-source witnesses, different
15-minute allocations with equal daily volume, invalid daily evidence,
concurrent changes, provider selection, and restoration with mixed provenance.
The full API suite passes **181 tests**. The default exact-match paths remain
unchanged.

Actual VGC publication changes exactly **two of 40,648 retained minute
candles**, preserves all timestamps and other records, then successfully
rechecks 40 candles on VPS without changing their values or revision. Its
825-candle HTTP range shows exactly the two recorded native differences;
all 80 fifteen-minute and 25 hourly candles remain identical to the original
aggregates. Health records the ready VPS revision and 40 completed rechecks.
Reports are `data/corroborated-minute-correction-preflight-20261003.json`,
`data/vgc-corroborated-minute-correction-results-20261003.json`, and
`data/vgc-correction-http-smoke-20261003.json`.

The selected minute universe now has **52 of 55** verified handoffs: 44 VPS and
eight DNSE. PLX, SSI, and VNINDEX remain frozen. Larger-interval agreement alone
does not license unconfirmed native corrections.

A fresh S3 index reconstructs all **394 objects / 319,980 archived rows**, **52
handoff receipts**, and **five recovery receipts**, replaying VGC's 825-row
window and two DNSE witnesses. SQLite integrity is `ok` and metadata matches.
The populated backup/restore preserves all **5,513,154 candles**, **12 source
checks**, and those archive/evidence records, including exact corrected VGC
candles. Both 828,301,312-byte files have SHA256
`d4624120cf6b9e269ff4e05e139e7fa840a9c190666980ef9266a8fd806ebd31`.
Evidence is in `data/vgc-correction-index-restore-20261003.json` and
`data/vgc-correction-backup-restore-20261003.json`.

Additional read-only VNDirect checks also fail to corroborate every changed
PLX/SSI candle in their exact five-session windows. Their records remain intact;
the raw comparisons are in
`data/remaining-minute-vndirect-corroboration-20261003.json`.
The offline wheel build, Ruff checks, and isolated packaged initialization/CLI
flag check pass. Package evidence is in
`data/corroborated-correction-wheel-smoke-20261003.json`.

## Actual web, SDK, and CLI rehearsal

An isolated headless Chromium loaded the public `aipriceaction.com` application
while routing its API reads to the loopback FastAPI service. Production routing,
accounts, and existing browser profiles were untouched. The original frontend
rendered FPT daily candles, indicators, and its volume profile. Selecting its
15-minute control returned populated CSV responses (including a 456-candle
request). No page errors or attempted API writes were recorded. The default
VNINDEX volume-profile request initially returned 404 for missing minute data;
after the bounded import it returned 200 through the same frontend.

The unchanged crypto page also renders BTCUSDT daily/15-minute charts and its
minute volume profile. The global page renders AAPL daily/weekly charts. Both
rehearsals recorded no page errors or attempted API writes. The first crypto
rehearsal clicked an ambiguous ticker button and opened a symbol-picker modal;
the corrected script uses the already populated default chart and scopes chart
controls to the visible dialog when present. The global default Dow chart was
empty because it was outside the initial watchlist; `^DJI` is now included and
its 754-row daily bootstrap completed. Global minute snapshots are being imported
after read-only checks confirmed the old API serves them. An operational Yahoo
handoff and broader global interval coverage remain unverified.

A final global rehearsal after migration renders both default index daily/weekly
charts and the ^GSPC minute volume profile. It records no page errors, network
errors, or attempted API writes. The script now waits for in-flight routes before
closing the isolated browser; an earlier late health callback was canceled during
browser teardown, rather than failing in the API.

The existing SDK initially joined stale August S3 candles with an October live
tail. This produced wrong daily dates/prices and stale moving averages even
when the latest minute OHLCV matched. Its internal `get_ohlcv(use_live=True)`
path now reads complete API ranges and warmed indicators. Methods and existing
CLI options remain unchanged. Archive-only reads retain their CSV paths;
unavailable/empty series use a complete archive fallback with DataFrame
provenance. A history-consistency 503 reaches the caller rather than being
hidden by stale archive fallback.

Additional live SDK checks compare BTCUSDT daily/minute/15-minute SMA and EMA
and AAPL daily/weekly SMA and EMA. All sampled values and missing indicators
match the local API; DataFrame provenance reports `api`. Sparse responses that
initially triggered fallback are covered by new SDK regressions.

Six sampled SDK requests (daily, minute, 15-minute; SMA and EMA) each returned
20 candles with **zero differences** in timestamps, OHLCV, and MA10–MA200
against the local API. An existing Python `aipa get-ohlcv-data FPT --interval
1m --limit 5 --ema --no-system-prompt` command also returned the expected data
and ICT timestamps, using a rehearsal-only client injection. This does not
publish a new SDK release or change its production default endpoint.

Repeatable read-only checks are in `scripts/check_web_client.py` (optional
Playwright/Chromium) and `scripts/check_sdk_client.py` (existing SDK environment).
Their local reports/screenshots are ignored under `data/`. Broader historical
scrolling, authenticated sync, and full production cutover still
require acceptance checks and data migration.

## Expanded recent data and bulk ingestion

All four selected Binance symbols completed daily/hourly/minute bootstrap:
**4,388 daily**, **105,308 hourly**, and **2,107,848 minute** candles. Each minute
series has **526,962 unique aligned timestamps**, beginning **2025-10-02 UTC**;
its timestamp span/count prove no missing minutes through the captured tail.
These are bounded capture results, not a claim that a stopped worker stays fresh.
Seven selected Yahoo daily series retain **5,281 rows**; the Dow index adds 754.

Monthly Binance spot files accelerate long minute backfills using their official
SHA-256 sidecars. A real August 2026 BTCUSDT file contained **44,640 rows**;
**40 completed live API candles matched exactly** in timestamp and OHLCV.
September's monthly checksum was absent at the probe time; the worker used
bounded live pages for that unpublished month. Parsing verifies the complete
calendar-month sequence, the 2025 microsecond timestamp change, ZIP/member size,
finite prices/volume, and legacy integer volumes before staging. Corrupt caches,
wrong hashes, missing/duplicate/out-of-order rows, and truncated months fail
without altering published history. The monthly cache is bounded to 128 MiB.
Publication timing and checksums are documented by
[Binance](https://github.com/binance/binance-public-data).

The public legacy SJC API snapshot supplies **1,097 recent daily rows** and
**274 older archived rows**. The official SJC quote service and website returned
403/Cloudflare challenges in actual probes. New official quote updates are not
verified; the preserved snapshot remains readable without pretending an update
succeeded.

VCB's wider-minute trial imported **56,003 rows**, with every observed daily
date represented. VPS matched **1,127 completed candles across five sessions**,
licensing an exact handoff under its explicit snapshot revision. A resumable
selected-universe migration is now applying bounded 31-day API exports, keeping
monthly receipts/checksums, preserving earlier indicator warm-up in RustFS, and
attempting the same strict handoff. MBB's price/volume disagreement and VPL's
insufficient completed overlap are recorded; these snapshots remain unchanged.
The bounded process completed for all 55 selected VN series: **2,848,973 minute
rows** with every date observed in their retained daily feed represented.
The first pass licensed 35 VPS handoffs. Strict alternate-provider checks then
licensed eight DNSE handoffs (MBB, VND, HSG, GVR, VNM, SAB, GEE, HAG), each with
2,000 exact completed candles. **43 of 55** now have certificates; CTR, GEG, HHS,
NKG, PLX, SBT, SSI, VDS, VGC, VGI, VNINDEX, and VPL remain independent snapshots.
No exact-match/coverage requirement was weakened. Reports are ignored under
`data/migration-vn-universe-minute.json` and
`data/migration-vn-minute-alternate-providers.json`.
Selected global minute imports use smaller seven-day exports because longer
trading sessions can hit the legacy API's 10,000-row limit. AAPL and MSFT each
imported 56,671 rows; empty older weekly exports remain retryable, and complete
one-year global coverage is not claimed. The global run ended with **363,484
minute rows across seven symbols**. Most stock/index snapshots begin in March
2026. GC=F stops during import because the legacy export contains a minute
timestamp that is not aligned to a minute; earlier valid receipts remain
published. No timestamp or OHLCV value was altered to hide this failure.

A live hourly worker exposed a startup race: another filtered worker temporarily
disabled the watchlist between transactions, causing a ticker lookup to fail.
Activation now uses one transaction, with WAL-reader and genuine-removal tests.
The resumed bounded hourly process exited successfully from saved checkpoints.
Its VNDirect index fallback publishes valid values but has sparse older dates;
53 DNSE stock hourly backfills remain pending after upstream history failures.
Invalid OHLC values were rejected, never clamped or filled.

The SQL audit reviews millions of local candles without materializing series in
Python. Its first populated run took **5.603 seconds**, with **30,015,488 bytes**
peak RSS. It reports continuous-market gaps/staleness, absent observed VN daily
dates, and ambiguous long gaps separately. VNINDEX's two Sunday observations
(**2025-05-04**, **2025-05-11**) are flagged as provider anomalies, preserved for
review, and excluded from expected weekday sessions. Listing floors and
unfinished sessions are respected; these checks still do not establish a full
holiday/suspension calendar. Fixed audit findings resolve on recheck without
hiding independent adjustment or migration findings.

Read-only probes subsequently identified the weekend daily cause: VPS mixed
UTC-midnight candles with 17:00 UTC (Vietnam-midnight) candles for the same
market dates. Their opens differ, so a guessed timezone shift/deduplication would
silently choose prices. The adapter now rejects affected requested pages while
ignoring unrelated excess history. VNDirect/DNSE sampled date conventions were
consistent. VNINDEX and VN30 daily windows were staged and rebuilt entirely on
VNDirect; recent VN daily count is now **40,695**. Both series are ready, no weekend
daily candles remain, and the follow-up audit resolves the corresponding finding.
The earlier backup preserves the prior state; this is a bounded source rebuild,
not proof that every provider adjustment/calendar policy is equivalent.

Ordinary live updates passed on MBB, HSG, and VND via DNSE and VCB via VPS.
Each wrote 40 provider candles within its evidenced revision, retaining imported
provenance elsewhere. No pending repair or new provider-failure finding arose.

An ordinary crypto restart exposed why a fixed 40-candle overlay is insufficient
after downtime. Live page size now grows with the elapsed continuous-market gap,
up to the existing 1,000-row API bound. A longer gap queues durable recovery and
preserves the published series. Regression tests cover both paths; a real bounded
restart filled the elapsed gap and retained aligned, continuous minute series.

A fresh local online backup contains **5,505,290 candles and 43 certificates**
and passes SQLite quick integrity checking. Its size is **822,169,600 bytes**.
An isolated index reconstructed **all 117 published objects and 43 certificates**
from RustFS, matching the main database. These object records account for
**242,701 archived rows**, largely minute indicator warm-up, plus older FPT/SJC
history. Older history across the whole selected universe still requires migration.

The existing legacy dotenv configures a loopback PostgreSQL connection. A small
inventory attempted enforced read-only transactions and short timeouts; that
connection was unavailable. No production connection, credentials, or sync
payloads were printed or modified. Private production export remains open.

Health retains existing fields and adds per-source/interval date bounds and
ingestion timestamps within `storage.coverage`. An old candle's timestamp remains
visible independently of a recent import. Counts and last-ingest values come
from one grouped SQL read. Per-series bounded provider-check and provisional
observation tracking is now implemented as described above; full expected-
session and provider adjustment-policy audits remain open.

## Populated local measurements

These read-only measurements used loopback FastAPI/RustFS during ongoing
ingestion. Three HTTP samples were taken for each request; cache invalidation
can occur between samples. They are local examples, not concurrent-load SLAs.

| HTTP request | Rows | First / subsequent samples (ms) |
| --- | ---: | --- |
| 55 VN tickers, daily EMA | 1,100 | 396.35 / 392.76 / 33.86 |
| BTCUSDT native minute export | 10,000 | 141.24 / 82.38 / 80.40 |
| BTCUSDT 15-minute EMA | 200 | 79.06 / 7.04 / 6.79 |
| VCB minute retention boundary | 64 | 50.06 / 3.03 / 2.92 |
| AAPL weekly EMA | 20 | 13.20 / 1.60 / 1.60 |

An independent reader used a separate emptied cache for each cold measurement:

| History query | Rows | Cold / warm (ms) | Cached Parquet bytes |
| --- | ---: | --- | ---: |
| FPT daily 2019–2020 | 502 | 112.75 / 16.41 | 13,640 |
| FPT daily retention boundary | 42 | 45.01 / 26.30 | 19,369 |
| VCB minute retention boundary | 64 | 56.64 / 41.01 | 36,117 |

The measurement process peaked at **126,746,624 bytes RSS**. The FastAPI process
used **111,008 KiB RSS before** and **128,000 KiB after** those checks. SQLite
measured **582,897,664 bytes** at the earlier populated checkpoint and continues
growing during bounded imports. Cached bytes include indicator lookback, rather
than being cloud-billing estimates. Full selected-universe archival transfer
costs and production-scale concurrent HTTP load remain to be measured. The
bounded local concurrent rehearsal below now supplements these isolated reads.

Additional profile batches use a fresh temporary object cache and four read
threads against local RustFS. All 55 configured VN tickers return profiles;
cached and uncached results match exactly. These are direct reader/analysis
measurements rather than concurrent HTTP SLAs:

| Profile range | Native candles | Cold / warm batch (ms) | Cold / warm median query (ms) | Peak RSS bytes |
| --- | ---: | --- | --- | ---: |
| September 2025 | 229,589 | 1,511.36 / 1,063.30 | 96.71 / 72.59 | 111,935,488 |
| September 2025–August 2026 | 2,843,368 | 37,566.18 / 38,684.09 | 2,742.85 / 2,885.78 | 318,685,184 |

The monthly batch caches **1,733,603 bytes**; the mixed one-year batch caches
**2,052,229 bytes**. The larger query is dominated by retained candle reads and
analysis; a warm object cache did not improve its measured total. SQLite uses
**826,900,480 bytes** at this checkpoint. Active verified Parquet objects use
**3,881,127 bytes** across 389 objects: daily **1,705,982**, minute **2,162,783**,
and hourly **12,362**. These exclude retained older versions, evidence and
manifests, and do not estimate cloud billing. Evidence is in
`data/archive-profile-benchmark.json`, `data/archive-profile-year-benchmark.json`,
and `data/active-archive-footprint-20261003.json`. The reproducible read-only
runner is `scripts/benchmark_archive_profiles.py`.

A four-client HTTP rehearsal runs for **30.5 seconds** with response caching
disabled and archive files warmed by sequential baselines. It completes
**356 concurrent requests with zero failures or candle-payload changes**.
Baselines cover daily and 15-minute reads for all 55 configured VN tickers,
a bulk daily request, six older daily series, four 10,000-row crypto exports,
three global weekly charts, and health. Payload hashes are compared throughout;
health's changing clock fields are validated separately.

| Concurrent HTTP request | Requests | Median / p95 (ms) |
| --- | ---: | ---: |
| VN daily, one ticker | 163 | 39.12 / 75.73 |
| VN 15-minute, 200 candles | 163 | 673.85 / 711.54 |
| Bulk daily, 55 tickers | 2 | 1,595.63 / 1,615.49 |
| Older daily history | 12 | 131.02 / 177.93 |
| Crypto minute, 10,000 candles | 8 | 926.88 / 1,015.58 |
| Global weekly | 6 | 115.80 / 132.86 |
| Health | 2 | 2,212.68 / 2,234.44 |

This is a bounded local mixed workload; the two-sample categories do not
establish reliable production percentiles. Cold S3 transfer and longer workloads
remain separate acceptance checks. The complete request/result evidence is
`data/http-concurrency-benchmark.json`; the read-only runner is
`scripts/benchmark_http.py`.

## Compatibility and data-quality boundaries

- Existing routes, request shapes, repeated symbols, aliases, date direction,
  JSON/CSV fields, SMA/EMA options, aggregations, auth, and static explorer are
  implemented. `cache` controls the bounded in-memory candle response cache.
  The `redis` and `snap` flags are accepted as compatibility inputs; SQLite/S3
  supplies the data. Diagnostic header values describe the actual storage.
- The packaged company/fundamental metadata is an existing static snapshot.
  No VCI provider or new VCI fundamental fetching is implemented. The existing
  SDK's legacy CSV, metadata, hashes, and fundamental URLs remain in place.
- Historical indicators need earlier S3 data and a consistent provider/revision.
  Missing or incompatible adjustment history must not be presented as a
  continuous verified series. Repairing affected historical requests can be
  unavailable while coherent recent requests remain readable.
- Published legacy crude-oil data contained an invalid OHLC range, including
  `CL=F` on `2026-09-06`, where close was below low. The fixture/import validator
  rejected it. Legitimate negative futures prices are allowed; SJC's
  quote-derived previous-close open has its own explicit validation rule.
- During RRG comparison, the legacy API exposed different precision for VCB
  candles through different request paths. One captured 100-candle average was
  `59667.0772`; another current request returned `59666.81`, with 51 differing
  closes. A completed-date recheck later matched both algorithms. The new
  reader computes all dependent values from the same verified candle history.

## Remaining acceptance work

1. Populate intraday/other-market series and audit the configured universe. Prove complete trading-session
   coverage with listing dates, holidays, suspensions, and no-trade periods;
   the current audit reports observed gaps without inventing a VN calendar.
2. Import retained and older legacy history under the new archive prefix.
   Verify adjustment compatibility before combining it with current providers.
   Public API exports supply sampled missing minute dates; wider provider
   adoption and whole-universe history still need verification.
3. Verify real provider corporate-action semantics separately for each native
   interval. Thresholds and staged repairs are tested, but do not prove a
   suspected revision was a dividend or that different providers share a basis.
4. Benchmark the populated universe, including large cold historical profiles,
   multi-symbol queries, memory, SQLite size, and archive transfer costs.
5. Extend the selected deployed-web and Python SDK/CLI rehearsal to all required
   flows, including authenticated sync and migration of existing sync records.
   Preserve rollback routing and the legacy database/archive.

At that checkpoint, the legacy backend and production data were not modified.
The existing SDK was adapted internally for coherent API reads, and TODO.md
described reviewable phases before actual Git commits were requested. The later
local Git checkpoints are listed at the top of this report; production deployment
remains outstanding.
