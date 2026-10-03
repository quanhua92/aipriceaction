# Implementation plan

The numbered sections describe implementation phases and their acceptance checks.
Checkboxes mark verified work; they do not certify complete data coverage or
production readiness. The implementation accumulated before the user requested
actual commits, so its Git history is grouped by subsystem rather than recreating
every intermediate phase below.

## Git checkpoints — 2026-10-04

- `c037c53`: SQLite storage, retention, S3 Parquet history, import/quality checks,
  packaged catalogs, dependency lock, and RustFS-only Compose; 59 tests pass.
- `23fc706`: selected-provider workers, revision-safe recovery/adoption, and Python
  operational CLI; 178 tests pass.
- `b8d74b5`: FastAPI routes, legacy web responses/analysis, and packaged explorer;
  41 tests pass. These three scoped runs cover all 278 API tests.
- `0a3724f`: SDK coherent API ranges, archive fallback provenance, and compatibility
  regressions; 251 offline tests pass, four existing real-S3 tests excluded.
- Migration/verification scripts and this plan, contract, runbook, and validation
  report are committed together as the final documentation/tooling checkpoint.

Local market databases, credentials, backups, distribution builds, and captured
evidence are ignored. Data coverage and production cutover gates remain open.

## Live API migration takes priority — 2026-10-04 ICT

The main local replacement now serves the five complete public-API daily
snapshots for EIB/HHS/GEX/HAG/SHS: **9,915 candles**, including SHS's existing
2018 dates, with **3,735 hot rows and 26 cold objects / 6,180 older rows**.
All previously served dates are preserved; the four EIB/HHS sessions are
recovered. Fresh exact 40-candle VPS checks license each bounded daily append
handoff. Original objects, raw exports, publication receipts, and populated
before/after backups remain preserved. Public candle migration does not require
direct legacy PostgreSQL access.

- [x] Publish these coherent snapshots atomically into main local SQLite and
  publish the S3 manifest under the shared writer lease.
- [x] Verify 41 full-year HTTP reads, 60 exact SDK indicator/history comparisons,
  5,680,552 unchanged unrelated candles, and unchanged unrelated operational rows.
- [x] Restore the current archive index: 445 active objects, 63 handoffs,
  34 recoveries, and one remaining unavailable-history record.
- [x] Restore a populated backup with identical checksum and `quick_check=ok`.
- [x] Repeat the 2,103-request query matrix: 2,091 pass; the 12 explicit failures
  concern only VND/VNINDEX weekly, two-week, and monthly indicator history.
- [x] Exercise all five selected daily/weekly charts and volume profiles in the
  unchanged public web UI with local API routing; preserve the separate failed
  VNINDEX benchmark calls as an outstanding acceptance gap.
- [ ] Recover VND's invalid 2020 candle and reconcile VNINDEX's pending 2020
  object using complete coherent history and verified native-provider overlap.
- [ ] Complete provider/session semantics and independent minute/daily basis
  checks; handle private sync inventory separately; finish production acceptance.
- [x] Measure actual cold/warm native history transfers across the selected VN
  universe, compare full candle provenance, and make failed/empty ranges return
  nonzero. Verify source-backed listing exclusions separately from missing data.
- [x] Preserve a committed successful daily observation when the subsequent
  historical probe fails, times out, or is cancelled. Record probe failures
  separately, bound their time budget, and keep historical revision repair active.
  Verify with five regressions and an isolated actual FPT recent-provider read.
- [x] Require observed tail overlap after prolonged VN daily outages, matching
  hourly/minute behavior. Expand once within the current provider, retained
  window and 1,000-candle budget; queue recovery when it cannot bridge the tail.
  Verify eight daily regressions and restore 100 actual FPT observed sessions in
  isolation with exact values/provenance and unchanged main data.

The observations below describe the earlier isolated staging checkpoints.

Complete 2019-through-current API snapshots for EIB/HHS/GEX/HAG/SHS now stage
9,665 exact daily candles, with three-calendar-year hot retention and 25 older
Parquet objects. Independent wide exports match every candle. The new
`--api-read-backend database` option prevents mixing recent Redis values with
older database values; frozen receipts reject a read-path change. Each of the
five snapshots has 40 exact completed VPS tail candles. These are isolated
candidates, not published main replacements or licensed provider handoffs.
VNINDEX's complete snapshot fails on an invalid 2019 candle; its separately
verified 2020 year remains preserved. VND's invalid 2020 candle remains open.

Daily adoption is now implemented and passes replay, race/lease, append,
correction, and restore checks. Five live isolated VPS handoffs preserve every
snapshot OHLCV/date; current daily SMA/EMA, historical, and weekly FastAPI reads
pass. A separate extended SHS snapshot includes its existing 2018 history and
preserves all 2,183 previously stored unique dates. The five intended candidates
now total 9,915 candles / 26 older objects. At that checkpoint main publication
and wider client acceptance remained outstanding; the publication above follows it.

The user explicitly selected the live `/tickers` API when legacy PostgreSQL is
unavailable. Database access is not a prerequisite for migrating public candles.
New checks capture all four missing EIB/HHS sessions plus valid GEX 2019, HAG
2019, SHS 2022, and VNINDEX 2020 history. Six full-year API imports publish
1,499 candles into isolated SQLite/RustFS storage and pass exact FastAPI reads.
Main published data remains unchanged. VND 2020 still has one invalid API candle.

Next, capture complete coherent legacy snapshots for affected tickers, including
retained data and required historical indicator lookback. Verify transitions to
selected native providers before main publication; do not splice EIB/HHS rows
into a different existing adjustment basis. Continue to archive older API-exported
candles as Parquet. Handle private sync records separately from price migration.
The earlier blocker audit below describes native-provider/main-data state before
this newly verified API migration path; it does not show that public API history
is unavailable.

## Previous completion blocker checkpoint — 2026-10-04 ICT

After the Git checkpoints, read-only API and native-provider checks reproduce
all nine unavailable ranges across EIB/HHS/VND/GEX/HAG/SHS/VNINDEX. VPS still
omits the four known EIB/HHS traded sessions; the five older candidates still
contain invalid OHLC or conflicting daily records. Previously examined alternate
provider revisions would lose readable archives or introduce unverified values.
The configured legacy PostgreSQL endpoint still refuses connections, preventing
private inventory and sync-record migration. Main candles and metadata stay exact.

Full replacement remains blocked on complete verified historical data,
established provider/session semantics, and read-only legacy inventory/export.
These repeated external blockers prevent satisfying recent reliability and
historical access; successful local tests and commits do not complete the goal.
Detailed current evidence and remaining acceptance gates are in VALIDATION.md.

## Current status — 2026-10-03

The API, SQLite/Parquet reader, selected-provider adapters, workers, and Python
operational CLI are implemented. The API suite passes 278 tests; the SDK's offline
suite passes 251 (four live fundamental tests excluded). Local checks
and measured limitations are in [VALIDATION.md](VALIDATION.md). The unchanged
JavaScript suite previously passed 219/220; its remaining assertion expects a
Redis diagnostic header. The new service reports actual SQLite/S3 storage.

Minute-only selections OCB/PNJ/DGC/NAB now serve `1h`/`4h` from available minute
candles while retaining existing hourly series for other tickers. All 24 SDK
interval/indicator checks and their daily/hourly public chart rehearsals pass.
The full configured query matrix covers 71 series and all ten supported
intervals: 2,059/2,103 requests pass, with 44 explicit historical-gap/repair
errors across seven VN tickers. This is not complete replacement acceptance.

The frozen legacy comparison now covers 43,632 local VN daily rows versus
43,636 observed legacy dates across 59 selected tickers: 57 date sets match,
four sessions are missing in EIB/HHS, and zero extra dates are present. Three
legacy OHLC rows are invalid. All 59 checksummed captures replay offline; price
and volume differences are reported separately from observed date coverage.

Seven isolated VNDirect repairs recovered all 13 initially missing sessions.
Their 30 older archive objects / 7,116 indexed rows were checked against the
same native basis: 27 reconcile with exact original dates, while EIB 2022,
HHS 2019, and HAG 2019 contain invalid native OHLC. EIB/HHS would lose readable
old archives, so their candidates remain isolated. Five complete daily series
(HAG/MSN/STB/VDS/VPL) are now published locally with fresh completed-provider
checks, verified cold replacements, immutable S3 before-images/receipts, and
rollback backups. Nine sessions are recovered, HAG 2021 becomes readable, and
HAG 2019 retains its previous pending status. Minute handoffs are unchanged.

SQLite now contains 5,684,283 candles, including 43,632 VN daily rows, with 207
series, 137 source checks, 736 quality rows, 1,277 import receipts, and 58 handoffs.
The five typed unavailable ranges comprise VND 2020 and four recent
EIB/HHS sessions. S3 has 446 active objects / 346,572 indexed rows, including 442
published and four pending objects. Selected SDK and web checks pass for the new daily bases;
remaining data coverage and production cutover acceptance are still open.

CTR now uses one verified VNDirect daily revision across 747 recent candles and
six older partitions / 1,435 rows. Every previously published date is preserved;
2019 gains 250 recovered dates and the pending 2022 partition becomes readable
with 248 dates. An independent wide provider response matches all 2,182 candles
exactly. Its 41,528 minute candles remain unchanged; all 248 completed minute-day
comparisons stay below the explicit 1% price threshold. One native volume
disagreement remains recorded: July 15, 2026 is 158,907 at VNDirect versus 156,000
at VPS/DNSE. No factor or override is inferred. The other 5,683,536 candles and
58 recent responses remain exact. Twelve SDK comparisons and selected CTR chart
checks pass; equivalent backward API queries preserve exact SDK indicators,
while forward-query EMA warmup can differ. Bulk EMA requests still return HTTP
503 when their lookback crosses the EIB gaps. Current index and populated backup
restores pass at that checkpoint, including 29 dated-year receipts and the
unchanged gap guards.

HCM now additionally uses one verified VNDirect daily revision across 747
retained candles and five cold partitions / 1,186 rows. Its missing 2020 year
recovers all 252 original dates; an independent wide response exactly reproduces
all 1,933 recent/cold candles. Every previously published date remains present.
None of its recent volume differences exceeds 1%; DNSE corroborates its one
larger historical volume change. Minute data remains exact, and all 248 observed
completed dates stay below the 1% price-basis audit threshold. The other
5,683,536 candles and 58 recent responses remain exact. Twelve SDK comparisons,
selected daily/15-minute/weekly charts and profiles, and populated backup restore
pass. That checkpoint reconstructs 442 objects, 58 handoffs, 30 dated recoveries,
and nine gaps. Known bulk EMA and unrelated VNINDEX weekly errors remain open.

VIB and VTP now additionally use coherent VNDirect daily revisions across 747
and 740 retained rows and 11 older partitions / 2,391 rows. VIB 2019 gains 250
verified dates, its pending 2020 year becomes readable with all 245 original
dates, and VTP 2019/2022 gain 250/249 dates. Wide native responses reproduce all
1,926 VIB and 1,952 VTP candles exactly. Every previous published date is retained;
the other 5,682,796 candles and 57 recent responses remain exact. Both minute-day
audits cover 248 observed completed dates with zero differences above 1%.
DNSE corroborates VIB's one large historical volume change and 35 of VTP's 38
large recent/cold volume changes; three VTP disagreements remain explicit in
SQLite and immutable S3 evidence, alongside the unchanged CTR finding. Seventy-three
raw captures/reports are uploaded with checksum/readback verification. Twenty-four
SDK cases, selected charts/profiles, current index reconstruction, and populated
backup restoration pass. That checkpoint includes 33 dated recoveries and six
gap markers. Numerical/provider disagreements and production acceptance remain open.

NAB now additionally uses a coherent VNDirect daily revision across 741 recent
candles and four older partitions / 744 rows. Its missing 2022 year gains all
249 original dates; a wide native response reproduces all 1,485 recent/cold
OHLCV rows exactly. Every previously published date remains present. All measured
volume changes stay below 1%, and all 248 observed completed minute-day comparisons
remain below the price audit threshold. The other 5,683,542 candles and 58 recent
responses remain exact. Twelve SDK cases, selected charts/profiles, populated
backup restore, and actual packaged index reconstruction pass. The current index
has 34 dated recoveries and five gaps; 13 raw captures/reports are preserved in S3.

Both VND alternate candidates recover its 252 original 2020 dates in isolation,
but neither preserves all currently readable older archives. VNDirect's November
29, 2019 open is 2,628 above its high of 2,618, blocking the 250-row 2019 year.
DNSE repeats two conflicting December 27, 2022 candles and also contains invalid
2019 rows outside its requested 2020 recovery. Main VND retains its VPS revision,
747 recent rows, and four readable archives exactly. Neither provider candidate
is published, and no high/low clamp, arbitrary duplicate choice, or cross-provider
factor is applied. Detailed failure evidence is recorded in `VALIDATION.md`.

ACB and LPB now additionally use coherent VNDirect daily revisions across 747
retained rows each and ten older partitions / 2,357 rows. Their pending 2020
archives become readable with all 247/242 original dates; no previous published
date is lost. Independent responses exactly reproduce 1,928/1,923 recent/cold
OHLCV rows. Both minute-day audits cover 248 observed completed dates with zero
differences above 1%; minute rows and handoffs stay exact. Two larger ACB 2022
volume changes are independently corroborated by DNSE. Older prices differ,
including ACB 2019/2021 maxima near 20%; targeted DNSE values and exact differences
are preserved without inferring an adjustment factor or claiming legacy price
identity. The other 5,682,789 candles and 57 recent responses remain exact.
Twenty-four SDK cases, selected charts/profiles, populated backup restore, and
actual packaged index reconstruction pass. The four remaining pending objects
are GEX 2019, HAG 2019, SHS 2022, and VNINDEX 2020. GEX's alternate retained
window contains invalid native OHLC and stays isolated. VNDirect's separate REST
alias returns HTTP 401 on all three historical conflict-date probes.

Further isolated GEX/DNSE and HAG/DNSE candidates preserve their complete recent
date sets but cannot replace readable 2022 archives because of conflicting native
December 27 candles. SHS/VNDirect fails recent OHLC validation, and VNINDEX/VPS
fails in its readable 2021 archive. DNSE VNINDEX's observed 09:15 ICT daily
timestamps are now supported in the parser, scoped to that provider/index. A
fresh repair then correctly rejects its missing August 12, 2024 date, preserving
the existing series. Seven added regression cases, the 258-test suite, a rebuilt
wheel's 500-row native replay, and packaged archive/HTTP checks pass. Candidate
evidence is retained in immutable S3 objects; main data and the index stay intact.

Five additional 2018 daily partitions (VCB/MBB/VIC/HPG/VHM) are now published
locally with 1,153 verified candles on their existing VPS revisions. Eight flat
zero-volume placeholders from two documented HOSE closure dates are excluded
only for the four explicitly reviewed tickers; original bytes and proof receipts
remain in S3. All remaining original dates and volumes match. Price differences
remain measured, including substantial HPG/VHM adjustment disagreements.
All 5,684,283 original hot candles and all 59 recent HTTP responses are unchanged.
Twenty historical SDK comparisons pass; that checkpoint's wheel restores all 424 objects,
58 handoffs, 12 dated-year receipts, and 11 gaps into fresh SQLite, preserves the
original FPT receipt, and serves the six checked 2018 years. Populated restore
also passes.

VCB/MBB/VIC/HPG now additionally retain all 2,004 original dates from 2016–2017
in eight native Parquet partitions. All 2016 volumes match; two 2017 volume
differences are independently corroborated by both VNDirect and DNSE. Prices
remain on the existing VPS revisions with measured legacy-provider differences.
All original hot candles and all 59 recent responses remain exact. Thirty-two
additional SDK checks and 12 historical aggregation cases pass. The packaged CLI
restores the latest 432 objects, 58 handoffs, 20 recovery receipts, and 11 gaps;
both populated backup checkpoints restore exactly. Broader historical coverage,
older warmup windows, and provider adjustment semantics remain open.

Eight more selected tickers (DGC/BSR/VGI/SHS/CEO/IDC/CTR/VTP) now have 1,558
verified 2018 candles, preserving every original date without closure exclusions.
DGC's OHLCV matches the legacy API exactly; measured price differences remain
for the others. Both alternate selected providers corroborate BSR's one changed
volume. GEE's 2018 CSV returns 403 and the old API returns no rows; its captures
are preserved without a guessed listing date or fabricated history. All hot data,
operational series, and 59 recent responses remain exact. Twenty-eight SDK
comparisons and actual packaged recovery pass; both SDK and HTTP retain explicit
errors for CTR/VTP's then-unavailable 2019 years. That checkpoint's populated
backup and index restore all 440 objects, 58 handoffs, 28 dated-year receipts,
and 11 gaps; CTR 2019 is recovered in the newer checkpoint above.

VN repairs now validate only their requested retained window, retaining a separate
backward cursor. Both DNSE EIB/HHS candidates complete 747-candle daily windows,
recover all four remaining sessions, and pass fresh completed-provider checks in
isolated storage. Six of their ten older objects reconcile; both 2022 duplicate
conflicts and both invalid 2019 years remain inside requested old history. Neither
candidate is published over the existing readable archives. Six regressions and
an actual unpacked-wheel replay verify the fix without changing main data.

Six failed legacy daily years initially received unavailable-range records: CTR 2019,
HCM 2020, VIB 2019, VND 2020, and VTP 2019/2022. Their empty HTTP 200 responses
were misleading; reads requiring these ranges now return explicit HTTP 503.
Recent SMA/EMA reads for all five symbols remain exact. The records persist in
the existing quality table, S3 manifests, and populated backups without a schema
version change. A complete verified dated recovery clears only its own range.
All 5,645,774 candles and existing series, provider checks, and jobs are preserved.
That checkpoint's backup restores 716 quality records, including all six range markers.
These guards expose missing history; recovering that history remains open.

The full retained VN minute/daily comparison now exposes 12 interval-basis
findings across 1,537 completed dates. The operational SQL audit records these
price disagreements without inferred factors or automatic replacement. Nine
stocks have older discrepancies; TPB has four recent discrepant sessions, and
two indices have high/low disagreements with matching closes. Dated native
VHM/TPB probes confirm meaningful provider differences, not just CSV rounding.
Isolated DNSE one-year recoveries stage 14,005 VHM and 13,491 TPB minutes back to
July 6, then stop on missing arrays before reaching the required floor. Their
55,897 and 54,557 original rows remain unchanged. The main datasets and jobs
are unchanged; real coverage limitations still prevent either replacement.
The actual packaged CLI and a populated backup preserve all 12 audit findings.

At the earlier NAB expansion checkpoint, the universe reached 59 selected VN
tickers. NAB was published after
fresh isolated daily/minute checks, exact five-session VPS handoff evidence,
indicator/archive checks, and a comparison of all completed minute dates with
daily OHLC. Its 741 daily and 37,759 minute candles cover 248 observed minute
dates; no OHLC difference exceeds the explicit 1% audit threshold. Its invalid
2022 year is a seventh typed history gap, with original bytes preserved in S3.
Recent indicators and raw daily reads at the retention floor work; early daily
SMA/EMA requests requiring the missing 2022 lookback explicitly remain unavailable.
The unchanged public NAB chart/profile and six SDK cases pass. At that checkpoint SQLite contained
5,684,274 candles, 207 series, 137 source checks, 719 quality rows, 1,238 import
receipts, and 58 handoffs. That S3 index restores 419 objects / 340,357 rows,
seven recovery receipts, and seven gaps. Fifty-six VN minute handoffs use VPS
(48) or DNSE (eight); PLX, SSI, and VNINDEX remain frozen. The latest populated
backup restored these records exactly. NAB adds daily/minute ingestion only.

Read-only alternate-provider checks reproduce every original date in all six
earlier missing years on VNDirect. Its entire retained daily windows differ from
the current VPS records, including many volumes. DNSE fails four of the six
historical probes and omits one retained VTP date. Main provider revisions remain
unchanged; availability alone does not establish equivalent adjustment semantics.
Raw originals, native responses, and exact comparison results are preserved.

The alternative DNSE mirror repeats the same conflicting December 27, 2022
daily candles for SHS and NAB. Its official authenticated OHLC and working-date
endpoints both return 401 without an API key; a separate VNDirect price endpoint
times out. These probes do not recover any missing year or establish adjustment
equivalence. Responses and checksums remain available as local evidence.

`publish-index` now retries metadata publication under the shared archive-writer
lease, without downloading candles or pruning. Recovery's final receipt/gap
publication and compaction use the same helper. Three new regressions cover
failed pointer writes, a competing writer, and retained verified recovery state.
The actual populated CLI preserves all 5,684,274 candles and metadata, leaves
the existing pointer byte-identical, and reconstructs the full index exactly.
The unpacked wheel verifies the command, HTTP guards, and restored gap metadata
offline. No additional database schema or Compose service is required.

Historical ticker discovery now merges registered/imported/archive identities
into the existing symbol-to-name map, preserving existing names and source modes.
The running API exposes 22 previously missing VN index names without changing
the selected 59-ticker watchlist, candle responses, or operational metadata.
Health separates archived rows/bounds and pending repairs from existing local
counts. An archive-only index exposes all 137 archived series without worker
activation or live-verification claims, returns FPT 2017 exactly, and rejected
NAB 2022 at that checkpoint; Commit 12r subsequently recovered it. Three new
regressions and an actual packaged CLI/HTTP smoke pass.
The unchanged public VNINDEX daily/15-minute chart/profile and six FPT SDK
SMA/EMA cases pass. VNINDEX's minute snapshot remains frozen; rendering does not
resolve its provider-verification or freshness limitation.

At the preceding expansion checkpoint, OCB, PNJ, and DGC were
added after isolated daily/minute/archive checks. SQLite now retains 42,882 VN
daily rows and 2,967,025 minute rows. Fifty-five minute series have verified
handoffs (47 VPS, eight DNSE); PLX, SSI, and VNINDEX remain frozen. The latest
backup restores all 5,645,774 candles, 135 source-check records, and 57 total
handoffs, including the two global indices. The active S3 index restores
414 objects / 336,330 rows; 405 are published and nine remain pending. Coherent
VN daily archives contain 62,506 rows in 268 objects across 57 tickers.
At that checkpoint NAB passed its recent-window checks but remained isolated because its legacy
2022 daily file and pinned VPS recovery both contain invalid OHLC. New entries
explicitly ingest only daily/minute data; native hourly coverage is not claimed.
The three published additions pass 33 HTTP recent/history/boundary checks and
18 SDK SMA/EMA cases. PNJ's unchanged public daily/15-minute chart and volume
profile also pass with local API routing. Their recent daily values still have measured legacy
differences; this is not a claim that every retained candle is perfect.

The initial 55 selected VN daily series completed bounded bootstrap: initially 40,699
SQLite rows, then 40,695 after correcting the two index daily timestamp bases.
The retention rollover left 40,641 daily rows before the three additions.
VPL uses a regulator-verified listing date. This proves cursor/window completion,
not a complete holiday/suspension audit. Four crypto daily/hourly/minute series
completed bootstrap. After rollover and a bounded outage-gap update, their
2,104,161 retained minute rows remain gap-free after fresh bounded updates. All
12 crypto daily/hourly/minute series now have dated provider checks; seven
global daily series completed, including the Dow index used by the global
landing page, and all seven pass a fresh 40-candle provider update. These
overlap checks preserve revisions; they do not certify every historical candle.
The preceding populated backup restored all 129 source-check records and
5,514,348 candles exactly. The original 55 VN minute snapshots retained 2,848,973 rows before
rollover, then 2,837,840, covering every observed daily date within the
one-year minute window. Fifty-two have
verified ongoing provider handoffs (44 VPS, eight DNSE); three remain frozen pending
verification. Selected global minute snapshots retain partial available legacy
history; complete yearly coverage remains unverified. The S&P and Dow minute
series now have verified Yahoo handoffs and ordinary updates; four stock minute
series and gold remain independent snapshots.
Native Yahoo minute preflights reveal precision loss in the old API's CSV export.
The importer now supports explicit JSON captures with full price precision and
frozen receipts. An isolated five-session SPY JSON import reproduces every
native OHLC price at 1,951 shared timestamps; two volume disagreements and four
extra legacy timestamps still block an automatic handoff. The main snapshots
remain preserved. Wider JSON recapture and provider verification remain open.
The wider isolated JSON recapture reproduces all 340,023 existing minute timestamps
and volumes across six stock/index series. Every old price equals the rounded
full-precision JSON value. Native index overlaps match all 1,951 compared
candles each. The two main index replacements now retain 113,334 full-precision
rows, preserve immutable before-images in RustFS, and pass ordinary 40-candle
Yahoo updates. Their watchlist entries enable minute ingestion explicitly.
HTTP reads, SDK SMA/EMA parity, unchanged public web charts, S3 metadata restore,
and populated SQLite restore pass. Four stock overlaps still disagree with
native prices or volume; they remain preserved while their JSON snapshots stay
isolated. These imports begin in March 2026 and do not prove a complete year.
FPT daily history now has ten reconciled 2014–2023 archive partitions (2,256
rows) and 747 recent SQLite rows, all on one verified VNDirect revision.
VPS reproduced the dates but differed substantially from legacy prices before
2021; an isolated retained-window rehearsal and all-partition preflight preceded
the whole-series switch. No inferred scaling or provider mixing was used.
The full HTTP comparison preserves every ordinary legacy date; the two FPT
2018 HOSE closure placeholders are excluded under a reviewed, explicit scope,
with original bytes and references preserved in S3. Five historical volume
disagreements above 1% remain recorded for review; exact numerical identity
and corporate-action semantics are not claimed. Two old minute files
(453 rows) were migrated independently and verified through the HTTP reader.
Five additional tickers (VCB, MBB, VIC, VHM, HPG) now retain 5,930 older daily
rows in reconciled 2019–2023 Parquet partitions. Archived-only HTTP reads and
EMA200 across their retention boundaries pass. The first selected-universe
daily-history pass completed all 55 tickers, recording eight invalid legacy
inputs and five pending coverage disagreements. The resumable pass completed
with independent yearly errors. Four corrupt index years are now recovered:
VNINDEX 2019/2021 and VN30 2019/2020. After rollover and compaction,
255 coherent daily objects retain 59,474 rows across 54 tickers. Six recorded
invalid source years remain unresolved and nine objects remain pending;
recent data remains intact. This does not prove all older years are complete.
All 55 selected daily series and all 52 adopted minute series now pass fresh
40-candle updates on their pinned providers, with unchanged revisions and no
new timestamps. All daily OHLCV and minute volumes remain identical; 37 minute
candles have only floating-point representation differences below 1e-8.
HTTP reads and current completed-recheck health records pass for all 107 series.
A populated backup restores all 108 source-check records exactly. The bounded
`refresh` CLI command makes these checks repeatable without processing old jobs.
The index recoveries reproduce the original date sets with verified VNDirect
prices and preserve checksummed raw CSVs/evidence in S3. A fresh restore verifies
395 archive objects (320,224 rows), 52 provider handoffs, and all seven recoveries.
IDC 2019 is recovered from its existing VNDirect basis after reproducing all
244 original dates and matching 40 completed retained candles. Its 747 recent
rows, revision, provider, and timestamps remain identical. Historical HTTP reads,
boundary SMA/EMA200, fresh S3 index restoration, and a populated backup restore
pass. VND 2020, HCM 2020, CTR 2019, and VTP 2019/2022 still return invalid
historical candles on their pinned VPS provider. Their original files are
preserved. VIB 2019/2020 passes a VNDirect date-coverage preflight, but VPS
matches the old API's recent prices and volumes more closely; the recent VIB
basis remains unchanged. Recovering older years must preserve recent reliability.
SHS 2021 now reproduces all 250 original dates on its existing DNSE revision;
its original CSV and recovery proof are preserved. Fixing the daily download
boundary and explicitly restarting failed staging repairs SHS 2023's 185-row
partition without changing its 747 recent candles. SHS 2022 remains pending on
conflicting native timestamps; early-2023 requests that need the prior candle
still return 503. Verified 2021/2023 ranges, early-2024 SMA200, S3 index restore,
and populated backup restore pass. Restarting affects only unleased failed
staging for an explicitly selected series.
Whole-series VPS/VNDirect preflights both reject SHS's recent 500-candle page
on invalid OHLC; neither licenses a provider replacement. DNSE's recent values
also differ from the legacy API, including 398 volumes, which remains a measured
data-quality limitation rather than a reason to join providers.
Isolated three-year daily preflights complete for OCB, NAB, PNJ, and DGC with
exact legacy date sets. All four minute snapshots cover all 248 observed daily
dates in the year. PNJ/NAB pass the default exact overlap; OCB/DGC pass the
existing complete-session path with exact minute and fresh/retained daily OHLCV.
All four pass ordinary daily/minute updates. OCB, PNJ, and DGC subsequently
publish with reconciled cold daily history and minute warm-up; NAB initially remained
isolated on the invalid 2022 year and is now published with an explicit gap.
No threshold, timestamp, or price factor was
changed to obtain those handoffs.
Compaction replaces 218 fragments with 109 partitions and verifies 32,413
unchanged candles, including corrections and provenance. Original objects remain
retained. Global hourly migration also imports 24,184 candles across seven
tickers; snapshots are stale/partial, with 2023 unavailable and an invalid gold
2026 timestamp. Actual AAPL and both default index hourly charts now render.

Resumable migration handles actual headerless yearly/daily S3 files and bounded
legacy API CSV exports. A separate one-year FPT minute S3 trial imported 50,631
rows across 225 dates; the archive stops partway through August 27 and misses 23
later dates observed in daily data. Sampled VNDirect/DNSE overlaps differ from
this snapshot. These bases must not be joined silently. Public API exports can
capture recent minute dates absent from S3: a separate trial imported 56,027
candles across all 248 observed daily dates in the one-year window. This snapshot
was kept isolated during verification. The initial main import retained 56,253 FPT
minute candles across 249 dates, including the UTC floor date. Exact matches on
1,130 completed candles across five sessions permit an explicit handoff to VPS;
original imported values/provenance remain intact. An ordinary VPS update passed.
Another initial import supplied 56,751 VNINDEX minute candles across 249 dates; its provider
handoff is blocked by measured OHLCV/session disagreements. Both series have
archived September/October warm-up under their explicit snapshot revisions.
VCB's 56,003 imported minute candles cover every observed daily date; an exact
1,127-candle/five-session VPS overlap licensed its handoff. Wider imports use
the same requirement. DNSE later verified MBB and seven other initially rejected
series without weakening the criterion. Eight sparse series subsequently pass
the explicit full-session path: every original/provider minute timestamp and
OHLCV matches across five completed sessions, and aggregated minutes reproduce
fresh/retained daily OHLCV including total volume. All eight ordinary updates
preserve published candle values. PLX, SSI, VGC, and VNINDEX remain frozen after
measured disagreements on all three providers. VGC subsequently passes a
corroborated correction: VPS and DNSE confirm two native candles, with unchanged
15-minute and daily results. Its handoff and ordinary update pass; PLX, SSI,
and VNINDEX remain frozen. The initial SJC import supplied 1,097 recent daily rows
and 274 archived rows from the legacy API; its official endpoint still returns
403, so live update coverage is not claimed.

The actual public chart renders FPT daily and 15-minute data and volume profiles
through loopback API routing without page errors. VNINDEX's default volume
profile now also loads. The existing SDK needed an internal fix: complete API
ranges replace its short live overlay, which otherwise joined stale August S3
data to October candles and produced incorrect MAs. Public signatures/CLI
commands remain unchanged, with coherent whole-series archive fallback.
Crypto daily/15-minute charts and volume profiles and AAPL daily/weekly charts
also render. SDK parity checks pass with undefined indicators kept empty.
The global default Dow chart was empty because it was outside the initial
watchlist; its daily bootstrap is now included. Global minute coverage and a
verified ongoing update path remain acceptance requirements.

Populated local measurements cover 55-ticker daily requests, 10,000-minute
crypto requests, cold/warm history, and daily/minute retention boundaries.
An SQL coverage audit handles millions of candles without materializing them
in Python; its first run took 5.603 seconds with 30,015,488 bytes peak RSS.
It reports sparse hourly history and weekend VNINDEX observations honestly.
Starting workers now changes watchlist activation in one transaction.
Raw provider probes traced the index observations to mixed UTC/Vietnam midnight
timestamps with conflicting opens. Affected provider pages now fail validation;
VNINDEX/VN30 daily windows were rebuilt coherently on VNDirect, and the subsequent
audit reports no weekend index bars. No guessed date shift or price correction
was applied. Ordinary updates succeeded on sampled VPS and DNSE handoffs.

Per-series health now separates imported timestamps from successful provider
updates, retaining dated failed attempts and the completed/provisional rows of
each bounded published overlap. SQLite schema version 2 upgrades existing files
without changing candle values. Real FPT daily/minute and BTC minute updates
pass; a GEG snapshot remains frozen with an explicit handoff requirement.
All 199 stored series remain individually visible. Populated health reads take
about 2.2 seconds locally; this is measured overhead, not a production SLA.
A bounded four-client HTTP rehearsal completes 356 requests over 30.5 seconds,
with no failures or changed candle payloads. All selected VN daily/15-minute
series, six older series, crypto exports, global weekly charts, and health are
included. Response caching is disabled; archive files are warmed by baselines.

Production migration and cutover remain unfinished. Provider adjustment-policy
verification, session audits, wider historical and minute coverage, populated-
universe load/transfer-cost benchmarks, wider web/SDK flows, and production migration remain
open. Checkboxes describe the listed implemented units; a phase is accepted only
when its entire acceptance statement and open items have been satisfied.

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

- [x] Choose an editable initial watchlist: 55 VN, four crypto, seven global daily
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
  objects use 3,881,127 bytes; retained older versions/evidence/manifests are
  outside this footprint. Production-wide cost/load checks remain open.
- [ ] Establish each chosen provider's adjustment policy separately for daily and
  intraday data. Define how affected S3 history becomes consistent after an
  adjustment: validated revision from that provider or a verified adjustment
  mapping. A guessed scaling factor is not an acceptable shortcut.

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

- [ ] Inventory current PostgreSQL/S3 coverage per ticker/interval. Read existing
  archive CSV and metadata before deciding what needs export or conversion.
- [ ] Import recent windows to SQLite; publish older data under a separate Parquet
  prefix. Preserve legacy CSV URLs and metadata for direct SDK consumers.
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
- [ ] Recover invalid legacy daily years from independently verified current
  providers, retaining checksummed original CSV evidence and exact timestamp
  coverage. The recovery command and evidence restoration are implemented and
  all four index years and IDC 2019 pass on their pinned VNDirect providers;
  SHS 2021 passes on its pinned DNSE revision.
  Six recorded source years remain unresolved. VIB's sampled VPS page also
  fails validation; its recent VPS prices/volumes match the old API more closely
  than a whole-series VNDirect replacement. Keep the recent basis intact while
  investigating verified historical recovery.
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
- [ ] Resolve VNINDEX's auction/session/volume differences and verify provider
  handoffs for the wider selected minute universe. Do not weaken the exact
  verification requirement merely because the index chart now displays.
  A full DNSE rebuild rehearsal in an isolated database stops after 23,453
  staged rows on an invalid May 5, 2026 minute candle. Across its 103 complete
  observed sessions, it also omits 162 legacy timestamps and adds 22. Preserve
  the native response and staging; the main 56,523-row snapshot remains intact.

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
- [ ] Resolve native-minute allocation disagreements for PLX/VGC/SSI using
  independently corroborated corrections and retained original evidence. Their
  completed daily aggregates match; this does not license arbitrary minute
  volume/price changes. VNINDEX also needs timestamp and aggregate review.

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
  Commit 12g subsequently publishes its recent windows with an explicit gap.
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

### Commit 12e — `feat(quality): expose retained minute and daily basis disagreements`

- [x] Compare completed local minute-session OHLC with observed daily OHLC in
  bounded SQL. Materialize session aggregates before indexed timestamp lookups;
  never materialize every minute candle in Python for this check.
- [x] Record disagreements above the explicit 1% review threshold, with dates,
  compared prices, sample counts, and daily provider identity. Exclude unfinished
  sessions and inactive tickers. Treat findings as observations, not dividends.
- [x] Resolve only audit findings after data changes; preserve independent
  provider-revision findings. Verify no automatic job, candle, or state changes.
- [x] Audit all 58 retained VN series, probe dated provider responses for VHM/TPB,
  and attempt complete retained-window recovery in isolated staging. Preserve
  incomplete downloads and original published records on missing native history.
- [x] Verify the actual packaged CLI, full API suite, lint/formatting, and exact
  quality/source-check preservation through a populated SQLite restore.
- [ ] Obtain complete verified provider history or an independently established
  adjustment policy before resolving the remaining interval-basis findings.
  Recent five-session equivalence does not license guessing older corrections.

Acceptance: operational audits make retained price-basis disagreements visible,
and any eventual repair proves the entire required window before publication.

### Commit 12f — `fix(history): reject known unavailable historical ranges`

- [x] Store typed, bounded unavailable ranges in the existing quality table;
  expose them through operational status and additive HTTP health metadata.
- [x] Reject empty, skipped, joined, or indicator-lookback reads requiring a
  known missing range. Preserve recent and satisfied directional limit reads.
- [x] Record failed older daily imports without overwriting current candles or
  already verified archived ranges. Publish their metadata using the existing
  archive-writer lease; preserve original failure evidence.
- [x] Validate and merge optional gap metadata during S3 index reconstruction.
  Older manifests must not erase newer local observations; keep schema version 2.
- [x] Clear a range only after complete dated recovery verifies its native dates,
  publishes its archive and receipt, and preserves the retained head. Failed
  recovery and successful recovery of a different year must preserve the marker.
- [x] Backfill six existing failed years; verify 18 historical HTTP error cases,
  ten recent SMA/EMA reads, SDK parity, S3 reconstruction, and populated restore.
- [x] Verify full tests, lint/formatting, offline build, and actual packaged reads.
- [ ] Recover the unavailable years from complete independently verified data.

Acceptance: known missing history cannot masquerade as a successful empty or
partial result; markers are recoverable metadata and require proven recovery.

### Commit 12g — `feat(data): publish NAB recent windows with an explicit historical gap`

- [x] Recheck isolated NAB daily/minute windows and ordinary updates on VPS.
  Preserve the existing 1,046-candle/five-completed-session handoff proof and
  independently verified first-listing date; enable daily/minute ingestion only.
- [x] Verify all 248 observed minute dates match the one-year daily date set;
  audit completed session OHLC against daily prices without inferring adjustments.
- [x] Publish 741 retained daily and 37,759 minute candles, five coherent archive
  objects, handoff/import receipts, and dated provider checks in a local transaction.
  Back up first and preserve the other published datasets.
- [x] Preserve the original bad 2022 CSV in S3 and explicitly mark that year
  unavailable. Recent reads must work; early daily indicator lookback must fail
  clearly when it requires the missing range.
- [x] Verify 15 HTTP cases, six actual SDK SMA/EMA cases, unchanged public daily/
  15-minute charts and volume profile, packaged watchlist/bootstrap behavior,
  exact S3 index reconstruction, and a populated backup/restore.
- [x] Probe alternate-provider coverage for the six preceding missing years and
  compare every retained daily candle before considering any provider replacement.
  Keep raw evidence; reject availability alone as proof of price-basis equivalence.
- [ ] Recover the remaining preceding missing years without guessed corrections
  or an unverified change to the reliable recent provider basis. NAB 2022 is
  recovered and independently verified in Commit 12r.

Acceptance: broader verified recent coverage is available immediately, with
explicit and recoverable older-range limitations and unchanged original data.

### Commit 12h — `fix(archive): serialize and retry metadata publication`

- [x] Use the existing global archive-writer lease for recovery's final receipt/
  resolved-gap publication and compaction's final manifest publication.
- [x] Add `publish-index` to retry metadata from the current SQLite index without
  provider downloads, Parquet uploads, candle pruning, or a new service.
- [x] Preserve verified local recovery state and the previous remote pointer on
  a busy writer or failed pointer write; allow metadata-only retry afterward.
- [x] Verify failed publication and competing writers with three regressions;
  pass all 237 tests, lint/format checks, and the distribution build.
- [x] Run the actual CLI against populated local SQLite/RustFS and reconstruct
  all 419 objects, 58 handoffs, seven recoveries, and seven gaps exactly.
- [x] Verify the actual unpacked-wheel CLI, unchanged seeded data, recent HTTP
  reads, unavailable-range errors, and reconstructed gap guards offline.
- [x] Record native mirror conflicts and official endpoint access limitations;
  preserve raw responses without changing main provider revisions or candles.

Acceptance: completed local work remains recoverable after metadata publication
fails, and metadata writers cannot overwrite an active writer's pointer.

### Commit 12i — `test(data): audit retained VN daily coverage and stage complete repairs`

- [x] Add a reproducible read-only comparison for every selected VN daily window.
  Preserve checksummed public legacy responses and verified resumable receipts;
  prove unchanged local daily rows and operational metadata.
- [x] Report exact date sets, missing/extra sessions, invalid legacy OHLC, and
  separate numerical discrepancies. Do not equate date parity with a calendar
  or identical provider adjustment/volume policies.
- [x] Verify the 13 missing sessions with bounded native probes on all three
  selected VN providers; preserve raw evidence and surrounding-value comparisons.
- [x] Stage seven complete VNDirect repairs in isolated SQLite/filesystem storage.
  Preserve every original date; verify the recovered sessions, recent SMA/EMA,
  and ordinary completed-provider checks on unchanged candidate revisions.
- [x] Record confirmed unavailable sessions atomically under the existing quality
  schema; verify 33 explicit HTTP rejections and unchanged 59-ticker recent reads.
- [x] Reconstruct all 419 active archive objects and 20 typed gaps exactly;
  preserve a populated pre-publication backup and verify a new backup/restore.
- [x] Pass lint/format checks across 50 files and build the distribution offline.
  Runtime code is unchanged; its preceding complete suite passes 240 tests.
- [ ] Reconcile the seven candidates' 30 older archive objects / 7,116 indexed
  rows to their complete VNDirect bases, including indicator warm-up boundaries.
  Twenty-seven reconcile with exact dates; invalid native OHLC blocks EIB 2022,
  HHS 2019, and HAG 2019. Their raw responses and original objects are preserved.
- [ ] Publish only after fresh candidate/head/archive checks, immutable before-
  images, and rollback backups. Resolve only the proven recovered ranges; verify
  real web/SDK clients and populated/index reconstruction after publication.
  Five candidates are published and verified; EIB/HHS remain isolated because
  replacing them would lose previously readable older history.

Acceptance: the recent daily universe has independently observed date coverage,
confirmed unavailable sessions are honest API errors, and recovered windows use
complete coherent revisions before they replace published data.

### Commit 12j — `feat(data): publish verified retained daily replacements`

- [x] Publish HAG/MSN/STB/VDS/VPL atomically with complete VNDirect daily windows
  and 19 verified cold objects; retain HAG 2019's existing pending object.
- [x] Preserve original hot data in immutable S3 before-images, original archive
  bytes, per-ticker checksummed receipts, and a populated before-publication backup.
- [x] Require fresh completed 40-candle provider checks before and after local
  publication; resolve only nine actually recovered session guards.
- [x] Recover HAG 2021's 250 archived candles; retain four EIB/HHS session guards
  and seven older unavailable years. Retire one obsolete HAG archive job.
- [x] Prove all 5,680,946 unrelated original candles, including provenance and
  update timestamps, remain exactly unchanged.
- [x] Verify 90 real HTTP cases (83 success / seven explicit unavailable reads),
  30 SDK interval/SMA/EMA comparisons, and the public HAG chart/volume profile.
- [x] Replay all 59 original legacy captures into a separate comparison report:
  57 exact observed date sets, four missing dates, and zero extra dates.
- [x] Verify fresh S3-index reconstruction and populated backup/restore after
  publication; record schema/integrity, exact metadata, counts, and checksums.
- [x] Pass lint/format checks and build offline; preceding runtime suite passes
  240 tests. No runtime or schema change is introduced by this data publication.

- [x] Probe EIB/HHS full DNSE repairs independently. Preserve 500 staged candles
  per ticker and conflicting December 27, 2022 replies; verify no main changes.
- [x] Bound validation to the requested retained window; preserve a separate
  pagination cursor and complete both candidates without selecting old duplicates.
- [ ] Independently verify older DNSE conflicts/invalid OHLC before considering
  either remaining daily publication; six of ten archives reconcile safely.

Acceptance: locally published replacements improve recent date coverage without
losing previously readable historical partitions or changing unrelated candles.

### Commit 12k — `fix(workers): scope VN repairs to their retention floor`

- [x] Pass the repair floor to VN provider normalization. Preserve the oldest
  selected native timestamp as a cursor independently from retained candles.
- [x] Reject invalid/conflicting requested candles, unverified boundary timestamp
  conventions, non-advancing cursors, and no-data without observed coverage.
- [x] Allow an older terminal page to finish only previously validated staging;
  retain the existing completed-coverage and current-tail publication checks.
- [x] Add six regression cases for old defects/missing values, requested duplicates/invalid OHLC,
  empty terminal pages, and old-only initial responses. Pass all 246 tests.
- [x] Resume the exact EIB/HHS checksummed DNSE pages without discarding existing
  staging. Each publishes 747 candles in isolated storage and recovers two dates.
- [x] Verify actual ordinary 40-candle updates on unchanged candidate revisions,
  eight HTTP reads, six reconciled older objects, and exact unchanged main state.
- [x] Preserve four remaining failed cold objects: both 2022 duplicate conflicts
  and both invalid 2019 native years. No main provider replacement is published.
- [x] Verify the unpacked wheel's real CLI initialization and both complete
  replayed 500/247-row worker repairs plus ordinary updates; pass lint/build.

Acceptance: a defect outside the configured window cannot invalidate an otherwise
complete recent repair; defects within requested history remain explicit failures.

### Commit 12l — `feat(data): recover five verified 2018 daily archives`

- [x] Capture original public CSV/API years and exact native responses in isolated
  storage; verify current provider/revision and actual completed 40-candle updates.
- [x] Review historical venue evidence and the two documented HOSE closure dates
  for VCB/MBB/VIC/HPG. Permit only finite, positive, flat zero-volume placeholders;
  preserve the existing FPT scope and receipt format unchanged.
- [x] Add full recovery/restore regressions for all five reviewed symbols and an
  unreviewed-ticker rejection; pass all 251 API tests, lint, and offline build.
- [x] Verify the five native years preserve all remaining legacy timestamps and
  volumes. Record numerical price disagreements without inferred adjustment factors.
- [x] Publish five immutable Parquet objects / 1,153 rows with verified S3 evidence,
  archive/series leases, transactional state checks, and a rollback backup.
- [x] Verify all 5,684,283 original hot candles and all operational series/checks/
  quality/jobs/handoffs remain exact; all 59 recent HTTP responses are unchanged.
- [x] Compare 20 real historical SDK SMA/EMA cases, respecting the existing SDK
  date-range tail limit and Rust short-series indicator behavior.
- [x] Reconstruct the exact S3 index and restore a populated backup. The actual
  packaged CLI rebuilds 424 objects, 58 handoffs, 12 recovery receipts, and 11 gaps;
  six 2018 HTTP reads pass and the known EIB session still returns explicit 503.
- [ ] Extend older served history beyond the currently verified partitions and
  resolve measured provider adjustment differences before production cutover.

Acceptance: old daily requests gain verified, coherent cold history while retained
data and existing interfaces remain unchanged; raw evidence and limitations persist.

### Commit 12m — `feat(data): extend verified pre-2018 daily history`

- [x] Capture original CSV/API and native 2016–2017 years for VCB/MBB/VIC/HPG
  in isolated storage; preserve every original timestamp without exclusions.
- [x] Check all volume/price disagreements. Corroborate the two August 15, 2017
  volumes independently through both VNDirect and DNSE; retain all raw captures.
- [x] Publish eight Parquet partitions / 2,004 rows on the existing VPS revisions
  with completed-provider checks, immutable originals, leases, and rollback backups.
- [x] Verify all original hot values/provenance/update timestamps and all existing
  operational rows remain exact; recent responses for all 59 symbols are unchanged.
- [x] Pass 32 additional historical SDK SMA/EMA comparisons and 12 actual weekly,
  two-week, and monthly HTTP aggregation cases across the archive year boundary.
- [x] Verify both S3-index and populated backup restores. The actual packaged CLI
  rebuilds the latest 432 objects, 58 handoffs, 20 recovery receipts, and 11 gaps;
  five 2016 HTTP reads pass and known unavailable sessions remain explicit 503.
- [ ] Preserve the remaining served history across the selected universe and
  complete older indicator warmup coverage before claiming production replacement.

Acceptance: the added years retain original date coverage on coherent current
revisions, expose measured numeric differences, and preserve recent series.

### Commit 12n — `feat(data): extend 2018 history across eight selected tickers`

- [x] Independently capture public CSV/API and native data for a bounded nine-ticker
  batch. Recover DGC/BSR/VGI/SHS/CEO/IDC/CTR/VTP with every original date retained.
- [x] Preserve GEE's denied CSV and empty API response without interpreting these
  as a listing date, complete coverage proof, or permission to invent candles.
- [x] Verify DGC's exact legacy OHLCV; measure the other provider price differences.
  Corroborate BSR's one changed volume through both VNDirect and DNSE.
- [x] Publish eight immutable Parquet partitions / 1,558 rows on the unchanged daily
  revisions, with completed-provider checks, scoped leases, and rollback backup.
- [x] Verify all 5,684,283 original hot candles and operational rows remain exact;
  recent HTTP responses for all 59 selected tickers are unchanged.
- [x] Pass 28 SDK SMA/EMA comparisons. Verify CTR/VTP's unavailable 2019 requests
  remain explicit HTTP/SDK errors with no concealed archive fallback.
- [x] Verify exact current index and populated backup restores. The packaged CLI
  restores 440 objects, 58 handoffs, 28 recovery receipts, and 11 gaps; nine 2018
  HTTP reads pass, including the existing FPT receipt and unchanged gap guards.
- [ ] Resolve the separately recorded missing historical years through complete
  independently verified series before production replacement acceptance.

Acceptance: more selected tickers gain coherent original-date history without
changing current series or hiding independently unavailable years.

### Commit 12o — `feat(data): reconcile CTR daily history on a verified native revision`

- [x] Stage the complete 747-row VNDirect retained window and reconcile all five
  existing older objects without dropping any previously published date.
- [x] Recover CTR 2019 from independently verified original timestamps; publish
  its 250 native candles and make the previously pending 248-row 2022 year readable.
- [x] Independently reproduce all 2,182 recent/cold OHLCV candles in one wider
  provider response; verify all 248 completed minute-day comparisons remain below 1%.
- [x] Corroborate four changed volumes with DNSE and preserve the unresolved
  July 15, 2026 disagreement in SQLite and immutable S3 evidence without an override.
- [x] Publish one coherent daily revision with scoped leases, old hot before-image,
  retained archive originals, immutable receipts, and a populated rollback backup.
- [x] Verify the other 5,683,536 candles, unrelated operational rows, and 58 recent
  responses remain exact. Replay all 59 frozen daily comparisons without writes.
- [x] Pass six recent and six historical SDK cases against equivalent API query
  windows; document forward/backward EMA warmup differences. Verify selected CTR
  daily/15-minute/weekly charts and profiles; preserve unrelated HTTP 503 guards.
- [x] Restore 441 objects, 58 handoffs, 29 dated recoveries, and 10 gaps with the
  actual packaged CLI. Verify exact populated backup restoration, including the
  unresolved volume finding; cold index reconstruction has no hot candles or jobs.
- [ ] Resolve the remaining six missing older years, seven pending objects, four
  recent sessions, and provider discrepancies before production replacement acceptance.

Acceptance: CTR's original date coverage improves on one coherent provider revision,
with measured numeric changes, recoverable originals, and explicit unresolved evidence.

### Commit 12p — `feat(data): recover HCM 2020 with coherent daily history`

- [x] Stage a complete 747-row VNDirect retained window and reconcile every
  original date in all four previously published HCM daily archives.
- [x] Independently capture original 2020 CSV/API timestamps and recover all
  252 native candles; clear only the verified HCM 2020 unavailable range.
- [x] Reproduce all 1,933 candidate candles in one wide native provider response.
  Measure recent/cold price and volume changes; corroborate the one historical
  volume difference above 1% through DNSE without changing native values.
- [x] Verify all 248 observed completed minute-day comparisons stay below 1%;
  preserve the minute series and handoff exactly.
- [x] Publish the daily revision with scoped leases, immutable S3 original hot
  before-image, retained old archives, dated evidence receipts, and rollback backup.
- [x] Verify every other candle and operational row remains exact, including
  58 recent responses and the existing CTR volume finding. Replay all 59 frozen
  daily captures without changing main data.
- [x] Pass six recent and six historical SDK SMA/EMA cases against equivalent
  API queries; record cross-query EMA warmup differences. Pass selected public
  daily/15-minute/weekly chart and profile checks while preserving known errors.
- [x] Verify the actual packaged index restore and populated SQLite restore:
  442 objects, 58 handoffs, 30 dated recoveries, nine gaps, and old recovery evidence.
- [ ] Recover the other five unavailable older years and four recent sessions;
  resolve pending archives and provider discrepancies before production acceptance.

Acceptance: HCM gains its complete original-date 2020 history on one coherent
native revision, with recoverable originals and measured provider differences.

### Commit 12q — `feat(data): recover VIB and VTP historical gaps coherently`

- [x] Stage complete VNDirect retained windows and reconcile all eight original
  older objects without losing previously published dates. VIB 2020 retains its
  245 dates and becomes readable on the new coherent revision.
- [x] Independently capture original yearly CSV/API dates; recover VIB 2019 and
  VTP 2019/2022 with 250/250/249 native candles, clearing only their gap markers.
- [x] Reproduce all 1,926 VIB and 1,952 VTP candidate OHLCV rows in independent
  wide provider responses. Verify all 248 completed minute-day comparisons per
  symbol remain below 1%, retaining minute rows and handoffs exactly.
- [x] Corroborate VIB's one large historical volume change and 35 VTP changes
  through DNSE. Independently reproduce three remaining VTP volumes in targeted
  VNDirect reads; retain them as unresolved findings without factors or overrides.
- [x] Atomically publish both daily revisions with scoped leases, immutable S3
  original hot before-images, retained archive originals, dated recovery/rebaseline
  receipts, and rollback backup. Checksum and read back 73 raw captures/reports.
- [x] Verify all other 5,682,796 candles and operational rows remain exact,
  including 57 recent HTTP responses and the existing CTR volume finding.
- [x] Pass 12 recent and 12 historical SDK cases against equivalent API queries;
  preserve missing early indicators and document cross-query EMA warmup differences.
  Pass selected daily/15-minute/weekly charts and volume profiles for both symbols.
- [x] Verify exact populated SQLite restore and actual packaged index restore:
  445 objects, 58 handoffs, 33 dated recoveries, six gaps, and old recovery receipts.
  Serve eight checked historical years while preserving remaining HTTP 503 guards.
- [ ] Recover VND 2020, four recent EIB/HHS sessions, and the other
  pending archives; resolve provider discrepancies before production acceptance.

Acceptance: three unavailable years and one pending year become readable with
original dates preserved, coherent daily revisions, and explicit disagreements.

### Commit 12r — `feat(data): recover NAB 2022 and preserve blocked VND candidates`

- [x] Stage NAB's complete 741-row VNDirect retained window and reconcile every
  original date in its three previously published older partitions.
- [x] Capture original 2022 CSV/API dates and recover all 249 native candles;
  reproduce all 1,485 recent/cold OHLCV rows in a separate wide provider response.
- [x] Measure recent/cold price and volume differences; no volume change exceeds
  1%. Verify all 248 completed minute-day comparisons stay below 1%, preserving
  37,759 minute rows and the existing handoff exactly.
- [x] Publish one coherent revision with scoped leases, immutable original hot
  before-image, retained archives, recovery/rebaseline receipts, 13 checksummed
  raw captures/reports, and a populated rollback backup. Clear only NAB 2022's gap.
- [x] Verify all other 5,683,542 candles and operational rows remain exact,
  including 58 recent HTTP responses and all existing provider volume findings.
- [x] Pass six recent and six historical SDK SMA/EMA cases, selected public
  daily/15-minute/weekly charts and profiles, and exact populated SQLite restore.
- [x] Verify actual packaged reconstruction: 446 objects, 58 handoffs, 34 dated
  recoveries, five gaps, zero hot candles/jobs, and nine historical HTTP reads.
  Preserve remaining VND/EIB HTTP 503 guards and old recovery evidence.
- [x] Stage complete VND VNDirect and DNSE recent windows; both recover 2020
  independently. Preserve captures and diagnose native 2019 OHLC violations and
  repeated 2022 duplicate conflicts. Keep both candidates isolated and VND intact.
- [ ] Resolve VND 2020 without losing readable archives, the four EIB/HHS recent
  sessions, pending objects, and provider discrepancies before production acceptance.

Acceptance: NAB gains its complete original-date 2022 year on a coherent revision;
unpublishable VND alternatives preserve the existing readable daily series.

### Commit 12s — `feat(data): reconcile ACB and LPB pending 2020 archives`

- [x] Stage both complete 747-row VNDirect recent windows and reconcile all ten
  older objects with their exact original date sets. ACB/LPB 2020 become readable
  with 247/242 dates; preserve all originals and previous published dates.
- [x] Independently reproduce all 1,928/1,923 candidate OHLCV rows. Corroborate
  both large ACB volume changes through DNSE, and measure older price changes
  separately, including targeted DNSE comparisons for ACB 2019/2021 and LPB 2019.
- [x] Verify 248 completed observed minute-days per symbol stay below 1%,
  preserving all 55,664/47,476 minute rows and handoffs exactly.
- [x] Atomically publish both daily revisions with scoped leases, immutable hot
  before-images, retained old archives, rebaseline receipts, 28 checksummed native
  captures/reports, and rollback backup. Retire only the two old archive jobs.
- [x] Verify all other 5,682,789 candles and unrelated operational rows remain
  exact, including 57 recent HTTP responses and existing CTR/VTP findings.
- [x] Pass 12 recent and 12 historical SDK cases, selected daily/15-minute/weekly
  charts and profiles, full populated backup restore, and actual packaged index
  restoration with 446 objects, 58 handoffs, 34 recoveries, and five gaps.
- [x] Serve 11 checked historical years through the actual wheel, while retaining
  VND/EIB HTTP 503 guards and earlier recovery receipts. Preserve four additional
  targeted price-evidence files in immutable S3 objects with readback checks.
- [x] Preserve GEX's failed native candidate and diagnose its May 13, 2025 OHLC
  violation. Preserve three VNDirect REST-alias HTTP 401 response captures.
- [ ] Resolve VND 2020, four recent EIB/HHS sessions, the four pending archives,
  and remaining provider discrepancies before production replacement acceptance.

Acceptance: both pending 2020 partitions become readable on coherent revisions,
with original dates and bytes preserved and numerical differences explicit.

### Commit 12t — `fix(providers): parse verified DNSE VNINDEX session timestamps`

- [x] Inspect the remaining GEX/DNSE, SHS/VNDirect, VNINDEX/VPS, and HAG/DNSE
  alternatives in isolated databases. Preserve full native responses and exact
  invalid-OHLC/duplicate evidence without changing readable main series.
- [x] Accept the captured 02:15 UTC daily convention only for DNSE VNINDEX,
  alongside its existing UTC-midnight/02:00 conventions. Preserve market dates,
  index price units, volume, and backward cursor. Reject unverified other series.
- [x] Add regressions for the native timestamp transition, invalid OHLC,
  conflicting duplicates, provider/symbol scope, and completed daily date loss.
- [x] Verify a fresh actual DNSE worker repair parses the response but rejects
  missing August 12, 2024. Keep all 747 existing VNINDEX dates and their OHLCV /
  provider/revision values in the candidate, and main data/metadata unchanged.
- [x] Pass all 258 API tests, Ruff lint/54-file format checks, and offline builds.
  Replay 500 frozen native rows through the actual rebuilt wheel; reconstruct
  446 archive objects and serve 11 historical years with gap guards preserved.
- [x] Preserve raw candidate captures/reports in immutable S3 objects with SHA-256
  and readback checks. Verify the active index pointer and SQLite snapshot stay
  unchanged; no provider switch, archive publication, or production routing change.
- [ ] Obtain complete valid provider data for remaining ranges and reconcile
  numerical discrepancies before production replacement acceptance.

Acceptance: the verified index timestamp convention works without shifting dates
or weakening candle/coverage validation; unpublishable alternatives stay isolated.

### Commit 12u — `fix(history): serve hourly queries for minute-only selections`

- [x] Derive `1h`/`4h` from minute candles only when no hourly series or active
  hourly archive exists. Preserve native hourly preferences, pending repair
  errors, directional limits, complete aggregation buckets, and basis checks.
- [x] Add five storage regression cases for SQLite/Parquet, forward/backward
  reads, native hourly precedence, and pending archives; add an HTTP regression
  for aliases and legacy CSV price scaling.
- [x] Audit all 71 configured series across ten supported intervals and three
  indicator modes, using daily-only requests for SJC. Preserve all 2,103 raw
  responses and record 44 HTTP 503 failures rather than treating them as passes.
- [x] Verify all prior 843 audit responses and 396 existing hourly responses
  remain byte-identical. Verify the 24 newly populated hourly queries against
  independently grouped native minutes and the unchanged Python SDK.
- [x] Exercise OCB/PNJ/DGC/NAB daily/one-hour charts and volume profiles in the
  public website with isolated localhost routing. Preserve the unsuccessful
  four-hour UI attempt: the public chart has no four-hour button.
- [x] Pass all 264 API tests, Ruff lint/55-file formatting, offline builds, and
  actual packaged hourly SQLite/Parquet checks. Reconstruct the current archive
  index and serve 11 historical years with known gap guards intact.
- [x] Measure a cold/warm RustFS profile read for all 59 selected VN tickers on
  October 2, 2025: 11,866 minutes, identical values, 815.65/381.49 ms total wall
  time. Record the separate failed 2020-minute experiment as absent coverage.
- [x] Refresh the localhost:3001 rehearsal API and stop the temporary shadow
  process. Verify all 24 corrected hourly responses and seven representative
  gap errors stay exact, with SQLite and the active archive pointer unchanged.
- [ ] Resolve the seven tickers' historical query failures and remaining data
  discrepancies; complete private inventory and cloud performance acceptance.

Acceptance: minute-only selections support hourly API/SDK/web queries without
changing existing series or pretending their minute history is complete.

### Commit 12v — `fix(workers): catch up VN intraday updates after downtime`

- [x] Keep ordinary minute/hourly checks at 40 candles. When newer data has
  outrun that overlap, retry once on the current provider with at most 1,000
  candles. Preserve the existing crypto catch-up behavior and VN daily policy.
- [x] Require an actual published-tail timestamp in the expanded page before
  appending. Queue existing durable recovery when it remains absent; preserve
  published rows, revisions, and provenance on missing/truncated pages or errors.
- [x] Compare all stored overlap covered by an expanded page. Corroborated older
  price revisions trigger staged recovery even outside the usual last-50-bar
  comparison; representation noise does not trigger a complete replacement.
- [x] Avoid guessed trading calendars: an observed sparse/weekend overlap works
  without expansion or fabricated candles. Preserve provider-switch recovery
  instead of merging an alternate provider into the published adjustment basis.
- [x] Add fourteen regressions for minute/hourly catch-up, the page cap, providers
  ignoring the requested count, sparse weekends, expanded failures, alternate
  providers, source-check outcomes, unchanged old candles, and lease release.
- [x] Capture/replay six real native pages across VPS/VNDirect/DNSE and execute
  six actual live isolated-worker checks. Each recovers 150 newer observed
  timestamps with exact native OHLCV; preserve request/response evidence.
- [x] Pass 278 API tests, Ruff lint/55-file formatting, and offline builds. Verify
  the actual wheel's catch-up/preservation checks and existing hourly/history
  regressions; preserve a checksum-addressed copy of the tested wheel.
- [x] Audit current SQLite integrity and calendar retention: all ten populated
  source/interval groups have zero candles outside their configured hot windows.
  Verify main daily/operational metadata stays unchanged during all rehearsals.
- [ ] Complete unresolved provider/calendar/history/private-inventory acceptance;
  the scoped catch-up proof does not certify the whole retained data set.

Acceptance: a bounded VN restart fills observed intraday data or preserves the
published series with a dated recovery/error, without silently skipping its tail.

### Commit 12w — `fix(migration): pin legacy API export read paths`

- [x] Keep existing default exports and frozen receipts compatible. Add an
  explicit database-backed public API option, without a direct PostgreSQL client.
- [x] Record the read backend and revision; reject a backend switch within an
  existing snapshot, including a new period or old archive-only receipts.
- [x] Add seven regressions for pinned requests, resumable captures, invalid
  inputs, same/new period switches, and pre-existing hot/cold receipts.
- [x] Capture independent wide and yearly daily exports for five affected
  tickers. Verify all 9,665 OHLCV rows, preserve old/alternate captures, and check
  40 exact completed VPS candles per ticker. Keep main data exact.
- [x] Implement replayable daily snapshot handoff evidence with completed-tail,
  provider identity, mutation/lease, append, correction, and restore checks.
- [x] Publish complete candidates only after fresh handoff, readable-date
  preservation, immutable originals, backup, and API/SDK/browser verification.
- [ ] Recover VNINDEX/VND invalid public history without guessed corrections;
  broader reliability and production cutover acceptance remain open.

Acceptance: public exports preserve one explicit read path and complete validated
snapshots can advance under a verified provider without erasing served history.

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
- [ ] Recapture selected global minute snapshots in JSON and resolve actual
  timestamp/volume/price disagreements before licensing live Yahoo updates.
- [x] Extend the default exact-overlap handoff to Yahoo with UTC minute finality,
  market/provider validation, preserved provenance, race/lease guards, and
  restoration checks. Enable and verify the two passing index minute series;
  preserve failed stock/gold snapshots and restrict VN correction proofs to VN.
- [x] Preserve undefined API indicators as missing SDK values. Rehearse crypto
  daily/minute/15-minute and global daily/weekly SMA/EMA and public web controls.
- [x] Record populated local recent, multi-ticker, cold/warm historical, and
  retention-boundary latency, memory, database size, and cached object bytes.
  Keep concurrent-load and full-universe S3 transfer-cost acceptance open.
- [x] Run a bounded concurrent HTTP rehearsal covering all 55 selected VN
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
- [ ] Record cold/warm latency, memory, SQLite size, and archive transfer costs for
  the chosen ticker universe; distinguish compatibility from identical latency.
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
